"""ICC 纯 struct 二进制解析层（零第三方依赖，仅标准库 + ``packstudio.engines.color_engine.errors``）。

公开导出面为 TECH-DESIGN §2.5 冻结契约（字段名/签名不得改名）：

- :mod:`header`：128 字节头部解析与版本族判定；
- :mod:`tags`：tag 表解析与常用 tag 类型解码；
- :mod:`inspect`：统一入口，产出 :class:`ProfileInfo`；
- :mod:`validate`：结构校验（:class:`ValidationReport`）与 HDR 检测。

``HdrCapability`` 定义在 validate.py（检测规则所在处），inspect.py 引用。
"""
from __future__ import annotations

from packstudio.engines.color_engine.icc.header import (
    COLOR_SPACES,
    DEVICE_CLASSES,
    HEADER_SIZE,
    MAGIC,
    PCS_SPACES,
    RENDERING_INTENTS,
    ProfileHeader,
    ProfileVersion,
    detect_version_family,
    parse_header,
)
from packstudio.engines.color_engine.icc.inspect import LutDetection, ProfileInfo, detect_luts, inspect_file, load_profile
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
from packstudio.engines.color_engine.icc.validate import (
    HdrCapability,
    Severity,
    ValidationIssue,
    ValidationReport,
    hdr_capability,
    validate_bytes,
    validate_profile,
)

__all__ = [
    # header
    "HEADER_SIZE",
    "MAGIC",
    "DEVICE_CLASSES",
    "COLOR_SPACES",
    "PCS_SPACES",
    "RENDERING_INTENTS",
    "ProfileHeader",
    "ProfileVersion",
    "parse_header",
    "detect_version_family",
    # tags
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
    "parse_tag_table",
    "decode_tag",
    "get_profile_description",
    # inspect
    "ProfileInfo",
    "load_profile",
    "inspect_file",
    "LutDetection",
    "detect_luts",
    # validate
    "Severity",
    "ValidationIssue",
    "ValidationReport",
    "validate_profile",
    "validate_bytes",
    "hdr_capability",
    "HdrCapability",
]
