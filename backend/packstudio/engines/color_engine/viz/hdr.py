"""HDR 传输特性曲线（ROADMAP v0.5.11）：PQ / HLG 的 OETF 数学与可视化数据。

公式经权威规范核查（v0.5.11）：

- **PQ（SMPTE ST 2084）**——常数 ``m1 = 2610/16384``、``m2 = 2523/32``、
  ``c1 = 3424/4096``、``c2 = 2413/128``、``c3 = 2392/128``（满足
  ``c1 = c3 − c2 + 1``）；EOTF（信号→绝对亮度 cd/m²）
  ``L = 10000·((max(E^(1/m2) − c1, 0)) / (c2 − c3·E^(1/m2)))^(1/m1)``；
  OETF 为其精确逆。参考值：信号 0.5 ↔ **92.25 nits**、信号 1 ↔ 10000 nits。
- **HLG（ITU-R BT.2100）**——OETF 分段：
  ``E' = √(3·E₀)``（0 ≤ E₀ ≤ 1/12）、``E' = a·ln(12·E₀ − b) + c``（1/12 < E₀ ≤ 1），
  常数 ``a = 0.17883277``、``b = 1 − 4a = 0.28466892``、
  ``c = 0.5 − a·ln(4a) = 0.55991073``；参考值：E'(1/12) = **0.5**（参考白）、
  E'(1) = 1；逆 OETF：``E₀ = E'²/3``（E' ≤ 0.5）、
  ``E₀ = (e^((E' − c)/a) + b)/12``（E' > 0.5）。
- **SDR 基准**——sRGB（IEC 61966-2-1 分段）与 Gamma 2.2 的 OETF，作对比曲线。

OETF 对比图约定：x = 归一化线性输入（PQ = L/10000 nits；HLG = 场景线性；
SDR = 相对显示线性），y = 信号 0-1；x 按 log 均匀采样（1e-4..1）以呈现
PQ 的感知分配形态。所有函数接受标量或 ndarray，标量入 → 标量出。
"""
from __future__ import annotations

from typing import Any, Final

import numpy

from packstudio.engines.color_engine.errors import VizError

#: PQ 满信号对应的最大绝对亮度（cd/m²，ST 2084）。
PQ_MAX_NITS: Final[float] = 10000.0

# ST 2084 常数（有理数精确分数形式）
PQ_M1: Final[float] = 2610.0 / 16384.0
PQ_M2: Final[float] = 2523.0 / 32.0
PQ_C1: Final[float] = 3424.0 / 4096.0
PQ_C2: Final[float] = 2413.0 / 128.0
PQ_C3: Final[float] = 2392.0 / 128.0

# BT.2100 HLG OETF 常数
HLG_A: Final[float] = 0.17883277
HLG_B: Final[float] = 1.0 - 4.0 * HLG_A  # 0.28466892
HLG_C: Final[float] = 0.5 - HLG_A * numpy.log(4.0 * HLG_A)  # 0.55991073

#: 支持的传输特性名（OETF 对比图曲线与 API transfers 白名单）。
SUPPORTED_TRANSFERS: Final[tuple[str, ...]] = ("pq", "hlg", "srgb", "gamma22")

#: 图例显示名（与 SUPPORTED_TRANSFERS 同序映射）。
TRANSFER_LABELS: Final[dict[str, str]] = {
    "pq": "PQ（SMPTE ST 2084）",
    "hlg": "HLG（ITU-R BT.2100）",
    "srgb": "sRGB（IEC 61966-2-1）",
    "gamma22": "Gamma 2.2（SDR 基准）",
}

#: CICP transfer_characteristics → 曲线名（与 icc.validate 的 HdrCapability
#: transfer_label 同口径：16=PQ / 18=HLG，ITU-T H.273 编码值）。
CICP_TO_TRANSFER: Final[dict[int, str]] = {16: "pq", 18: "hlg"}

#: 曲线 x 采样下界（log 轴最小 decade：1e-4）。
_X_MIN_DECADE: Final[float] = -4.0

