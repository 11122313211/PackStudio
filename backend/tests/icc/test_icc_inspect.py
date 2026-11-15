"""icc/inspect.py 单元测试（TECH-DESIGN §7.1 测试矩阵）。

覆盖：真实 AdobeRGB1998（560 字节 / 10 tag / 描述含 "Adobe RGB"）、真实 sRGB
汇总字段、bytes/路径/文件对象三来源、v5-iccMAX 最小处理、损坏 tag 降级、
summarize/as_json 契约与 JSON 可序列化、IO 错误码，以及真实 profile 参数化
的完整 load 链路。
"""
from __future__ import annotations

import io
import json
import pathlib

import pytest

from fixtures.synthetic import SYSTEM_PROFILES, build_matrix_trc_profile, require_system_profile
from packstudio.engines.color_engine.errors import IccParseError, IccToolkitError
from packstudio.engines.color_engine.icc.inspect import ProfileInfo, inspect_file, load_profile, summarize
from packstudio.engines.color_engine.icc.tags import DescTag, Lut16Tag, RawTag
from packstudio.engines.color_engine.icc.validate import HdrCapability


@pytest.fixture(params=sorted(SYSTEM_PROFILES), ids=sorted(SYSTEM_PROFILES))
def real_profile(request: pytest.FixtureRequest) -> pathlib.Path:
    """真实系统 profile 参数化（不存在 → skip）。"""
    return require_system_profile(request.param)


def test_load_adobe_rgb() -> None:
    """真实 AdobeRGB1998：560 字节、10 tag、描述含 'Adobe RGB'（§7.1）。"""
    path = require_system_profile("AdobeRGB1998")
    info = inspect_file(path)
    assert isinstance(info, ProfileInfo)
    assert info.file_size == 560
    assert len(info.tag_table) == 10
    assert "Adobe RGB" in info.description
    assert info.version_family == "v2"
    assert info.path == str(path)
    assert info.header.size == 560


def test_load_srgb_summary() -> None:
    """真实 sRGB：desc 解码为 DescTag、HDR=False、描述与 header 一致。"""
    info = inspect_file(require_system_profile("sRGB"))
    assert info.description == "sRGB IEC61966-2.1"
    assert isinstance(info.tag_values["desc"], DescTag)
    assert info.hdr.is_hdr is False
    assert info.hdr.cicp is None
    assert info.version_family == "v2"
    assert info.header.device_class == "mntr"
    assert info.file_size == 3144
    assert len(info.tag_table) == 17


def test_load_from_bytes_path_and_fileobj(tmp_path: pathlib.Path) -> None:
    """三种来源：bytes（path=None）/ Path / BinaryIO。"""
    data = build_matrix_trc_profile(with_cicp=(9, 16, 9, 1))

    from_bytes = load_profile(data)
    assert from_bytes.path is None
    assert from_bytes.file_size == len(data)
    assert from_bytes.hdr.is_hdr is True

    target = tmp_path / "synthetic.icc"
    target.write_bytes(data)
    from_path = load_profile(target)
    assert from_path.path == str(target)
    assert from_path.as_json() == inspect_file(target).as_json()

    from_obj = load_profile(io.BytesIO(data))
    assert from_obj.path is None
    assert from_obj.as_json() == from_bytes.as_json()


def test_load_v5_minimal() -> None:
    """v5-iccMAX：仅识别（header/hdr 填充），tag_values 为空 dict（§4.3）。

    说明：description 仍按 §4.2.1 的 get_profile_description（desc→mluc→model）
    提取——该函数不做版本门控，设计行为链中它无条件执行。
    """
    data = build_matrix_trc_profile(version=0x05000000)
    info = load_profile(data)
    assert info.version_family == "v5-iccMAX"
    assert info.header.version.major == 5
    assert info.tag_values == {}
    assert len(info.tag_table) == 9  # tag 表布局与 v2 相同，仍解析
    assert info.description == "Synthetic Test RGB"  # desc 提取不受 v5 门控
    assert info.hdr.is_hdr is False


