"""图像 ICC 处理流水线：assign/embed/strip/convert 与批量处理（TECH-DESIGN §4.7）。

ICC 数据全部经 Pillow 原生 ``im.info["icc_profile"]`` 机制流转：
- 读取：``Image.open`` 自动解出 TIFF tag 34675 / JPEG APP2 / PNG iCCP /
  PSD 资源块中的 ICC 字节到 ``im.info["icc_profile"]``；
- 写出：写层在 ``save`` 时**显式**传 ``icc_profile=`` 字节
  （Pillow 12.3 实测：JPEG 从 info 回退嵌入已失效，显式传参对
  TIFF/JPEG/PNG 三种格式均可靠）；strip 后不传该参数即保证无 ICC 写出。

PSD 只读（Pillow PsdImagePlugin 不支持写出）：写出 ``.psd`` 一律
:class:`~packstudio.engines.color_engine.errors.UnsupportedFormatError`。

转换经 :mod:`packstudio.engines.color_engine.cmm.engine` 的构建/应用分离引擎（本模块级共享实例）。
对 icc/ 解析层仅做惰性导入（profile 色空间判定），缺席时回退原始头部字节
读取，核心路径不依赖 icc/。
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from PIL import Image, ImageFile

from packstudio.engines.color_engine.cmm.engine import RenderingIntent, TransformEngine, TransformSpec
from packstudio.engines.color_engine.errors import (
    IccToolkitError,
    ImagingError,
    ProfileMismatchError,
    UnsupportedFormatError,
)

logger = logging.getLogger("icct.imaging")

__all__ = [
    "ProfileOp",
    "FileResult",
    "read_image",
    "get_embedded_profile",
    "assign_profile",
    "embed_profile",
    "strip_profile",
    "convert_image",
    "process_file",
    "process_batch",
    "SUPPORTED_READ",
    "SUPPORTED_WRITE",
]

SUPPORTED_READ: Final[frozenset[str]] = frozenset(
    {".tif", ".tiff", ".jpg", ".jpeg", ".png", ".psd"}
)
SUPPORTED_WRITE: Final[frozenset[str]] = frozenset(
    {".tif", ".tiff", ".jpg", ".jpeg", ".png"}
)
# PSD 只读（Pillow PsdImagePlugin 无写出器）——写出 .psd → UnsupportedFormatError

_FORMAT_BY_EXT: Final[dict[str, str]] = {
    ".tif": "TIFF", ".tiff": "TIFF",
    ".jpg": "JPEG", ".jpeg": "JPEG",
    ".png": "PNG",
}

_SOURCE_FORMATS: Final[frozenset[str]] = frozenset({"tiff", "jpeg", "png", "psd"})

# profile 数据色空间 → 兼容的 PIL 图像模式（assign/embed 的匹配校验）
_SPACE_MODES: Final[dict[str, frozenset[str]]] = {
    "RGB ": frozenset({"RGB", "RGBA", "RGBX", "P"}),
    "CMYK": frozenset({"CMYK"}),
    "GRAY": frozenset({"L", "LA", "1"}),
    "Lab ": frozenset({"LAB"}),
}


class NoSourceProfileError(ImagingError):
    """图像既无嵌入 profile 又未显式提供 src_profile（设计 §4.7 指定 code）。"""

    code = "NO_SOURCE_PROFILE"


class ProfileOp(StrEnum):
    """profile 操作类型（TECH-DESIGN §3.6）。"""

    ASSIGN = "assign"
    EMBED = "embed"
    STRIP = "strip"
    CONVERT = "convert"


@dataclass(frozen=True, slots=True)
class FileResult:
    """单文件处理结果（TECH-DESIGN §3.6；异常不外抛，全部落入本结构）。

    Attributes:
        src: 源文件路径字符串。
        dst: 目标文件路径字符串（失败时仍为预期目标）。
        op: 操作名（ProfileOp 值）。
        ok: 是否成功。
        error_code: 失败时的稳定机器码（IccToolkitError.code 或 "IO"/"INTERNAL"）。
        message: 失败时的人读信息。
        src_profile: 源图像嵌入 profile 的描述名（无则 None）。
        dst_profile: 结果图像携带 profile 的描述名（无则 None）。
    """

    src: str
    dst: str | None
    op: str
    ok: bool
    error_code: str | None
    message: str | None
    src_profile: str | None
    dst_profile: str | None

    def as_json(self) -> dict[str, Any]:
        """JSON 契约输出（字段名 = dataclass 字段名，设计 §2.3）。"""
        return {
            "src": self.src,
            "dst": self.dst,
            "op": self.op,
            "ok": self.ok,
            "error_code": self.error_code,
            "message": self.message,
            "src_profile": self.src_profile,
            "dst_profile": self.dst_profile,
        }


_ENGINE: Final[TransformEngine] = TransformEngine()
"""模块级共享变换引擎（设计 §4.7）。"""


# ---- 读取与嵌入态 -------------------------------------------------------

def read_image(path: Path) -> Image.Image:
    """打开并完全解码图像文件（TECH-DESIGN §4.7）。

    行为:
        按后缀白名单校验后用 Pillow 打开并 ``load()``（PSD 走内建
        PsdImagePlugin 合成层）。EXIF 转向保留原样（不做 exif_transpose）。
        截断文件策略：首次解码抛 "image file is truncated" 时，临时置位
        ``ImageFile.LOAD_TRUNCATED_IMAGES`` 重试一次（不全局开启），记
        WARNING；仍失败转 ImagingError。
    参数:
        path: 图像路径（后缀须在 :data:`SUPPORTED_READ` 内）。
    返回:
        已解码的 ``Image.Image``。
    异常:
        UnsupportedFormatError: 后缀不在白名单。
        ImagingError: Pillow 解码失败（含 PSD 合成失败、炸弹防护触发）。
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_READ:
        raise UnsupportedFormatError(
            f"不支持的读取格式: {suffix or '<无后缀>'}",
            detail={"path": str(path), "suffix": suffix},
        )
    try:
        with Image.open(path) as im:
            im.load()
            return im
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if "truncated" in str(exc).lower():
            return _read_truncated(path)
        raise ImagingError(
            f"图像解码失败: {exc}", detail={"path": str(path)}
        ) from exc


