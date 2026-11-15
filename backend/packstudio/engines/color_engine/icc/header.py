"""ICC profile 头部（128 字节）解析。

TECH-DESIGN §3.1 / §4.1 的实现：

- ``ProfileVersion`` / ``ProfileHeader`` 数据模型（字段名即 JSON 契约）；
- ``parse_header``：128 字节头的纯 struct 解析，魔数 ``acsp`` 校验；
- ``detect_version_family``：v2 / v4 / v5-iccMAX 版本族判定；
- ``DEVICE_CLASSES`` 等模块级对照表，供 CLI/WebUI 人读输出使用。

零第三方依赖（仅标准库）。未知枚举值（如设备类 ``abst ``）原样保留，
不映射为 "unknown" 丢弃（TECH-DESIGN §2.3 规则 4）。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Final, Literal

from packstudio.engines.color_engine.errors import IccParseError

__all__ = [
    "HEADER_SIZE",
    "MAGIC",
    "DEVICE_CLASSES",
    "COLOR_SPACES",
    "PCS_SPACES",
    "PLATFORM_SIGS",
    "RENDERING_INTENTS",
    "ProfileVersion",
    "ProfileHeader",
    "parse_header",
    "detect_version_family",
]

#: ICC header 固定长度（字节）。
HEADER_SIZE: Final[int] = 128

#: 偏移 36-39 的固定魔数。
MAGIC: Final[bytes] = b"acsp"

#: 设备类签名 → 人读名称（未知签名原样保留）。
DEVICE_CLASSES: Final[dict[str, str]] = {
    "scnr": "输入设备（扫描仪/相机）",
    "mntr": "显示设备",
    "prtr": "输出设备（打印机）",
    "link": "设备链接 profile",
    "spac": "色彩空间转换 profile",
    "abst": "抽象 profile",
    "nmcl": "命名色彩 profile",
    "ictr": "输入设备（ICC.1 早期别名）",
    "nm": "命名色彩 profile（别名）",
}

#: 数据色彩空间签名 → 人读名称。
COLOR_SPACES: Final[dict[str, str]] = {
    "XYZ ": "CIEXYZ",
    "Lab ": "CIELAB",
    "Luv ": "CIELUV",
    "Yxy ": "CIEYxy",
    "YCbr": "YCbCr",
    "RGB ": "RGB",
    "GRAY": "Gray",
    "HSV ": "HSV",
    "HLS ": "HLS",
    "CMYK": "CMYK",
    "CMY ": "CMY",
    "nMCL": "命名色彩",
}

#: PCS（profile connection space）签名 → 人读名称。
PCS_SPACES: Final[dict[str, str]] = {
    "XYZ ": "CIEXYZ (D50)",
    "Lab ": "CIELAB (D50)",
}

#: 平台签名 → 人读名称。
PLATFORM_SIGS: Final[dict[str, str]] = {
    "APPL": "Apple Computer",
    "MSFT": "Microsoft",
    "SGI ": "Silicon Graphics",
    "SUNW": "Sun Microsystems",
    "\0\0\0\0": "（未指定）",
}

#: rendering intent 编号（字符串键）→ 人读名称。
RENDERING_INTENTS: Final[dict[str, str]] = {
    "0": "感性（Perceptual）",
    "1": "相对比色（Relative Colorimetric）",
    "2": "饱和度（Saturation）",
    "3": "绝对比色（Absolute Colorimetric）",
}


def _sig_str(raw: bytes) -> str:
    """4 字节签名字段转字符串。

    按 §3.1 约定"未知值原样保留"：使用 latin-1 逐字节 1:1 映射，
    不剥离空白/NUL（``'RGB '``、``'IEC '``、``'\\0\\0\\0\\0'`` 均按原样呈现）。
    """
    return raw.decode("latin-1")


def _s15_fixed16(value: int) -> float:
    """s15Fixed16 定点数 → float（TECH-DESIGN §4.1：round(v / 65536, 6)）。"""
    return round(value / 65536.0, 6)


@dataclass(frozen=True, slots=True)
class ProfileVersion:
    """ICC 版本号（原始 uint32 与三元组并存）。

    Attributes:
        major:  主版本 0-255（raw 的最高字节）。
        minor:  次版本 0-15（BCD 半字节，来自 ``raw >> 20 & 0xF``）。
        bugfix: 修订号 0-15（BCD 半字节，来自 ``raw >> 16 & 0xF``）。
        raw:    原始 uint32：[31:24]=major [23:20]=minor [19:16]=bugfix [15:0] 保留 0。
    """

    major: int
    minor: int
    bugfix: int
    raw: int

    def as_json(self) -> dict[str, Any]:
        """JSON 契约（字段名 = dataclass 字段名，TECH-DESIGN §2.3）。"""
        return {"major": self.major, "minor": self.minor, "bugfix": self.bugfix, "raw": self.raw}


@dataclass(frozen=True, slots=True)
class ProfileHeader:
    """ICC profile 头部 128 字节的全字段解析结果。

    Attributes:
        size:              字节 0-3，声明的文件总长。
        preferred_cmm:     字节 4-7，首选 CMM 4 字符签名，未知值原样保留。
        version:           字节 8-11，见 :class:`ProfileVersion`。
        device_class:      字节 12-15，如 ``'mntr'`` / ``'prtr'``。
        color_space:       字节 16-19，如 ``'RGB '`` / ``'CMYK'``。
        pcs:               字节 20-23，``'XYZ '`` 或 ``'Lab '``。
        date_time:         字节 24-35，6×uint16 → ``"YYYY-MM-DD HH:MM:SS"`` 字符串。
        magic:             字节 36-39，固定 ``'acsp'``。
        platform:          字节 40-43，如 ``'MSFT'`` / ``'\\0\\0\\0\\0'``。
        flags:             字节 44-47，bit0 embedded、bit1 use-anywhere。
        manufacturer:      字节 48-51，4 字符签名原样保留。
        model:             字节 52-55，4 字符签名。
        attributes:        字节 56-63，两个 uint32（reflective/glossy 等位）。
        rendering_intent:  字节 64-67，0-3。
        illuminant_xyz:    字节 68-79，s15Fixed16 → float 三元组（标准为 D50）。
        creator:           字节 80-83，4 字符签名。
        profile_id:        字节 84-99，16 字节 hex；全零表示未计算 MD5。
        reserved:          字节 100-127，必须全 0；非零由 validate 判 WARNING。
    """

    size: int
    preferred_cmm: str
    version: ProfileVersion
    device_class: str
    color_space: str
    pcs: str
    date_time: str
    magic: str
    platform: str
    flags: int
    manufacturer: str
    model: str
    attributes: tuple[int, int]
    rendering_intent: int
    illuminant_xyz: tuple[float, float, float]
    creator: str
    profile_id: str
    reserved: bytes

    def as_json(self) -> dict[str, Any]:
        """JSON 契约（字段名 = dataclass 字段名；bytes → hex 字符串）。"""
        return {
            "size": self.size,
            "preferred_cmm": self.preferred_cmm,
            "version": self.version.as_json(),
            "device_class": self.device_class,
            "color_space": self.color_space,
            "pcs": self.pcs,
            "date_time": self.date_time,
            "magic": self.magic,
            "platform": self.platform,
            "flags": self.flags,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "attributes": list(self.attributes),
            "rendering_intent": self.rendering_intent,
            "illuminant_xyz": list(self.illuminant_xyz),
            "creator": self.creator,
            "profile_id": self.profile_id,
            "reserved": self.reserved.hex(),
        }


def parse_header(data: bytes) -> ProfileHeader:
    """解析 ICC profile 头部前 128 字节。

    行为（TECH-DESIGN §4.1）：校验 ``len(data) >= 128`` 与 ``data[36:40] == b"acsp"``，
    然后按 §3.1 布局做纯 struct 解析。s15Fixed16 用 ``round(v / 65536, 6)`` 转换；
    未知枚举签名原样保留。date_time 全 0 不抛异常（仅表现为 ``0000-00-00 00:00:00``）。

    Args:
        data: profile 字节（至少前 128 字节有效）。

    Returns:
        填充完毕的 :class:`ProfileHeader`。

    Raises:
        IccParseError: 数据不足 128 字节，或偏移 36 处魔数不是 ``acsp``。
    """
    if len(data) < HEADER_SIZE:
        raise IccParseError(
            f"数据长度 {len(data)} 字节，不足以容纳 {HEADER_SIZE} 字节的 ICC header",
            detail={"offset": 0, "expected": HEADER_SIZE, "actual": len(data)},
        )
    if data[36:40] != MAGIC:
        raise IccParseError(
            "不是 ICC profile：偏移 36 处魔数不是 'acsp'",
            detail={"offset": 36, "actual": data[36:40].hex()},
        )

    (size,) = struct.unpack_from(">I", data, 0)
    (version_raw,) = struct.unpack_from(">I", data, 8)
    version = ProfileVersion(
        major=(version_raw >> 24) & 0xFF,
        minor=(version_raw >> 20) & 0x0F,
        bugfix=(version_raw >> 16) & 0x0F,
        raw=version_raw,
    )
    year, month, day, hour, minute, second = struct.unpack_from(">6H", data, 24)
    (flags,) = struct.unpack_from(">I", data, 44)
    attr0, attr1 = struct.unpack_from(">II", data, 56)
    (rendering_intent,) = struct.unpack_from(">I", data, 64)
    ix, iy, iz = struct.unpack_from(">iii", data, 68)

    return ProfileHeader(
        size=size,
        preferred_cmm=_sig_str(data[4:8]),
        version=version,
        device_class=_sig_str(data[12:16]),
        color_space=_sig_str(data[16:20]),
        pcs=_sig_str(data[20:24]),
        date_time=f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}",
        magic=data[36:40].decode("ascii"),
        platform=_sig_str(data[40:44]),
        flags=flags,
        manufacturer=_sig_str(data[48:52]),
        model=_sig_str(data[52:56]),
        attributes=(attr0, attr1),
        rendering_intent=rendering_intent,
        illuminant_xyz=(_s15_fixed16(ix), _s15_fixed16(iy), _s15_fixed16(iz)),
        creator=_sig_str(data[80:84]),
        profile_id=data[84:100].hex(),
        reserved=bytes(data[100:128]),
    )


def detect_version_family(version: ProfileVersion) -> Literal["v2", "v4", "v5-iccMAX"]:
    """判定 profile 版本族。

    行为（TECH-DESIGN §4.1）：major==5（或 raw 高字节为 5）→ ``'v5-iccMAX'``（仅打
    标签，不深解析）；major==4 → ``'v4'``；major≤3 → ``'v2'``。major>5 同样归入
    v5 族桶（validate 会另行以 VERSION_UNKNOWN 提示版本号非法）。本函数不抛异常。
    """
    major = (version.raw >> 24) & 0xFF
    if major >= 5 or (major == 0 and (version.raw >> 24) == 5):
        return "v5-iccMAX"
    if major == 4:
        return "v4"
    return "v2"