def test_corrupt_tag_degrades_to_raw() -> None:
    """损坏 tag 降级为 RawTag，整体 inspect 不抛异常。"""
    data = build_matrix_trc_profile(
        extra_tags={
            b"bad1": b"mft2" + b"\x00" * 16,  # mft2 截断
            b"zzzz": b"zzzz" + b"\x00" * 4,  # 未知类型
        }
    )
    info = load_profile(data)
    assert isinstance(info.tag_values["bad1"], RawTag)
    assert info.tag_values["bad1"].type_sig == "mft2"
    assert isinstance(info.tag_values["zzzz"], RawTag)
    assert info.tag_values["desc"].ascii_text == "Synthetic Test RGB"


def test_mft2_tag_decoded_from_real_profile(tmp_path: pathlib.Path) -> None:
    """注入合法 mft2 的合成体：tag_values 给出 Lut16Tag 结构字段。"""
    from fixtures.synthetic import mft2_payload

    data = build_matrix_trc_profile(extra_tags={b"A2B1": mft2_payload()})
    info = load_profile(data)
    value = info.tag_values["A2B1"]
    assert isinstance(value, Lut16Tag)
    assert (value.input_channels, value.output_channels, value.clut_points) == (3, 3, 9)
    assert value.input_entries == 256 and value.output_entries == 256


def test_summarize_json_contract() -> None:
    """summarize == as_json；字段名 = ProfileInfo 字段名；JSON 可直接序列化。"""
    info = inspect_file(require_system_profile("sRGB"))
    payload = summarize(info)
    assert payload is not info.as_json()
    assert payload == info.as_json()
    assert set(payload) == set(ProfileInfo.__dataclass_fields__)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    assert isinstance(text, str)
    assert payload["description"] == "sRGB IEC61966-2.1"
    assert payload["version_family"] == "v2"
    # 日期为字符串（§2.3 规则 2：无日期对象）
    assert isinstance(payload["header"]["date_time"], str)


def test_hdr_field_in_json() -> None:
    """hdr 嵌套对象走 HdrCapability.as_json，字段名契约稳定。"""
    data = build_matrix_trc_profile(with_cicp=(9, 16, 9, 1))
    payload = summarize(load_profile(data))
    assert set(payload["hdr"]) == set(HdrCapability.__dataclass_fields__)
    assert payload["hdr"]["is_hdr"] is True
    assert payload["hdr"]["transfer_label"] == "PQ"


def test_io_error_code() -> None:
    """不存在的路径 → IccToolkitError（code='IO'，detail 含 path），非 IccParseError。"""
    missing = r"E:\作品\ICC工具集\__no_such_profile__.icc"
    with pytest.raises(IccToolkitError) as exc_info:
        load_profile(missing)
    assert not isinstance(exc_info.value, IccParseError)
    assert exc_info.value.code == "IO"
    assert "path" in exc_info.value.detail


def test_unsupported_source_type() -> None:
    """非法来源类型 → IccToolkitError(code='IO')。"""
    with pytest.raises(IccToolkitError) as exc_info:
        load_profile(12345)  # type: ignore[arg-type]
    assert exc_info.value.code == "IO"


def test_short_data_raises_parse_error() -> None:
    """不足 128 字节 → IccParseError（load_profile 链路）。"""
    with pytest.raises(IccParseError):
        load_profile(b"\x00" * 64)


def test_real_profiles_inspect(real_profile: pathlib.Path) -> None:
    """真实 profile：完整 load 链路（header/tags/desc/hdr/family）健全。"""
    info = inspect_file(real_profile)
    assert info.header.magic == "acsp"
    assert info.header.size == info.file_size
    assert info.version_family in {"v2", "v4", "v5-iccMAX"}
    assert info.description and info.description != ""
    assert len(info.tag_table) > 0
    assert isinstance(info.hdr, HdrCapability)
    assert set(info.tag_values) == {entry.signature for entry in info.tag_table}
    for value in info.tag_values.values():
        assert hasattr(value, "as_json")  # §3.2 契约：所有 tag 值可 JSON 化
