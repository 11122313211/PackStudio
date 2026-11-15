"""测量验证：实测 Lab vs 参考 Lab 的 ΔE00 统计与 ISO 12647-2 判定（TECH-DESIGN §4.9）。

参考 Lab 来源（二选一）：
    1. 内置参考表（``REFERENCE_TABLES``，v1 仅 Fogra39 九色，§6.1 数值）；
    2. 经 profile 变换 device 值推导——此路径调用 ``cmm.transform_lab``
       （惰性导入；cmm 由并行开发，未就绪前该路径在运行时才失败）。

依赖注入：``verify_cgats`` 提供 keyword-only ``lab_transform`` 参数，可注入
fake 变换函数（签名 ``(profile, device, intent, bpc) -> (n, 3) float64``，
device 为 CGATS 标度：CMYK 0-100 / RGB 0-255）以便单测；默认走惰性 cmm 路径。

8bit 量化注意（设计 R5）：PIL "LAB" 模式为 8bit，经 cmm 变换的 Lab 存在
±0.39 量化噪音——对 ISO 容差（≥3.0）影响可忽略，文档注明即可。

公开 API：
    ReferenceSet / PatchVerdict / DeltaEStats / VerifyResult / IsoVerdict /
    delta_e_00 / verify_cgats / verify_reference / reference_table /
    REFERENCE_TABLES / summarize / overall_verdict / verify_report_to_cgats
"""
from __future__ import annotations

import logging
import os  # noqa: F401（os.PathLike 注解引用，annotations 为惰性字符串）
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeAlias

import colour
import numpy as np

from packstudio.engines.color_engine import __version__
from packstudio.engines.color_engine.errors import MeasurementError
from packstudio.engines.color_engine.measurement.cgats import CgatsData

# 设计 R3 兜底：屏蔽 colour-science 在旧 scipy 组合下的弃用警告噪音。
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"colour")

logger = logging.getLogger("icct.measurement")

__all__ = [
    "ReferencePatch",
    "ReferenceSet",
    "PatchVerdict",
    "DeltaEStats",
    "IsoVerdict",
    "VerifyResult",
    "REFERENCE_TABLES",
    "ISO12647_2_TOLERANCES",
    "VERDICT_WARNING_MARGIN",
    "delta_e_00",
    "reference_table",
    "verify_cgats",
    "verify_reference",
    "summarize",
    "overall_verdict",
    "verify_report_to_cgats",
    "channel_diagnostics",
]

try:  # cmm 为并行开发模块；就绪前用同值 IntEnum 镜像兜底（数值契约见设计 §3.6）
    from packstudio.engines.color_engine.cmm.engine import RenderingIntent
except ImportError:  # pragma: no cover — cmm 未就绪期的运行兜底
    from enum import IntEnum

    class RenderingIntent(IntEnum):  # type: ignore[no-redef]
        """cmm.engine.RenderingIntent 的同值镜像（PERCEPTUAL=0 … ABSOLUTE_COLORIMETRIC=3）。"""

        PERCEPTUAL = 0
        RELATIVE_COLORIMETRIC = 1
        SATURATION = 2
        ABSOLUTE_COLORIMETRIC = 3


#: 可注入的 Lab 变换函数签名：
#: ``(profile, device, intent, bpc) -> (n, 3) float64``；
#: ``device`` 为 CGATS 标度（CMYK 0-100 / RGB 0-255），形状 (n, k)。
LabTransformFn: TypeAlias = Callable[..., np.ndarray]


# ---------------------------------------------------------------------------
# ISO 12647-2 / Fogra39 内置参考表（数值逐字取自 TECH-DESIGN §6.1，2026-11-07
# 经 WebSearch/WebFetch 核实；来源：
#   https://registry.color.org/cmyk-registry/fogra39
#   https://www.color.org/chardata/FOGRA39L.txt   （1617 补丁版，本表取自其子集）
#   https://www.color.org/chardata/FOGRA39.txt    （1485 补丁版）
# 测量条件：D50、2° 视场、45/0 几何、无偏振滤片、白背衬（ISO 13655）；
# 印刷条件：ISO 12647-2:2004/Amd 1，纸类 1/2（涂布纸 ≥115 g/m²），TVI 曲线 A(CMY)/B(K)。
# 注：二手资料（ISO 12647-2:2004 规范表值）常给出 R(MY)=48/69/48、G(CY)=50/-66/25，
# 与官方 characterization 数据有 ≤2 差异；本表采用官方 characterization 数值。
# ---------------------------------------------------------------------------

# 判定容差：非规范原文（二手文献近似，严格 ΔE*ab 判定【待复核】），可被 --tolerance 覆盖。
ISO12647_2_TOLERANCES: Final[dict[str, float]] = {
    "cmy": 5.0,  # C/M/Y 固体；CM/CY/MY/CMY 叠印色无单独规定，按 CMY 组处理
    "k": 4.0,  # K 固体
    "paper": 3.0,  # 纸白
}

#: ΔE00 超出容差但不超过 容差+1.0 判为 WARNING（工程警示带，非规范值，可调）。
VERDICT_WARNING_MARGIN: Final[float] = 1.0