def _read_truncated(path: Path) -> Image.Image:
    """截断文件重试：临时开启 LOAD_TRUNCATED_IMAGES（线程不保证互斥，见模块注）。"""
    logger.warning("图像文件不完整，启用 LOAD_TRUNCATED_IMAGES 重试: %s", path)
    previous = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        with Image.open(path) as im:
            im.load()
            return im
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImagingError(
            f"图像解码失败（含截断重试）: {exc}", detail={"path": str(path)}
        ) from exc
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous


def get_embedded_profile(im: Image.Image) -> tuple[str, bytes | None]:
    """读取图像当前嵌入的 ICC 字节。

    行为:
        从 ``im.info["icc_profile"]`` 取值（TIFF tag 34675 / JPEG APP2 /
        PNG iCCP / PSD 资源块由 Pillow 解码阶段统一解出）。来源标签取
        ``im.format`` 小写；四种约定格式之外若确有 ICC 字节则标
        ``"unknown"``。
    参数:
        im: 图像。
    返回:
        ``(来源, icc 字节)``；无嵌入时 ``("none", None)``。
    异常:
        不抛。
    """
    data = im.info.get("icc_profile")
    if not data:
        return ("none", None)
    fmt = (im.format or "").lower()
    if fmt in _SOURCE_FORMATS:
        return (fmt, data)
    return ("unknown", data)


# ---- profile 字节与色空间帮助 -------------------------------------------

