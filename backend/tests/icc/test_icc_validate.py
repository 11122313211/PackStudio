"""icc/validate.py 单元测试（TECH-DESIGN §7.1 测试矩阵）。

覆盖：合法 profile 通过、截断/重叠/坏魔数/尺寸不符/保留区/坏 intent/光源偏离/
count 溢出/越界/未对齐/坏签名/重复、按 device_class 必需 tag 表（prtr/link/
矩阵-或-A2B0 分支）、版本与 tag 合规（mAB 出现在 v2）、desc 类型非典型、
tag 类型截断、PCS 组合，以及 CICP/ADGC/HAGC 的 HDR 检测全路径与真实 profile
参数化校验。
"""
from __future__ import annotations

import pathlib
import struct

import pytest

from fixtures.synthetic import (
    SYSTEM_PROFILES,
    build_matrix_trc_profile,
    cicp_type_payload,
    mab_payload,
    mluc_type_payload,
    mutate,
    require_system_profile,
)
from packstudio.engines.color_engine.icc.header import parse_header
from packstudio.engines.color_engine.icc.validate import (
    HdrCapability,
    Severity,
    validate_bytes,
    validate_profile,
)


def codes(report: object) -> list[str]:
    """提取报告中的 code 列表（辅助断言）。"""
    return [issue.code for issue in report.issues]  # type: ignore[attr-defined]


@pytest.fixture(params=sorted(SYSTEM_PROFILES), ids=sorted(SYSTEM_PROFILES))
def real_profile(request: pytest.FixtureRequest) -> pathlib.Path:
    """真实系统 profile 参数化（不存在 → skip）。"""
    return require_system_profile(request.param)


# ---------------------------------------------------------------------------
# 通过路径与真实 profile
# ---------------------------------------------------------------------------


def test_valid_synthetic_passes() -> None:
    """合成矩阵/TRC profile：valid=True，0 ERROR 0 WARNING 0 INFO。"""
    report = validate_bytes(build_matrix_trc_profile())
    assert report.valid is True
    assert report.counts == {"error": 0, "warning": 0, "info": 0}
    assert report.issues == ()


def test_valid_japan_color() -> None:
    """真实 JapanColor2001Coated（prtr 类）：valid=True，0 ERROR（§7.1）。"""
    report = validate_profile(require_system_profile("JapanColor2001Coated"))
    assert report.valid is True
    assert report.counts["error"] == 0


def test_real_profiles_no_error(real_profile: pathlib.Path) -> None:
    """真实系统 profile：结构校验无 ERROR（警告允许，如 RSWOP 尺寸未对齐）。"""
    report = validate_profile(real_profile)
    assert report.valid is True
    assert report.counts["error"] == 0


# ---------------------------------------------------------------------------
# 头部级规则
# ---------------------------------------------------------------------------


def test_truncated_100_bytes() -> None:
    """截断到 100 字节 → HDR_SHORT_FILE ERROR（§7.1）。"""
    report = validate_bytes(mutate(build_matrix_trc_profile(), truncate=100))
    assert "HDR_SHORT_FILE" in codes(report)
    assert report.valid is False
    assert report.issues[0].severity == Severity.ERROR


def test_flip_magic() -> None:
    """魔数破坏 → HDR_MAGIC ERROR（§7.1）。"""
    report = validate_bytes(mutate(build_matrix_trc_profile(), flip_magic=True))
    assert "HDR_MAGIC" in codes(report)
    assert report.valid is False


def test_size_mismatch_declared_gt_actual() -> None:
    """声明尺寸 > 实际（尾部截断）→ SIZE_MISMATCH ERROR。"""
    data = build_matrix_trc_profile()
    report = validate_bytes(mutate(data, truncate=len(data) - 8))
    assert "SIZE_MISMATCH" in codes(report)
    assert any(i.severity == Severity.ERROR and i.code == "SIZE_MISMATCH" for i in report.issues)