@dataclass(frozen=True, slots=True)
class ReferencePatch:
    """参考表中的一行（一个固体色/叠印色/纸白目标）。

    Attributes:
        target: 目标名，如 "paper"、"C"、"M"、"Y"、"K"、"CM"、"CY"、"MY"、"CMY"。
        device: 对应 device 码（CMYK 0-100），如 C = (100, 0, 0, 0)。
        lab: 目标 CIELAB (L*, a*, b*)。
        tolerance: ΔE00 判定容差（源自 ISO12647_2_TOLERANCES）。
    """

    target: str
    device: tuple[float, ...]
    lab: tuple[float, float, float]
    tolerance: float

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约）。"""
        return {
            "target": self.target,
            "device": list(self.device),
            "lab": list(self.lab),
            "tolerance": float(self.tolerance),
        }


@dataclass(frozen=True, slots=True)
class ReferenceSet:
    """一套内置参考表（如 Fogra39）。

    Attributes:
        name: 规范名（查找的主键），如 "Fogra39"。
        description: 数据来源与测量条件说明（人读）。
        device_fields: device 列名（与每行 device 元组对齐），如
            ("CMYK_C","CMYK_M","CMYK_Y","CMYK_K")。
        patches: 全部目标行。
    """

    name: str
    description: str
    device_fields: tuple[str, ...]
    patches: tuple[ReferencePatch, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约）。"""
        return {
            "name": self.name,
            "description": self.description,
            "device_fields": list(self.device_fields),
            "patches": [p.as_json() for p in self.patches],
        }


REFERENCE_TABLES: Final[dict[str, ReferenceSet]] = {
    "Fogra39": ReferenceSet(
        name="Fogra39",
        description=(
            "ISO 12647-2:2004/Amd 1 纸类 1/2（涂布纸）characterization 数据（Fogra39L）；"
            "D50/2°/45:0/ISO 13655 无偏振、白背衬；数值来源 "
            "https://www.color.org/chardata/FOGRA39L.txt 与 "
            "https://registry.color.org/cmyk-registry/fogra39（TECH-DESIGN §6.1，2026-11-07 核实）"
        ),
        device_fields=("CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"),
        patches=(
            ReferencePatch("paper", (0.0, 0.0, 0.0, 0.0), (95.0, 0.0, -2.0), ISO12647_2_TOLERANCES["paper"]),
            ReferencePatch("C", (100.0, 0.0, 0.0, 0.0), (55.0, -37.0, -50.0), ISO12647_2_TOLERANCES["cmy"]),
            ReferencePatch("M", (0.0, 100.0, 0.0, 0.0), (48.0, 74.0, -3.0), ISO12647_2_TOLERANCES["cmy"]),
            ReferencePatch("Y", (0.0, 0.0, 100.0, 0.0), (89.0, -5.0, 93.0), ISO12647_2_TOLERANCES["cmy"]),
            ReferencePatch("K", (0.0, 0.0, 0.0, 100.0), (16.0, 0.0, 0.0), ISO12647_2_TOLERANCES["k"]),
            ReferencePatch("CM", (100.0, 100.0, 0.0, 0.0), (24.0, 22.0, -46.0), ISO12647_2_TOLERANCES["cmy"]),
            ReferencePatch("CY", (100.0, 0.0, 100.0, 0.0), (50.0, -65.0, 27.0), ISO12647_2_TOLERANCES["cmy"]),
            ReferencePatch("MY", (0.0, 100.0, 100.0, 0.0), (47.0, 68.0, 48.0), ISO12647_2_TOLERANCES["cmy"]),
            ReferencePatch("CMY", (100.0, 100.0, 100.0, 0.0), (23.0, 0.0, 0.0), ISO12647_2_TOLERANCES["cmy"]),
        ),
    ),
}

_ALIASES: Final[dict[str, str]] = {"fogra39": "Fogra39", "fogra39l": "Fogra39"}

_INTENT_LABELS: Final[dict[int, str]] = {
    0: "perceptual",
    1: "relative_colorimetric",
    2: "saturation",
    3: "absolute_colorimetric",
}


# ---------------------------------------------------------------------------
# 数据模型（TECH-DESIGN §3.4）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchVerdict:
    """单个 patch 的实测 vs 参考判定行。

    Attributes:
        patch_id: SAMPLE_ID（或参考表 target 名）。
        measured_lab: 实测 CIELAB。
        reference_lab: 参考 CIELAB（profile 推导或内置表值）。
        delta_e_76: CIE76 色差。
        delta_e_00: CIEDE2000 色差。
        device: 采样自测量文件的 device 值（CGATS 标度），无 device 列时 None。
    """

    patch_id: str
    measured_lab: tuple[float, float, float]
    reference_lab: tuple[float, float, float]
    delta_e_76: float
    delta_e_00: float
    device: tuple[float, ...] | None

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约）。"""
        return {
            "patch_id": self.patch_id,
            "measured_lab": list(self.measured_lab),
            "reference_lab": list(self.reference_lab),
            "delta_e_76": float(self.delta_e_76),
            "delta_e_00": float(self.delta_e_00),
            "device": list(self.device) if self.device is not None else None,
        }


@dataclass(frozen=True, slots=True)
class DeltaEStats:
    """ΔE00 统计汇总。

    Attributes:
        count: 参与判定的 patch 数。
        mean: ΔE00 均值。
        max: ΔE00 最大值。
        stdev: ΔE00 总体标准差（ddof=0）。
        percentiles: {"p50","p90","p95","p99"}（线性插值百分位）。
        worst: ΔE00 降序前 5 的 PatchVerdict。
    """

    count: int
    mean: float
    max: float
    stdev: float
    percentiles: dict[str, float]
    worst: tuple[PatchVerdict, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约）。"""
        return {
            "count": int(self.count),
            "mean": float(self.mean),
            "max": float(self.max),
            "stdev": float(self.stdev),
            "percentiles": {k: float(v) for k, v in self.percentiles.items()},
            "worst": [v.as_json() for v in self.worst],
        }


