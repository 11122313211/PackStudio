"""icc LUT 精度感知（ROADMAP v0.4.8）单元测试。

覆盖：detect_luts 的类型/位深/网格/意图/方向提取（mft1/mft2/mAB/mBA/RawTag/
para 非 LUT 降级）、ProfileInfo.luts 接线与 as_json 契约、mAB CLUT 精度字节
（ICC v4 规范：1=8-bit / 2=16-bit），以及真实 CMYK profile（RSWOP/
JapanColor2001Coated，缺失则 skip）的实测精度——后者同时锁定 mft1/mft2
字段偏移的 ICC v4 规范布局（旧实现误读字节 5/6/7，真实文件 in/out/g 在
8/9/10、矩阵@12、mft2 entries@48/表@52）。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from fixtures.synthetic import (
    SYSTEM_PROFILES,
    build_matrix_trc_profile,
    mab_payload,
    mft1_payload,
    mft2_payload,
    para_type_payload,
)
from packstudio.engines.color_engine.icc.inspect import LutDetection, ProfileInfo, load_profile

RSWOP = pathlib.Path(SYSTEM_PROFILES["RSWOP"])
JAPAN = pathlib.Path(SYSTEM_PROFILES["JapanColor2001Coated"])


def _lut_by_sig(info: ProfileInfo, sig: str) -> LutDetection | None:
    for lut in info.luts:
        if lut.signature == sig:
            return lut
    return None


def test_detect_luts_empty_for_matrix_trc() -> None:
    """矩阵/TRC profile：无变换 tag → luts 空元组。"""
    info = load_profile(build_matrix_trc_profile())
    assert info.luts == ()


def test_detect_mft2_synthetic() -> None:
    """合成 mft2（A2B1）：类型/位深/网格/通道/表项/意图/方向/label 全量提取。"""
    data = build_matrix_trc_profile(
        extra_tags={b"A2B1": mft2_payload(in_ch=3, out_ch=3, grid=9, in_entries=256, out_entries=256)}
    )
    info = load_profile(data)
    assert len(info.luts) == 1
    lut = info.luts[0]
    assert isinstance(lut, LutDetection)
    assert lut.signature == "A2B1"
    assert lut.direction == "device_to_pcs" and lut.intent == "colorimetric"
    assert lut.type_sig == "mft2" and lut.readable is True
    assert lut.table_bits == 16
    assert (lut.input_channels, lut.output_channels) == (3, 3)
    assert lut.has_clut is True
    assert lut.clut_points == (9, 9, 9)
    assert lut.clut_total == 9**3 * 3 == 2187
    assert (lut.input_table_entries, lut.output_table_entries) == (256, 256)
    assert lut.label == "16-bit CLUT 9×9×9（3→3）"


def test_detect_mft1_synthetic() -> None:
    """合成 mft1（B2A0）：8-bit、256 表项、统一网格展开。"""
    data = build_matrix_trc_profile(extra_tags={b"B2A0": mft1_payload(in_ch=4, out_ch=3, grid=17)})
    lut = load_profile(data).luts[0]
    assert lut.signature == "B2A0"
    assert lut.direction == "pcs_to_device" and lut.intent == "perceptual"
    assert lut.type_sig == "mft1" and lut.table_bits == 8
    assert (lut.input_channels, lut.output_channels) == (4, 3)
    assert lut.clut_points == (17, 17, 17, 17)
    assert lut.clut_total == 17**4 * 3
    assert (lut.input_table_entries, lut.output_table_entries) == (256, 256)
    assert lut.label == "8-bit CLUT 17×17×17×17（4→3）"


def test_detect_mab_clut_precision() -> None:
    """合成 mAB：CLUT 精度字节按 ICC v4 规范（1=8-bit / 2=16-bit）读取。"""
    for bits in (8, 16):
        data = build_matrix_trc_profile(
            extra_tags={b"A2B2": mab_payload(kind=b"mAB ", grid=(9, 9, 9), clut_precision_bits=bits)}
        )
        lut = load_profile(data).luts[0]
        assert lut.signature == "A2B2"
        assert lut.intent == "saturation"
        assert lut.type_sig == "mAB "
        assert lut.table_bits == bits
        assert lut.clut_points == (9, 9, 9)
        assert lut.clut_total == 9**3 * 3
        assert lut.label == f"{bits}-bit CLUT 9×9×9（3→3）"


def test_detect_mab_without_clut() -> None:
    """合成 mBA 无 CLUT（矩阵/曲线变换）：has_clut=False、精度 None。"""
    data = build_matrix_trc_profile(
        extra_tags={b"B2A1": mab_payload(kind=b"mBA ", with_clut=False)}
    )
    lut = load_profile(data).luts[0]
    assert lut.signature == "B2A1"
    assert lut.direction == "pcs_to_device" and lut.intent == "colorimetric"
    assert lut.has_clut is False
    assert lut.table_bits is None and lut.clut_points is None and lut.clut_total is None
    assert lut.label == "无 CLUT（矩阵/曲线变换，3→3）"


def test_detect_rawtag_and_non_lut_type() -> None:
    """损坏 mft2（RawTag 降级）如实报告 readable=False；para 挂变换 tag 报告非 LUT 类型。"""
    data = build_matrix_trc_profile(
        extra_tags={
            b"A2B0": b"mft2" + b"\x00" * 16,  # 截断 → RawTag
            b"B2A2": para_type_payload(0, [2.4]),
        }
    )
    info = load_profile(data)
    a2b0 = _lut_by_sig(info, "A2B0")
    assert a2b0 is not None
    assert a2b0.readable is False and a2b0.type_sig == "mft2"
    assert a2b0.label == "无法解析（类型 mft2）"
    b2a2 = _lut_by_sig(info, "B2A2")
    assert b2a2 is not None
    assert b2a2.readable is True
    assert b2a2.direction == "pcs_to_device" and b2a2.intent == "saturation"
    assert b2a2.label == "非 LUT 类型（ParaTag）"


def test_lut_json_contract() -> None:
    """LutDetection.as_json 字段名 = dataclass 字段名；clut_points 序列化为 list；
    ProfileInfo.as_json 含 'luts' 键且整体可 JSON 序列化。"""
    data = build_matrix_trc_profile(extra_tags={b"A2B0": mft1_payload()})
    info = load_profile(data)
    payload = info.as_json()
    assert set(payload) == set(ProfileInfo.__dataclass_fields__)
    assert "luts" in payload
    lut_json = payload["luts"][0]
    assert set(lut_json) == set(LutDetection.__dataclass_fields__)
    assert lut_json["clut_points"] == [17, 17, 17]
    assert isinstance(json.dumps(payload, ensure_ascii=False), str)


def test_real_rswop_luts() -> None:
    """真实 RSWOP.icm：A2B* 16-bit 9⁴ CMYK→Lab；B2A0 16-bit 17³ Lab→CMYK。

    同时回归锁定 mft 偏移为 ICC v4 规范布局（旧偏移会把真实文件读成 0→0）。
    """
    if not RSWOP.exists():
        pytest.skip("RSWOP.icm not present")
    info = load_profile(RSWOP)
    assert len(info.luts) == 6
    a2b1 = _lut_by_sig(info, "A2B1")
    assert a2b1 is not None
    assert a2b1.type_sig == "mft2" and a2b1.table_bits == 16
    assert (a2b1.input_channels, a2b1.output_channels) == (4, 3)
    assert a2b1.clut_points == (9, 9, 9, 9)
    assert a2b1.clut_total == 9**4 * 3 == 19683
    assert (a2b1.input_table_entries, a2b1.output_table_entries) == (256, 2)
    assert a2b1.intent == "colorimetric"
    b2a0 = _lut_by_sig(info, "B2A0")
    assert b2a0 is not None
    assert b2a0.type_sig == "mft2" and b2a0.table_bits == 16
    assert (b2a0.input_channels, b2a0.output_channels) == (3, 4)
    assert b2a0.clut_points == (17, 17, 17)
    assert b2a0.direction == "pcs_to_device" and b2a0.intent == "perceptual"


def test_real_japan_color_luts() -> None:
    """真实 JapanColor2001Coated.icc：B2A0 mft1 8-bit 33³ Lab→CMYK。"""
    if not JAPAN.exists():
        pytest.skip("JapanColor2001Coated.icc not present")
    info = load_profile(JAPAN)
    b2a0 = _lut_by_sig(info, "B2A0")
    assert b2a0 is not None
    assert b2a0.type_sig == "mft1" and b2a0.table_bits == 8
    assert (b2a0.input_channels, b2a0.output_channels) == (3, 4)
    assert b2a0.clut_points == (33, 33, 33)
    assert b2a0.clut_total == 33**3 * 4 == 143748
    assert b2a0.label == "8-bit CLUT 33×33×33（3→4）"
