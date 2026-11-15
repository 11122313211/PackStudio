"""ICC tag 表与常用 tag 类型解码。

TECH-DESIGN §3.2 / §4.2 的实现：

- ``parse_tag_table``：128 偏移的 tag count + count×12 字节表项；
- ``decode_tag``：按 tag 数据前 4 字节类型签名分发解码，未知/损坏一律降级为
  :class:`RawTag`，**永不抛异常**（单个 tag 损坏不影响整体 inspect）；
- ``get_profile_description``：desc → mluc（zh-CN→zh→en→首条）→ header.model 级联；
- mft1/mft2/mAB/mBA 为结构化解码（mAB/mBA 仅结构摘要，不展开数值求值，
  变换一律走 cmm/LittleCMS，见 §8 R1）。

零第三方依赖（仅标准库）。
"""
from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from packstudio.engines.color_engine.errors import IccParseError

__all__ = [
    "TagTableEntry",
    "RawTag",
    "DescTag",
    "TextTag",
    "MlucTag",
    "XyzTag",
    "SignatureTag",
    "CurveTag",
    "ParaTag",
    "Lut8Tag",
    "Lut16Tag",
    "LutAToBTag",
    "TAG_TYPE_DECODERS",
    "TAG_TYPE_MIN_SIZES",
    "parse_tag_table",
    "decode_tag",
    "get_profile_description",
]

logger = logging.getLogger("icct.icc.tags")

#: tag count 字段所在偏移（128）与首个表项偏移（132）。
_TAG_COUNT_OFFSET: Final[int] = 128
_TAG_TABLE_OFFSET: Final[int] = 132
_TAG_ENTRY_SIZE: Final[int] = 12

#: 各类型签名解码所需的最小数据长度（validate 规则 18 TAG_TYPE_TRUNCATED 依据）。
TAG_TYPE_MIN_SIZES: Final[dict[str, int]] = {
    "desc": 12,
    "mluc": 16,
    "text": 9,
    "XYZ ": 20,
    "curv": 12,
    "para": 12,
    "sig ": 12,
    "mft1": 48,
    "mft2": 52,
    "mAB ": 32,
    "mBA ": 32,
}

# para（ParametricCurveType）functionType → 参数个数（g 与后随系数）。
_PARA_PARAM_COUNT: Final[dict[int, int]] = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}


def _s15_fixed16(value: int) -> float:
    """s15Fixed16 定点数 → float（与 header 模块一致的换算）。"""
    return round(value / 65536.0, 6)


@dataclass(frozen=True, slots=True)
class TagTableEntry:
    """tag 表项（TECH-DESIGN §3.2）。

    Attributes:
        signature: 4 字符 tag 签名，如 ``'desc'``、``'A2B0'``。
        offset:    tag 数据相对文件起始的偏移。
        size:      tag 数据字节数。
    """

    signature: str
    offset: int
    size: int

    def as_json(self) -> dict[str, Any]:
        """JSON 契约（字段名 = dataclass 字段名）。"""
        return {"signature": self.signature, "offset": self.offset, "size": self.size}


@dataclass(frozen=True, slots=True)
class RawTag:
    """未知/未实现/损坏类型的 tag 原始字节（TECH-DESIGN §3.2）。"""

    type_sig: str
    data: bytes

    def as_json(self) -> dict[str, Any]:
        """JSON 契约；``data`` 序列化为 hex 字符串。"""
        return {"type_sig": self.type_sig, "data": self.data.hex()}


@dataclass(frozen=True, slots=True)
class DescTag:
    """textDescriptionType（'desc'，v2 常用）。

    Attributes:
        ascii_text:   ASCII 描述串（去尾部 NUL；空串表示未提供）。
        unicode_text: Unicode 描述（UCS-2BE 解码；旧版写入器把 ASCII 直存该段时
                      也按 ASCII 兼容解码）；无则 None。
        mac_desc:     Macintosh 描述（Roman 脚本 latin-1 解码）；无则 None。
    """

    ascii_text: str
    unicode_text: str | None
    mac_desc: str | None

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {"ascii_text": self.ascii_text, "unicode_text": self.unicode_text, "mac_desc": self.mac_desc}