@dataclass(frozen=True, slots=True)
class IsoVerdict:
    """单个固体色/纸白的 ISO 12647-2 判定行。

    Attributes:
        patch_id: SAMPLE_ID。
        target: 参考表目标名，如 "C"、"K"、"paper"。
        measured_lab: 实测 CIELAB。
        reference_lab: 内置参考表 CIELAB。
        delta_e_00: CIEDE2000 色差。
        tolerance: 本目标生效的 ΔE00 容差。
        passed: delta_e_00 <= tolerance。
    """

    patch_id: str
    target: str
    measured_lab: tuple[float, float, float]
    reference_lab: tuple[float, float, float]
    delta_e_00: float
    tolerance: float
    passed: bool

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约）。"""
        return {
            "patch_id": self.patch_id,
            "target": self.target,
            "measured_lab": list(self.measured_lab),
            "reference_lab": list(self.reference_lab),
            "delta_e_00": float(self.delta_e_00),
            "tolerance": float(self.tolerance),
            "passed": bool(self.passed),
        }


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """verify 流程的完整结果（TECH-DESIGN §3.4）。

    Attributes:
        profile_path: profile 路径；纯参考表路径为 ""。
        profile_description: profile 描述（cmm 获取；不可用时 ""）。
        intent: 意图标签（"perceptual"/"relative_colorimetric"/"saturation"/
            "absolute_colorimetric"）；verify_reference 为 ""。
        bpc: 是否启用黑点补偿。
        reference_name: 内置参考表名（如 "Fogra39"）；profile 推导参考时 None。
        patches: 逐 patch 明细。
        stats: ΔE00 统计。
        iso: ISO 判定行（使用内置参考表时非 None；否则 None）。
        passed: 全部 iso.passed 且 stats.max <= tolerance。
        tolerance: 生效的整体达标线（--tolerance 覆盖值或参考表最大容差；
            无标准时 None）。v1 新增字段（additive，JSON 契约允许新增）。
    """

    profile_path: str
    profile_description: str
    intent: str
    bpc: bool
    reference_name: str | None
    patches: tuple[PatchVerdict, ...]
    stats: DeltaEStats
    iso: tuple[IsoVerdict, ...] | None
    passed: bool
    tolerance: float | None = None

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约；§5.7 --json data 即本输出）。

        v0.3.7 起附加 ``channel_diagnostics`` 键（additive，向后兼容）：
        CMYK device 数据可用时为通道诊断 dict，否则 None。
        """
        return {
            "profile_path": self.profile_path,
            "profile_description": self.profile_description,
            "intent": self.intent,
            "bpc": bool(self.bpc),
            "reference_name": self.reference_name,
            "patches": [v.as_json() for v in self.patches],
            "stats": self.stats.as_json(),
            "iso": [v.as_json() for v in self.iso] if self.iso is not None else None,
            "passed": bool(self.passed),
            "tolerance": float(self.tolerance) if self.tolerance is not None else None,
            "channel_diagnostics": channel_diagnostics(self),
        }


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------


def _measurement_error(message: str, code: str, detail: dict[str, Any]) -> MeasurementError:
    """构造带稳定机器码的 MeasurementError（code 属于错误响应契约）。"""
    err = MeasurementError(message, detail=detail)
    err.code = code
    return err


def _norm_name(name: str) -> str:
    """字段/目标名归一化：去空白折叠为下划线并大写（与 cgats 一致）。"""
    return "_".join(name.upper().split())


def _as_lab_matrix(lab: np.ndarray, arg_name: str = "lab") -> np.ndarray:
    """校验并归一化 Lab 输入为 (n, 3) float64；一维长度 3 视为单行。"""
    arr = np.asarray(lab, dtype=np.float64)
    if arr.ndim == 1 and arr.shape[0] == 3:
        arr = arr.reshape(1, 3)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise _measurement_error(
            f"{arg_name} 必须是形状 (n, 3) 的数组", "BAD_LAB_SHAPE", {"shape": list(arr.shape)}
        )
    return arr


def _lab_tuple(row: np.ndarray) -> tuple[float, float, float]:
    return (float(row[0]), float(row[1]), float(row[2]))


