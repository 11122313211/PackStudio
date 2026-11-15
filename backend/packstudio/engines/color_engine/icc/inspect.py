"""ICC profile 统一检查入口：字节/路径/文件对象 → :class:`ProfileInfo`。

TECH-DESIGN §3.3 / §4.3 的实现：``load_profile`` 串联 header 解析、tag 表解析、
全部 tag 解码（单 tag 异常吞为 RawTag）、描述提取、HDR 能力检测与版本族判定；
``inspect_file`` 为文件路径入口；``summarize`` 为 ``as_json()`` 别名。

零第三方依赖（仅标准库）。
"""
from __future__ import annotations

import logging
import math
import os
import pathlib
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Final

from packstudio.engines.color_engine.errors import IccToolkitError
from packstudio.engines.color_engine.icc.header import ProfileHeader, detect_version_family, parse_header
from packstudio.engines.color_engine.icc.tags import (
    Lut16Tag,
    Lut8Tag,
    LutAToBTag,
    RawTag,
    TagTableEntry,
    decode_tag,
    get_profile_description,
    parse_tag_table,
)
from packstudio.engines.color_engine.icc.validate import HdrCapability, hdr_capability

__all__ = [
    "ProfileInfo",
    "LutDetection",
    "load_profile",
    "inspect_file",
    "summarize",
]

logger = logging.getLogger("icct.icc.inspect")


class _IccIoError(IccToolkitError):
    """私有异常：文件读取失败（``code="IO"``，TECH-DESIGN §4.3）。"""

    code = "IO"


#: LUT 感知扫描的 tag 清单（v2/v4 变换表，按表序）。
_LUT_TAG_ORDER: Final[tuple[str, ...]] = ("A2B0", "A2B1", "A2B2", "B2A0", "B2A1", "B2A2")

#: tag 签名序号（0/1/2）→ 渲染意图。
_LUT_INTENTS: Final[dict[int, str]] = {0: "perceptual", 1: "colorimetric", 2: "saturation"}


@dataclass(frozen=True, slots=True)
class LutDetection:
    """单个变换 tag（A2B*/B2A*）的 LUT 结构与精度摘要（ROADMAP v0.4.8）。

    只报告可从字节结构确证的事实（类型/位深/网格/通道），不做质量评分；
    变换求值一律走 cmm/LittleCMS（TECH-DESIGN §8 R1）。

    Attributes:
        signature:            tag 签名（"A2B0" 等）。
        direction:            "device_to_pcs" | "pcs_to_device"。
        intent:               "perceptual" | "colorimetric" | "saturation"；
                              序号非 0-2 时 None。
        type_sig:             tag 类型签名（"mft1"/"mft2"/"mAB "/"mBA "；
                              损坏 tag 为 RawTag 的类型或 ""）。
        readable:             False = 结构损坏/未知类型（RawTag 降级）。
        table_bits:           表/CLUT 精度位深：mft1=8、mft2=16、mAB/mBA 取
                              CLUT 精度字节；无 CLUT 或未知为 None。
        input_channels:       输入通道数（不可读为 None）。
        output_channels:      输出通道数（不可读为 None）。
        has_clut:             是否含 CLUT（mft1/mft2 恒 True）。
        clut_points:          每输入通道网格点数；mft1/mft2 为统一 g 展开。
        clut_total:           CLUT 数据点总数（网格点乘积 × 输出通道）。
        input_table_entries:  输入表每通道表项数（mft1=256；mft2=input_entries）。
        output_table_entries: 输出表每通道表项数（mft1=256；mft2=output_entries）。
        label:                人读单行摘要（如 "16-bit CLUT 9×9×9（3→3）"）。
    """

    signature: str
    direction: str
    intent: str | None
    type_sig: str
    readable: bool
    table_bits: int | None
    input_channels: int | None
    output_channels: int | None
    has_clut: bool
    clut_points: tuple[int, ...] | None
    clut_total: int | None
    input_table_entries: int | None
    output_table_entries: int | None
    label: str

    def as_json(self) -> dict[str, Any]:
        """JSON 契约（字段名 = dataclass 字段名；clut_points 序列化为 list）。"""
        return {
            "signature": self.signature,
            "direction": self.direction,
            "intent": self.intent,
            "type_sig": self.type_sig,
            "readable": self.readable,
            "table_bits": self.table_bits,
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "has_clut": self.has_clut,
            "clut_points": list(self.clut_points) if self.clut_points is not None else None,
            "clut_total": self.clut_total,
            "input_table_entries": self.input_table_entries,
            "output_table_entries": self.output_table_entries,
            "label": self.label,
        }


def _grid_label(points: tuple[int, ...] | None) -> str | None:
    """网格点元组 → "9×9×9" 形式字符串；空/None 返回 None。"""
    if not points:
        return None
    return "×".join(str(p) for p in points)


