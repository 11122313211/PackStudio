"""色域采样与可视化（TECH-DESIGN §4.11）。

- RGB 矩阵/TRC profile：N³ 立方均匀采样；CMYK LUT profile：grid⁴ 网格采样。
- 采样值经 profile→PCS 变换得到 Lab 点云。**变换可注入**：公开 API 提供
  keyword-only ``transform`` 参数（签名见 :data:`TransformFn`），默认实现
  惰性导入 ``packstudio.engines.color_engine.cmm.engine.transform_lab``（cmm 由并行开发交付；
  缺席时抛 VizError）。单测注入确定性 fake 变换即可全流程验证。
- 体积用 scipy ConvexHull（Lab 单位³，相对指标），退化输入防护返回 0.0。
- 绘图：matplotlib Agg 后端（模块级 ``matplotlib.use("Agg")``），CIE 1931
  xy 色度图（含光谱轨迹）与 Lab a-b 等亮度切片，多 profile 叠加，仅写文件、
  不调用任何 show()。语义色 token 经 :mod:`packstudio.engines.color_engine.viz.theme` 实时读取
  （``viz.set_theme()`` 切换 dark/light，v0.5.4）。
"""

from __future__ import annotations

import enum
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

import matplotlib
matplotlib.use("Agg")  # 必须先于 pyplot：无 GUI 环境，仅落盘输出
# CJK 字形回退（标题/图例含中文；DejaVu Sans 无 CJK 会产生 glyph warning 与乱码）
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei",
    "DejaVu Sans", "Arial", "sans-serif",
]
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.colors as mcolors  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
import numpy  # noqa: E402
from scipy.optimize import linprog  # noqa: E402
from scipy.spatial import ConvexHull  # noqa: E402
from scipy.spatial import HalfspaceIntersection  # noqa: E402
from scipy.spatial import QhullError  # noqa: E402
from PIL import Image as PILImage  # noqa: E402  动画 GIF 帧合成（v0.5.6）
from packstudio.engines.color_engine.viz.theme import TOKENS as _T  # noqa: E402

logger = logging.getLogger("icct.viz.gamut")

# ---------------------------------------------------------------------------
# 异常与 RenderingIntent：优先项目统一层级/真实 cmm 枚举；并行开发期使用本地
# 等价定义（同值 0-3，见设计 §3.6），errors.py / cmm 落地后自动切换。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 分支取决于并行模块是否就绪
    from packstudio.engines.color_engine.errors import IccToolkitError, VizError
except ImportError:  # pragma: no cover - 并行开发脚手架
    class IccToolkitError(Exception):
        """所有工具异常的基类（errors.py 就绪前的本地等价定义）。"""

        code: str = "ICC_TOOLKIT_ERROR"

        def __init__(self, message: str, *,
                     detail: dict[str, Any] | None = None) -> None:
            super().__init__(message)
            self.detail: dict[str, Any] = dict(detail or {})

    class VizError(IccToolkitError):
        """可视化模块失败。"""

        code: str = "VIZ"


class _SpaceMismatchError(VizError):
    """profile 色空间与采样函数不匹配。code="SPACE_MISMATCH"（契约码）。"""

    code: str = "SPACE_MISMATCH"


try:  # pragma: no cover - 分支取决于并行模块是否就绪
    from packstudio.engines.color_engine.cmm.engine import RenderingIntent
except ImportError:  # pragma: no cover - 并行开发脚手架（镜像同值枚举）
    class RenderingIntent(enum.IntEnum):  # type: ignore[no-redef]
        """与 cmm.engine.RenderingIntent 同值（设计 §3.6）。"""

        PERCEPTUAL = 0
        RELATIVE_COLORIMETRIC = 1
        SATURATION = 2
        ABSOLUTE_COLORIMETRIC = 3


__all__ = [
    "GamutSample",
    "TransformFn",
    "sample_rgb_gamut",
    "sample_cmyk_gamut",
    "estimate_gamut_volume",
    "plot_chromaticity",
    "plot_ab_slice",
]

# ---------------------------------------------------------------------------
# 主题 token（DESIGN.md §4.12 映射）：注册表移至 viz.theme（ROADMAP v0.5.4），
# 绘图时经 _T 实时读取；set_theme() 原地更新，已导入引用自动跟随。
# ---------------------------------------------------------------------------
# D50 白点 xy（CIE 1931 2°，PCS 约定白点，色度图上标记）
_D50_XY = (0.3457, 0.3585)

_DEFAULT_INTENT = RenderingIntent.RELATIVE_COLORIMETRIC

# CIELAB ↔ XYZ 用 D50 白点（PCS 约定）
_D50_XYZ = numpy.array([0.9642, 1.0, 0.8249], dtype=numpy.float64)
_CIE_EPS = 216.0 / 24389.0
_CIE_KAPPA = 24389.0 / 27.0


# ---------------------------------------------------------------------------
# 数据模型与变换接口
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GamutSample:
    """一个 profile 的色域采样结果。

    Attributes:
        profile_path: profile 来源（路径字符串；内存 bytes 为 "<bytes>"）。
        description: profile 描述（解析失败时回退路径名）。
        lab: Lab 点云，形状 (n, 3) float64。
        volume: ConvexHull 体积（Lab 单位³，相对指标；退化为 0.0）。
        vertices: 凸包顶点数（退化时 0）。
    """

    profile_path: str
    description: str
    lab: numpy.ndarray
    volume: float
    vertices: int


TransformFn: TypeAlias = Callable[[bytes | str, numpy.ndarray, RenderingIntent, bool],
                                  numpy.ndarray]
"""可注入的 profile→Lab 变换。

签名: ``transform(profile, values, intent, bpc) -> lab``

- ``profile``: profile 路径或字节（原样透传给底层引擎）。
- ``values``: 设备值数组，形状 (n, k) float64，k=3（RGB）或 4（CMYK），
  各分量归一化到 [0, 1]。
- 返回: Lab 数组，形状 (n, 3) float64。

默认实现 :func:`_default_transform` 惰性导入 cmm 引擎（设计 §4.5
``transform_lab``：内部把 (n,k) 归一化值转为 uint8 0-255 条带图后变换，
8bit Lab：L∈[0,100]、a/b∈[-128,127]）。
"""