def _delta_e_76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIE76 色差：sqrt(ΔL²+Δa²+Δb²)，输入 (n, 3) → (n,)。"""
    return np.sqrt(np.sum((lab1 - lab2) ** 2, axis=-1))


def _intent_label(intent: object) -> str:
    """把 RenderingIntent（或其整数值）转为稳定字符串标签。"""
    try:
        value = int(intent)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(intent)
    return _INTENT_LABELS.get(value, str(value))


def _find_lab_cols(data: CgatsData) -> tuple[int, int, int] | None:
    """在 values 列中定位 LAB_L/LAB_A/LAB_B；缺任一列返回 None。"""
    idx = (data.column_index("LAB_L"), data.column_index("LAB_A"), data.column_index("LAB_B"))
    if any(i is None for i in idx):
        return None
    return (int(idx[0]), int(idx[1]), int(idx[2]))


def _find_device_cols(data: CgatsData) -> tuple[str, tuple[int, ...]] | None:
    """识别 device 列组：优先 CMYK_C/M/Y/K，其次 RGB_R/G/B；都不齐返回 None。"""
    cmyk = tuple(data.column_index(f"CMYK_{c}") for c in "CMYK")
    if all(i is not None for i in cmyk):
        return "CMYK", tuple(int(i) for i in cmyk)
    rgb = tuple(data.column_index(f"RGB_{c}") for c in "RGB")
    if all(i is not None for i in rgb):
        return "RGB", tuple(int(i) for i in rgb)
    return None


def _match_device_to_table(device_row: np.ndarray, table: ReferenceSet) -> ReferencePatch | None:
    """把一行 device 值与参考表 device 码精确对齐（atol=1e-3）；未命中返回 None。"""
    for patch in table.patches:
        ref = np.asarray(patch.device, dtype=np.float64)
        if ref.shape == device_row.shape and bool(np.allclose(ref, device_row, atol=1e-3)):
            return patch
    return None


def _align_table(measured: CgatsData, table: ReferenceSet) -> tuple[list[int], list[ReferencePatch]]:
    """把测量行与参考表 patch 对齐。

    优先按 SAMPLE_ID 与 target 名对齐（大小写/空白不敏感；允许测量文件含
    参考表之外的额外 patch，未命中的行排除并记 INFO）；名称不可用时若行数
    与参考表一致则按行序一一对应。

    返回:
        (行索引列表, 对齐的参考 patch 列表)，两者一一对应。

    异常:
        MeasurementError: code="REFERENCE_MISMATCH"（两种对齐均失败）。
    """
    n = measured.values.shape[0]
    targets = {_norm_name(p.target) for p in table.patches}
    ids_norm = [_norm_name(pid) for pid in measured.patch_ids]

    if targets <= set(ids_norm):
        first_at: dict[str, int] = {}
        for i, name in enumerate(ids_norm):
            if name in targets and name not in first_at:
                first_at[name] = i
        rows: list[int] = []
        patches: list[ReferencePatch] = []
        for patch in table.patches:
            rows.append(first_at[_norm_name(patch.target)])
            patches.append(patch)
        extra = n - len(rows)
        if extra > 0:
            logger.info("CGATS 中 %d 个 patch 不在参考表内，已排除出判定", extra)
        return rows, patches

    if n == len(table.patches):
        return list(range(n)), list(table.patches)

    raise _measurement_error(
        "测量数据无法与参考表对齐：SAMPLE_ID 与目标名不匹配且行数不等",
        "REFERENCE_MISMATCH",
        {"rows": int(n), "reference": table.name, "reference_rows": len(table.patches)},
    )


def _build_stats(verdicts: list[PatchVerdict]) -> DeltaEStats:
    """由逐 patch 判定构建 ΔE00 统计（要求 verdicts 非空）。"""
    de00 = np.asarray([v.delta_e_00 for v in verdicts], dtype=np.float64)
    percents = np.percentile(de00, [50, 90, 95, 99])
    percentiles = {f"p{p}": float(v) for p, v in zip((50, 90, 95, 99), percents)}
    worst = tuple(sorted(verdicts, key=lambda v: v.delta_e_00, reverse=True)[:5])
    return DeltaEStats(
        count=len(verdicts),
        mean=float(np.mean(de00)),
        max=float(np.max(de00)),
        stdev=float(np.std(de00)),
        percentiles=percentiles,
        worst=worst,
    )


@dataclass(frozen=True, slots=True)
class _Row:
    """verify 内部的逐行中间表示（对齐后）。"""

    patch_id: str
    measured_lab: tuple[float, float, float]
    reference_lab: tuple[float, float, float]
    device: tuple[float, ...] | None
    ref_patch: ReferencePatch | None = None


def _build_result(
    rows: list[_Row],
    *,
    profile_path: str,
    profile_description: str,
    intent: str,
    bpc: bool,
    table: ReferenceSet | None,
    tolerance: float | None,
) -> VerifyResult:
    """由对齐行计算 ΔE76/ΔE00、统计、ISO 判定并组装 VerifyResult。"""
    if not rows:
        raise _measurement_error("没有任何可判定的 patch 行", "NO_PATCHES", {})
    measured = np.asarray([r.measured_lab for r in rows], dtype=np.float64)
    reference = np.asarray([r.reference_lab for r in rows], dtype=np.float64)
    de00 = delta_e_00(measured, reference)
    de76 = _delta_e_76(measured, reference)

    verdicts = [
        PatchVerdict(
            row.patch_id,
            row.measured_lab,
            row.reference_lab,
            float(de76[i]),
            float(de00[i]),
            row.device,
        )
        for i, row in enumerate(rows)
    ]
    stats = _build_stats(verdicts)

    iso_verdicts: list[IsoVerdict] = []
    for i, row in enumerate(rows):
        patch = row.ref_patch
        if patch is None:
            continue
        tol = float(tolerance) if tolerance is not None else patch.tolerance
        iso_verdicts.append(
            IsoVerdict(
                patch_id=row.patch_id,
                target=patch.target,
                measured_lab=row.measured_lab,
                reference_lab=patch.lab,
                delta_e_00=float(de00[i]),
                tolerance=tol,
                passed=bool(float(de00[i]) <= tol),
            )
        )
    iso: tuple[IsoVerdict, ...] | None = tuple(iso_verdicts) if table is not None else None

    if tolerance is not None:
        tol_max: float | None = float(tolerance)
    elif iso is not None:
        # 空命中时退回参考表最大容差；table 在 iso 非 None 时必有值
        tol_max = max(v.tolerance for v in iso) if iso else max(p.tolerance for p in table.patches)  # type: ignore[union-attr]
    else:
        tol_max = None

    iso_ok = all(v.passed for v in iso) if iso is not None else True
    max_ok = stats.max <= tol_max if tol_max is not None else True
    passed = bool(iso_ok and max_ok)

    return VerifyResult(
        profile_path=profile_path,
        profile_description=profile_description,
        intent=intent,
        bpc=bpc,
        reference_name=table.name if table is not None else None,
        patches=tuple(verdicts),
        stats=stats,
        iso=iso,
        passed=passed,
        tolerance=tol_max,
    )


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def delta_e_00(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 色差（包装 colour.delta_E，method="CIE 2000"）。

    行为：
        输入自动转 float64；L 通道 clip 到 >=0（设计约定）。形状 (n, 3) 对
        (n, 3) 计算，返回 (n,) float64；一维长度 3 视为单行（返回长度 1）。

    参数:
        lab1: (n, 3) 或 (3,) CIELAB。
        lab2: 与 lab1 同形状。

    返回:
        (n,) float64 ΔE00。

    异常:
        MeasurementError: 形状非法或不一致（code="BAD_LAB_SHAPE"）。
    """
    a = _as_lab_matrix(lab1, "lab1").copy()
    b = _as_lab_matrix(lab2, "lab2").copy()
    if a.shape != b.shape:
        raise _measurement_error(
            "lab1 与 lab2 形状不一致", "BAD_LAB_SHAPE", {"lab1": list(a.shape), "lab2": list(b.shape)}
        )
    a[:, 0] = np.clip(a[:, 0], 0.0, None)
    b[:, 0] = np.clip(b[:, 0], 0.0, None)
    return np.asarray(colour.delta_E(a, b, method="CIE 2000"), dtype=np.float64)


