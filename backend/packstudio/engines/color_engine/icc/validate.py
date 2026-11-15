"""ICC profile 结构校验与 HDR 能力检测。

TECH-DESIGN §3.3 / §4.4 的实现：

- 19 条结构校验规则（``validate_bytes`` 依序执行，code 稳定）；
- 按 device_class 与版本族的必需 tag 表（简化自 ICC.1:2022，normative 表以
  规范原文为准）；
- ``hdr_capability``：CICP/ADGC/HAGC 的 HDR 能力检测（防御式解析，见 §8 R6）。

约定：校验失败**不抛异常**，返回 :class:`ValidationReport`（数据）；
只有"根本无法当 profile 读"才由 header/tags 层抛 ``IccParseError``。

零第三方依赖（仅标准库）。
"""
from __future__ import annotations

import enum
import logging
import pathlib
import struct
from dataclasses import dataclass
from typing import Any, Final

from packstudio.engines.color_engine.errors import IccToolkitError
from packstudio.engines.color_engine.icc.header import (
    MAGIC,
    ProfileHeader,
    ProfileVersion,
    detect_version_family,
)
from packstudio.engines.color_engine.icc.tags import TAG_TYPE_MIN_SIZES, RawTag, TagTableEntry, decode_tag

__all__ = [
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "HdrCapability",
    "validate_profile",
    "validate_bytes",
    "hdr_capability",
]

logger = logging.getLogger("icct.icc.validate")

#: 标准 D50 参考白点（s15Fixed16 换算后近似值）。
_D50_XYZ: Final[tuple[float, float, float]] = (0.9642, 1.0, 0.8249)

#: 光源色差容差（TECH-DESIGN §4.4 规则 8）。
_ILLUMINANT_TOLERANCE: Final[float] = 0.002

#: 已知可共享同一存储区间的 tag 签名（v2 profile 常见三 TRC 同一 curv 等，
#: TECH-DESIGN §4.4 规则 14 允许清单）。
_SHARED_TAG_SIGS: Final[frozenset[str]] = frozenset({"rTRC", "gTRC", "bTRC", "kTRC", "chad"})

_TAG_ENTRY_SIZE: Final[int] = 12
_TAG_COUNT_OFFSET: Final[int] = 128
_TAG_TABLE_OFFSET: Final[int] = 132