def _default_transform(profile: bytes | str, values: numpy.ndarray,
                       intent: RenderingIntent, bpc: bool) -> numpy.ndarray:
    """默认变换：惰性导入 cmm.engine.transform_lab（并行开发边界）。"""
    try:
        from packstudio.engines.color_engine.cmm.engine import transform_lab
    except Exception as exc:  # noqa: BLE001 - 缺依赖/坏安装统一转 VizError
        raise VizError(
            f"cmm 引擎不可用，默认采样变换失败: {exc}",
            detail={"reason": "cmm_unavailable"}) from exc
    v8 = numpy.clip(values, 0.0, 1.0)
    v8 = numpy.round(v8 * 255.0).astype(numpy.uint8)
    lab = transform_lab(profile, v8, RenderingIntent(int(intent)), bool(bpc))
    return numpy.asarray(lab, dtype=numpy.float64).reshape(-1, 3)


# ---------------------------------------------------------------------------
# profile 元信息（防御式：icc/cmm 缺席时仍有确定回退）
# ---------------------------------------------------------------------------
def _profile_source(profile: bytes | str) -> tuple[str, str]:
    """返回 (path_repr, fallback_description)。"""
    if isinstance(profile, (bytes, bytearray, memoryview)):
        return "<bytes>", "<memory profile>"
    path_str = str(os.fspath(profile))
    return path_str, Path(path_str).stem


def _peek_color_space(profile: bytes | str) -> str | None:
    """直接读 profile 头 16-19 字节取数据色空间签名（如 'RGB '/'CMYK'）。

    说明：设计允许 viz 导入 icc；此处为消除对并行模块的硬依赖，直接按
    ICC.1 头部布局做 4 字节只读窥视，语义与 icc.header 等价。不可判定
    （文件缺失/不足 20 字节）返回 None，由调用方放行。
    """
    if isinstance(profile, (bytes, bytearray, memoryview)):
        data = bytes(profile[:20])
    else:
        try:
            with open(os.fspath(profile), "rb") as handle:
                data = handle.read(20)
        except OSError:
            return None
    if len(data) < 20:
        return None
    return data[16:20].decode("ascii", "replace")


def _profile_description(profile: bytes | str) -> str:
    """profile 描述：优先 icc.inspect，其次 cmm，最后回退路径名。"""
    _, fallback = _profile_source(profile)
    try:
        from packstudio.engines.color_engine.icc.inspect import load_profile
        return str(load_profile(profile).description) or fallback
    except Exception:  # noqa: BLE001 - 逐级回退
        pass
    try:
        from packstudio.engines.color_engine.cmm.engine import open_profile, profile_names
        return str(profile_names(open_profile(profile))[1]) or fallback
    except Exception:  # noqa: BLE001
        return fallback


def _require_space(profile: bytes | str, expected: str, function: str) -> None:
    """色空间守卫：不匹配 → VizError(code="SPACE_MISMATCH")。"""
    cs = _peek_color_space(profile)
    if cs is not None and cs != expected:
        raise _SpaceMismatchError(
            f"{function} 需要 {expected.strip()} profile，实际色空间为 {cs!r}",
            detail={"color_space": cs, "expected": expected.strip()})


# ---------------------------------------------------------------------------
# 采样
# ---------------------------------------------------------------------------
def _run_transform(profile: bytes | str, values: numpy.ndarray,
                   intent: RenderingIntent, bpc: bool,
                   transform: TransformFn | None) -> numpy.ndarray:
    """执行注入变换（缺省用 cmm 默认实现），归一输出 (n, 3) float64。"""
    fn = transform if transform is not None else _default_transform
    lab = fn(profile, numpy.ascontiguousarray(values, dtype=numpy.float64),
             intent, bool(bpc))
    lab = numpy.asarray(lab, dtype=numpy.float64).reshape(-1, 3)
    if lab.shape[0] != values.shape[0]:
        raise VizError(
            "变换返回行数与输入不一致",
            detail={"input_rows": int(values.shape[0]), "output_rows": int(lab.shape[0])})
    return lab


def _hull_of(points: numpy.ndarray) -> ConvexHull | None:
    """ConvexHull 包装：点数不足或几何退化 → None（log WARNING，不抛）。"""
    if points.shape[0] < 5:
        logger.debug("色域点数 %d < 5，凸包跳过", points.shape[0])
        return None
    try:
        return ConvexHull(points)
    except (QhullError, ValueError) as exc:
        logger.warning("色域点云退化，凸包不可用: %s", exc)
        return None


def sample_rgb_gamut(profile: bytes | str, *,
                     intent: RenderingIntent = _DEFAULT_INTENT,
                     bpc: bool = True, density: int = 17,
                     transform: TransformFn | None = None) -> GamutSample:
    """RGB 立方均匀采样（TECH-DESIGN §4.11）。

    行为:
        在 RGB 设备空间 [0,1]³ 上取 ``density³`` 个均匀网格点，经
        ``transform``（默认 cmm.transform_lab）变换为 Lab 点云，再用
        scipy ConvexHull 估算体积。点数 < 5 或共面等退化输入体积记 0.0
        （log WARNING，不抛异常）。
    参数:
        profile: RGB ICC profile 路径或字节。
        intent: 渲染意图（透传给变换）。
        bpc: 黑点补偿。
        density: 每轴采样数（≥2；默认 17 → 4913 点）。
        transform: 可注入 profile→Lab 变换（见 :data:`TransformFn`）；
            None 时使用 cmm 默认实现。
    返回:
        GamutSample: 含 (density³, 3) Lab 点云与体积。
    异常:
        VizError: density 非法、profile 非 RGB（code="SPACE_MISMATCH"）、
        或默认变换时 cmm 不可用。
    """
    if not isinstance(density, int) or isinstance(density, bool) or density < 2:
        raise VizError(f"density 必须为 ≥2 的整数，得到 {density!r}",
                       detail={"density": density})
    _require_space(profile, "RGB ", "sample_rgb_gamut")
    axes = numpy.linspace(0.0, 1.0, density)
    grid = numpy.stack(numpy.meshgrid(axes, axes, axes, indexing="ij"),
                       axis=-1).reshape(-1, 3)
    lab = _run_transform(profile, grid, intent, bpc, transform)
    hull = _hull_of(lab)
    path_repr, _ = _profile_source(profile)
    return GamutSample(
        profile_path=path_repr,
        description=_profile_description(profile),
        lab=lab,
        volume=float(hull.volume) if hull is not None else 0.0,
        vertices=int(hull.vertices.size) if hull is not None else 0,
    )