def test_size_mismatch_declared_lt_actual() -> None:
    """声明尺寸 < 实际（尾部追加）→ SIZE_MISMATCH WARNING，仍 valid。"""
    data = build_matrix_trc_profile() + b"\x00" * 8
    report = validate_bytes(data)
    assert "SIZE_MISMATCH" in codes(report)
    assert all(i.severity == Severity.WARNING for i in report.issues if i.code == "SIZE_MISMATCH")
    assert report.valid is True


def test_size_alignment() -> None:
    """header.size 非 4 倍数 → SIZE_ALIGNMENT WARNING。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">I", data, 0, len(data) + 2)  # 声明值凑成非 4 倍数且 > 实际
    report = validate_bytes(bytes(data))
    assert "SIZE_ALIGNMENT" in codes(report)


def test_reserved_not_zero() -> None:
    """保留区非零 → RESERVED_NOT_ZERO WARNING，仍 valid。"""
    data = bytearray(build_matrix_trc_profile())
    data[100] = 0x01
    report = validate_bytes(bytes(data))
    assert "RESERVED_NOT_ZERO" in codes(report)
    assert report.valid is True


def test_bad_intent() -> None:
    """rendering_intent > 3 → BAD_INTENT ERROR。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">I", data, 64, 7)
    report = validate_bytes(bytes(data))
    assert "BAD_INTENT" in codes(report)
    assert report.valid is False


def test_illuminant_not_d50() -> None:
    """光源改为 D65 → ILLUMINANT_NOT_D50 WARNING。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">iii", data, 68, int(0.3127 * 65536), int(0.3290 * 65536), int(0.3583 * 65536))
    report = validate_bytes(bytes(data))
    assert "ILLUMINANT_NOT_D50" in codes(report)
    assert report.valid is True


def test_version_major_out_of_range() -> None:
    """版本 major > 5 → VERSION_UNKNOWN INFO（§4.4 规则 6）。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">I", data, 8, 0x06000000)
    report = validate_bytes(bytes(data))
    assert "VERSION_UNKNOWN" in codes(report)
    assert all(i.severity == Severity.INFO for i in report.issues if i.code == "VERSION_UNKNOWN")


def test_v5_info_note() -> None:
    """v5 profile → VERSION_UNKNOWN INFO（仅识别说明）。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">I", data, 8, 0x05000000)
    report = validate_bytes(bytes(data))
    assert "VERSION_UNKNOWN" in codes(report)
    assert report.valid is True


# ---------------------------------------------------------------------------
# tag 表级规则
# ---------------------------------------------------------------------------


def test_tag_count_overflow() -> None:
    """tag count 超上限 → TAG_COUNT_OVERFLOW ERROR（§4.4 规则 9）。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">I", data, 128, 999)
    report = validate_bytes(bytes(data))
    assert "TAG_COUNT_OVERFLOW" in codes(report)
    assert report.valid is False


def test_tag_out_of_range() -> None:
    """表项 offset+size 超文件长 → TAG_OUT_OF_RANGE ERROR（§4.4 规则 10）。"""
    data = bytearray(build_matrix_trc_profile())
    count = struct.unpack_from(">I", data, 128)[0]
    patched = False
    for i in range(count):
        sig, off, size = struct.unpack_from(">4sII", data, 132 + 12 * i)
        if sig == b"wtpt":
            struct.pack_into(">I", data, 132 + 12 * i + 8, len(data) + 1000)
            patched = True
    assert patched
    report = validate_bytes(bytes(data))
    assert "TAG_OUT_OF_RANGE" in codes(report)


def test_tag_misaligned() -> None:
    """offset 未 4 字节对齐 → TAG_MISALIGNED WARNING（§4.4 规则 11）。"""
    data = bytearray(build_matrix_trc_profile())
    count = struct.unpack_from(">I", data, 128)[0]
    for i in range(count):
        sig, off, size = struct.unpack_from(">4sII", data, 132 + 12 * i)
        if sig == b"cprt":
            struct.pack_into(">I", data, 132 + 12 * i + 4, off + 2)
    report = validate_bytes(bytes(data))
    assert "TAG_MISALIGNED" in codes(report)


