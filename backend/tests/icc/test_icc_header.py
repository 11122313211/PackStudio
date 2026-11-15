"""icc/header.py 单元测试（TECH-DESIGN §7.1 测试矩阵）。

覆盖：真实 sRGB profile 的已知 header 字段断言（设计期已核实值）、短数据与
坏魔数的 IccParseError 路径、合成 profile 的日期/版本解析、v5-iccMAX 版本族
判定、保留区暴露、常量契约与 as_json 字段名契约。
"""
from __future__ import annotations

import pathlib
import struct

import pytest

from fixtures.synthetic import SYSTEM_PROFILES, build_matrix_trc_profile, require_system_profile
from packstudio.engines.color_engine.errors import IccParseError
from packstudio.engines.color_engine.icc.header import (
    HEADER_SIZE,
    MAGIC,
    DEVICE_CLASSES,
    PLATFORM_SIGS,
    RENDERING_INTENTS,
    ProfileHeader,
    ProfileVersion,
    detect_version_family,
    parse_header,
)


@pytest.fixture(params=sorted(SYSTEM_PROFILES), ids=sorted(SYSTEM_PROFILES))
def real_profile(request: pytest.FixtureRequest) -> pathlib.Path:
    """真实系统 profile 参数化（不存在 → skip，保证 Linux CI 上整体跳过）。"""
    return require_system_profile(request.param)


def test_parse_srgb_header() -> None:
    """真实 sRGB profile 的已知 header 字段（TECH-DESIGN §4.1【已核实】）。"""
    data = require_system_profile("sRGB").read_bytes()
    header = parse_header(data)
    assert header.size == 3144
    assert header.magic == "acsp"
    assert header.device_class == "mntr"
    assert header.color_space == "RGB "
    assert header.pcs == "XYZ "
    assert (header.version.major, header.version.minor, header.version.bugfix) == (2, 1, 0)
    assert header.version.raw == 0x02100000
    assert header.preferred_cmm == "Lino"
    assert header.platform == "MSFT"
    assert header.manufacturer == "IEC "
    assert header.model == "sRGB"
    assert header.creator == "HP  "
    assert header.date_time == "1998-02-09 06:49:00"
    assert header.rendering_intent == 0
    assert header.illuminant_xyz == pytest.approx((0.964203, 1.0, 0.824905), abs=1e-6)
    assert set(header.profile_id) == {"0"}  # 未计算 MD5 → 全零
    assert header.reserved == bytes(28)


def test_parse_rejects_bad_magic() -> None:
    """129 字节全零 / 128 字节坏魔数 / 长度不足 → IccParseError（code 契约）。"""
    with pytest.raises(IccParseError) as exc_short:
        parse_header(b"\x00" * 127)
    assert exc_short.value.code == "ICC_PARSE"

    with pytest.raises(IccParseError):
        parse_header(b"\x00" * 129)  # 长度够但魔数错

    bad_magic = bytearray(build_matrix_trc_profile()[:HEADER_SIZE])
    bad_magic[36:40] = b"XXXX"
    with pytest.raises(IccParseError) as exc_magic:
        parse_header(bytes(bad_magic))
    assert exc_magic.value.code == "ICC_PARSE"
    assert exc_magic.value.detail["offset"] == 36


def test_parse_synthetic_roundtrip() -> None:
    """合成 profile：日期/版本/厂商字段按 §7.2 布局往返。"""
    data = build_matrix_trc_profile()
    header = parse_header(data)
    assert header.size == len(data)
    assert header.date_time == "2020-01-01 00:00:00"
    assert (header.version.major, header.version.minor, header.version.bugfix) == (2, 1, 0)
    assert header.device_class == "mntr"
    assert header.manufacturer == "TKIT"
    assert header.model == "TST1"
    assert header.preferred_cmm == "\0\0\0\0"
    assert header.illuminant_xyz == pytest.approx((0.964203, 1.0, 0.824905), abs=1e-5)
    assert header.attributes == (0, 0)
    assert header.flags == 0


def test_parse_date_all_zero_does_not_raise() -> None:
    """date_time 全 0 仅表现为零值字符串，不抛异常（§4.1）。"""
    data = bytearray(build_matrix_trc_profile())
    struct.pack_into(">6H", data, 24, 0, 0, 0, 0, 0, 0)
    header = parse_header(bytes(data))
    assert header.date_time == "0000-00-00 00:00:00"


def test_reserved_bytes_exposed() -> None:
    """保留区非零时原样暴露（校验判定属 validate，不抛异常）。"""
    data = bytearray(build_matrix_trc_profile())
    data[100] = 0xFF
    header = parse_header(bytes(data))
    assert header.reserved[0] == 0xFF


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0x05000000, "v5-iccMAX"),
        (0x04000000, "v4"),
        (0x04300000, "v4"),
        (0x02100000, "v2"),
        (0x02000000, "v2"),
        (0x00000000, "v2"),
    ],
)
def test_v5_family_detection(raw: int, expected: str) -> None:
    """版本族判定（TECH-DESIGN §7.1：raw=0x05000000 → v5-iccMAX）。"""
    version = ProfileVersion(
        major=(raw >> 24) & 0xFF,
        minor=(raw >> 20) & 0x0F,
        bugfix=(raw >> 16) & 0x0F,
        raw=raw,
    )
    assert detect_version_family(version) == expected


def test_constants_contract() -> None:
    """模块级常量契约（§4.1）。"""
    assert HEADER_SIZE == 128
    assert MAGIC == b"acsp"
    assert "mntr" in DEVICE_CLASSES and "prtr" in DEVICE_CLASSES
    assert "MSFT" in PLATFORM_SIGS
    assert set(RENDERING_INTENTS) == {"0", "1", "2", "3"}


def test_header_json_field_names() -> None:
    """ProfileHeader/ProfileVersion 的 as_json 字段名 = dataclass 字段名（§2.3）。"""
    header = parse_header(build_matrix_trc_profile())
    assert set(header.as_json()) == set(ProfileHeader.__dataclass_fields__)
    assert set(header.as_json()["version"]) == set(ProfileVersion.__dataclass_fields__)


def test_real_profiles_header_size_matches_file(real_profile: pathlib.Path) -> None:
    """真实 profile：header.size 与实际文件长度一致且魔数正确。"""
    data = real_profile.read_bytes()
    header = parse_header(data)
    assert header.magic == "acsp"
    assert header.size == len(data)
    assert detect_version_family(header.version) in {"v2", "v4", "v5-iccMAX"}