class Severity(enum.IntEnum):
    """校验问题严重级别（数值即排序权重，ERROR 最重）。"""

    INFO = 10
    WARNING = 20
    ERROR = 30


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """单条校验问题。

    Attributes:
        severity: 严重级别。
        code:     稳定机读码（如 ``"HDR_MAGIC"``、``"TAG_OUT_OF_RANGE"``）。
        message:  人读信息（中文为主，可含英文术语）。
        location: 位置提示，如 ``"header:36"`` / ``"tag[A2B0]"`` / ``"header:reserved"``。
    """

    severity: Severity
    code: str
    message: str
    location: str | None = None

    def as_json(self) -> dict[str, Any]:
        """JSON 契约；severity 序列化为名称字符串。"""
        return {
            "severity": self.severity.name,
            "code": self.code,
            "message": self.message,
            "location": self.location,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """校验报告（``valid`` = 无 ERROR；``issues`` 按 severity 降序、再按 location 排）。"""

    valid: bool
    issues: tuple[ValidationIssue, ...]
    counts: dict[str, int]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {
            "valid": self.valid,
            "issues": [issue.as_json() for issue in self.issues],
            "counts": dict(self.counts),
        }


@dataclass(frozen=True, slots=True)
class HdrCapability:
    """HDR 能力摘要（定义于本模块，inspect.py 引用，TECH-DESIGN §2.5）。

    Attributes:
        is_hdr:          综合判定（transfer∈{16,18} 或 ADGC/HAGC 任一存在）。
        cicp:            CICP 四字段；无 cicp tag 或数据不可读 → None。
                         键：colour_primaries / transfer_characteristics /
                         matrix_coefficients / full_range_flag。
        transfer_label:  'PQ' | 'HLG' | None（仅当 transfer_characteristics∈{16,18}）。
        primaries_label: 'BT.2020' | 'P3-D65' | 'P3-DCI' | 'BT.709' | None。
        has_adgc:        'ADGC' tag 存在（ICC.1:2022 修订，2025-04）。
        has_hagc:        'HAGC' tag 存在（提案，存在即报告）。
        notes:           人读说明（如 malformed、ADGC/HAGC 共存提示）。
    """

    is_hdr: bool
    cicp: dict[str, int | None] | None
    transfer_label: str | None
    primaries_label: str | None
    has_adgc: bool
    has_hagc: bool
    notes: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {
            "is_hdr": self.is_hdr,
            "cicp": dict(self.cicp) if self.cicp is not None else None,
            "transfer_label": self.transfer_label,
            "primaries_label": self.primaries_label,
            "has_adgc": self.has_adgc,
            "has_hagc": self.has_hagc,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


class _IccIoError(IccToolkitError):
    """私有异常：文件读取失败（``code="IO"``，TECH-DESIGN §4.3）。"""

    code = "IO"


def _read_profile_bytes(source: str | pathlib.Path) -> bytes:
    """读文件字节；OSError 统一转 IccToolkitError(code="IO")。"""
    path = pathlib.Path(source)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise _IccIoError(
            f"无法读取 profile 文件: {path}（{exc.strerror or exc}）",
            detail={"path": str(path), "errno": exc.errno},
        ) from exc


def _issue(
    severity: Severity,
    code: str,
    message: str,
    location: str | None = None,
) -> ValidationIssue:
    """构造 :class:`ValidationIssue`（内部便捷函数）。"""
    return ValidationIssue(severity=severity, code=code, message=message, location=location)


def _build_report(issues: list[ValidationIssue]) -> ValidationReport:
    """汇总 issues → 排序（severity 降序、再按 location）→ 统计数量的报告。"""
    issues.sort(key=lambda i: (-i.severity.value, i.location or ""))
    counts = {
        "error": sum(1 for i in issues if i.severity == Severity.ERROR),
        "warning": sum(1 for i in issues if i.severity == Severity.WARNING),
        "info": sum(1 for i in issues if i.severity == Severity.INFO),
    }
    return ValidationReport(valid=counts["error"] == 0, issues=tuple(issues), counts=counts)


def _check_header(data: bytes, issues: list[ValidationIssue]) -> ProfileVersion:
    """头部级规则 2-8 与 19（前提：len(data) >= 128）。"""
    n = len(data)
    (size_declared,) = struct.unpack_from(">I", data, 0)

    # 规则 2：魔数
    if data[36:40] != MAGIC:
        issues.append(_issue(Severity.ERROR, "HDR_MAGIC", "魔数不是 'acsp'，不是有效的 ICC profile", "header:36"))

    # 规则 3：声明尺寸与实际长度一致
    if size_declared > n:
        issues.append(
            _issue(
                Severity.ERROR,
                "SIZE_MISMATCH",
                f"header.size 声明 {size_declared} 字节，实际仅 {n} 字节（数据被截断）",
                "header:0",
            )
        )
    elif size_declared < n:
        issues.append(
            _issue(
                Severity.WARNING,
                "SIZE_MISMATCH",
                f"header.size 声明 {size_declared} 字节，实际 {n} 字节（尾部有多余数据）",
                "header:0",
            )
        )

    # 规则 4：尺寸 4 字节对齐
    if size_declared % 4 != 0:
        issues.append(_issue(Severity.WARNING, "SIZE_ALIGNMENT", f"header.size={size_declared} 不是 4 的倍数", "header:0"))

    # 规则 5：保留区全 0
    if n >= 128 and any(data[100:128]):
        issues.append(_issue(Severity.WARNING, "RESERVED_NOT_ZERO", "header 保留区（100-127）非全 0", "header:reserved"))

    # 规则 6：版本号
    (version_raw,) = struct.unpack_from(">I", data, 8)
    major = (version_raw >> 24) & 0xFF
    if major > 5:
        issues.append(_issue(Severity.INFO, "VERSION_UNKNOWN", f"版本 major={major} 超出已知范围 0-5", "header:8"))
    elif major == 5:
        issues.append(_issue(Severity.INFO, "VERSION_UNKNOWN", "v5-iccMAX 版本：仅识别，不深解析 tag", "header:8"))

    # 规则 7：rendering intent
    (intent,) = struct.unpack_from(">I", data, 64)
    if intent > 3:
        issues.append(_issue(Severity.ERROR, "BAD_INTENT", f"rendering_intent={intent} 超出 0-3", "header:64"))

    # 规则 8：光源 ≈ D50
    ix, iy, iz = struct.unpack_from(">iii", data, 68)
    illuminant = (round(ix / 65536.0, 6), round(iy / 65536.0, 6), round(iz / 65536.0, 6))
    if (
        abs(illuminant[0] - _D50_XYZ[0]) > _ILLUMINANT_TOLERANCE
        or abs(illuminant[1] - _D50_XYZ[1]) > _ILLUMINANT_TOLERANCE
        or abs(illuminant[2] - _D50_XYZ[2]) > _ILLUMINANT_TOLERANCE
    ):
        issues.append(
            _issue(
                Severity.WARNING,
                "ILLUMINANT_NOT_D50",
                f"header 光源 {illuminant} 偏离 D50 {_D50_XYZ}",
                "header:68",
            )
        )

    # 规则 19：color_space 与 PCS 组合
    device_class = data[12:16].decode("latin-1", errors="replace")
    color_space = data[16:20].decode("latin-1", errors="replace")
    pcs = data[20:24].decode("latin-1", errors="replace")
    if pcs not in ("XYZ ", "Lab "):
        issues.append(
            _issue(
                Severity.WARNING,
                "PCS_COMBO_UNUSUAL",
                f"PCS='{pcs}' 不是 'XYZ '/'Lab '（ICC 规范仅允许两者）",
                "header:20",
            )
        )
    elif device_class in ("mntr", "scnr", "ictr") and pcs == "Lab ":
        issues.append(
            _issue(Severity.INFO, "PCS_COMBO_UNUSUAL", f"{device_class} 类 profile 建议 XYZ PCS，Lab 为非典型组合", "header:20")
        )
    elif device_class == "link" and color_space not in ("XYZ ", "Lab ") and pcs not in ("XYZ ", "Lab "):
        issues.append(
            _issue(
                Severity.INFO,
                "PCS_COMBO_UNUSUAL",
                f"link 类：输入 '{color_space}' 与输出 '{pcs}' 均为设备空间（需与下游预期一致）",
                "header:20",
            )
        )

    return ProfileVersion(
        major=(version_raw >> 24) & 0xFF,
        minor=(version_raw >> 20) & 0x0F,
        bugfix=(version_raw >> 16) & 0x0F,
        raw=version_raw,
    )


def _iter_entries(data: bytes, issues: list[ValidationIssue]) -> tuple[list[TagTableEntry], bool]:
    """读 tag 表项并执行规则 9-13、16-18；返回表项列表与是否发生 count 溢出。"""
    n = len(data)
    (count,) = struct.unpack_from(">I", data, _TAG_COUNT_OFFSET)
    cap = 2 + (n - _TAG_TABLE_OFFSET) // _TAG_ENTRY_SIZE

    # 规则 9：tag count 上限
    overflow = False
    if count > cap:
        issues.append(
            _issue(
                Severity.ERROR,
                "TAG_COUNT_OVERFLOW",
                f"tag count={count} 超出文件可容纳上限 {cap}",
                "header:128",
            )
        )
        count = cap
        overflow = True

    seen: set[str] = set()
    entries: list[TagTableEntry] = []
    family = detect_version_family(_header_version(data))
    for i in range(count):
        base = _TAG_TABLE_OFFSET + i * _TAG_ENTRY_SIZE
        if base + _TAG_ENTRY_SIZE > n:
            logger.warning("tag 表项 %d 越出数据尾部，停止解析", i)
            break
        sig_raw, offset, size = struct.unpack_from(">4sII", data, base)
        signature = sig_raw.decode("latin-1", errors="replace")
        location = f"tag[{signature}]"

        # 规则 12：签名 ASCII 可打印
        if not (signature and all(0x20 <= ord(ch) <= 0x7E for ch in signature)):
            issues.append(
                _issue(Severity.WARNING, "TAG_BAD_SIGNATURE", f"tag 签名 {signature!r} 非 ASCII 可打印字符", location)
            )
            continue

        # 规则 10：offset+size 越界
        if offset + size > n:
            issues.append(
                _issue(
                    Severity.ERROR,
                    "TAG_OUT_OF_RANGE",
                    f"tag '{signature}' 存储区间 [{offset}, {offset + size}) 超出文件长度 {n}",
                    location,
                )
            )
            continue

        # 规则 11：offset 4 字节对齐
        if offset % 4 != 0:
            issues.append(
                _issue(Severity.WARNING, "TAG_MISALIGNED", f"tag '{signature}' offset={offset} 未按 4 字节对齐", location)
            )

        # 规则 13：同签名重复
        if signature in seen:
            issues.append(_issue(Severity.WARNING, "TAG_DUPLICATE", f"tag 签名 '{signature}' 重复出现", location))
        seen.add(signature)

        # 规则 18：类型签名可读且长度达到该类型最小长度
        if size < 4:
            issues.append(
                _issue(
                    Severity.ERROR,
                    "TAG_TYPE_TRUNCATED",
                    f"tag '{signature}' 数据仅 {size} 字节，类型签名不可读",
                    location,
                )
            )
        else:
            type_sig = data[offset : offset + 4].decode("latin-1", errors="replace")
            min_size = TAG_TYPE_MIN_SIZES.get(type_sig)
            if min_size is not None and size < min_size:
                issues.append(
                    _issue(
                        Severity.ERROR,
                        "TAG_TYPE_TRUNCATED",
                        f"tag '{signature}' 类型 '{type_sig}' 数据长度 {size} 小于最小要求 {min_size}",
                        location,
                    )
                )

        # 规则 16：mAB/mBA 出现在 v2 → 版本声明不符
        # 规则 17：desc 类型与版本不符（允许但非典型）
        if size >= 4:
            type_sig = data[offset : offset + 4].decode("latin-1", errors="replace")
            if family == "v2" and type_sig in ("mAB ", "mBA "):
                issues.append(
                    _issue(
                        Severity.WARNING,
                        "TAG_VERSION_MISMATCH",
                        f"v2 profile 中 tag '{signature}' 使用 {type_sig.strip()} 类型（v4 专用）",
                        location,
                    )
                )
            if signature == "desc":
                if family == "v2" and type_sig == "mluc":
                    issues.append(
                        _issue(
                            Severity.INFO,
                            "DESC_TYPE_UNUSUAL",
                            "v2 profile 的 desc tag 使用 mluc 类型（典型为 desc 类型）",
                            location,
                        )
                    )
                elif family == "v4" and type_sig == "desc":
                    issues.append(
                        _issue(
                            Severity.INFO,
                            "DESC_TYPE_UNUSUAL",
                            "v4 profile 的 desc tag 使用 textDescriptionType（典型为 mluc 类型）",
                            location,
                        )
                    )

        entries.append(TagTableEntry(signature=signature, offset=offset, size=size))
    return entries, overflow


def _header_version(data: bytes) -> ProfileVersion:
    """从原始字节读版本号（供表项级规则使用，不校验魔数）。"""
    (raw,) = struct.unpack_from(">I", data, 8)
    return ProfileVersion(
        major=(raw >> 24) & 0xFF,
        minor=(raw >> 20) & 0x0F,
        bugfix=(raw >> 16) & 0x0F,
        raw=raw,
    )


def _check_overlaps(data: bytes, entries: list[TagTableEntry], issues: list[ValidationIssue]) -> None:
    """规则 14：tag 存储区间重叠检测（共享允许清单除外）。"""
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            e1, e2 = entries[i], entries[j]
            identical = e1.offset == e2.offset and e1.size == e2.size
            if identical and e1.signature in _SHARED_TAG_SIGS and e2.signature in _SHARED_TAG_SIGS:
                continue  # v2 常见的 TRC 共享等，允许清单内不计警告
            if e1.offset < e2.offset + e2.size and e2.offset < e1.offset + e1.size:
                v1 = decode_tag(data, e1)
                v2 = decode_tag(data, e2)
                unreadable = isinstance(v1, RawTag) or isinstance(v2, RawTag)
                issues.append(
                    _issue(
                        Severity.ERROR if unreadable else Severity.WARNING,
                        "TAG_OVERLAP",
                        f"tag '{e1.signature}' 与 '{e2.signature}' 存储区间重叠"
                        + ("且至少一方无法解码" if unreadable else ""),
                        f"tag[{e1.signature}]|tag[{e2.signature}]",
                    )
                )


def _check_required_tags(
    data: bytes,
    entries: list[TagTableEntry],
    issues: list[ValidationIssue],
) -> None:
    """规则 15：按 device_class 与版本族的必需 tag 表（TECH-DESIGN §4.4）。

    简化自 ICC.1:2022；normative 表以规范原文为准（实现时复核）。
    """
    signatures = {entry.signature for entry in entries}
    device_class = data[12:16].decode("latin-1", errors="replace")
    family = detect_version_family(_header_version(data))

    def require(sig: str, severity: Severity = Severity.ERROR) -> None:
        if sig not in signatures:
            label = "v4 建议" if severity is Severity.WARNING else "必需"
            issues.append(
                _issue(severity, "REQUIRED_TAG_MISSING", f"{label} tag '{sig}' 缺失", f"tag[{sig}]")
            )

    if device_class in ("scnr", "ictr", "mntr"):
        require("desc")
        require("wtpt")
        matrix_group = all(t in signatures for t in ("rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"))
        if not matrix_group and "A2B0" not in signatures:
            issues.append(
                _issue(
                    Severity.ERROR,
                    "REQUIRED_TAG_MISSING_MATRIX_OR_A2B0",
                    "矩阵/TRC 组（rXYZ+gXYZ+bXYZ+rTRC+gTRC+bTRC）与 A2B0 均缺失",
                    "tags",
                )
            )
    elif device_class == "prtr":
        require("desc")
        require("wtpt")
        require("A2B0")
        require("B2A0")
        if family == "v4":
            for tag in ("gamt", "A2B1", "B2A1"):
                require(tag, Severity.WARNING)
    elif device_class == "link":
        require("A2B0")
        if family == "v4" and not ({"D2B0", "B2D0"} & signatures):
            issues.append(
                _issue(Severity.WARNING, "REQUIRED_TAG_MISSING", "v4 link 类建议 D2B0/B2D0 至少其一", "tags")
            )
    elif device_class == "spac":
        require("desc")
        require("A2B0")
        require("B2A0")
    elif device_class == "abst":
        require("A2B0")
    elif device_class in ("nmcl", "nm"):
        require("meta")
    # 未知 device_class：原样保留，不做必需 tag 判定

    if "cprt" not in signatures:
        issues.append(
            _issue(Severity.INFO, "REQUIRED_TAG_MISSING", "'cprt' 版权 tag 缺失（建议提供）", "tag[cprt]")
        )


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def validate_bytes(data: bytes) -> ValidationReport:
    """对 profile 字节执行全部结构校验规则（TECH-DESIGN §4.4 规则 1-19）。

    规则依序执行：头部级（魔数/尺寸/保留区/版本/意图/光源/PCS 组合）与
    tag 表级（count 上限/越界/对齐/签名/重复/重叠/必需表/版本合规/截断）。
    文件不足 132 字节时报 ``HDR_SHORT_FILE`` ERROR（此时能读到的头部规则仍执行）。

    Args:
        data: profile 全量字节。

    Returns:
        :class:`ValidationReport`（不抛结构异常；根本无法解析时以 ERROR 项体现）。
    """
    issues: list[ValidationIssue] = []
    n = len(data)

    # 规则 1：文件 ≥ 132 字节且能读 tag count
    if n < _TAG_TABLE_OFFSET:
        issues.append(
            _issue(
                Severity.ERROR,
                "HDR_SHORT_FILE",
                f"文件仅 {n} 字节，不足 132 字节，无法读取 tag 表",
                "header",
            )
        )

    if n >= 128:
        _check_header(data, issues)
    if n >= _TAG_TABLE_OFFSET:
        entries, _overflow = _iter_entries(data, issues)
        _check_overlaps(data, entries, issues)
        _check_required_tags(data, entries, issues)

    return _build_report(issues)


def validate_profile(path: str | pathlib.Path) -> ValidationReport:
    """对 profile 文件执行全部结构校验（:func:`validate_bytes` 的文件入口）。

    Args:
        path: profile 文件路径。

    Returns:
        :class:`ValidationReport`。

    Raises:
        IccToolkitError: 文件读取失败（``code="IO"``，detail 含 path/errno）。
    """
    return validate_bytes(_read_profile_bytes(path))


def hdr_capability(tags: dict[str, object], header: ProfileHeader) -> HdrCapability:
    """检测 HDR 能力（TECH-DESIGN §4.4 HDR 检测规则 1-6）。

    规则：
    1. ``cicp`` tag（cicpType）：数据 ≥12 字节时读字节 8/9/10/11 →
       colour_primaries / transfer_characteristics / matrix_coefficients /
       full_range_flag(bit0)；不足 20 字节额外记非标准长度 note。
    2. transfer_characteristics ∈ {16, 18} → ``is_hdr=True``，label 'PQ'/'HLG'。
    3. colour_primaries == 9 → ``primaries_label='BT.2020'``（仅提示广色域）。
    4. ``ADGC`` / ``HAGC`` tag 存在 → 对应布尔置位，且任一存在即 ``is_hdr=True``。
    5. 无上述 tag → ``HdrCapability(is_hdr=False, cicp=None, ...)``。
    6. cicp 数据 < 12 字节 → notes 记 malformed，不判定 HDR（不抛异常）。

    Args:
        tags:   tag 签名 → 解码值（``decode_tag`` 结果；cicp/ADGC/HAGC 均为 RawTag）。
        header: 已解析的 profile 头部（用于上下文说明）。

    Returns:
        :class:`HdrCapability`（防御式，绝不抛异常）。
    """
    notes: list[str] = []
    cicp_dict: dict[str, int | None] | None = None
    transfer_label: str | None = None
    primaries_label: str | None = None
    is_hdr = False

    raw_cicp = tags.get("cicp")
    if raw_cicp is not None:
        cicp_bytes: bytes | None = None
        if isinstance(raw_cicp, (bytes, bytearray)):
            cicp_bytes = bytes(raw_cicp)
        else:
            data_attr = getattr(raw_cicp, "data", None)
            if isinstance(data_attr, (bytes, bytearray)):
                cicp_bytes = bytes(data_attr)
        if cicp_bytes is None:
            notes.append("cicp tag 存在但数据不可读（malformed），不判定 HDR")
        elif len(cicp_bytes) < 12:
            notes.append(f"cicp tag 数据仅 {len(cicp_bytes)} 字节（malformed），不判定 HDR")
        else:
            primaries = cicp_bytes[8]
            transfer = cicp_bytes[9]
            matrix = cicp_bytes[10]
            full_range = cicp_bytes[11] & 0x01
            cicp_dict = {
                "colour_primaries": primaries,
                "transfer_characteristics": transfer,
                "matrix_coefficients": matrix,
                "full_range_flag": full_range,
            }
            if len(cicp_bytes) < 20:
                notes.append(f"cicp tag 数据 {len(cicp_bytes)} 字节，不足标准 20 字节（按可读字段解析）")
            if transfer == 16:
                is_hdr = True
                transfer_label = "PQ"
                notes.append("transfer_characteristics=16（SMPTE ST 2084 PQ，HDR）")
            elif transfer == 18:
                is_hdr = True
                transfer_label = "HLG"
                notes.append("transfer_characteristics=18（ARIB STD-B67 HLG，HDR）")
            if primaries == 9:
                primaries_label = "BT.2020"
                notes.append("colour_primaries=9（BT.2020/BT.2100 广色域）")
            elif primaries == 11:
                primaries_label = "P3-D65"
            elif primaries == 12:
                primaries_label = "P3-DCI"
            elif primaries == 1:
                primaries_label = "BT.709"
            if detect_version_family(header.version) == "v2":
                notes.append("cicp tag 出现在 v2 profile（非典型，cicp 为 ICC.1:2022 v4.4 引入）")

    has_adgc = "ADGC" in tags
    has_hagc = "HAGC" in tags
    if has_adgc:
        notes.append("Adaptive Gain Curve (ICC.1:2022 Amd, 2025-04)")
        is_hdr = True
    if has_hagc:
        notes.append("Headroom Adaptive Gain Curve (提案)")
        is_hdr = True
    if has_adgc and has_hagc:
        notes.append("ADGC present; superseded by HAGC proposal")

    return HdrCapability(
        is_hdr=is_hdr,
        cicp=cicp_dict,
        transfer_label=transfer_label,
        primaries_label=primaries_label,
        has_adgc=has_adgc,
        has_hagc=has_hagc,
        notes=tuple(notes),
    )