@dataclass(frozen=True, slots=True)
class TextTag:
    """TextType（'text'，7-bit ASCII，尾部 NUL 结束）。"""

    text: str

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class MlucTag:
    """multiLocalizedUnicodeType（'mluc'，v4 常用）。

    Attributes:
        records: (语言码, 国家/脚本码, 文本) 三元组序列，如 ``('zh', 'CN', '彩色')``。
    """

    records: tuple[tuple[str, str, str], ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约；records 序列化为嵌套 list。"""
        return {"records": [list(rec) for rec in self.records]}


@dataclass(frozen=True, slots=True)
class XyzTag:
    """XYZType（'XYZ '）：n 组 s15Fixed16 三元组（wtpt/rXYZ/lumi 等）。"""

    values: tuple[tuple[float, float, float], ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约；values 序列化为嵌套 list。"""
        return {"values": [list(v) for v in self.values]}


@dataclass(frozen=True, slots=True)
class SignatureTag:
    """SignatureType（'sig '）：4 字符签名（tech/视条件等）。"""

    signature: str

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {"signature": self.signature}


@dataclass(frozen=True, slots=True)
class CurveTag:
    """CurveType（'curv'）。

    Attributes:
        count:  表项数：0=恒等（以 gamma=1.0 表示）；1=u8Fixed8 gamma；>1=u16 表。
        gamma:  count=1 时的 gamma 值（如 0x0233 → ≈2.2）；其余情况 None
                （count=0 恒等以 gamma=1.0 表示）。
        values: count>1 时的 0..1 线性化表（u16/65535），长度=count。
    """

    count: int
    gamma: float | None
    values: tuple[float, ...] | None

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        values = list(self.values) if self.values is not None else None
        return {"count": self.count, "gamma": self.gamma, "values": values}


@dataclass(frozen=True, slots=True)
class ParaTag:
    """ParametricCurveType（'para'，v4）。

    Attributes:
        function_type: 0-4（0:g；1:g,a,b；2:g,a,b,c；3:g,a,b,c,d；4:g,a,b,c,d,e,f）。
        params:        s15Fixed16 参数序列，长度由 function_type 决定。
    """

    function_type: int
    params: tuple[float, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {"function_type": self.function_type, "params": list(self.params)}


@dataclass(frozen=True, slots=True)
class Lut8Tag:
    """lut8Type（'mft1'）结构字段（TECH-DESIGN §4.2.2；v1 不做数值求值）。"""

    input_channels: int
    output_channels: int
    clut_points: int
    matrix: tuple[float, ...]
    input_table: tuple[int, ...]
    clut: tuple[int, ...]
    output_table: tuple[int, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "clut_points": self.clut_points,
            "matrix": list(self.matrix),
            "input_table": list(self.input_table),
            "clut": list(self.clut),
            "output_table": list(self.output_table),
        }


@dataclass(frozen=True, slots=True)
class Lut16Tag:
    """lut16Type（'mft2'）结构字段：同 mft1，但输入/输出表与 CLUT 为 u16，
    且字节 44-47 为 input_entries/output_entries（uint16）。

    u16 值域 0-65535 语义上归一化 0..1（求值路径属 cmm，本层存原始整数）。
    """

    input_channels: int
    output_channels: int
    clut_points: int
    input_entries: int
    output_entries: int
    matrix: tuple[float, ...]
    input_table: tuple[int, ...]
    clut: tuple[int, ...]
    output_table: tuple[int, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "clut_points": self.clut_points,
            "input_entries": self.input_entries,
            "output_entries": self.output_entries,
            "matrix": list(self.matrix),
            "input_table": list(self.input_table),
            "clut": list(self.clut),
            "output_table": list(self.output_table),
        }


@dataclass(frozen=True, slots=True)
class LutAToBTag:
    """lutAtoBType / lutBtoAType（'mAB '/'mBA '，v4）结构摘要。

    不展开数值求值（TECH-DESIGN §8 R1：变换一律走 cmm/LittleCMS）。

    Attributes:
        kind:            'AtoB' | 'BtoA'。
        input_channels:  输入通道数。
        output_channels: 输出通道数。
        has_b_curves:    B 曲线元素存在。
        has_matrix:      矩阵元素存在（该元素无类型签名，为 3×(3+1) s15Fixed16 裸数据）。
        has_m_curves:    M 曲线元素存在。
        has_clut:        CLUT 元素存在。
        has_a_curves:    A 曲线元素存在。
        clut_points:     每输入通道网格点数（有 CLUT 且网格可读时）；否则 None。
        clut_precision:  CLUT 数据精度位深（有 CLUT 且精度字节合法时）：
                         ICC v4 规范精度字节 1=8-bit / 2=16-bit；无 CLUT 或
                         字节非法（0/其他）为 None。
        element_types:   各存在元素的类型签名，物理顺序 B→Matrix→M→CLUT→A；
                         matrix/CLUT 元素无类型签名，分别以 ``'matrix'``/``'clut'``
                         占位标注。
    """

    kind: str
    input_channels: int
    output_channels: int
    has_b_curves: bool
    has_matrix: bool
    has_m_curves: bool
    has_clut: bool
    has_a_curves: bool
    clut_points: tuple[int, ...] | None
    element_types: tuple[str, ...]
    clut_precision: int | None = None

    def as_json(self) -> dict[str, Any]:
        """JSON 契约。"""
        return {
            "kind": self.kind,
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "has_b_curves": self.has_b_curves,
            "has_matrix": self.has_matrix,
            "has_m_curves": self.has_m_curves,
            "has_clut": self.has_clut,
            "has_a_curves": self.has_a_curves,
            "clut_points": list(self.clut_points) if self.clut_points is not None else None,
            "clut_precision": self.clut_precision,
            "element_types": list(self.element_types),
        }


# ---------------------------------------------------------------------------
# 各类型解码器（输入 = tag 数据整体，含 4 字节类型签名；结构不合法时 raise，
# 由 decode_tag 统一捕获并降级为 RawTag）
# ---------------------------------------------------------------------------


def _decode_desc_unicode(raw: bytes) -> str | None:
    """desc 的 Unicode 段解码。

    主路径 UCS-2BE；兼容旧版写入器（如 Lino sRGB）把 ASCII 直存该段的历史 quirk：
    全部字节为可打印 ASCII 时按 ASCII 解码（真 UCS-2BE 拉丁文本的高字节为 0x00，
    不会落入该分支）。
    """
    if not raw:
        return None
    raw = raw.rstrip(b"\x00")
    if not raw:
        return None
    if all(0x20 <= b <= 0x7E for b in raw):
        # 旧版写入器（如 Lino sRGB）把 ASCII 直存 Unicode 段的历史 quirk。
        return raw.decode("ascii")
    text = raw.decode("utf-16-be", errors="replace").rstrip("\x00")
    return text or None


def parse_desc(chunk: bytes) -> DescTag:
    """textDescriptionType（'desc'）解码。

    布局：sig4 + reserved4 + ASCII 串长 u32（含终止 NUL）+ ASCII 串
    + Unicode 语言码 u32 + Unicode 脚本码 u32 + Unicode 串字节长 u32 + Unicode 串
    + Mac 脚本码 u8 + Mac 串长 u8（含终止 NUL）+ Mac 串。

    防御式解析（TECH-DESIGN §8 R6 精神）：Unicode 串长非法（超出剩余数据）时，
    重试旧版写入器（如 1998 Lino sRGB）把 count 域提前 1 字节的历史布局
    （count u32 在 pos+7、串在 pos+11）；仍失败则放弃 Unicode/Mac 段，不抛异常。
    """
    if len(chunk) < 12:
        raise ValueError(f"desc 数据不足：{len(chunk)} < 12")
    (ascii_count,) = struct.unpack_from(">I", chunk, 8)
    ascii_count = min(ascii_count, max(0, len(chunk) - 12))
    ascii_text = chunk[12 : 12 + ascii_count].decode("ascii", errors="replace").rstrip("\x00")
    pos = 12 + ascii_count
    unicode_text: str | None = None
    mac_desc: str | None = None
    if pos + 12 <= len(chunk):
        _lang, _script, uni_count = struct.unpack_from(">III", chunk, pos)
        str_pos = pos + 12
        if not (0 <= uni_count <= len(chunk) - str_pos):
            # 旧版写入器 count 域提前 1 字节的 quirk：重试 pos+7。
            legacy_count = struct.unpack_from(">I", chunk, pos + 7)[0] if pos + 11 <= len(chunk) else 0
            if 0 < legacy_count <= len(chunk) - (pos + 11):
                uni_count = legacy_count
                str_pos = pos + 11
                logger.debug("desc 命中旧版写入器的 count 提前 1 字节布局")
            else:
                uni_count = 0
        if uni_count > 0:
            unicode_text = _decode_desc_unicode(chunk[str_pos : str_pos + uni_count])
            mac_pos = str_pos + uni_count
        else:
            mac_pos = pos + 12
        if mac_pos + 2 <= len(chunk):
            mac_count = chunk[mac_pos + 1]
            mac_count = min(mac_count, max(0, len(chunk) - mac_pos - 2))
            mac_raw = chunk[mac_pos + 2 : mac_pos + 2 + mac_count]
            mac_text = mac_raw.decode("latin-1", errors="replace").rstrip("\x00")
            mac_desc = mac_text or None
    return DescTag(ascii_text=ascii_text, unicode_text=unicode_text, mac_desc=mac_desc)


def parse_mluc(chunk: bytes) -> MlucTag:
    """multiLocalizedUnicodeType（'mluc'）解码。

    布局：sig4 + reserved4 + 记录数 u32 + 记录尺寸 u32（标准 12）+ count×
    (串字节长 u32 + 串偏移 u32)；每记录 = 语言码 2 + 国家码 2 + UTF-16BE 文本。
    """
    if len(chunk) < 16:
        raise ValueError(f"mluc 数据不足：{len(chunk)} < 16")
    count, record_size = struct.unpack_from(">II", chunk, 8)
    if count == 0:
        return MlucTag(records=())
    if record_size < 12:
        raise ValueError(f"mluc 记录尺寸非法：{record_size}")
    capacity = max(0, (len(chunk) - 16) // record_size)
    if count > capacity:
        logger.warning("mluc 记录数 %d 超出可读范围，截断为 %d", count, capacity)
        count = capacity
    records: list[tuple[str, str, str]] = []
    for i in range(count):
        base = 16 + i * record_size
        str_len, str_off = struct.unpack_from(">II", chunk, base)
        if str_len < 4 or str_off + str_len > len(chunk):
            logger.warning("mluc 记录 %d 越界或过短（len=%d off=%d），跳过", i, str_len, str_off)
            continue
        rec = chunk[str_off : str_off + str_len]
        lang = rec[0:2].decode("ascii", errors="replace")
        script = rec[2:4].decode("ascii", errors="replace")
        text = rec[4:].decode("utf-16-be", errors="replace").rstrip("\x00")
        records.append((lang, script, text))
    return MlucTag(records=tuple(records))


def parse_text(chunk: bytes) -> TextTag:
    """TextType（'text'）解码：sig4 + reserved4 + 7-bit ASCII（尾部 NUL）。"""
    if len(chunk) < 9:
        raise ValueError(f"text 数据不足：{len(chunk)} < 9")
    raw = chunk[8:]
    nul = raw.find(b"\x00")
    if nul >= 0:
        raw = raw[:nul]
    return TextTag(text=raw.decode("ascii", errors="replace"))


def parse_xyz(chunk: bytes) -> XyzTag:
    """XYZType（'XYZ '）解码：sig4 + reserved4 + n×(3×s15Fixed16)。"""
    if len(chunk) < 8:
        raise ValueError(f"XYZ 数据不足：{len(chunk)} < 8")
    n = max(0, (len(chunk) - 8) // 12)
    values = []
    for i in range(n):
        x, y, z = struct.unpack_from(">iii", chunk, 8 + i * 12)
        values.append((_s15_fixed16(x), _s15_fixed16(y), _s15_fixed16(z)))
    return XyzTag(values=tuple(values))


def parse_curv(chunk: bytes) -> CurveTag:
    """CurveType（'curv'）解码。

    count=0 → 恒等（gamma=1.0 表示法）；count=1 → u8Fixed8 gamma（如 0x0233≈2.2）；
    count>1 → u16 线性化表（值 /65535 归一化 0..1）。
    """
    if len(chunk) < 12:
        raise ValueError(f"curv 数据不足：{len(chunk)} < 12")
    (count,) = struct.unpack_from(">I", chunk, 8)
    if count == 0:
        return CurveTag(count=0, gamma=1.0, values=None)
    available = max(0, (len(chunk) - 12) // 2)
    if count == 1:
        if available < 1:
            raise ValueError("curv count=1 但 gamma 值缺失")
        (v,) = struct.unpack_from(">H", chunk, 12)
        return CurveTag(count=1, gamma=round(v / 256.0, 6), values=None)
    n = min(count, available)
    values = tuple(struct.unpack_from(">H", chunk, 12 + 2 * i)[0] / 65535.0 for i in range(n))
    return CurveTag(count=count, gamma=None, values=values)


def parse_para(chunk: bytes) -> ParaTag:
    """ParametricCurveType（'para'）解码。

    布局：sig4 + reserved4 + functionType u16 + reserved u16 + n×s15Fixed16；
    n 由 functionType 0..4 决定（1/3/4/5/7）。可选尾随 curv 表忽略（结构摘要层）。
    """
    if len(chunk) < 12:
        raise ValueError(f"para 数据不足：{len(chunk)} < 12")
    (ftype,) = struct.unpack_from(">H", chunk, 8)
    n = _PARA_PARAM_COUNT.get(ftype)
    if n is None:
        raise ValueError(f"para functionType {ftype} 未知")
    n = min(n, max(0, (len(chunk) - 12) // 4))
    params = tuple(_s15_fixed16(v) for v in struct.unpack_from(f">{n}i", chunk, 12))
    return ParaTag(function_type=ftype, params=params)


def parse_sig(chunk: bytes) -> SignatureTag:
    """SignatureType（'sig '）解码：sig4 + reserved4 + 签名 4（字节 8-11）。"""
    if len(chunk) < 12:
        raise ValueError(f"sig 数据不足：{len(chunk)} < 12")
    return SignatureTag(signature=chunk[8:12].decode("latin-1", errors="replace"))


def parse_mft1(chunk: bytes) -> Lut8Tag:
    """lut8Type（'mft1'）解码（TECH-DESIGN §4.2.2，布局同 ICC v4 规范）。

    布局：sig4 + 保留4 + in(8) + out(9) + g(10) + 保留(11)
    + 9×s15Fixed16 矩阵(12-47) + 输入表 in×256×u8 + CLUT g^in×out×u8
    + 输出表 out×256×u8。
    """
    if len(chunk) < 48:
        raise ValueError(f"mft1 数据不足：{len(chunk)} < 48")
    in_ch, out_ch, grid = chunk[8], chunk[9], chunk[10]
    matrix = tuple(_s15_fixed16(v) for v in struct.unpack_from(">9i", chunk, 12))
    n_clut = (grid**in_ch if in_ch else 0) * out_ch
    needed = 48 + in_ch * 256 + n_clut + out_ch * 256
    if len(chunk) < needed:
        raise ValueError(f"mft1 结构不完整：需要 {needed} 字节，实际 {len(chunk)}")
    pos = 48
    input_table = tuple(chunk[pos : pos + in_ch * 256])
    pos += in_ch * 256
    clut = tuple(chunk[pos : pos + n_clut])
    pos += n_clut
    output_table = tuple(chunk[pos : pos + out_ch * 256])
    return Lut8Tag(
        input_channels=in_ch,
        output_channels=out_ch,
        clut_points=grid,
        matrix=matrix,
        input_table=input_table,
        clut=clut,
        output_table=output_table,
    )


def parse_mft2(chunk: bytes) -> Lut16Tag:
    """lut16Type（'mft2'）解码（TECH-DESIGN §4.2.2，布局同 ICC v4 规范）。

    与 mft1 差异：字节 48-49/50-51 为 input_entries/output_entries（u16），
    输入表/CLUT/输出表均为 u16（数据自字节 52 起）。u16 语义上归一化 0..1
    （本层存原始整数）。
    """
    if len(chunk) < 52:
        raise ValueError(f"mft2 数据不足：{len(chunk)} < 52")
    in_ch, out_ch, grid = chunk[8], chunk[9], chunk[10]
    matrix = tuple(_s15_fixed16(v) for v in struct.unpack_from(">9i", chunk, 12))
    in_entries, out_entries = struct.unpack_from(">HH", chunk, 48)
    n_clut = (grid**in_ch if in_ch else 0) * out_ch
    needed = 52 + (in_ch * in_entries + n_clut + out_ch * out_entries) * 2
    if len(chunk) < needed:
        raise ValueError(f"mft2 结构不完整：需要 {needed} 字节，实际 {len(chunk)}")
    pos = 52
    input_table = struct.unpack_from(f">{in_ch * in_entries}H", chunk, pos)
    pos += in_ch * in_entries * 2
    clut = struct.unpack_from(f">{n_clut}H", chunk, pos)
    pos += n_clut * 2
    output_table = struct.unpack_from(f">{out_ch * out_entries}H", chunk, pos)
    return Lut16Tag(
        input_channels=in_ch,
        output_channels=out_ch,
        clut_points=grid,
        input_entries=in_entries,
        output_entries=out_entries,
        matrix=matrix,
        input_table=input_table,
        clut=clut,
        output_table=output_table,
    )


def parse_mab(chunk: bytes) -> LutAToBTag:
    """lutAtoBType / lutBtoAType（'mAB '/'mBA '）结构摘要（TECH-DESIGN §4.2.3）。

    布局：sig4 + reserved4 + in(8) + out(9) + reserved(10-11)
    + 5×u32 偏移表（12-31）：[0]=B 曲线 [1]=矩阵 [2]=M 曲线 [3]=CLUT [4]=A 曲线，
    0 表示无此元素。仅记录存在性/通道数/网格点/CLUT 精度/元素类型清单，
    不做数值求值。

    CLUT 元素布局（ICC v4 lutAtoBType/lutBtoAType）：每输入通道 1 字节网格点数
    + 1 字节精度（1=8-bit / 2=16-bit）+ 3 字节保留 + 数据。
    """
    if len(chunk) < 32:
        raise ValueError(f"mAB/mBA 数据不足：{len(chunk)} < 32")
    type_sig = chunk[0:4].decode("latin-1", errors="replace")
    kind = "AtoB" if type_sig == "mAB " else "BtoA"
    in_ch, out_ch = chunk[8], chunk[9]
    b_off, mat_off, m_off, clut_off, a_off = struct.unpack_from(">5I", chunk, 12)
    has_b, has_matrix, has_m, has_clut, has_a = (off != 0 for off in (b_off, mat_off, m_off, clut_off, a_off))

    clut_points: tuple[int, ...] | None = None
    clut_precision: int | None = None
    if has_clut and clut_off + in_ch <= len(chunk):
        clut_points = tuple(chunk[clut_off : clut_off + in_ch])
        if clut_off + in_ch + 1 <= len(chunk):
            # 精度字节紧跟网格点数之后：1=8-bit / 2=16-bit；其余视为未知。
            clut_precision = {1: 8, 2: 16}.get(chunk[clut_off + in_ch])

    element_types: list[str] = []
    for role, present, off in (
        ("B", has_b, b_off),
        ("matrix", has_matrix, mat_off),
        ("M", has_m, m_off),
        ("clut", has_clut, clut_off),
        ("A", has_a, a_off),
    ):
        if not present:
            continue
        if role in ("matrix", "clut"):
            # 矩阵与 CLUT 元素无类型签名，按角色占位标注。
            element_types.append(role)
        elif off + 4 <= len(chunk):
            element_types.append(chunk[off : off + 4].decode("latin-1", errors="replace").rstrip("\x00 "))
        else:
            element_types.append("?")
    return LutAToBTag(
        kind=kind,
        input_channels=in_ch,
        output_channels=out_ch,
        has_b_curves=has_b,
        has_matrix=has_matrix,
        has_m_curves=has_m,
        has_clut=has_clut,
        has_a_curves=has_a,
        clut_points=clut_points,
        clut_precision=clut_precision,
        element_types=tuple(element_types),
    )


#: 类型签名 → 解码器分发表（TECH-DESIGN §4.2.1：模块级 dict）。
TAG_TYPE_DECODERS: Final[dict[str, Callable[[bytes], object]]] = {
    "desc": parse_desc,
    "mluc": parse_mluc,
    "text": parse_text,
    "XYZ ": parse_xyz,
    "curv": parse_curv,
    "para": parse_para,
    "sig ": parse_sig,
    "mft1": parse_mft1,
    "mft2": parse_mft2,
    "mAB ": parse_mab,
    "mBA ": parse_mab,
}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def parse_tag_table(data: bytes) -> tuple[TagTableEntry, ...]:
    """解析 tag 表（TECH-DESIGN §4.2.1）。

    行为：读偏移 128 的 tag count，随后 count×12 字节表项（sig4+offset4+size4）。
    count 上限 ``len(data)//12 + 2``（防畸形大 count，超出截断并记 WARNING）；
    每项要求 ``offset+size <= len(data)``——越界项**跳过并记 WARNING**（不抛，
    越界判定交由 validate 规则 10）。

    Args:
        data: profile 全量字节。

    Returns:
        按表序排列的 :class:`TagTableEntry` 元组（越界项不包含在内）。

    Raises:
        IccParseError: 数据不足 132 字节，无法读取 tag count。
    """
    if len(data) < _TAG_TABLE_OFFSET:
        raise IccParseError(
            f"数据长度 {len(data)} 字节，不足 132 字节，无法读取 tag count",
            detail={"offset": _TAG_COUNT_OFFSET, "actual": len(data)},
        )
    (count,) = struct.unpack_from(">I", data, _TAG_COUNT_OFFSET)
    limit = len(data) // _TAG_ENTRY_SIZE + 2
    if count > limit:
        logger.warning("tag count=%d 超出合理上限 %d，按上限截断解析", count, limit)
        count = limit
    entries: list[TagTableEntry] = []
    for i in range(count):
        base = _TAG_TABLE_OFFSET + i * _TAG_ENTRY_SIZE
        if base + _TAG_ENTRY_SIZE > len(data):
            logger.warning("tag 表项 %d 越出数据尾部，停止解析", i)
            break
        sig_raw, offset, size = struct.unpack_from(">4sII", data, base)
        entry = TagTableEntry(
            signature=sig_raw.decode("latin-1", errors="replace"),
            offset=offset,
            size=size,
        )
        if entry.offset + entry.size > len(data):
            logger.warning(
                "tag %r 表项越界（offset+size=%d > 文件长 %d），跳过该表项",
                entry.signature,
                entry.offset + entry.size,
                len(data),
            )
            continue
        entries.append(entry)
    return tuple(entries)


def decode_tag(data: bytes, entry: TagTableEntry) -> object:
    """按类型签名解码单个 tag（TECH-DESIGN §4.2.1）。

    行为：取 ``data[offset:offset+size]``，按前 4 字节类型签名查
    :data:`TAG_TYPE_DECODERS` 分发；未知类型返回 :class:`RawTag`。
    **永不抛异常**：任何解码失败（结构损坏/截断）一律降级为 RawTag 并记 WARNING，
    严重性判定交由 validate 规则 18（TAG_TYPE_TRUNCATED）。

    Args:
        data:   profile 全量字节。
        entry:  来自 :func:`parse_tag_table` 的表项。

    Returns:
        §3.2 的 Tag dataclass 之一；未知/损坏类型为 :class:`RawTag`。
    """
    if entry.offset < 0 or entry.size < 4 or entry.offset + entry.size > len(data):
        return RawTag(type_sig="", data=b"")
    chunk = data[entry.offset : entry.offset + entry.size]
    type_sig = chunk[:4].decode("latin-1", errors="replace")
    decoder = TAG_TYPE_DECODERS.get(type_sig)
    if decoder is None:
        return RawTag(type_sig=type_sig, data=chunk)
    try:
        return decoder(chunk)
    except Exception as exc:  # noqa: BLE001 —— 设计约定：单 tag 损坏不中断整体
        logger.warning("tag %r（类型 %r）解码失败，降级为 RawTag：%s", entry.signature, type_sig, exc)
        return RawTag(type_sig=type_sig, data=chunk)


def get_profile_description(data: bytes, entries: tuple[TagTableEntry, ...]) -> str:
    """提取 profile 描述（TECH-DESIGN §4.2.1）。

    依次尝试：``desc``（textDescriptionType，优先 ASCII，空则 Unicode）→
    ``mluc``（zh-CN → zh → en → 首条）→ header.model（字节 52-55）。
    全部落空返回 ``"<unnamed>"``。

    Args:
        data:    profile 全量字节。
        entries: :func:`parse_tag_table` 的结果。

    Returns:
        描述字符串（不抛异常；损坏 tag 自动跳过）。
    """
    by_sig: dict[str, TagTableEntry] = {}
    for entry in entries:
        by_sig.setdefault(entry.signature, entry)

    desc_entry = by_sig.get("desc")
    if desc_entry is not None:
        value = decode_tag(data, desc_entry)
        if isinstance(value, DescTag):
            text = value.ascii_text.strip()
            if text:
                return text
            if value.unicode_text and value.unicode_text.strip():
                return value.unicode_text.strip()

    mluc_entry = by_sig.get("mluc")
    if mluc_entry is not None:
        value = decode_tag(data, mluc_entry)
        if isinstance(value, MlucTag) and value.records:
            records = value.records
            for lang, script in (("zh", "CN"), ("zh", None), ("en", None)):
                for rec_lang, _rec_script, rec_text in records:
                    if rec_lang == lang and (script is None or _rec_script == script):
                        if rec_text.strip():
                            return rec_text.strip()
            first_text = records[0][2].strip()
            if first_text:
                return first_text

    model = data[52:56].decode("latin-1", errors="replace").strip("\x00 ").strip()
    return model or "<unnamed>"