def reference_table(name: str) -> ReferenceSet:
    """按名称取内置参考表（大小写不敏感；"fogra39"/"Fogra39L" 均指向 Fogra39）。

    参数:
        name: 参考表名。

    返回:
        ReferenceSet

    异常:
        MeasurementError: 未知参考表（code="NO_REFERENCE"）。
    """
    key = name.strip().casefold()
    canonical = _ALIASES.get(key)
    if canonical is None:
        for table_name in REFERENCE_TABLES:
            if table_name.casefold() == key:
                canonical = table_name
                break
    if canonical is None:
        raise _measurement_error(
            f"未知的内置参考表: {name!r}（可用: {', '.join(REFERENCE_TABLES)}）",
            "NO_REFERENCE",
            {"name": name},
        )
    return REFERENCE_TABLES[canonical]


def _device_to_pil_scale(device: np.ndarray) -> np.ndarray:
    """CGATS 标度 → PIL 图像标度。

    CGATS 惯例 CMYK 列为 0-100 百分比，而 PIL "CMYK" 模式通道为 0-255；
    RGB 列在 CGATS 中通常已是 0-255，原样传递。
    """
    if device.shape[1] == 4:
        return np.rint(device * 255.0 / 100.0)
    return device


def _default_lab_transform(
    profile: bytes | str | os.PathLike[str],
    device: np.ndarray,
    intent: RenderingIntent,
    bpc: bool,
) -> np.ndarray:
    """默认参考 Lab 推导：经 cmm.transform_lab 把 device 值变换为 Lab。

    惰性导入 cmm（并行开发模块）；device 先按 CGATS 惯例换算到 PIL 标度。
    测试可注入 fake（verify_cgats 的 lab_transform 参数）或 monkeypatch 本函数。
    """
    from packstudio.engines.color_engine.cmm.engine import transform_lab  # 惰性导入：cmm 并行开发中

    return np.asarray(transform_lab(profile, _device_to_pil_scale(device), intent, bpc), dtype=np.float64)


def _profile_description(profile: bytes | str | os.PathLike[str]) -> str:
    """经 cmm 取 profile 描述；cmm 未就绪或加载失败时降级为 ""（非关键路径）。"""
    try:
        from packstudio.engines.color_engine.cmm.engine import open_profile, profile_names  # 惰性导入

        return str(profile_names(open_profile(profile))[1])
    except Exception as exc:  # noqa: BLE001 — 描述获取失败不应阻断验证主流程
        logger.debug("profile 描述获取失败（cmm 未就绪或加载失败）: %r", exc)
        return ""