def _detect_one_lut(signature: str, value: object) -> LutDetection:
    """单个变换 tag 值 → :class:`LutDetection`（按类型分派，永不抛异常）。"""
    direction = "device_to_pcs" if signature.startswith("A2B") else "pcs_to_device"
    intent = _LUT_INTENTS.get(int(signature[-1])) if signature[-1].isdigit() else None
    common: dict[str, Any] = {
        "signature": signature,
        "direction": direction,
        "intent": intent,
        "type_sig": getattr(value, "type_sig", "") or type(value).__name__,
        "readable": False,
        "table_bits": None,
        "input_channels": None,
        "output_channels": None,
        "has_clut": False,
        "clut_points": None,
        "clut_total": None,
        "input_table_entries": None,
        "output_table_entries": None,
    }
    if isinstance(value, RawTag):
        return LutDetection(label=f"无法解析（类型 {common['type_sig'] or '未知'}）", **common)

    common["readable"] = True
    if isinstance(value, (Lut8Tag, Lut16Tag)):
        grid = value.clut_points
        in_ch, out_ch = value.input_channels, value.output_channels
        bits = 8 if isinstance(value, Lut8Tag) else 16
        in_entries = 256 if isinstance(value, Lut8Tag) else value.input_entries
        out_entries = 256 if isinstance(value, Lut8Tag) else value.output_entries
        clut_total = len(value.clut)
        grid_str = _grid_label((grid,) * in_ch if in_ch and grid else None)
        clut_part = f"CLUT {grid_str}" if grid_str else "CLUT 网格不可读"
        common.update(
            type_sig="mft1" if isinstance(value, Lut8Tag) else "mft2",
            table_bits=bits,
            input_channels=in_ch,
            output_channels=out_ch,
            has_clut=True,
            clut_points=(grid,) * in_ch if in_ch else None,
            clut_total=clut_total,
            input_table_entries=in_entries,
            output_table_entries=out_entries,
        )
        common["label"] = f"{bits}-bit {clut_part}（{in_ch}→{out_ch}）"
        return LutDetection(**common)

    if isinstance(value, LutAToBTag):
        common.update(
            type_sig="mAB " if value.kind == "AtoB" else "mBA ",
            input_channels=value.input_channels,
            output_channels=value.output_channels,
            has_clut=value.has_clut,
            clut_points=value.clut_points,
            clut_total=(math.prod(value.clut_points) * value.output_channels)
            if value.clut_points
            else None,
            table_bits=value.clut_precision,
        )
        grid_str = _grid_label(value.clut_points)
        if not value.has_clut:
            common["label"] = f"无 CLUT（矩阵/曲线变换，{value.input_channels}→{value.output_channels}）"
        elif value.clut_precision is not None:
            common["label"] = f"{value.clut_precision}-bit CLUT {grid_str}（{value.input_channels}→{value.output_channels}）"
        else:
            common["label"] = f"CLUT {grid_str}（{value.input_channels}→{value.output_channels}，精度未知）"
        return LutDetection(**common)

    # 非 LUT 类型（para/其他）挂在变换 tag 上：如实报告，不猜结构。
    common["label"] = f"非 LUT 类型（{type(value).__name__}）"
    return LutDetection(**common)


def detect_luts(tag_values: dict[str, object]) -> tuple[LutDetection, ...]:
    """从已解码的 tag 值中检测全部变换 LUT（A2B0-2/B2A0-2）并汇总精度。

    Args:
        tag_values: ``load_profile`` 产出的 signature → Tag dataclass 映射
                   （未解码项为 RawTag）。

    Returns:
        按固定表序（A2B0→B2A2）排列的 :class:`LutDetection` 元组；
        无变换 tag 时为空元组。损坏 tag（RawTag）也包含在内（readable=False）。
    """
    return tuple(
        _detect_one_lut(sig, tag_values[sig]) for sig in _LUT_TAG_ORDER if sig in tag_values
    )


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """profile 全量信息汇总（字段名即 JSON 契约，TECH-DESIGN §3.3）。

    Attributes:
        path:           来源路径；内存字节构造时为 None。
        file_size:      数据总字节数。
        header:         128 字节头部解析结果。
        tag_table:      tag 表项元组（v5-iccMAX 仍解析表结构）。
        tag_values:     tag 签名 → §3.2 Tag dataclass（解析失败项为 RawTag；
                        v5-iccMAX 允许为空 dict，仅识别不深解析）。
        description:    desc/mluc 提取结果，退回 header.model。
        hdr:            HDR 能力摘要（:class:`HdrCapability`）。
        version_family: 'v2' | 'v4' | 'v5-iccMAX'。
        luts:           变换 LUT 精度摘要（:class:`LutDetection` 元组，
                        ROADMAP v0.4.8；无变换 tag 为空元组）。
    """

    path: str | None
    file_size: int
    header: ProfileHeader
    tag_table: tuple[TagTableEntry, ...]
    tag_values: dict[str, object]
    description: str
    hdr: HdrCapability
    version_family: str
    luts: tuple[LutDetection, ...] = ()

    def as_json(self) -> dict[str, Any]:
        """JSON 契约（嵌套对象走各自 ``as_json()``）。"""
        return {
            "path": self.path,
            "file_size": self.file_size,
            "header": self.header.as_json(),
            "tag_table": [entry.as_json() for entry in self.tag_table],
            "tag_values": {sig: value.as_json() for sig, value in self.tag_values.items()},
            "description": self.description,
            "hdr": self.hdr.as_json(),
            "luts": [lut.as_json() for lut in self.luts],
            "version_family": self.version_family,
        }