def _profile_bytes(profile: bytes | bytearray | memoryview | Path) -> bytes:
    """把 profile 参数统一为字节（bytes 原样；路径读文件，失败 ImagingError）。"""
    if isinstance(profile, (bytes, bytearray, memoryview)):
        return bytes(profile)
    path = Path(os.fspath(profile))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ImagingError(
            f"profile 文件读取失败: {exc}",
            detail={"path": str(path), "errno": exc.errno},
        ) from exc


def _profile_space(data: bytes) -> str:
    """profile 数据色空间签名：惰性导入 icc 层，缺席/失败回退原始头部读取。"""
    try:
        from packstudio.engines.color_engine.icc.header import parse_header  # icc/ 并行开发，惰性导入

        return str(parse_header(data).color_space)
    except Exception:  # noqa: BLE001 - icc/ 缺席或数据畸形时回退
        if len(data) < 20:
            raise ImagingError(
                "profile 数据过短，无法判定色空间",
                detail={"size": len(data)},
            ) from None
        return data[16:20].decode("ascii", "replace")


def _ensure_compatible(space: str, im: Image.Image) -> None:
    """profile 色空间与图像模式匹配校验，不匹配抛 ProfileMismatchError。"""
    allowed = _SPACE_MODES.get(space, frozenset())
    if im.mode not in allowed:
        known = space in _SPACE_MODES
        hint = "无法识别的 profile 色空间" if not known else "profile 色空间与图像模式不匹配"
        raise ProfileMismatchError(
            f"{hint}: {space!r} vs 图像模式 {im.mode!r}",
            detail={"profile_color_space": space, "image_mode": im.mode},
        )


def _describe_profile_bytes(data: bytes | None) -> str | None:
    """ICC 字节 → 描述名（经本模块族 cmm 引擎；失败返回 None，不抛）。"""
    if not data:
        return None
    try:
        from packstudio.engines.color_engine.cmm.engine import open_profile, profile_names

        name, description, _info = profile_names(open_profile(data))
        return (description or name) or None
    except Exception:  # noqa: BLE001 - 元信息仅是附属输出，失败不阻断主流程
        return None


# ---- 像素不动、只动标签 -------------------------------------------------

def assign_profile(im: Image.Image, profile: bytes | Path) -> Image.Image:
    """把图像的嵌入 profile 替换/设置为给定 profile（像素不动，仅重贴标签）。

    行为:
        浅拷贝图像（``im.copy()``，不动原对象），置
        ``info["icc_profile"]``。profile 色空间与图像模式不匹配
        （如 CMYK profile 配 RGB 图）抛 ProfileMismatchError。
    参数:
        im: 输入图像。
        profile: profile 字节或路径。
    返回:
        新图像（原图像不被修改）。
    异常:
        ProfileMismatchError: 色空间与模式不匹配或 profile 无法识别。
        ImagingError: profile 文件读取失败/数据过短。
    """
    data = _profile_bytes(profile)
    _ensure_compatible(_profile_space(data), im)
    out = im.copy()
    out.info["icc_profile"] = data
    return out


def embed_profile(im: Image.Image, profile: bytes | Path) -> Image.Image:
    """嵌入 profile（幂等：已有嵌入先剥离再贴）。

    行为:
        等价 assign 语义但保留独立接口以便未来做校验差异；已有嵌入时先
        :func:`strip_profile` 再 assign，保证最终 info 中恰为目标 profile。
    参数:
        im: 输入图像。
        profile: profile 字节或路径。
    返回:
        新图像（原图像不被修改）。
    异常:
        同 :func:`assign_profile`。
    """
    if im.info.get("icc_profile") is not None:
        im = strip_profile(im)
    return assign_profile(im, profile)