def verify_cgats(
    measured: CgatsData,
    profile: bytes | str | os.PathLike[str] | None = None,
    *,
    intent: RenderingIntent = RenderingIntent.RELATIVE_COLORIMETRIC,
    bpc: bool = True,
    reference: str | None = "Fogra39",
    lab_transform: LabTransformFn | None = None,
    tolerance: float | None = None,
) -> VerifyResult:
    """对一份 CGATS 测量数据做 ΔE00 统计与 ISO 12647-2 判定。

    行为：
        定位测量文件的 LAB_L/A/B 列（必需）与 device 列（自动识别
        CMYK_C/M/Y/K 或 RGB_R/G/B）。参考 Lab 来源：
        - 有 device 列且给了 profile → 经 ``lab_transform``（默认
          cmm.transform_lab 惰性路径，device 先换算到 PIL 标度：CMYK 0-100
          → 0-255）逐 patch 推导参考 Lab；
        - 无 device 列（纯 Lab 文件）或无 profile → 直接用内置参考表值
          （先按 SAMPLE_ID 与 target 名对齐，失败且行数一致时按行序对齐）。
        ``reference`` 给出时另用内置表生成 ISO 判定（profile 路径下按
        device 码精确对齐，如 C=100/0/0/0；容差取 ISO12647_2_TOLERANCES
        或被 ``tolerance`` 统一覆盖）。
        经 cmm 的 Lab 存在 8bit 量化（±0.39，设计 R5），对 ISO 容差影响可忽略。

    参数:
        measured: 解析后的测量数据。
        profile: 用于推导参考 Lab 的 profile（bytes/路径）；纯参考表路径可
            为 None（设计签名扩展：CLI `verify --reference` 时无需 profile）。
        intent: 变换意图（默认 relative colorimetric）。
        bpc: 黑点补偿（默认开）。
        reference: 内置参考表名；None 表示纯 profile 推导参考。
        lab_transform: 注入的 Lab 变换函数 ``(profile, device, intent, bpc)
            -> (n, 3)``，device 为 CGATS 标度；None 用默认 cmm 路径。
        tolerance: 统一覆盖 ISO 判定容差 / 作为无参考表时的整体达标线。

    返回:
        VerifyResult

    异常:
        MeasurementError: 缺 Lab 列（code="NO_LAB"）、缺参考来源
            （code="NO_REFERENCE"）、对齐失败（code="REFERENCE_MISMATCH"）、
            注入变换返回形状非法（code="TRANSFORM_BAD_SHAPE"）。
        CgatsParseError: 由 cgats 层传播。
        ProfileLoadError: profile 无法加载（经 cmm 路径传播）。
    """
    lab_idx = _find_lab_cols(measured)
    if lab_idx is None:
        raise _measurement_error(
            "测量数据缺少 LAB_L/LAB_A/LAB_B 列", "NO_LAB", {"fields": list(measured.fields)}
        )
    device = _find_device_cols(measured)
    table = reference_table(reference) if reference is not None else None

    n = measured.values.shape[0]
    measured_lab = measured.values[:, list(lab_idx)]
    device_values = measured.values[:, list(device[1])] if device is not None else None

    rows: list[_Row] = []
    if device_values is not None and profile is not None:
        transform = lab_transform if lab_transform is not None else _default_lab_transform
        reference_lab = np.asarray(transform(profile, device_values, intent, bpc), dtype=np.float64)
        if reference_lab.shape != (n, 3):
            raise _measurement_error(
                "Lab 变换返回形状必须是 (n, 3)", "TRANSFORM_BAD_SHAPE", {"shape": list(reference_lab.shape)}
            )
        if not np.isfinite(reference_lab).all():
            raise _measurement_error("Lab 变换返回含非有限值", "TRANSFORM_BAD_VALUE", {})
        match_iso = table is not None and device_values.shape[1] == len(table.device_fields)
        for i in range(n):
            patch = _match_device_to_table(device_values[i], table) if match_iso else None  # type: ignore[arg-type]
            rows.append(
                _Row(
                    patch_id=measured.patch_ids[i],
                    measured_lab=_lab_tuple(measured_lab[i]),
                    reference_lab=_lab_tuple(reference_lab[i]),
                    device=tuple(float(v) for v in device_values[i]),
                    ref_patch=patch,
                )
            )
        if match_iso:
            hits = sum(1 for r in rows if r.ref_patch is not None)
            logger.debug("ISO device 码对齐: %d/%d patch 命中参考表", hits, n)
    elif table is not None:
        row_idx, patches = _align_table(measured, table)
        for i, patch in zip(row_idx, patches):
            rows.append(
                _Row(
                    patch_id=measured.patch_ids[i],
                    measured_lab=_lab_tuple(measured_lab[i]),
                    reference_lab=patch.lab,
                    device=(tuple(float(v) for v in device_values[i])
                            if device_values is not None else None),
                    ref_patch=patch,
                )
            )
    else:
        raise _measurement_error(
            "缺少参考来源：需提供 profile（配 device 列）或内置 reference（如 Fogra39）",
            "NO_REFERENCE",
            {"has_profile": profile is not None, "reference": reference},
        )

    result = _build_result(
        rows,
        profile_path="" if profile is None else str(profile),
        profile_description=_profile_description(profile) if profile is not None else "",
        intent=_intent_label(intent),
        bpc=bpc,
        table=table,
        tolerance=tolerance,
    )
    logger.debug(
        "verify 完成: patches=%d mean=%.3f max=%.3f passed=%s",
        result.stats.count, result.stats.mean, result.stats.max, result.passed,
    )
    return result


def verify_reference(lab: np.ndarray, reference: str = "Fogra39") -> VerifyResult:
    """纯 Lab 输入对内置参考表的快捷验证（无需 profile）。

    行为：
        lab 按行序与参考表 patch 一一对应（行数必须等于参考表 patch 数）；
        patch_id 取参考表 target 名；ISO 判定覆盖全部行。

    参数:
        lab: (n, 3) float64 CIELAB（n 必须等于参考表行数）。
        reference: 内置参考表名。

    返回:
        VerifyResult（profile_path/profile_description 为 ""，intent 为 ""）

    异常:
        MeasurementError: 行数不符（code="REFERENCE_MISMATCH"）、形状非法
            （code="BAD_LAB_SHAPE"）、未知参考表（code="NO_REFERENCE"）。
    """
    table = reference_table(reference)
    arr = _as_lab_matrix(lab)
    if arr.shape[0] != len(table.patches):
        raise _measurement_error(
            "Lab 行数与参考表 patch 数不一致",
            "REFERENCE_MISMATCH",
            {"rows": int(arr.shape[0]), "reference_rows": len(table.patches)},
        )
    rows = [
        _Row(
            patch_id=patch.target,
            measured_lab=_lab_tuple(arr[i]),
            reference_lab=patch.lab,
            device=patch.device,
            ref_patch=patch,
        )
        for i, patch in enumerate(table.patches)
    ]
    return _build_result(
        rows,
        profile_path="",
        profile_description="",
        intent="",
        bpc=False,
        table=table,
        tolerance=None,
    )