def _read_source(source: bytes | str | os.PathLike[str] | BinaryIO) -> tuple[str | None, bytes]:
    """统一读取来源：bytes 直接用；路径读文件；文件对象 read()。

    Returns:
        (来源路径或 None, 数据字节)。

    Raises:
        IccToolkitError: 路径读取失败（``code="IO"``，detail 含 path/errno）。
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        return None, bytes(source)
    if isinstance(source, (str, os.PathLike)):
        path = pathlib.Path(source)
        try:
            return str(path), path.read_bytes()
        except OSError as exc:
            raise _IccIoError(
                f"无法读取 profile 文件: {path}（{exc.strerror or exc}）",
                detail={"path": str(path), "errno": exc.errno},
            ) from exc
    if hasattr(source, "read"):
        data = source.read()
        if not isinstance(data, (bytes, bytearray)):
            raise _IccIoError("文件对象 read() 未返回 bytes", detail={"type": type(data).__name__})
        return None, bytes(data)
    raise _IccIoError(
        f"不支持的 profile 来源类型: {type(source).__name__}",
        detail={"type": type(source).__name__},
    )


def load_profile(source: bytes | str | os.PathLike[str] | BinaryIO) -> ProfileInfo:
    """统一入口：解析 profile 并汇总为 :class:`ProfileInfo`。

    行为（TECH-DESIGN §4.3）：bytes 直接用；路径/文件对象读全量 →
    ``parse_header`` + ``parse_tag_table`` + 全部 ``decode_tag``（单 tag 异常吞为
    RawTag）+ ``get_profile_description`` + ``hdr_capability`` +
    ``detect_version_family``。v5-iccMAX 头仅填充 header/hdr，tag_values 允许为
    空 dict（仅识别，不深解析）。

    Args:
        source: profile 字节、文件路径（str/PathLike）或二进制文件对象。

    Returns:
        :class:`ProfileInfo`（path/file_size 填充完毕）。

    Raises:
        IccParseError:    profile 结构无法解析（尺寸不足/魔数错误等）。
        IccToolkitError:  路径读取失败（``code="IO"``）。
    """
    started = time.perf_counter()
    source_path, data = _read_source(source)

    header = parse_header(data)
    family = detect_version_family(header.version)

    if family == "v5-iccMAX":
        # v5-iccMAX：仅识别，tag 值不深解析（TECH-DESIGN §4.3）。
        entries = parse_tag_table(data)
        tag_values: dict[str, object] = {}
    else:
        entries = parse_tag_table(data)
        tag_values = {}
        for entry in entries:
            tag_values[entry.signature] = decode_tag(data, entry)

    description = get_profile_description(data, entries)
    hdr = hdr_capability(tag_values, header)
    luts = detect_luts(tag_values)

    logger.debug(
        "profile 解析完成：path=%s size=%d tags=%d family=%s 耗时=%.2fms",
        source_path,
        len(data),
        len(entries),
        family,
        (time.perf_counter() - started) * 1000.0,
    )
    if tag_values:
        undecoded = sorted(
            sig for sig, value in tag_values.items() if isinstance(value, RawTag) and value.type_sig
        )
        logger.info(
        "profile %s：tag 数 %d；未解码 tag（未知类型/损坏）：%s",
        source_path or "<memory>",
        len(entries),
        undecoded or "无",
    )

    return ProfileInfo(
        path=source_path,
        file_size=len(data),
        header=header,
        tag_table=entries,
        tag_values=tag_values,
        description=description,
        hdr=hdr,
        luts=luts,
        version_family=family,
    )


def inspect_file(path: str | os.PathLike[str]) -> ProfileInfo:
    """检查指定路径的 profile 文件（``load_profile`` 的路径入口）。

    行为：``load_profile(path)``；``ProfileInfo.path`` / ``file_size`` 由
    ``load_profile`` 一并填充。

    Args:
        path: profile 文件路径。

    Returns:
        :class:`ProfileInfo`。

    Raises:
        IccParseError:   profile 结构无法解析。
        IccToolkitError: 文件读取失败（``code="IO"``，detail 含 path/errno）。
    """
    return load_profile(path)


def summarize(info: ProfileInfo) -> dict[str, Any]:
    """``ProfileInfo.as_json()`` 别名，供 CLI/Web 序列化（字段名 = dataclass 字段名）。"""
    return info.as_json()