#: 曲线点数范围（API n 参数同口径）。
MIN_POINTS: Final[int] = 17
MAX_POINTS: Final[int] = 257


def _scalar_or_array(value: Any, result: numpy.ndarray) -> Any:
    """标量入 → 标量出（float），ndarray 入 → ndarray。"""
    return float(result) if numpy.ndim(value) == 0 else result


def pq_eotf(signal: Any) -> Any:
    """PQ EOTF：信号 0-1 → 绝对亮度 cd/m²（0..10000）。

    异常:
        VizError: signal 含非有限值。
    """
    arr = numpy.asarray(signal, dtype=numpy.float64)
    if not numpy.isfinite(arr).all():
        raise VizError(f"signal 含非有限值: {signal!r}")
    s = numpy.clip(arr, 0.0, 1.0)
    xp = s ** (1.0 / PQ_M2)
    luma = PQ_MAX_NITS * (
        numpy.maximum(xp - PQ_C1, 0.0) / (PQ_C2 - PQ_C3 * xp)) ** (1.0 / PQ_M1)
    return _scalar_or_array(signal, luma)


def pq_oetf(luma: Any) -> Any:
    """PQ OETF：绝对亮度 cd/m²（0..10000）→ 信号 0-1（pq_eotf 的精确逆）。

    异常:
        VizError: luma 含非有限值或为负。
    """
    arr = numpy.asarray(luma, dtype=numpy.float64)
    if not numpy.isfinite(arr).all() or (arr < 0.0).any():
        raise VizError(f"luma 须为非负有限值: {luma!r}")
    y = numpy.clip(arr / PQ_MAX_NITS, 0.0, 1.0) ** PQ_M1
    sig = ((PQ_C1 + PQ_C2 * y) / (1.0 + PQ_C3 * y)) ** PQ_M2
    return _scalar_or_array(luma, sig)


def hlg_oetf(scene_linear: Any) -> Any:
    """HLG OETF：场景线性 E₀（0-1 相对）→ 信号 0-1（BT.2100 分段）。

    异常:
        VizError: scene_linear 含非有限值或为负。
    """
    arr = numpy.asarray(scene_linear, dtype=numpy.float64)
    if not numpy.isfinite(arr).all() or (arr < 0.0).any():
        raise VizError(f"scene_linear 须为非负有限值: {scene_linear!r}")
    e = numpy.clip(arr, 0.0, 1.0)
    # where 两分支都会被求值：log 分支输入 clip 到安全下界（12·(1/12) − b =
    # 1 − b = 4a > 0），e ≤ 1/12 的元素由 sqrt 分支采纳，杜绝 log(负数) 警告。
    e_safe = numpy.maximum(e, 1.0 / 12.0)
    sig = numpy.where(e <= 1.0 / 12.0,
                      numpy.sqrt(3.0 * e),
                      HLG_A * numpy.log(12.0 * e_safe - HLG_B) + HLG_C)
    return _scalar_or_array(scene_linear, sig)


def hlg_inverse_oetf(signal: Any) -> Any:
    """HLG 逆 OETF：信号 0-1 → 场景线性 E₀（BT.2100 分段，hlg_oetf 的逆）。

    异常:
        VizError: signal 含非有限值。
    """
    arr = numpy.asarray(signal, dtype=numpy.float64)
    if not numpy.isfinite(arr).all():
        raise VizError(f"signal 含非有限值: {signal!r}")
    s = numpy.clip(arr, 0.0, 1.0)
    e = numpy.where(s <= 0.5, s * s / 3.0,
                    (numpy.exp((s - HLG_C) / HLG_A) + HLG_B) / 12.0)
    return _scalar_or_array(signal, e)


def srgb_oetf(linear: Any) -> Any:
    """sRGB OETF：相对线性显示亮度（0-1）→ 信号（IEC 61966-2-1 分段）。

    异常:
        VizError: linear 含非有限值或为负。
    """
    arr = numpy.asarray(linear, dtype=numpy.float64)
    if not numpy.isfinite(arr).all() or (arr < 0.0).any():
        raise VizError(f"linear 须为非负有限值: {linear!r}")
    e = numpy.clip(arr, 0.0, 1.0)
    sig = numpy.where(e <= 0.0031308, 12.92 * e, 1.055 * e ** (1.0 / 2.4) - 0.055)
    return _scalar_or_array(linear, sig)


