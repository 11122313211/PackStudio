"""icc/tags.py 单元测试（TECH-DESIGN §7.1 测试矩阵）。

覆盖：真实 sRGB 的 17 个 tag 与必需签名集合、desc/mluc 合成往返与语言偏好、
curv gamma/恒等/线性表、para 函数类型、XYZ/sig、mft1/mft2 结构字段、
mAB/mBA 结构摘要、未知与损坏 tag 的 RawTag 降级（永不抛异常）、tag 表越界
与 count 上限防御、描述提取级联与 as_json 字段名契约。
"""
from __future__ import annotations

import struct

import pytest

from fixtures.synthetic import (
    build_matrix_trc_profile,
    curv_type_payload,
    desc_type_payload,
    mluc_type_payload,
    para_type_payload,
    require_system_profile,
    sig_type_payload,
    text_type_payload,
    xyz_type_payload,
    mab_payload,
    mft1_payload,
    mft2_payload,
)
from packstudio.engines.color_engine.icc.header import parse_header
from packstudio.engines.color_engine.icc.tags import (
    CurveTag,
    DescTag,
    Lut16Tag,
    Lut8Tag,
    LutAToBTag,
    MlucTag,
    ParaTag,
    RawTag,
    SignatureTag,
    TagTableEntry,
    TextTag,
    XyzTag,
    decode_tag,
    get_profile_description,
    parse_tag_table,
)


def test_tag_table_srgb() -> None:
    """真实 sRGB：17 个 tag，包含 cprt/desc/wtpt/rXYZ/gXYZ/bXYZ/rTRC…（§7.1）。"""
    data = require_system_profile("sRGB").read_bytes()
    entries = parse_tag_table(data)
    assert len(entries) == 17
    signatures = {entry.signature for entry in entries}
    assert {"cprt", "desc", "wtpt", "rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"} <= signatures
    for entry in entries:
        assert entry.offset >= 132 and entry.size > 0
        assert entry.offset + entry.size <= len(data)