def sample_cmyk_gamut(profile: bytes | str, *,
                      intent: RenderingIntent = _DEFAULT_INTENT,
                      bpc: bool = True, grid: int = 6,
                      transform: TransformFn | None = None) -> GamutSample:
    """CMYK 网格采样（TECH-DESIGN §4.11）。

    行为:
        在 CMYK 设备空间 [0,1]⁴ 上取 ``grid⁴`` 个均匀网格点（默认
        6⁴=1296 点），经 ``transform`` 变换为 Lab 点云并估算体积。
    参数:
        profile: CMYK ICC profile 路径或字节。
        intent: 渲染意图（透传给变换）。
        bpc: 黑点补偿。
        grid: 每轴网格数（2..8；>8 采样代价过高 → VizError）。
        transform: 可注入 profile→Lab 变换（见 :data:`TransformFn`）。
    返回:
        GamutSample: 含 (grid⁴, 3) Lab 点云与体积。
    异常:
        VizError: grid 非法、profile 非 CMYK（code="SPACE_MISMATCH"）、
        或默认变换时 cmm 不可用。
    """
    if not isinstance(grid, int) or isinstance(grid, bool) or grid < 2:
        raise VizError(f"grid 必须为 ≥2 的整数，得到 {grid!r}",
                       detail={"grid": grid})
    if grid > 8:
        raise VizError(f"grid={grid} 超过 8，采样代价过高（grid⁴ 点数）",
                       detail={"grid": grid, "max": 8})
    _require_space(profile, "CMYK", "sample_cmyk_gamut")
    axes = numpy.linspace(0.0, 1.0, grid)
    grid_pts = numpy.stack(numpy.meshgrid(axes, axes, axes, axes, indexing="ij"),
                           axis=-1).reshape(-1, 4)
    lab = _run_transform(profile, grid_pts, intent, bpc, transform)
    hull = _hull_of(lab)
    path_repr, _ = _profile_source(profile)
    return GamutSample(
        profile_path=path_repr,
        description=_profile_description(profile),
        lab=lab,
        volume=float(hull.volume) if hull is not None else 0.0,
        vertices=int(hull.vertices.size) if hull is not None else 0,
    )


def estimate_gamut_volume(lab: numpy.ndarray) -> float:
    """Lab 点云的 ConvexHull 体积（Lab 单位³，独立可测入口）。

    行为:
        过滤非有限行后计算凸包体积；点数 < 5、共线/共面等退化或 qhull
        异常一律返回 0.0（log WARNING），不抛异常。
    参数:
        lab: 形状 (n, 3) float64（其他可 reshape 成 (n,3) 的形状亦可）。
    返回:
        float: 体积；退化输入为 0.0。
    """
    points = numpy.asarray(lab, dtype=numpy.float64).reshape(-1, 3)
    points = points[numpy.isfinite(points).all(axis=1)]
    hull = _hull_of(points)
    return float(hull.volume) if hull is not None else 0.0


# ---------------------------------------------------------------------------
# 色度换算
# ---------------------------------------------------------------------------
def _lab_to_xyz(lab: numpy.ndarray) -> numpy.ndarray:
    """CIELAB（D50）→ XYZ，形状保持 (..., 3)。"""
    lab = numpy.asarray(lab, dtype=numpy.float64)
    lightness, a_star, b_star = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (lightness + 16.0) / 116.0
    fx = fy + a_star / 500.0
    fz = fy - b_star / 200.0

    def _finv(t: numpy.ndarray) -> numpy.ndarray:
        t3 = t ** 3
        return numpy.where(t3 > _CIE_EPS, t3, (116.0 * t - 16.0) / _CIE_KAPPA)

    xyz = numpy.stack([_finv(fx), _finv(fy), _finv(fz)], axis=-1) * _D50_XYZ
    return xyz


def _xyz_to_xy(xyz: numpy.ndarray) -> numpy.ndarray:
    """XYZ → CIE 1931 xy；和接近 0 的点（黑）置 NaN 以便丢弃。"""
    total = xyz.sum(axis=-1, keepdims=True)
    total = numpy.where(total <= 1e-12, numpy.nan, total)
    return xyz[..., :2] / total


def _lab_to_xy(lab: numpy.ndarray) -> numpy.ndarray:
    """Lab (n,3) → xy (n,2)，含 NaN 的行由调用方过滤。"""
    return _xyz_to_xy(_lab_to_xyz(numpy.asarray(lab, dtype=numpy.float64)))


# CIE 1931 2° 标准观察者光谱轨迹 xy（360-700nm，5nm 步进）。
# 机器生成数据：由 colour-science 的 CIE_1931_2_Degree_Standard_Observer CMFs
# 经 wavelength_to_XYZ 计算（scripts/gen_spectral_locus.py 可再生成）。
# 用途：色度图光谱轨迹 polyline。此前每次绘制都逐波长调用 colour（冷路径
# ~1.1s、热路径 ~11ms）；改为常量后 viz 绘图路径零 colour 依赖（v0.4.3）。
_SPECTRAL_LOCUS_XY_RAW = """
360 0.175560 0.005294
365 0.175161 0.005256
370 0.174821 0.005221
375 0.174510 0.005182
380 0.174112 0.004964
385 0.174008 0.004981
390 0.173801 0.004915
395 0.173560 0.004923
400 0.173337 0.004797
405 0.173021 0.004775
410 0.172577 0.004799
415 0.172087 0.004833
420 0.171407 0.005102
425 0.170301 0.005789
430 0.168878 0.006900
435 0.166895 0.008556
440 0.164412 0.010858
445 0.161105 0.013793
450 0.156641 0.017705
455 0.150985 0.022740
460 0.143960 0.029703
465 0.135503 0.039879
470 0.124118 0.057803
475 0.109594 0.086843
480 0.091294 0.132702
485 0.068706 0.200723
490 0.045391 0.294976
495 0.023460 0.412703
500 0.008168 0.538423
505 0.003859 0.654823
510 0.013870 0.750186
515 0.038852 0.812016
520 0.074302 0.833803
525 0.114161 0.826207
530 0.154722 0.805864
535 0.192876 0.781629
540 0.229620 0.754329
545 0.265775 0.724324
550 0.301604 0.692308
555 0.337363 0.658848
560 0.373102 0.624451
565 0.408736 0.589607
570 0.444062 0.554714
575 0.478775 0.520202
580 0.512486 0.486591
585 0.544787 0.454434
590 0.575151 0.424232
595 0.602933 0.396497
600 0.627037 0.372491
605 0.648233 0.351395
610 0.665764 0.334011
615 0.680079 0.319747
620 0.691504 0.308342
625 0.700606 0.299301
630 0.707918 0.292027
635 0.714032 0.285929
640 0.719033 0.280935
645 0.723032 0.276948
650 0.725992 0.274008
655 0.728272 0.271728
660 0.729969 0.270031
665 0.731089 0.268911
670 0.731993 0.268007
675 0.732719 0.267281
680 0.733417 0.266583
685 0.734047 0.265953
690 0.734390 0.265610
695 0.734592 0.265408
700 0.734690 0.265310
"""