def overall_verdict(result: VerifyResult) -> Literal["PASS", "WARNING", "FAIL"]:
    """三级总体判定（供 CLI/WebUI 展示；非设计契约字段，展示层语义）。

    规则：
        PASS   —— result.passed 为 True；
        WARNING —— 未通过，但所有超差项的超出量 ≤ VERDICT_WARNING_MARGIN
                   （默认 1.0 ΔE00，工程警示带，非规范值）；
        FAIL   —— 其余情形。
    """
    if result.passed:
        return "PASS"
    excesses: list[float] = []
    if result.iso:
        excesses = [v.delta_e_00 - v.tolerance for v in result.iso if not v.passed]
    if result.tolerance is not None and result.stats.max > result.tolerance:
        excesses.append(result.stats.max - result.tolerance)
    if excesses and max(excesses) <= VERDICT_WARNING_MARGIN:
        return "WARNING"
    return "FAIL"


def summarize(result: VerifyResult) -> dict[str, Any]:
    """JSON 摘要：stats + iso 表 + worst 5（供 CLI/报告落盘）。"""
    return {
        "stats": result.stats.as_json(),
        "iso": [v.as_json() for v in result.iso] if result.iso is not None else None,
        "worst": [v.as_json() for v in result.stats.worst],
    }


# ---------------------------------------------------------------------------
# 验证报告 → CGATS.17 回写（ROADMAP v0.3.2）
# ---------------------------------------------------------------------------