def test_tag_table_count_mismatch() -> None:
    """tag count 声明远超合理上限：按上限截断解析，不抛异常，合法表项保留。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">I", data, 128, 999)  # 远超合理上限 408//12+2=36
    entries = parse_tag_table(bytes(data))
    assert 9 <= len(entries) <= 36  # 9 个合法表项必在；垃圾区域表项可跳过


def test_tag_table_skips_out_of_range() -> None:
    """越界表项跳过不抛（validate 规则 10 负责判定）。"""
    data = bytearray(build_matrix_trc_profile())
    # 把 wtpt 表项的 size 改为覆盖到文件末之外
    count = struct.unpack_from(">I", data, 128)[0]
    for i in range(count):
        sig, off, size = struct.unpack_from(">4sII", data, 132 + 12 * i)
        if sig == b"wtpt":
            struct.pack_into(">I", data, 132 + 12 * i + 8, len(data) + 1000)
    entries = parse_tag_table(bytes(data))
    assert all(entry.signature != "wtpt" for entry in entries)
    assert len(entries) == 8


def test_tag_table_too_short_raises() -> None:
    """数据不足 132 字节 → IccParseError。"""
    from packstudio.engines.color_engine.errors import IccParseError

    with pytest.raises(IccParseError):
        parse_tag_table(b"\x00" * 131)


def test_desc_roundtrip_synthetic() -> None:
    """合成 desc：ASCII 往返；unicode/mac 缺省为 None。"""
    data = build_matrix_trc_profile()
    entries = parse_tag_table(data)
    desc_entry = next(e for e in entries if e.signature == "desc")
    value = decode_tag(data, desc_entry)
    assert isinstance(value, DescTag)
    assert value.ascii_text == "Synthetic Test RGB"
    assert value.unicode_text is None
    assert value.mac_desc is None


def test_desc_unicode_and_mac() -> None:
    """合成 desc：UCS-2BE Unicode 段与 Macintosh 段解码。"""
    payload = desc_type_payload("Base", unicode_text="彩色描述", mac_text="MacDesc")
    value = decode_tag(payload, TagTableEntry(signature="desc", offset=0, size=len(payload)))
    assert isinstance(value, DescTag)
    assert value.ascii_text == "Base"
    assert value.unicode_text == "彩色描述"
    assert value.mac_desc == "MacDesc"


def test_desc_legacy_ascii_unicode_quirk() -> None:
    """Lino sRGB 历史 quirk：Unicode 段直存 ASCII 字节时按 ASCII 兼容解码。"""
    srgb = require_system_profile("sRGB").read_bytes()
    entries = parse_tag_table(srgb)
    value = decode_tag(srgb, next(e for e in entries if e.signature == "desc"))
    assert isinstance(value, DescTag)
    assert value.unicode_text == "sRGB IEC61966-2.1"


def test_mluc_records_and_language_preference() -> None:
    """合成 mluc：zh-CN/en 记录提取；描述提取偏好 zh-CN → zh → en → 首条。"""
    payload = mluc_type_payload([("en", "US", "Adobe RGB"), ("zh", "CN", "彩色空间")])
    value = decode_tag(payload, TagTableEntry(signature="mluc", offset=0, size=len(payload)))
    assert isinstance(value, MlucTag)
    assert ("zh", "CN", "彩色空间") in value.records
    assert ("en", "US", "Adobe RGB") in value.records

    data = build_matrix_trc_profile(extra_tags={b"mluc": payload})
    entries = parse_tag_table(data)
    # desc 仍存在且优先 → 去掉 desc 后 mluc 的 zh-CN 生效
    from fixtures.synthetic import mutate

    no_desc = mutate(data, remove_tag=b"desc")
    entries2 = parse_tag_table(no_desc)
    assert get_profile_description(no_desc, entries2) == "彩色空间"

    en_only = mluc_type_payload([("en", "US", "English only")])
    data3 = build_matrix_trc_profile(extra_tags={b"mluc": en_only})
    assert get_profile_description(data3, parse_tag_table(data3)) == "Synthetic Test RGB"  # desc 优先
    no_desc3 = mutate(data3, remove_tag=b"desc")
    assert get_profile_description(no_desc3, parse_tag_table(no_desc3)) == "English only"


def test_mluc_empty_count() -> None:
    """mluc count=0 → records=()。"""
    payload = b"mluc" + b"\x00" * 4 + struct.pack(">II", 0, 12)
    value = decode_tag(payload, TagTableEntry(signature="mluc", offset=0, size=len(payload)))
    assert value == MlucTag(records=())


def test_curv_gamma() -> None:
    """curv：count=1 payload 0x0233 → gamma≈2.2；count=0 → 恒等；count>1 → u16 表。"""
    payload = b"curv" + b"\x00" * 4 + struct.pack(">I", 1) + struct.pack(">H", 0x0233) + b"\x00\x00"
    value = decode_tag(payload, TagTableEntry(signature="rTRC", offset=0, size=len(payload)))
    assert isinstance(value, CurveTag)
    assert value.count == 1
    assert value.gamma == pytest.approx(2.2, abs=0.01)
    assert value.values is None

    identity = decode_tag(
        b"curv" + b"\x00" * 4 + struct.pack(">I", 0),
        TagTableEntry(signature="rTRC", offset=0, size=12),
    )
    assert isinstance(identity, CurveTag)
    assert identity.gamma == 1.0 and identity.values is None

    table_payload = curv_type_payload(values=[0.0, 0.5, 1.0])
    table = decode_tag(table_payload, TagTableEntry(signature="rTRC", offset=0, size=len(table_payload)))
    assert isinstance(table, CurveTag)
    assert table.count == 3
    assert table.gamma is None
    assert table.values is not None and len(table.values) == 3
    assert all(0.0 <= v <= 1.0 for v in table.values)


def test_curv_shared_by_trc_tags() -> None:
    """合成体：rTRC/gTRC/bTRC 共享同一区间，解码结果一致（§7.2 共享路径）。"""
    data = build_matrix_trc_profile(trc_gamma=2.4)
    entries = parse_tag_table(data)
    by_sig = {e.signature: e for e in entries}
    assert by_sig["rTRC"].offset == by_sig["gTRC"].offset == by_sig["bTRC"].offset
    values = [decode_tag(data, by_sig[sig]) for sig in ("rTRC", "gTRC", "bTRC")]
    assert all(isinstance(v, CurveTag) and v.gamma is not None for v in values)
    assert values[0].gamma == values[1].gamma == values[2].gamma == pytest.approx(2.4, abs=0.01)


def test_para_function_types() -> None:
    """para：f=0 → 1 参数；f=4 → 7 参数；未知 f → RawTag（不抛异常）。"""
    p0 = para_type_payload(0, [2.4])
    v0 = decode_tag(p0, TagTableEntry(signature="x", offset=0, size=len(p0)))
    assert isinstance(v0, ParaTag) and v0.function_type == 0 and len(v0.params) == 1
    assert v0.params[0] == pytest.approx(2.4, abs=1e-5)

    p4 = para_type_payload(4, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    v4 = decode_tag(p4, TagTableEntry(signature="x", offset=0, size=len(p4)))
    assert isinstance(v4, ParaTag) and len(v4.params) == 7

    bad = b"para" + b"\x00" * 4 + struct.pack(">H", 9) + b"\x00\x00" + b"\x00" * 8
    vbad = decode_tag(bad, TagTableEntry(signature="x", offset=0, size=len(bad)))
    assert isinstance(vbad, RawTag)


def test_xyz_and_sig() -> None:
    """XYZType/sig 解码。"""
    payload = xyz_type_payload((0.9642, 1.0, 0.8249))
    value = decode_tag(payload, TagTableEntry(signature="wtpt", offset=0, size=len(payload)))
    assert isinstance(value, XyzTag)
    assert len(value.values) == 1
    assert value.values[0] == pytest.approx((0.9642, 1.0, 0.8249), abs=1e-5)

    sig_payload = sig_type_payload(b"CMYK")
    sig_value = decode_tag(sig_payload, TagTableEntry(signature="tech", offset=0, size=len(sig_payload)))
    assert isinstance(sig_value, SignatureTag) and sig_value.signature == "CMYK"


def test_text_payload() -> None:
    """TextType 解码（尾部 NUL 去除）。"""
    payload = text_type_payload("Public domain")
    value = decode_tag(payload, TagTableEntry(signature="cprt", offset=0, size=len(payload)))
    assert value == TextTag(text="Public domain")


def test_mft1_structure() -> None:
    """合成 mft1：in=3 out=3 g=17 → 表长与 CLUT 长度符合布局公式。"""
    payload = mft1_payload(in_ch=3, out_ch=3, grid=17)
    value = decode_tag(payload, TagTableEntry(signature="A2B0", offset=0, size=len(payload)))
    assert isinstance(value, Lut8Tag)
    assert value.input_channels == 3 and value.output_channels == 3 and value.clut_points == 17
    assert len(value.matrix) == 9
    assert value.matrix[0] == pytest.approx(1.0, abs=1e-6)
    assert len(value.input_table) == 3 * 256
    assert len(value.clut) == 17**3 * 3
    assert len(value.output_table) == 3 * 256


def test_mft2_structure() -> None:
    """合成 mft2（§7.1）：in=3 out=3 g=9、input_entries=256；CLUT 长度=9³×3。"""
    payload = mft2_payload(in_ch=3, out_ch=3, grid=9, in_entries=256, out_entries=256)
    value = decode_tag(payload, TagTableEntry(signature="A2B0", offset=0, size=len(payload)))
    assert isinstance(value, Lut16Tag)
    assert value.input_channels == 3
    assert value.output_channels == 3
    assert value.clut_points == 9
    assert value.input_entries == 256
    assert value.output_entries == 256
    assert len(value.matrix) == 9
    assert len(value.input_table) == 3 * 256
    assert len(value.clut) == 9**3 * 3 == 2187
    assert len(value.output_table) == 3 * 256
    assert all(0 <= v <= 65535 for v in value.clut)


def test_mab_summary() -> None:
    """合成 mAB 偏移表 → LutAToBTag 布尔正确；offset=0 → 对应 False（§7.1）。"""
    payload = mab_payload(kind=b"mAB ", in_ch=3, out_ch=3, grid=(9, 9, 9))
    value = decode_tag(payload, TagTableEntry(signature="A2B0", offset=0, size=len(payload)))
    assert isinstance(value, LutAToBTag)
    assert value.kind == "AtoB"
    assert value.input_channels == 3 and value.output_channels == 3
    assert value.has_b_curves is True
    assert value.has_matrix is False  # offset=0
    assert value.has_m_curves is False
    assert value.has_clut is True
    assert value.has_a_curves is True
    assert value.clut_points == (9, 9, 9)
    assert value.element_types == ("curv", "clut", "curv")

    bto_a = mab_payload(kind=b"mBA ")
    v2 = decode_tag(bto_a, TagTableEntry(signature="B2A0", offset=0, size=len(bto_a)))
    assert isinstance(v2, LutAToBTag) and v2.kind == "BtoA"

    minimal = mab_payload(with_b=False, with_clut=False, with_a=False)
    v3 = decode_tag(minimal, TagTableEntry(signature="A2B0", offset=0, size=len(minimal)))
    assert isinstance(v3, LutAToBTag)
    assert (v3.has_b_curves, v3.has_matrix, v3.has_m_curves, v3.has_clut, v3.has_a_curves) == (
        False,
        False,
        False,
        False,
        False,
    )
    assert v3.clut_points is None
    assert v3.element_types == ()


def test_decode_unknown_and_truncated_returns_raw() -> None:
    """未知类型与损坏 tag 一律 RawTag，decode_tag 永不抛异常。"""
    data = build_matrix_trc_profile(
        extra_tags={
            b"zzzz": b"zzzz" + b"\x00" * 4 + b"whatever",
            b"bad1": b"mft2" + b"\x00" * 16,  # mft2 截断（< 48）
            b"bad2": b"curv" + b"\x00" * 2,  # < 12
            b"bad3": b"para" + b"\x00" * 4 + struct.pack(">H", 99),  # 未知 functionType
        }
    )
    entries = parse_tag_table(data)
    by_sig = {e.signature: e for e in entries}
    for sig, type_sig in (("zzzz", "zzzz"), ("bad1", "mft2"), ("bad2", "curv"), ("bad3", "para")):
        value = decode_tag(data, by_sig[sig])
        assert isinstance(value, RawTag)
        assert value.type_sig == type_sig


def test_get_profile_description_srgb() -> None:
    """真实 sRGB：描述为 'sRGB IEC61966-2.1'（desc ASCII）。"""
    srgb = require_system_profile("sRGB").read_bytes()
    assert get_profile_description(srgb, parse_tag_table(srgb)) == "sRGB IEC61966-2.1"


def test_get_profile_description_fallbacks() -> None:
    """描述级联：desc → mluc → header.model → '<unnamed>'。"""
    data = build_matrix_trc_profile()
    assert get_profile_description(data, parse_tag_table(data)) == "Synthetic Test RGB"

    no_desc = mutate_fix(data)
    assert get_profile_description(no_desc, parse_tag_table(no_desc)) == "TST1"  # model 兜底

    no_model = bytearray(no_desc)
    no_model[52:56] = b"\x00\x00\x00\x00"
    assert get_profile_description(bytes(no_model), parse_tag_table(bytes(no_model))) == "<unnamed>"


def mutate_fix(data: bytes) -> bytes:
    """移除 desc 的便捷封装（避免测试文件间导入 mutate 的循环命名困惑）。"""
    from fixtures.synthetic import mutate

    return mutate(data, remove_tag=b"desc")


@pytest.mark.parametrize(
    ("value", "fields"),
    [
        (RawTag(type_sig="zzzz", data=b"\x01\x02"), {"type_sig", "data"}),
        (DescTag(ascii_text="a", unicode_text=None, mac_desc=None), {"ascii_text", "unicode_text", "mac_desc"}),
        (TextTag(text="t"), {"text"}),
        (MlucTag(records=(("zh", "CN", "彩"),)), {"records"}),
        (XyzTag(values=((1.0, 0.0, 0.0),)), {"values"}),
        (SignatureTag(signature="RGB "), {"signature"}),
        (CurveTag(count=1, gamma=2.2, values=None), {"count", "gamma", "values"}),
        (ParaTag(function_type=0, params=(2.4,)), {"function_type", "params"}),
        (
            Lut8Tag(1, 3, 17, (1.0,), (0,), (0,), (0,)),
            {"input_channels", "output_channels", "clut_points", "matrix", "input_table", "clut", "output_table"},
        ),
        (
            Lut16Tag(1, 3, 17, 256, 256, (1.0,), (0,), (0,), (0,)),
            {
                "input_channels",
                "output_channels",
                "clut_points",
                "input_entries",
                "output_entries",
                "matrix",
                "input_table",
                "clut",
                "output_table",
            },
        ),
        (
            LutAToBTag("AtoB", 3, 3, True, False, False, True, True, (9, 9, 9), ("curv",)),
            {
                "kind",
                "input_channels",
                "output_channels",
                "has_b_curves",
                "has_matrix",
                "has_m_curves",
                "has_clut",
                "has_a_curves",
                "clut_points",
                "clut_precision",
                "element_types",
            },
        ),
        (TagTableEntry(signature="desc", offset=132, size=48), {"signature", "offset", "size"}),
    ],
)
def test_tag_json_field_names(value: object, fields: set[str]) -> None:
    """各 tag dataclass 的 as_json 字段名 = dataclass 字段名（§2.3 契约）。"""
    as_json = value.as_json()  # type: ignore[attr-defined]
    assert set(as_json) == fields


def test_real_profile_adobe_rgb_tags() -> None:
    """真实 AdobeRGB1998：10 个 tag（§7.1 已核实清单规模）。"""
    data = require_system_profile("AdobeRGB1998").read_bytes()
    entries = parse_tag_table(data)
    assert len(entries) == 10
    assert {"rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC", "wtpt", "desc"} <= {e.signature for e in entries}
    header = parse_header(data)
    assert header.size == len(data)


def test_parse_header_consistency_after_tag_ops() -> None:
    """tag 表解析结果与 header 解析无耦合：remove_tag 变异后仍可完整解析。"""
    data = build_matrix_trc_profile()
    from fixtures.synthetic import mutate

    trimmed = mutate(data, remove_tag=b"cprt")
    entries = parse_tag_table(trimmed)
    assert [e.signature for e in entries] == ["desc", "wtpt", "rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"]
    header = parse_header(trimmed)
    assert header.size == len(trimmed)