def _parse_spectral_locus(raw: str) -> numpy.ndarray:
    """解析三列空白分隔表（wavelength/x/y）为 (n, 2) xy 数组。"""
    rows = [ln.split() for ln in raw.strip().splitlines() if ln.strip()]
    arr = numpy.asarray(rows, dtype=numpy.float64)
    return arr[:, 1:3]


_SPECTRAL_LOCUS_XY: numpy.ndarray | None = _parse_spectral_locus(_SPECTRAL_LOCUS_XY_RAW)


def _parse_spectral_wavelengths(raw: str) -> numpy.ndarray:
    """解析三列空白分隔表（wavelength/x/y）首列为 (n,) 波长数组（nm）。"""
    rows = [ln.split() for ln in raw.strip().splitlines() if ln.strip()]
    arr = numpy.asarray(rows, dtype=numpy.float64)
    return arr[:, 0]


#: 与 _SPECTRAL_LOCUS_XY 同序的波长列（主波长标注用，v0.5.5）。
_SPECTRAL_LOCUS_WAVELENGTHS: numpy.ndarray = _parse_spectral_wavelengths(
    _SPECTRAL_LOCUS_XY_RAW)


def _spectral_locus_xy() -> numpy.ndarray | None:
    """CIE 1931 光谱轨迹 xy（360-700nm，5nm 步进）。

    v0.4.3 起使用机器生成的内置常量（来源见 ``_SPECTRAL_LOCUS_XY_RAW`` 注释），
    不再逐波长调用 colour-science（消除绘图路径 ~1.1s 冷开销与 colour 依赖）。
    返回 (n, 2) 数组；异常退化时 None（画图端跳过轨迹，不抛）。
    """
    return _SPECTRAL_LOCUS_XY


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------
def _ensure_png(out: str | os.PathLike[str]) -> Path:
    """输出路径规范化：后缀强制 .png，父目录自动创建。"""
    path = Path(out)
    if path.suffix.lower() != ".png":
        path = path.with_suffix(".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


#: 图表支持写出格式（v0.5.3 SVG 矢量导出；matplotlib 按扩展名选后端）。
_PLOT_FORMATS: Final[tuple[str, ...]] = (".png", ".svg")


def _ensure_format(out: str | os.PathLike[str], fmt: str | None) -> Path:
    """输出路径规范化（v0.5.3）：``fmt`` 显式指定 png/svg；None 按后缀
    （png/svg 保留，其余与无后缀 → .png），父目录自动创建。非法格式 → VizError。
    """
    path = Path(out)
    if fmt is not None:
        suffix = f".{str(fmt).lower().lstrip('.')}"
        if suffix not in _PLOT_FORMATS:
            raise VizError(
                f"不支持的图表格式: {fmt}（支持 png/svg）",
                detail={"format": str(fmt), "supported": list(_PLOT_FORMATS)},
            )
        path = path.with_suffix(suffix)
    elif path.suffix.lower() not in _PLOT_FORMATS:
        path = path.with_suffix(".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _style_theme(fig: Any, ax: Any) -> None:
    """当前主题画布配色（DESIGN.md token：canvas/ink/hairline；现代极简坐标轴）。"""
    fig.set_facecolor(_T["canvas"])
    ax.set_facecolor(_T["canvas"])
    # 只留 bottom/left 发丝轴，去 top/right（现代图表观感）
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(_T["hairline_strong"])
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=_T["ink_tertiary"], labelsize=9, length=3, width=0.8)
    ax.grid(True, color=_T["hairline"], linewidth=0.6, linestyle="-", alpha=0.9)
    ax.set_axisbelow(True)


def _legend_theme(ax: Any, loc: str = "upper right") -> None:
    """统一图例样式：无边框、ink 文字、hairline 分隔（贴 UI 观感）。

    图内无带标签 artist（如动画边缘 L* 帧全带空）时不建图例，避免
    matplotlib 的「No artists with labels」UserWarning。
    """
    if not ax.get_legend_handles_labels()[0]:
        return None
    legend = ax.legend(loc=loc, fontsize=9, frameon=False, handlelength=1.6,
                       borderaxespad=0.6, labelcolor=_T["ink"])
    return legend


def _glow_line(ax: Any, x: Any, y: Any, color: str, *, linewidth: float = 2.0) -> None:
    """描边 + 底层低透明宽线 = 深色画布上的廉价辉光（无阴影依赖）。"""
    ax.plot(x, y, color=color, linewidth=linewidth + 3.0, alpha=0.18,
            solid_capstyle="round", zorder=3)
    ax.plot(x, y, color=color, linewidth=linewidth, solid_capstyle="round",
            zorder=4)


def _series_color(index: int) -> str:
    series = _T["series"]
    return series[index % len(series)]


def _legend_label(index: int, description: str) -> str:
    """统一图例标识（v0.5.8）：``P<n> · <description>``（n 从 1 起，与
    _series_color 的系列色序号一致）——多 profile 多图（色度图/切片/重叠/
    动画）间同一色票同一命名。"""
    return f"P{index + 1} · {description}"


def _validate_samples(profiles: Sequence[GamutSample]) -> list[GamutSample]:
    samples = list(profiles)
    if not samples:
        raise VizError("至少需要一个 GamutSample", detail={"profiles": 0})
    return samples


def plot_chromaticity(profiles: Sequence[GamutSample], out: str | os.PathLike[str],
                      *, dpi: int = 150, show_spectral_locus: bool = True,
                      title: str | None = None,
                      format: str | None = None,
                      show_wavelengths: bool = False) -> Path:
    """CIE 1931 xy 色度图（TECH-DESIGN §4.11）。

    行为:
        每个 GamutSample 的 Lab 点云 → XYZ → xy；多 profile 叠加（凸包描边 +
        半透明填充；点数不足以成凸包时退化为散点）。``show_spectral_locus``
        时用 colour-science CIE 1931 2° CMFs 画光谱轨迹与紫线闭合段。
        深色画布配色沿用 DESIGN.md token；D50 白点以十字标记。图内默认不绘
        标题（由调用方 UI 图注承载；传入 ``title`` 时仍绘制）。matplotlib
        Agg 后端，仅写文件。
    参数:
        profiles: 一个或多个 GamutSample。
        out: 输出路径（缺省格式 png）。
        dpi: 输出分辨率。
        show_spectral_locus: 是否绘制光谱轨迹。
        title: 图题（缺省 "CIE 1931 xy 色度图"）。
        format: 输出格式（v0.5.3）：None=按 out 后缀（png/svg，其余 → .png）；
                "png"/"svg" 强制（svg 为矢量导出）。
        show_wavelengths: 是否标注主波长/补色波长（v0.5.5）。每样本取距
                D50 白点最远的 xy 点（最大饱和方向），画白点→光谱轨迹交点
                连线并标注波长（紫区点自动标注补色波长）。增强项：失败
                log WARNING 跳过，不阻塞出图。
    返回:
        pathlib.Path: 实际写出路径。
    异常:
        VizError: profiles 为空；format 非 png/svg。
    """
    samples = _validate_samples(profiles)
    out_path = _ensure_format(out, format)
    fig, ax = plt.subplots(figsize=(8.0, 7.0), dpi=dpi)
    _style_theme(fig, ax)
    try:
        if show_spectral_locus:
            locus = _spectral_locus_xy()
            if locus is not None and locus.shape[0] >= 2:
                ax.plot(locus[:, 0], locus[:, 1], color=_T["ink_tertiary"],
                        linewidth=1.2, label="spectral locus")
                ax.plot([locus[-1, 0], locus[0, 0]], [locus[-1, 1], locus[0, 1]],
                        color=_T["ink_tertiary"], linewidth=1.0, linestyle="--")
    except Exception as exc:  # noqa: BLE001 - 增强项失败不阻塞出图
        logger.warning("光谱轨迹绘制失败（跳过）: %s", exc)
    for index, sample in enumerate(samples):
        xy = _lab_to_xy(sample.lab)
        points = xy[numpy.isfinite(xy).all(axis=1)]
        color = _series_color(index)
        hull = _hull_of(points)
        if hull is not None:
            poly = points[hull.vertices]
            ax.fill(poly[:, 0], poly[:, 1], color=color, alpha=0.16, linewidth=0,
                    zorder=2)
            closed = numpy.vstack([poly, poly[:1]])
            _glow_line(ax, closed[:, 0], closed[:, 1], color, linewidth=1.8)
            ax.plot([], [], color=color, linewidth=1.8,
                    label=_legend_label(index, sample.description))
        elif points.shape[0] > 0:
            ax.scatter(points[:, 0], points[:, 1], s=6, color=color,
                       linewidths=0, label=_legend_label(index, sample.description),
                       zorder=4)
    # D50 白点标记（PCS 参考白）
    ax.scatter([_D50_XY[0]], [_D50_XY[1]], marker="+", s=46, color=_T["ink_muted"],
               linewidths=1.2, zorder=5, label="D50 white point")
    if show_wavelengths:
        try:
            _draw_wavelength_marks(ax, samples)
        except Exception as exc:  # noqa: BLE001 - 增强项失败不阻塞出图
            logger.warning("主波长标注绘制失败（跳过）: %s", exc)
    ax.set_xlim(-0.05, 0.85)
    ax.set_ylim(-0.05, 0.90)
    ax.set_xlabel("x (CIE 1931)", color=_T["ink_subtle"])
    ax.set_ylabel("y (CIE 1931)", color=_T["ink_subtle"])
    if title:
        ax.set_title(title, color=_T["ink"])
    _legend_theme(ax, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("色度图已写出: %s（%d 个 profile）", out_path, len(samples))
    return out_path


def plot_ab_slice(profiles: Sequence[GamutSample], out: str | os.PathLike[str],
                  *, lightness: float = 50.0, band: float = 5.0,
                  dpi: int = 150, format: str | None = None) -> Path:
    """Lab a-b 等亮度切片（TECH-DESIGN §4.11）。

    行为:
        取每个样本中 |L − lightness| ≤ band 的点投影到 a*-b* 平面，透明度
        按邻近度线性衰减（切面中心不透明、带边缘 0.15）。多 profile 同图
        叠加，legend 为 profile description。带内无点的样本 log WARNING 后
        跳过。Agg 后端，仅写文件。
    参数:
        profiles: 一个或多个 GamutSample。
        out: 输出路径（缺省格式 png）。
        lightness: 切片中心亮度 L*。
        band: 半带宽（>0）。
        dpi: 输出分辨率。
        format: 输出格式（v0.5.3）：None=按 out 后缀（png/svg，其余 → .png）；
                "png"/"svg" 强制（svg 为矢量导出）。
    返回:
        pathlib.Path: 实际写出路径。
    异常:
        VizError: profiles 为空、band ≤ 0；format 非 png/svg。
    """
    samples = _validate_samples(profiles)
    if band <= 0:
        raise VizError(f"band 必须为正数，得到 {band!r}", detail={"band": band})
    out_path = _ensure_format(out, format)
    fig, ax = plt.subplots(figsize=(8.0, 8.0), dpi=dpi)
    _style_theme(fig, ax)
    _ab_slice_scatter(ax, samples, lightness, band)
    ax.set_xlabel("a*", color=_T["ink_subtle"])
    ax.set_ylabel("b*", color=_T["ink_subtle"])
    ax.set_aspect("equal", adjustable="datalim")
    _legend_theme(ax, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("a-b 切片已写出: %s（L*=%g±%g）", out_path, lightness, band)
    return out_path


def _ab_slice_scatter(ax: Any, samples: Sequence[GamutSample],
                      lightness: float, band: float) -> None:
    """a-b 切片散点绘制主体（plot_ab_slice 与动画帧共用，v0.5.6）。

    每样本取 |L − lightness| ≤ band 的点投影 a*-b*，透明度按邻近度线性
    衰减；带内无点的样本 log WARNING 后跳过。
    """
    for index, sample in enumerate(samples):
        lab = numpy.asarray(sample.lab, dtype=numpy.float64).reshape(-1, 3)
        lab = lab[numpy.isfinite(lab).all(axis=1)]
        distance = numpy.abs(lab[:, 0] - lightness)
        mask = distance <= band
        if not mask.any():
            logger.warning("profile %s 在 L=%.1f±%.1f 带内无样本点，已跳过",
                           sample.description, lightness, band)
            continue
        selected = lab[mask]
        proximity = 1.0 - distance[mask] / band
        rgba = numpy.zeros((selected.shape[0], 4), dtype=numpy.float64)
        rgba[:, :3] = mcolors.to_rgb(_series_color(index))
        rgba[:, 3] = 0.15 + 0.75 * proximity
        ax.scatter(selected[:, 1], selected[:, 2], c=rgba, s=14, linewidths=0,
                   label=f"{_legend_label(index, sample.description)} (L*≈{lightness:g}±{band:g})",
                   zorder=4)


# ---------------------------------------------------------------------------
# 色域交集/并集（ROADMAP v0.5.2）：两 profile 的共享色域体积计算与绘图。
# 体积语义与 estimate_gamut_volume 一致——全部为凸包体积（Lab 单位³，相对
# 指标）；交集 = 两凸包半空间交的多面体体积（HalfspaceIntersection 精确
# 计算，切比雪夫中心经 linprog 求内点），并集 = 合并点云的凸包体积。
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GamutOverlap:
    """两 GamutSample 的交集/并集体积结果（overlap_ratio 为派生只读属性）。"""

    profile_a: str
    profile_b: str
    description_a: str
    description_b: str
    volume_a: float
    volume_b: float
    intersection_volume: float
    union_volume: float

    @property
    def overlap_ratio(self) -> float:
        """交集/并集体积比（Jaccard 语义；并集体积退化 0 时返回 0.0）。"""
        if self.union_volume <= 0.0:
            return 0.0
        return self.intersection_volume / self.union_volume


def _hull_intersection_vertices(lab_a: numpy.ndarray,
                                lab_b: numpy.ndarray) -> numpy.ndarray:
    """两凸包交集多面体的顶点 (n, 3)。

    退化/不相交/半空间不可行一律返回空数组（log INFO，不抛）——调用方以
    空结果渲染「无交集」。切比雪夫中心：max r s.t. n·x + r ≤ −b（qhull
    equations 的法向量已单位化，r ≥ 0 不可行即交集为空）。
    """
    empty = numpy.empty((0, 3), dtype=numpy.float64)
    hull_a = _hull_of(lab_a)
    hull_b = _hull_of(lab_b)
    if hull_a is None or hull_b is None:
        return empty
    halfspaces = numpy.vstack([hull_a.equations, hull_b.equations])
    a_ub = numpy.hstack([halfspaces[:, :3],
                         numpy.ones((halfspaces.shape[0], 1))])
    b_ub = -halfspaces[:, 3]
    result = linprog(c=[0.0, 0.0, 0.0, -1.0], A_ub=a_ub, b_ub=b_ub,
                     bounds=[(None, None), (None, None), (None, None),
                             (0.0, None)],
                     method="highs")
    if not result.success or result.x is None:
        logger.info("两凸包无公共内点（交集为空）")
        return empty
    try:
        intersection = HalfspaceIntersection(halfspaces, result.x[:3])
        return numpy.asarray(intersection.intersections, dtype=numpy.float64)
    except (QhullError, ValueError) as exc:
        logger.warning("半空间交集退化: %s", exc)
        return empty


def gamut_overlap(a: GamutSample, b: GamutSample) -> GamutOverlap:
    """两 profile 点云的交集/并集体积与重合率（ROADMAP v0.5.2）。

    行为:
        交集 = 两凸包半空间交的多面体体积（精确算法，非采样估计）；并集 =
        合并点云的凸包体积；重合率 = 交集/并集（Jaccard 语义）。退化输入
        （点数 < 5、共面、不相交）交集记 0.0，与 estimate_gamut_volume 的
        容错语义一致，不抛异常。
    参数:
        a, b: GamutSample（来自 sample_rgb_gamut / sample_cmyk_gamut）。
    返回:
        GamutOverlap: 双方体积 + intersection_volume + union_volume +
        overlap_ratio（派生属性）。
    异常:
        VizError: a 或 b 不是 GamutSample、或两者为空。
    """
    _validate_samples([a, b])
    points_a = numpy.asarray(a.lab, dtype=numpy.float64).reshape(-1, 3)
    points_b = numpy.asarray(b.lab, dtype=numpy.float64).reshape(-1, 3)
    points_a = points_a[numpy.isfinite(points_a).all(axis=1)]
    points_b = points_b[numpy.isfinite(points_b).all(axis=1)]
    vertices = _hull_intersection_vertices(points_a, points_b)
    intersection = 0.0
    if vertices.shape[0] >= 5:
        hull = _hull_of(vertices)
        if hull is not None:
            intersection = float(hull.volume)
    union = estimate_gamut_volume(numpy.vstack([points_a, points_b]))
    return GamutOverlap(
        profile_a=a.profile_path, profile_b=b.profile_path,
        description_a=a.description, description_b=b.description,
        volume_a=estimate_gamut_volume(points_a),
        volume_b=estimate_gamut_volume(points_b),
        intersection_volume=intersection, union_volume=union,
    )


def plot_gamut_overlap(a: GamutSample, b: GamutSample, out: str | os.PathLike[str],
                       *, lightness: float = 50.0, band: float = 5.0,
                       dpi: int = 150, format: str | None = None) -> Path:
    """两 profile 色域重叠 a-b 切片（ROADMAP v0.5.2）。

    行为:
        双方带内样本半透明散点 + 交集多面体顶点的带内投影（× 标记）。注意
        交集顶点为 3D 多面体顶点在带内的投影，不等价于 2D 切片区域的交，
        图例文字如实标注。带内无点的样本 log WARNING 后跳过（同
        plot_ab_slice）。
    参数:
        a, b: GamutSample。
        out: 输出路径（缺省格式 png）。
        lightness: 切片中心亮度 L*。
        band: 半带宽（>0）。
        dpi: 输出分辨率。
        format: 输出格式（None=按后缀 png/svg，"png"/"svg" 强制）。
    返回:
        pathlib.Path: 实际写出路径。
    异常:
        VizError: 参数非法（band ≤ 0、format 白名单外、样本为空）。
    """
    _validate_samples([a, b])
    if band <= 0:
        raise VizError(f"band 必须为正数，得到 {band!r}", detail={"band": band})
    out_path = _ensure_format(out, format)
    fig, ax = plt.subplots(figsize=(8.0, 8.0), dpi=dpi)
    _style_theme(fig, ax)
    for index, sample in enumerate((a, b)):
        lab = numpy.asarray(sample.lab, dtype=numpy.float64).reshape(-1, 3)
        lab = lab[numpy.isfinite(lab).all(axis=1)]
        distance = numpy.abs(lab[:, 0] - lightness)
        mask = distance <= band
        if not mask.any():
            logger.warning("profile %s 在 L=%.1f±%.1f 带内无样本点，已跳过",
                           sample.description, lightness, band)
            continue
        ax.scatter(lab[mask, 1], lab[mask, 2], s=14,
                   color=_series_color(index), alpha=0.45, linewidths=0,
                   label=f"{_legend_label(index, sample.description)} (L*≈{lightness:g}±{band:g})",
                   zorder=4)
    vertices = _hull_intersection_vertices(a.lab, b.lab)
    if vertices.shape[0]:
        in_band = vertices[numpy.abs(vertices[:, 0] - lightness) <= band]
        if in_band.shape[0]:
            ax.scatter(in_band[:, 1], in_band[:, 2], s=26, marker="x",
                       linewidths=1.6, color=_T["ink"],
                       label="交集多面体顶点（带内投影）", zorder=5)
    ax.set_xlabel("a*", color=_T["ink_subtle"])
    ax.set_ylabel("b*", color=_T["ink_subtle"])
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_title(f"色域重叠：{a.description} × {b.description}",
                 color=_T["ink"])
    _legend_theme(ax, loc="upper right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("色域重叠切片已写出: %s（L*=%g±%g）", out_path, lightness, band)
    return out_path


# ---------------------------------------------------------------------------
# 主波长/补色波长（ROADMAP v0.5.5）：色度点相对 D50 白点的光谱波长标注。
# 定义——白点 W、色度点 P：主波长 = 射线 W→P 延长（t>1）与光谱轨迹折线的
# 首个交点波长；P 落在紫区（延长线只穿紫线、不穿轨迹）时改取反向射线
# W→(W−P) 的首个轨迹交点，记补色波长（kind="complementary"）；P 与 W 重合
# 为无彩色，无主波长定义。算法为解析射线-折线求交（精确，非采样）。
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class WavelengthMark:
    """主波长/补色波长标注结果（kind: dominant/complementary/achromatic）。"""

    x: float
    y: float
    kind: str
    wavelength: float | None  # nm；achromatic → None
    hit_x: float | None  # 光谱轨迹交点 xy；achromatic → None
    hit_y: float | None

    @property
    def label(self) -> str:
        """图上文字标签（dominant 前缀省略，complementary 加「补」）。"""
        if self.kind == "achromatic" or self.wavelength is None:
            return "achromatic"
        prefix = "" if self.kind == "dominant" else "补 "
        return f"{prefix}{self.wavelength:.1f} nm"


def _ray_locus_hit(locus: numpy.ndarray, waves: numpy.ndarray,
                   wx: float, wy: float, dx: float, dy: float, *,
                   t_min: float) -> tuple[float, float, float] | None:
    """射线 W + t·(dx,dy) 与轨迹折线的首个交点（t ≥ t_min）。

    返回 (wavelength_nm, hit_x, hit_y)；无交点 → None。解析求交：对每段
    (A, A+v) 解 W + t·d = A + s·v（0 ≤ s ≤ 1），取最小 t。
    """
    best: tuple[float, float, float] | None = None
    for i in range(locus.shape[0] - 1):
        ax_ = float(locus[i, 0])
        ay_ = float(locus[i, 1])
        vx = float(locus[i + 1, 0]) - ax_
        vy = float(locus[i + 1, 1]) - ay_
        denom = dx * vy - dy * vx
        if abs(denom) < 1e-15:  # 射线与该段平行
            continue
        t = ((ax_ - wx) * vy - (ay_ - wy) * vx) / denom
        s = ((ax_ - wx) * dy - (ay_ - wy) * dx) / denom
        if t < t_min or not 0.0 <= s <= 1.0:
            continue
        if best is None or t < best[0]:
            best = (float(waves[i] + s * (waves[i + 1] - waves[i])),
                    ax_ + s * vx, ay_ + s * vy)
    return best


def dominant_wavelength(x: float, y: float, *,
                        white_xy: tuple[float, float] = _D50_XY) -> WavelengthMark:
    """色度点 (x, y) 的主波长/补色波长（ROADMAP v0.5.5）。

    行为:
        射线 W→P 延长与光谱轨迹的首个交点为主波长（kind="dominant"）；
        无交点（紫区）时改取反向射线的首个轨迹交点（kind="complementary"）；
        P 与白点重合为无彩色（kind="achromatic"，wavelength/hit 均 None）。
        解析射线-折线求交，波长沿轨迹段线性插值。
    参数:
        x, y: CIE 1931 xy 色度坐标。
        white_xy: 参考白 xy（缺省 D50 PCS 白点）。
    返回:
        WavelengthMark: kind + 波长 + 轨迹交点坐标 + label。
    异常:
        VizError: 光谱轨迹数据不可用，或正反双向均无交点（数值异常）。
    """
    locus = _spectral_locus_xy()
    waves = _SPECTRAL_LOCUS_WAVELENGTHS
    if (locus is None or locus.shape[0] < 2
            or waves.shape[0] != locus.shape[0]):
        raise VizError("光谱轨迹数据不可用，无法计算主波长")
    wx = float(white_xy[0])
    wy = float(white_xy[1])
    px = float(x)
    py = float(y)
    dx = px - wx
    dy = py - wy
    if float(numpy.hypot(dx, dy)) < 1e-12:
        return WavelengthMark(px, py, "achromatic", None, None, None)
    hit = _ray_locus_hit(locus, waves, wx, wy, dx, dy, t_min=1.0 - 1e-9)
    kind = "dominant"
    if hit is None:  # 紫区：延长线只穿紫线 → 反向射线取补色波长
        hit = _ray_locus_hit(locus, waves, wx, wy, -dx, -dy, t_min=1e-9)
        kind = "complementary"
    if hit is None:
        raise VizError("射线与光谱轨迹无交点",
                       detail={"x": px, "y": py})
    return WavelengthMark(px, py, kind, hit[0], hit[1], hit[2])


def _draw_wavelength_marks(ax: Any, samples: Sequence[GamutSample]) -> None:
    """主波长标注绘制（v0.5.5）：每样本取距白点最远的 xy 点（最大饱和
    方向），白点→轨迹交点连线 + 波长文字。单样本失败 log WARNING 跳过。
    """
    white = numpy.asarray(_D50_XY, dtype=numpy.float64)
    for index, sample in enumerate(samples):
        try:
            xy = _lab_to_xy(numpy.asarray(sample.lab,
                                          dtype=numpy.float64).reshape(-1, 3))
            points = xy[numpy.isfinite(xy).all(axis=1)]
            if points.shape[0] == 0:
                continue
            distances = numpy.hypot(points[:, 0] - white[0],
                                    points[:, 1] - white[1])
            far = points[int(numpy.argmax(distances))]
            mark = dominant_wavelength(float(far[0]), float(far[1]))
            if mark.kind == "achromatic" or mark.hit_x is None or mark.hit_y is None:
                continue
            color = _series_color(index)
            ax.plot([white[0], mark.hit_x], [white[1], mark.hit_y],
                    linestyle=":", linewidth=1.0, color=color, alpha=0.85,
                    zorder=3)
            ax.scatter([far[0]], [far[1]], s=18, color=color, zorder=5)
            ax.annotate(mark.label, (mark.hit_x, mark.hit_y),
                        xytext=(5, 5), textcoords="offset points",
                        fontsize=8, color=color, zorder=6)
        except Exception as exc:  # noqa: BLE001 - 增强项失败不阻塞出图
            logger.warning("主波长标注失败（跳过 %s）: %s",
                           sample.description, exc)


def _ensure_gif(out: str | os.PathLike[str]) -> Path:
    """动画输出路径规范化（v0.5.6）：后缀强制 .gif，父目录自动创建。"""
    path = Path(out)
    if path.suffix.lower() != ".gif":
        path = path.with_suffix(".gif")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def plot_ab_slice_animation(profiles: Sequence[GamutSample],
                            out: str | os.PathLike[str], *,
                            l_start: float = 5.0, l_end: float = 95.0,
                            steps: int = 19, band: float = 5.0,
                            dpi: int = 100, interval_ms: int = 200) -> Path:
    """a-b 等亮度切片 L* 扫描动画导出 GIF（ROADMAP v0.5.6）。

    行为:
        L* 在 ``[l_start, l_end]`` 均匀取 ``steps`` 帧，逐帧渲染 a-b 切片
        （与 plot_ab_slice 同一绘制主体 ``_ab_slice_scatter`` 与主题
        token，帧标题为当前 L*），Agg 画布出 RGB 缓冲后由 Pillow 合成
        循环 GIF（``loop=0`` 永久循环、帧时长 ``interval_ms``）。仅依赖
        Pillow（matplotlib 既有依赖），无 ffmpeg。
    参数:
        profiles: 一个或多个 GamutSample。
        out: 输出路径（后缀强制 .gif）。
        l_start, l_end: L* 扫描范围（0 ≤ start < end ≤ 100）。
        steps: 帧数（2..64）。
        band: 每帧切片半带宽（>0）。
        dpi: 输出分辨率。
        interval_ms: 帧间隔毫秒（>0）。
    返回:
        pathlib.Path: 实际写出路径（.gif）。
    异常:
        VizError: profiles 为空、L* 范围/帧数/带宽/帧间隔非法。
    """
    samples = _validate_samples(profiles)
    if not isinstance(steps, int) or isinstance(steps, bool) or not 2 <= steps <= 64:
        raise VizError(f"steps 必须为 2-64 的整数，得到 {steps!r}",
                       detail={"steps": steps})
    if not (0.0 <= l_start < l_end <= 100.0):
        raise VizError(
            f"L* 范围非法：需 0 ≤ l_start < l_end ≤ 100，得到 "
            f"[{l_start!r}, {l_end!r}]",
            detail={"l_start": l_start, "l_end": l_end},
        )
    if band <= 0:
        raise VizError(f"band 必须为正数，得到 {band!r}", detail={"band": band})
    if not isinstance(interval_ms, (int, float)) or isinstance(interval_ms, bool) \
            or interval_ms <= 0:
        raise VizError(f"interval_ms 必须为正数，得到 {interval_ms!r}",
                       detail={"interval_ms": interval_ms})
    out_path = _ensure_gif(out)
    frames: list[PILImage.Image] = []
    for lightness in numpy.linspace(l_start, l_end, steps):
        current = float(lightness)
        fig, ax = plt.subplots(figsize=(6.4, 6.4), dpi=dpi)
        _style_theme(fig, ax)
        _ab_slice_scatter(ax, samples, current, band)
        ax.set_xlabel("a*", color=_T["ink_subtle"])
        ax.set_ylabel("b*", color=_T["ink_subtle"])
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_title(f"L* = {current:.0f}", color=_T["ink"])
        _legend_theme(ax, loc="upper right")
        fig.tight_layout()
        fig.canvas.draw()
        rgba = numpy.asarray(fig.canvas.buffer_rgba())
        frames.append(PILImage.fromarray(rgba[:, :, :3].copy()))
        plt.close(fig)
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(interval_ms), loop=0)
    logger.info("a-b 切片动画已写出: %s（%d 帧，L* %.0f→%.0f，%d ms/帧）",
                out_path, len(frames), l_start, l_end, int(interval_ms))
    return out_path


# ---------------------------------------------------------------------------
# 体积趋势（ROADMAP v0.5.10）：多次测量的色域体积变化曲线。被跟踪 profile
# 的每次测量为一个 (批次标签, 体积 Lab³) 点；多 profile 多系列叠加（假定
# 批次对齐，x 刻度取首系列标签）。主题 token 实时读取。
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VolumeSeries:
    """一个被跟踪 profile 的体积测量序列（多次测量，按时间/批次顺序）。"""

    name: str  # profile 标识（图例）
    points: tuple[tuple[str, float], ...]  # (批次标签, 体积 Lab³)


def plot_volume_trend(series: Sequence[VolumeSeries], out: str | os.PathLike[str],
                      *, dpi: int = 150, format: str | None = None,
                      title: str | None = None) -> Path:
    """体积趋势曲线：多次测量的色域体积变化（ROADMAP v0.5.10）。

    行为:
        x = 测量批次序（按传入顺序，1 起），y = 色域体积（Lab 单位³，与
        GamutSample.volume 同口径）。多系列（多 profile）同图叠加，系列色
        与其它绘图面一致；x 刻度标签取首系列的批次标签（多系列时假定批次
        对齐）。主题 token 实时读取。
    参数:
        series: 一个或多个 VolumeSeries。
        out: 输出路径（format 语义同 plot_chromaticity）。
        dpi: 输出分辨率。
        format: 输出格式（None=按后缀 png/svg，"png"/"svg" 强制）。
        title: 图题（缺省不绘）。
    返回:
        pathlib.Path: 实际写出路径。
    异常:
        VizError: series 为空、某序列无点、体积非有限值。
    """
    if not series:
        raise VizError("至少需要一个 VolumeSeries", detail={"series": 0})
    for s in series:
        if not s.points:
            raise VizError(f"序列 {s.name!r} 无测量点", detail={"name": s.name})
        for label, volume in s.points:
            if not numpy.isfinite(volume):
                raise VizError(
                    f"序列 {s.name!r} 批次 {label!r} 体积非有限值: {volume!r}",
                    detail={"name": s.name, "label": str(label), "volume": float(volume)},
                )
    out_path = _ensure_format(out, format)
    fig, ax = plt.subplots(figsize=(8.0, 5.2), dpi=dpi)
    _style_theme(fig, ax)
    for index, s in enumerate(series):
        ys = [float(v) for _, v in s.points]
        ax.plot(range(1, len(ys) + 1), ys, marker="o", markersize=4,
                linewidth=1.8, color=_series_color(index), label=s.name,
                zorder=4)
    max_len = max(len(s.points) for s in series)
    ax.set_xticks(range(1, max_len + 1))
    first_labels = [label for label, _ in series[0].points]
    if len(first_labels) == max_len:
        ax.set_xticklabels(first_labels, rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("测量批次（按传入顺序）", color=_T["ink_subtle"])
    ax.set_ylabel("色域体积（Lab 单位³）", color=_T["ink_subtle"])
    if title:
        ax.set_title(title, color=_T["ink"])
    _legend_theme(ax, loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("体积趋势已写出: %s（%d 系列）", out_path, len(series))
    return out_path