def gamma22_oetf(linear: Any) -> Any:
    """Gamma 2.2 OETF：相对线性（0-1）→ 信号（纯幂，SDR 简单 gamma 基准）。

    异常:
        VizError: linear 含非有限值或为负。
    """
    arr = numpy.asarray(linear, dtype=numpy.float64)
    if not numpy.isfinite(arr).all() or (arr < 0.0).any():
        raise VizError(f"linear 须为非负有限值: {linear!r}")
    sig = numpy.clip(arr, 0.0, 1.0) ** (1.0 / 2.2)
    return _scalar_or_array(linear, sig)


_OETF_BY_NAME: Final[dict[str, Any]] = {
    # x = 归一化线性输入（PQ 除以满量程 10000 nits；HLG/SDR 为相对线性）
    "pq": lambda x: pq_oetf(x * PQ_MAX_NITS),
    "hlg": hlg_oetf,
    "srgb": srgb_oetf,
    "gamma22": gamma22_oetf,
}


def transfer_curve_points(transfer: str, n: int = 65) -> tuple[tuple[float, float], ...]:
    """一条传输特性的 OETF 采样点序列（对比图数据源）。

    行为:
        x 在 [1e-4, 1] log 均匀取 ``n`` 点，y = 对应 OETF 信号；x/y 各保留
        8/6 位小数（JSON 友好）。x=1 时所有传输特性信号恒为 1。
    参数:
        transfer: pq / hlg / srgb / gamma22。
        n: 点数（17..257）。
    返回:
        tuple[(x, y), ...]: x 严格递增、y 单调不减。
    异常:
        VizError: transfer 不在白名单或 n 越界。
    """
    if transfer not in SUPPORTED_TRANSFERS:
        raise VizError(
            f"不支持的传输特性: {transfer!r}（支持 {'/'.join(SUPPORTED_TRANSFERS)}）",
            detail={"transfer": str(transfer),
                    "supported": list(SUPPORTED_TRANSFERS)},
        )
    if not isinstance(n, int) or isinstance(n, bool) or not MIN_POINTS <= n <= MAX_POINTS:
        raise VizError(
            f"n 必须为 {MIN_POINTS}-{MAX_POINTS} 的整数，得到 {n!r}",
            detail={"n": n, "min": MIN_POINTS, "max": MAX_POINTS},
        )
    t = numpy.linspace(0.0, 1.0, n)
    x = 10.0 ** (_X_MIN_DECADE * (1.0 - t))
    y = numpy.clip(numpy.asarray(_OETF_BY_NAME[transfer](x),
                                 dtype=numpy.float64), 0.0, 1.0)
    return tuple((round(float(xi), 8), round(float(yi), 6))
                 for xi, yi in zip(x, y))


def reference_points() -> list[dict[str, Any]]:
    """曲线参考标注点（图上标记 + API 返回）。

    PQ：信号 0.5 ↔ pq_eotf(0.5) nits（≈92.25）；HLG：信号 0.5 ↔ 1/12 场景
    线性（参考白）。input 归一化口径与 transfer_curve_points 的 x 一致。
    """
    pq_ref_luma = float(pq_eotf(0.5))
    return [
        {
            "transfer": "pq",
            "input": round(pq_ref_luma / PQ_MAX_NITS, 8),
            "signal": 0.5,
            "nits": round(pq_ref_luma, 4),
            "label": f"50% 信号 ↔ {pq_ref_luma:.2f} nits",
        },
        {
            "transfer": "hlg",
            "input": round(1.0 / 12.0, 8),
            "signal": 0.5,
            "nits": None,
            "label": "50% 信号 ↔ 1/12 场景线性（参考白）",
        },
    ]