def verify_report_to_cgats(report: dict[str, Any]) -> CgatsData:
    """把 VerifyResult.as_json() 报告 dict 回写为 CgatsData（ROADMAP v0.3.2）。

    行为：
        从报告 dict 抽取逐 patch 明细，构造 ``SAMPLE_ID + [设备列] + 实测
        LAB_L/A/B + REF_L/A/B + DELTA_E_76/DELTA_E_00`` 列结构的 CgatsData；
        配合 :func:`packstudio.engines.color_engine.measurement.write_cgats` 导出即可得到可重新
        解析的 CGATS.17 验证报告（LAB 列为实测值，重新上传 verify 仍可判定）。
        设备列名按首个非空 device 的元数命名：4 → CMYK_C/M/Y/K、3 →
        RGB_R/G/B、其余 → DEV_0..DEV_{n-1}；device 全空则无设备列。
        meta 记录 ORIGINATOR（icct 版本）、DESCRIPTOR 与 REFERENCE
        （参考表名优先，其次 profile 路径）。

    参数:
        report: VerifyResult.as_json() 的输出 dict。

    返回:
        CgatsData

    异常:
        MeasurementError: code="BAD_REPORT"——报告非 dict / patches 缺失或
            为空 / patch 项非 dict / patch_id 非空字符串 / Lab 与数值字段
            缺失或类型非法 / device 元数不一致 / 数值非有限。
    """
    if not isinstance(report, dict):
        raise _measurement_error(
            "报告必须是 dict（VerifyResult.as_json() 的输出）",
            "BAD_REPORT", {"got": type(report).__name__},
        )
    patches = report.get("patches")
    if not isinstance(patches, list) or not patches:
        raise _measurement_error(
            "报告缺少非空 patches 明细", "BAD_REPORT",
            {"key": "patches", "got": type(patches).__name__},
        )

    meta: dict[str, str] = {
        "ORIGINATOR": f"icc-toolkit {__version__}",
        "DESCRIPTOR": "ICC Toolkit verification report (measured vs reference)",
    }
    ref_name = report.get("reference_name")
    if isinstance(ref_name, str) and ref_name:
        meta["REFERENCE"] = ref_name
    else:
        profile_path = report.get("profile_path")
        if isinstance(profile_path, str) and profile_path:
            meta["REFERENCE"] = profile_path

    def _num(value: Any, pid: str, field: str) -> float:
        try:
            out = float(value)
        except (TypeError, ValueError):
            raise _measurement_error(
                f"patch {pid!r} 的 {field} 不是数值", "BAD_REPORT",
                {"patch_id": pid, "field": field, "got": type(value).__name__},
            ) from None
        if not bool(np.isfinite(out)):
            raise _measurement_error(
                f"patch {pid!r} 的 {field} 非有限值", "BAD_REPORT",
                {"patch_id": pid, "field": field},
            )
        return out

    def _lab3(value: Any, pid: str, prefix: str) -> tuple[float, float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise _measurement_error(
                f"patch {pid!r} 的 {prefix} 必须是长度 3 的序列", "BAD_REPORT",
                {"patch_id": pid, "field": prefix, "got": type(value).__name__},
            )
        return (_num(value[0], pid, f"{prefix}[0]"),
                _num(value[1], pid, f"{prefix}[1]"),
                _num(value[2], pid, f"{prefix}[2]"))

    rows: list[list[float]] = []
    ids: list[str] = []
    device_n: int | None = None

    for idx, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise _measurement_error(
                "patches 含非对象项", "BAD_REPORT", {"index": idx, "got": type(patch).__name__},
            )
        pid = patch.get("patch_id")
        if not isinstance(pid, str) or not pid:
            raise _measurement_error(
                "patch_id 必须是非空字符串", "BAD_REPORT",
                {"index": idx, "got": type(pid).__name__},
            )
        measured = _lab3(patch.get("measured_lab"), pid, "measured_lab")
        reference = _lab3(patch.get("reference_lab"), pid, "reference_lab")
        de76 = _num(patch.get("delta_e_76"), pid, "delta_e_76")
        de00 = _num(patch.get("delta_e_00"), pid, "delta_e_00")
        device_raw = patch.get("device")
        if device_raw is None:
            device: tuple[float, ...] = ()
        else:
            if not isinstance(device_raw, (list, tuple)):
                raise _measurement_error(
                    f"patch {pid!r} 的 device 必须是序列或 null", "BAD_REPORT",
                    {"patch_id": pid, "got": type(device_raw).__name__},
                )
            device = tuple(_num(v, pid, f"device[{i}]") for i, v in enumerate(device_raw))
            if device_n is None:
                device_n = len(device)
            elif len(device) != device_n:
                raise _measurement_error(
                    f"patch {pid!r} 的 device 元数与其它行不一致", "BAD_REPORT",
                    {"patch_id": pid, "device_n": len(device), "expected": device_n},
                )
        rows.append([*device, *measured, *reference, de76, de00])
        ids.append(pid)

    if device_n is None:
        device_fields: list[str] = []
    elif device_n == 4:
        device_fields = [f"CMYK_{c}" for c in "CMYK"]
    elif device_n == 3:
        device_fields = [f"RGB_{c}" for c in "RGB"]
    else:
        device_fields = [f"DEV_{i}" for i in range(device_n)]

    fields = ["SAMPLE_ID", *device_fields,
              "LAB_L", "LAB_A", "LAB_B", "REF_L", "REF_A", "REF_B",
              "DELTA_E_76", "DELTA_E_00"]
    values = np.asarray(rows, dtype=np.float64).reshape(len(rows), len(fields) - 1)
    return CgatsData(
        meta=meta,
        fields=tuple(fields),
        patch_ids=tuple(ids),
        values=values,
        sample_names=None,
    )


# ---------------------------------------------------------------------------
# 通道级诊断（ROADMAP v0.3.7）
# ---------------------------------------------------------------------------

_CHANNEL_NAMES = ("C", "M", "Y", "K")


def channel_diagnostics(result: VerifyResult) -> dict[str, Any] | None:
    """CMYK 通道级诊断（ROADMAP v0.3.7）：通道覆盖与 ΔE00 相关性提示。

    行为：
        需要 patch 携带 4 通道 device 值（CGATS 0-100 标度）；不满足则返回
        None。对每个通道统计：
        - coverage_pct：全 patches 中该通道最大 device 值 / 100（覆盖上限）；
        - active_count：该通道值 > 50 的 patch 数（通道「激活」的判定线）；
        - mean_de：激活 patch 的 ΔE00 均值（无激活 patch 则 None）；
        - solid_de：纯通道固体色（该通道 100、其余 0）的 ΔE00（无则 None）。
        hints：激活 patch 的平均 ΔE00 显著最高的通道给排查建议（判定线：
        该通道均值 ≥ 2.0 且比次高通道高 ≥ 0.5）；全部均衡时给均衡提示；
        无 device 数据时整体返回 None（UI 据此隐藏面板）。

    参数:
        result: verify 结果（patches 需带 device 值）。

    返回:
        {"channels": [{channel, coverage_pct, active_count, mean_de, solid_de}],
         "hints": [str]}；无 CMYK device 数据时 None。

    异常:
        无（数据不满足条件返回 None，不抛错）。
    """
    patches = result.patches
    if not patches:
        return None
    devices = [p.device for p in patches]
    if any(d is None for d in devices):
        return None
    if any(len(d) != 4 for d in devices):
        return None  # 仅支持 CMYK 4 通道；RGB 设备暂不诊断
    de00s = [float(p.delta_e_00) for p in patches]

    channels: list[dict[str, Any]] = []
    for i, name in enumerate(_CHANNEL_NAMES):
        vals = [float(d[i]) for d in devices]
        active_idx = [j for j, v in enumerate(vals) if v > 50.0]
        active_de = [de00s[j] for j in active_idx]
        solid = next(
            (de00s[j] for j, d in enumerate(devices)
             if abs(d[i] - 100.0) <= 1e-3
             and all(abs(d[k]) <= 1e-3 for k in range(4) if k != i)),
            None,
        )
        channels.append({
            "channel": name,
            "coverage_pct": max(vals) / 100.0,
            "active_count": len(active_idx),
            "mean_de": (sum(active_de) / len(active_de)) if active_de else None,
            "solid_de": solid,
        })

    hints: list[str] = []
    scored = [c for c in channels if c["mean_de"] is not None]
    if scored:
        ranked = sorted(scored, key=lambda c: c["mean_de"], reverse=True)
        top = ranked[0]
        second = ranked[1]["mean_de"] if len(ranked) > 1 else 0.0
        if top["mean_de"] >= 2.0 and top["mean_de"] - second >= 0.5:
            hints.append(
                f"{top['channel']} 通道参与的平均 ΔE00 显著最高"
                f"（{top['mean_de']:.2f}，次高 {second:.2f}）——优先检查该通道"
                f"（墨键/版材/墨量）"
            )
        elif all(c["mean_de"] < 2.0 for c in scored):
            hints.append("各通道平均 ΔE00 均低于 2.0，通道表现均衡")
        else:
            hints.append("各通道平均 ΔE00 接近，无明显异常通道")
    return {"channels": channels, "hints": hints}