def test_tag_bad_signature() -> None:
    """签名含不可打印字节 → TAG_BAD_SIGNATURE WARNING（§4.4 规则 12）。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">4s", data, 132, b"\x01\x02\x03\x04")  # 第一个表项签名
    report = validate_bytes(bytes(data))
    assert "TAG_BAD_SIGNATURE" in codes(report)


def test_tag_duplicate() -> None:
    """同签名重复出现 → TAG_DUPLICATE WARNING（§4.4 规则 13）。"""
    data = bytearray(build_matrix_trc_profile())
    # 将 gTRC 表项的签名改为 rTRC → rTRC 重复
    count = struct.unpack_from(">I", data, 128)[0]
    for i in range(count):
        sig, off, size = struct.unpack_from(">4sII", data, 132 + 12 * i)
        if sig == b"gTRC":
            struct.pack_into(">4s", data, 132 + 12 * i, b"rTRC")
    report = validate_bytes(bytes(data))
    assert "TAG_DUPLICATE" in codes(report)


def test_overlap_tags() -> None:
    """两 tag 区间交叠 → TAG_OVERLAP（§7.1 + §4.4 规则 14）。"""
    report = validate_bytes(mutate(build_matrix_trc_profile(), overlap_tags=True))
    assert "TAG_OVERLAP" in codes(report)


def test_shared_trc_no_overlap_warning() -> None:
    """rTRC/gTRC/bTRC 完全共享区间（允许清单）→ 无 TAG_OVERLAP。"""
    report = validate_bytes(build_matrix_trc_profile())
    assert "TAG_OVERLAP" not in codes(report)


def test_overlap_with_undecodable_tag_is_error() -> None:
    """重叠且一方无法解码 → TAG_OVERLAP ERROR。"""
    data = bytearray(build_matrix_trc_profile())
    # 把 cprt 表项指向 desc 区间，并把 desc payload 改坏为超短 'desc' 类型
    count = struct.unpack_from(">I", data, 128)[0]
    desc_off = desc_size = None
    for i in range(count):
        sig, off, size = struct.unpack_from(">4sII", data, 132 + 12 * i)
        if sig == b"desc":
            desc_off, desc_size = off, size
        if sig == b"cprt":
            struct.pack_into(">II", data, 132 + 12 * i + 4, off, 8)  # cprt 缩为 8 字节（< text 最小 9）
    assert desc_off is not None
    # cprt(8 字节, 类型 'text') 与 desc 区间重叠且 cprt 不可解码
    struct.pack_into(">II", data, 132 + 12 + 4, desc_off, 8)
    report = validate_bytes(bytes(data))
    assert "TAG_TYPE_TRUNCATED" in codes(report)
    overlaps = [i for i in report.issues if i.code == "TAG_OVERLAP"]
    assert overlaps and all(i.severity == Severity.ERROR for i in overlaps)


# ---------------------------------------------------------------------------
# 必需 tag 表（规则 15）
# ---------------------------------------------------------------------------


def test_required_tags_prtr() -> None:
    """prtr 缺 A2B0/B2A0 → REQUIRED_TAG_MISSING ERROR（§7.1）。"""
    report = validate_bytes(build_matrix_trc_profile(device_class=b"prtr"))
    assert "REQUIRED_TAG_MISSING" in codes(report)
    assert report.valid is False
    locations = {issue.location for issue in report.issues if issue.code == "REQUIRED_TAG_MISSING"}
    assert "tag[A2B0]" in locations and "tag[B2A0]" in locations
    assert all(i.severity == Severity.ERROR for i in report.issues if i.code == "REQUIRED_TAG_MISSING")


def test_required_tags_prtr_v4_extras_warning() -> None:
    """v4 prtr 缺 gamt/A2B1/B2A1 → REQUIRED_TAG_MISSING WARNING（v4 更严）。"""
    data = build_matrix_trc_profile(
        version=0x04000000,
        device_class=b"prtr",
        extra_tags={b"A2B0": mab_payload(), b"B2A0": mab_payload(kind=b"mBA ")},
    )
    report = validate_bytes(data)
    required = [i for i in report.issues if i.code == "REQUIRED_TAG_MISSING" and i.severity == Severity.ERROR]
    assert required == []  # A2B0/B2A0 已补齐
    warnings = [i for i in report.issues if i.code == "REQUIRED_TAG_MISSING" and i.severity == Severity.WARNING]
    assert {i.location for i in warnings} == {"tag[gamt]", "tag[A2B1]", "tag[B2A1]"}
    assert report.valid is True  # 仅 WARNING


def test_required_matrix_or_a2b0() -> None:
    """移除 rTRC 后矩阵/TRC 组不完整且无 A2B0 → 专用 code ERROR。"""
    report = validate_bytes(mutate(build_matrix_trc_profile(), remove_tag=b"rTRC"))
    assert "REQUIRED_TAG_MISSING_MATRIX_OR_A2B0" in codes(report)
    assert report.valid is False


def test_required_link_missing_a2b0() -> None:
    """link 类缺 A2B0 → REQUIRED_TAG_MISSING ERROR。"""
    report = validate_bytes(build_matrix_trc_profile(device_class=b"link"))
    assert "REQUIRED_TAG_MISSING" in codes(report)


def test_required_spac_and_abst() -> None:
    """spac 缺 B2A0；abst 缺 A2B0 → ERROR；nmcl 缺 meta → ERROR。"""
    spac = validate_bytes(build_matrix_trc_profile(device_class=b"spac"))
    assert "tag[B2A0]" in {i.location for i in spac.issues if i.code == "REQUIRED_TAG_MISSING"}
    abst = validate_bytes(build_matrix_trc_profile(device_class=b"abst"))
    assert "tag[A2B0]" in {i.location for i in abst.issues if i.code == "REQUIRED_TAG_MISSING"}
    nmcl = validate_bytes(build_matrix_trc_profile(device_class=b"nmcl"))
    assert "tag[meta]" in {i.location for i in nmcl.issues if i.code == "REQUIRED_TAG_MISSING"}


def test_cprt_missing_info() -> None:
    """cprt 缺失 → INFO 级提示。"""
    report = validate_bytes(mutate(build_matrix_trc_profile(), remove_tag=b"cprt"))
    cprt_issues = [i for i in report.issues if i.location == "tag[cprt]"]
    assert cprt_issues and all(i.severity == Severity.INFO for i in cprt_issues)
    assert report.valid is True


# ---------------------------------------------------------------------------
# 版本/类型合规（规则 16-18）与 PCS 组合（规则 19）
# ---------------------------------------------------------------------------


def test_mab_in_v2_warning() -> None:
    """v2 profile 中 tag 使用 mAB 类型 → TAG_VERSION_MISMATCH WARNING（§4.4 规则 16）。"""
    data = build_matrix_trc_profile(extra_tags={b"A2B0": mab_payload()})
    report = validate_bytes(data)
    assert "TAG_VERSION_MISMATCH" in codes(report)
    assert report.valid is True


def test_desc_type_unusual() -> None:
    """v4 用 textDescriptionType / v2 用 mluc 做 desc → DESC_TYPE_UNUSUAL INFO。"""
    v4_report = validate_bytes(build_matrix_trc_profile(version=0x04000000))
    assert "DESC_TYPE_UNUSUAL" in codes(v4_report)
    mluc_payload = mluc_type_payload([("zh", "CN", "合成")])
    v2_data = build_matrix_trc_profile(extra_tags={b"desc": mluc_payload})
    v2_report = validate_bytes(v2_data)
    assert "DESC_TYPE_UNUSUAL" in codes(v2_report)


def test_tag_type_truncated() -> None:
    """tag 数据长度小于该类型最小长度 → TAG_TYPE_TRUNCATED ERROR（§4.4 规则 18）。"""
    data = build_matrix_trc_profile(extra_tags={b"bad1": b"mft2" + b"\x00" * 16})
    report = validate_bytes(data)
    assert "TAG_TYPE_TRUNCATED" in codes(report)
    assert report.valid is False


def test_pcs_combo_unusual() -> None:
    """prtr PCS 改为设备空间 → WARNING；mntr PCS=Lab → INFO。"""
    data = bytearray(
        build_matrix_trc_profile(
            device_class=b"prtr",
            extra_tags={b"A2B0": mab_payload(), b"B2A0": mab_payload(kind=b"mBA ")},
        )
    )
    data[20:24] = b"CMYK"
    report = validate_bytes(bytes(data))
    pcs_issues = [i for i in report.issues if i.code == "PCS_COMBO_UNUSUAL"]
    assert pcs_issues and pcs_issues[0].severity == Severity.WARNING

    lab_data = bytearray(build_matrix_trc_profile())
    lab_data[20:24] = b"Lab "
    lab_report = validate_bytes(bytes(lab_data))
    lab_issues = [i for i in lab_report.issues if i.code == "PCS_COMBO_UNUSUAL"]
    assert lab_issues and all(i.severity == Severity.INFO for i in lab_issues)


# ---------------------------------------------------------------------------
# HDR 检测（hdr_capability + inspect 联动）
# ---------------------------------------------------------------------------


def test_hdr_cicp_pq() -> None:
    """CICP(9,16,9,1) → is_hdr=True、transfer_label='PQ'、primaries='BT.2020'（§7.1）。"""
    info_data = build_matrix_trc_profile(with_cicp=(9, 16, 9, 1))
    header = parse_header(info_data)
    from packstudio.engines.color_engine.icc.tags import parse_tag_table, decode_tag

    tags = {e.signature: decode_tag(info_data, e) for e in parse_tag_table(info_data)}
    from packstudio.engines.color_engine.icc.validate import hdr_capability

    hdr = hdr_capability(tags, header)
    assert hdr.is_hdr is True
    assert hdr.transfer_label == "PQ"
    assert hdr.primaries_label == "BT.2020"
    assert hdr.cicp == {
        "colour_primaries": 9,
        "transfer_characteristics": 16,
        "matrix_coefficients": 9,
        "full_range_flag": 1,
    }
    assert hdr.has_adgc is False and hdr.has_hagc is False


def test_hdr_cicp_hlg() -> None:
    """CICP(9,18,9,0) → is_hdr=True、transfer_label='HLG'。"""
    from packstudio.engines.color_engine.icc.inspect import load_profile

    data = build_matrix_trc_profile(with_cicp=(9, 18, 9, 0))
    hdr = load_profile(data).hdr
    assert hdr.is_hdr is True
    assert hdr.transfer_label == "HLG"
    assert hdr.cicp is not None and hdr.cicp["transfer_characteristics"] == 18


def test_hdr_adgc_hagc() -> None:
    """tc=1（非 HDR 传输函数）+ ADGC → has_adgc=True 且 is_hdr=True（§4.4 规则 4）。"""
    data = build_matrix_trc_profile(
        with_cicp=(9, 1, 9, 1),
        extra_tags={b"ADGC": b"ADGC" + b"\x00" * 8},
    )
    from packstudio.engines.color_engine.icc.inspect import load_profile

    hdr = load_profile(data).hdr
    assert hdr.has_adgc is True
    assert hdr.is_hdr is True  # HDR 元数据即使 tc 非 PQ/HLG
    assert hdr.transfer_label is None
    assert any("Adaptive Gain Curve" in note for note in hdr.notes)

    both = build_matrix_trc_profile(
        extra_tags={b"ADGC": b"ADGC" + b"\x00" * 8, b"HAGC": b"HAGC" + b"\x00" * 8}
    )
    hdr_both = load_profile(both).hdr
    assert hdr_both.has_adgc and hdr_both.has_hagc and hdr_both.is_hdr
    assert any("superseded by HAGC proposal" in note for note in hdr_both.notes)


def test_hdr_malformed_cicp() -> None:
    """cicp 数据 < 12 字节 → malformed note、不判定 HDR、不抛异常（§4.4 规则 6）。"""
    data = build_matrix_trc_profile(extra_tags={b"cicp": b"cicp" + b"\x00\x00"})
    from packstudio.engines.color_engine.icc.inspect import load_profile

    hdr = load_profile(data).hdr
    assert hdr.is_hdr is False
    assert hdr.cicp is None
    assert any("malformed" in note for note in hdr.notes)


def test_hdr_short_cicp_still_parses_fields() -> None:
    """cicp 12≤len<20 → 字段可读 + 非标准长度 note。"""
    data = build_matrix_trc_profile(extra_tags={b"cicp": cicp_type_payload(9, 16, 9, 1)[:16]})
    from packstudio.engines.color_engine.icc.inspect import load_profile

    hdr = load_profile(data).hdr
    assert hdr.is_hdr is True
    assert hdr.transfer_label == "PQ"
    assert hdr.cicp is not None
    assert any("不足标准 20 字节" in note for note in hdr.notes)


def test_hdr_capability_type_contract() -> None:
    """HdrCapability 定义于 validate（§2.5），默认无 tag → is_hdr=False。"""
    data = build_matrix_trc_profile()
    header = parse_header(data)
    from packstudio.engines.color_engine.icc.tags import decode_tag, parse_tag_table
    from packstudio.engines.color_engine.icc.validate import hdr_capability

    tags = {e.signature: decode_tag(data, e) for e in parse_tag_table(data)}
    hdr = hdr_capability(tags, header)
    assert isinstance(hdr, HdrCapability)
    assert hdr.is_hdr is False
    assert hdr.cicp is None
    assert hdr.transfer_label is None
    assert hdr.primaries_label is None
    assert hdr.notes == ()


def test_report_json_contract() -> None:
    """ValidationReport/Issue as_json 字段名契约 + severity 名称序列化。"""
    report = validate_bytes(build_matrix_trc_profile())
    as_json = report.as_json()
    assert set(as_json) == {"valid", "issues", "counts"}
    assert set(as_json["counts"]) == {"error", "warning", "info"}

    bad = validate_bytes(mutate(build_matrix_trc_profile(), flip_magic=True))
    issue_json = bad.issues[0].as_json()
    assert set(issue_json) == {"severity", "code", "message", "location"}
    assert issue_json["severity"] == "ERROR"


def test_issues_sorted_by_severity_then_location() -> None:
    """issues 按 severity 降序、再按 location 排序（§3.3 不变量）。"""
    data = bytearray(build_matrix_trc_profile())
    data[100] = 0x01  # RESERVED_NOT_ZERO WARNING
    struct.pack_into(">I", data, 64, 7)  # BAD_INTENT ERROR
    report = validate_bytes(bytes(data))
    values = [i.severity.value for i in report.issues]
    assert values == sorted(values, reverse=True)


def test_validate_profile_path_equivalence(tmp_path: pathlib.Path) -> None:
    """validate_profile(文件) 与 validate_bytes(同字节) 结果一致。"""
    data = build_matrix_trc_profile(with_cicp=(9, 16, 9, 1))
    target = tmp_path / "synthetic.icc"
    target.write_bytes(data)
    assert validate_profile(str(target)).as_json() == validate_bytes(data).as_json()