def strip_profile(im: Image.Image) -> Image.Image:
    """剥离图像的嵌入 profile（删除 ``info["icc_profile"]``）。

    行为:
        浅拷贝后移除 ``info["icc_profile"]``；写出层（:func:`process_file`）
        对无 ICC 字节的图像不传 ``icc_profile`` 参数，保证写出的文件不带 ICC。
    参数:
        im: 输入图像。
    返回:
        新图像（原图像不被修改）。
    异常:
        不抛。
    """
    out = im.copy()
    out.info.pop("icc_profile", None)
    return out


# ---- 像素变换 ------------------------------------------------------------

def _decoded_bytes(im: Image.Image) -> int:
    """估计解码后字节数（宽×高×通道数×1 字节；8bit 路径为绝对主流）。"""
    return im.width * im.height * max(1, len(im.getbands()))


_BAND_TARGET_BYTES: Final[int] = 32 * 1024 * 1024          # 单带目标工作区
# 注（v0.4.2 实测）：不再设自动分块阈值常量——PIL 整图 applyTransform 在 C 层
# 已按行流式（92MB 解码图峰值仅 +9MB），自动分块因 crop/paste 拷贝反而更高
# （+188MB）；分块仅显式 opt-in（tiled_threshold_bytes）。


def _convert_tiled(spec, im: Image.Image, *, band_bytes: int) -> Image.Image:
    """分块（横向条带）执行变换，输出与整图变换逐像素一致（lcms 逐点变换）。

    峰值 = 整图源 + 整图目标 + 单带工作区；变换工作区被限制在 band_bytes
    量级，并避免引擎在单次调用里为整图分配额外中间缓冲。首带确定输出模式
    （引擎按源 profile 色空间决定，如 RGBA 源剥离 alpha 输出 RGB）。
    """
    width, height = im.size
    band_rows = max(1, band_bytes // max(1, width * max(1, len(im.getbands()))))
    out: Image.Image | None = None
    y = 0
    while y < height:
        y2 = min(height, y + band_rows)
        band = im.crop((0, y, width, y2))
        converted = _ENGINE.apply(spec, band)
        if out is None:
            out = Image.new(converted.mode, (width, height))
        out.paste(converted, (0, y))
        y = y2
    if out is None:  # 0 像素图（防御；read_image 不产生）
        out = _ENGINE.apply(spec, im)
    return out


def convert_image(im: Image.Image, dst_profile: bytes | Path,
                  src_profile: bytes | Path | None = None, *,
                  intent: RenderingIntent = RenderingIntent.PERCEPTUAL,
                  bpc: bool = False,
                  tiled_threshold_bytes: int | None = None,
                  band_bytes: int = _BAND_TARGET_BYTES) -> Image.Image:
    """经 CMM 引擎把图像从源 profile 变换到目标 profile。

    行为:
        源 profile 取 ``src_profile`` 参数（优先），否则图像嵌入值；两者皆无
        抛 ``ImagingError(code="NO_SOURCE_PROFILE")``。经共享
        :class:`~packstudio.engines.color_engine.cmm.engine.TransformEngine` 变换后贴
        ``dst_profile`` 标签；输出模式由目标 profile 色空间决定
        （CMYK→RGB 输出 'RGB'，RGB→CMYK 输出 'CMYK'）。
        v0.4.2 大图流式：``tiled_threshold_bytes`` 给定时，解码尺寸估计超过
        阈值即走横向条带分块变换（输出与整图逐像素一致，单带工作区受
        ``band_bytes`` 约束）；``0`` 表示无条件强制分块。**缺省 None 不自动
        分块**——实测 PIL 整图 ``apply_transform`` 在 C 层已按行流式
        （92MB 解码图峰值仅 +9MB），自动分块因 crop/paste 拷贝反而更高
        （+188MB）；分块机制为显式 opt-in 与逐带解码管线预留（详见
        CHANGELOG 0.3.15 实测结论）。
    参数:
        im: 输入图像。
        dst_profile: 目标 profile 字节或路径。
        src_profile: 源 profile（优先于图像嵌入值）。
        intent: 渲染意图（默认感知）。
        bpc: 黑点补偿。
        tiled_threshold_bytes: 分块触发阈值（字节）；None=不自动分块，0=强制。
        band_bytes: 单带工作区目标字节数（默认 32MB）。
    返回:
        变换后的新图像，``info["icc_profile"]`` = 目标 profile 字节。
    异常:
        NoSourceProfileError(ImagingError): 无源 profile（code="NO_SOURCE_PROFILE"）。
        ProfileLoadError / IntentUnsupportedError / CmmError: 透传自引擎。
        ImagingError: 图像模式与源 profile 不兼容。
    """
    dst_bytes = _profile_bytes(dst_profile)
    if src_profile is not None:
        src_bytes: bytes | None = _profile_bytes(src_profile)
    else:
        src_bytes = im.info.get("icc_profile")
    if not src_bytes:
        raise NoSourceProfileError(
            "图像未嵌入 profile 且未提供 src_profile，无法转换",
            detail={"image_mode": im.mode},
        )
    spec = TransformSpec(
        src_profile=src_bytes,
        dst_profile=dst_bytes,
        intent=RenderingIntent(int(intent)),
        bpc=bool(bpc),
    )
    if tiled_threshold_bytes is not None and _decoded_bytes(im) > int(tiled_threshold_bytes):
        out = _convert_tiled(spec, im, band_bytes=band_bytes)
    else:
        out = _ENGINE.apply(spec, im)
    out.info["icc_profile"] = dst_bytes
    return out


# ---- 单文件 / 批量 -------------------------------------------------------

def _out_extension(out_format: str | None, fallback: str) -> str:
    """决定输出后缀并校验（写出白名单；.psd → UnsupportedFormatError）。"""
    raw = (out_format or fallback).strip().lower()
    ext = raw if raw.startswith(".") else f".{raw}"
    if ext not in SUPPORTED_WRITE:
        raise UnsupportedFormatError(
            f"不支持的写出格式: {ext}",
            detail={"suffix": ext, "supported": sorted(SUPPORTED_WRITE)},
        )
    return ext


def _write_image(im: Image.Image, dst: Path, ext: str, quality: int) -> None:
    """写出图像；输出目录自动创建；ICC 字节显式随 save 传入（见模块注）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fmt = _FORMAT_BY_EXT[ext]
    params: dict[str, Any] = {}
    icc = im.info.get("icc_profile")
    if icc:
        params["icc_profile"] = icc  # Pillow 12.3 下 JPEG 必须显式传参
    if fmt == "JPEG":
        params["quality"] = int(quality)
        if getattr(im, "format", None) == "JPEG":
            params["subsampling"] = "keep"  # 仅 JPEG 源支持；失败自动降级重试
    try:
        im.save(dst, format=fmt, **params)
    except ValueError as exc:
        if params.pop("subsampling", None) == "keep":
            logger.debug("subsampling='keep' 不可用，降级默认子采样重试: %s", exc)
            im.save(dst, format=fmt, **params)
        else:
            raise
    logger.info("写出 %s（%s%s）", dst, fmt,
                f", icc {len(icc)} bytes" if icc else ", 无 icc")


def process_file(src: Path, dst: Path, op: ProfileOp, *,
                 profile: Path | bytes | None = None,
                 src_profile: Path | bytes | None = None,
                 intent: RenderingIntent = RenderingIntent.PERCEPTUAL,
                 bpc: bool = False,
                 out_format: str | None = None,
                 quality: int = 92) -> FileResult:
    """单文件全流程：read → op → write（异常不外抛，全部转 FileResult）。

    行为:
        op=assign/embed 需要 ``profile``；op=strip 忽略 ``profile``
        （CLI 层负责禁止同用）；op=convert 以 ``profile`` 为目标 profile
        （必需），源取 ``src_profile``（优先）或图像嵌入值。写出格式取
        ``out_format``，缺省按 dst 后缀；JPEG 默认 quality=92；写出目录
        自动创建（mkdir parents）。失败时 ``FileResult(ok=False,
        error_code, message)``：IccToolkitError → 其 code，OSError → "IO"，
        其余 → "INTERNAL"。
    参数:
        src: 源文件路径。
        dst: 目标文件路径。
        op: 操作类型。
        profile: profile 路径或字节（op 相关性见上）。
        src_profile: 源 profile 路径或字节（仅 convert；优先于图像嵌入值，
            v0.2.0 增补——未嵌入图像的 convert 一步化）。
        intent: 渲染意图（仅 convert 有效）。
        bpc: 黑点补偿（仅 convert 有效）。
        out_format: 输出格式（"tif"/"jpg"/"png" 等；psd 非法）。
        quality: JPEG 质量（默认 92）。
    返回:
        :class:`FileResult`。
    异常:
        不抛（全部转结果）。
    """
    src_path = Path(src)
    dst_path = Path(dst)
    op = ProfileOp(op)
    src_profile_desc: str | None = None
    try:
        im = read_image(src_path)
        src_profile_desc = _describe_profile_bytes(im.info.get("icc_profile"))
        profile_bytes = _profile_bytes(profile) if profile is not None else None
        src_bytes = _profile_bytes(src_profile) if src_profile is not None else None
        if op is ProfileOp.ASSIGN or op is ProfileOp.EMBED:
            if profile_bytes is None:
                raise ImagingError(
                    f"op={op.value} 需要 profile 参数",
                    detail={"op": op.value},
                )
            if op is ProfileOp.ASSIGN:
                out = assign_profile(im, profile_bytes)
            else:
                out = embed_profile(im, profile_bytes)
        elif op is ProfileOp.STRIP:
            out = strip_profile(im)
        else:  # CONVERT
            if profile_bytes is None:
                raise ImagingError(
                    "op=convert 需要 profile 参数（目标 profile）",
                    detail={"op": "convert"},
                )
            out = convert_image(im, profile_bytes, src_bytes,
                                intent=intent, bpc=bpc)
        dst_profile_desc = _describe_profile_bytes(out.info.get("icc_profile"))
        ext = _out_extension(out_format, dst_path.suffix)
        _write_image(out, dst_path, ext, quality)
    except IccToolkitError as exc:
        logger.warning("处理失败 %s: [%s] %s", src_path, exc.code, exc)
        return FileResult(src=str(src_path), dst=str(dst_path), op=op.value, ok=False,
                          error_code=exc.code, message=str(exc),
                          src_profile=src_profile_desc, dst_profile=None)
    except OSError as exc:
        logger.warning("处理失败 %s: IO %s", src_path, exc)
        return FileResult(src=str(src_path), dst=str(dst_path), op=op.value, ok=False,
                          error_code="IO", message=str(exc),
                          src_profile=src_profile_desc, dst_profile=None)
    except Exception as exc:  # noqa: BLE001 - 契约：process_file 不外抛
        logger.exception("未分类错误 %s", src_path)
        return FileResult(src=str(src_path), dst=str(dst_path), op=op.value, ok=False,
                          error_code="INTERNAL", message=f"{type(exc).__name__}: {exc}",
                          src_profile=src_profile_desc, dst_profile=None)
    logger.info("完成 %s: %s → %s", op.value, src_path, dst_path)
    return FileResult(src=str(src_path), dst=str(dst_path), op=op.value, ok=True,
                      error_code=None, message=None,
                      src_profile=src_profile_desc, dst_profile=dst_profile_desc)


def dynamic_workers(sizes: list[int], *,
                    cpu: int | None = None,
                    small_total_bytes: int = 16 * 1024 * 1024,
                    huge_single_bytes: int = 64 * 1024 * 1024,
                    small_workers: int = 8,
                    default_workers: int = 4) -> int:
    """按文件大小分布选择批量并发度（ROADMAP v0.4.4）。

    规则（可注入阈值便于测试）：
        - 任一单文件 ≥ ``huge_single_bytes``（默认 64MB）→ 1
          （大图串行：解码内存 ≈ 像素×8 字节量级，并发会成倍抬高峰值）；
        - 全部文件总大小 ≤ ``small_total_bytes``（默认 16MB）→ min(8, cpu)
          （小文件收益在 IO/编码重叠，高并发几乎无内存代价）；
        - 其余（混合/中等）→ min(4, cpu)（默认档）。
        - 空列表 → 1。

    参数:
        sizes: 各源文件字节数（缺失文件按 0 计，由调用方容忍）。
        cpu: CPU 数；None 时 os.cpu_count()。
        small_workers / default_workers: 两档并发上限。

    返回:
        int: 建议并发线程数（≥1）。
    """
    if cpu is None:
        cpu = os.cpu_count() or 1
    cpu = max(1, int(cpu))
    if not sizes:
        return 1
    if max(sizes) >= huge_single_bytes:
        return 1
    if sum(sizes) <= small_total_bytes:
        return max(1, min(int(small_workers), cpu))
    return max(1, min(int(default_workers), cpu))


def process_batch(srcs: list[Path], dst_dir: Path, op: ProfileOp, *,
                  profile: Path | bytes | None = None,
                  intent: RenderingIntent = RenderingIntent.PERCEPTUAL,
                  bpc: bool = False,
                  out_format: str | None = None,
                  quality: int = 92,
                  workers: int | None = None,
                  progress_cb: Callable[[FileResult], None] | None = None,
                  ) -> list[FileResult]:
    """批量处理：ThreadPoolExecutor 并发，结果顺序 = 输入顺序。

    行为:
        目标命名规则 ``<stem>-<op><ext>``（与 CLI --dst-dir 一致）；
        ``ext`` 取 ``out_format``，缺省按源后缀（.psd 源会得到
        UNSUPPORTED_FORMAT 的失败结果，符合 PSD 只读契约）。每个文件
        完成后回调 ``progress_cb(result)``（完成顺序）。Pillow 解码部分
        持 GIL，并发收益主要在 IO/编码。
        ``workers=None``（默认）为自动模式：按源文件大小分布经
        :func:`dynamic_workers` 选择并发度（v0.4.4）；显式整数则固定并发。
    参数:
        srcs: 源文件路径列表。
        dst_dir: 输出目录（自动创建）。
        op: 操作类型。
        profile: profile 路径或字节。
        intent: 渲染意图。
        bpc: 黑点补偿。
        out_format: 输出格式；None 按源后缀。
        quality: JPEG 质量。
        workers: 并发线程数；None = 按文件大小自动。
        progress_cb: 每文件完成回调。
    返回:
        ``list[FileResult]``，顺序与 ``srcs`` 一致。
    异常:
        progress_cb 抛出的异常原样传播（调用方契约）；其余不抛。
    """
    op = ProfileOp(op)
    dst_dir = Path(dst_dir)
    sources = [Path(s) for s in srcs]
    results: list[FileResult | None] = [None] * len(sources)

    def _one(index: int, source: Path) -> tuple[int, FileResult]:
        raw = out_format or source.suffix
        ext = raw if raw.startswith(".") else f".{raw}"
        target = dst_dir / f"{source.stem}-{op.value}{ext}"
        return index, process_file(
            source, target, op,
            profile=profile, intent=intent, bpc=bpc,
            out_format=out_format, quality=quality,
        )

    if workers is None:
        sizes = [source.stat().st_size if source.exists() else 0 for source in sources]
        worker_count = dynamic_workers(sizes)
    else:
        worker_count = max(1, min(int(workers), len(sources) or 1))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(_one, index, source) for index, source in enumerate(sources)]
        for future in as_completed(futures):
            index, result = future.result()
            results[index] = result
            if progress_cb is not None:
                progress_cb(result)
    return [r for r in results if r is not None]
