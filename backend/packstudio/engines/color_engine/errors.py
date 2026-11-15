# 本文件由开发 subagent C 依据 TECH-DESIGN.md §2.1 逐字转录创建（2026-11-07）。
# 背景：errors.py 属共享基础设施，未划入任何 subagent 的独占文件清单，
# 但 measurement/ 模块 import 时即依赖此异常层级，缺少它无法运行任何测试。
# 内容为设计文档 §2.1 的机械转录；如模块属主后续提交正式版本，以其为准。
"""公共异常层级（TECH-DESIGN §2.1）。

零第三方依赖，供所有层使用。约定：
- ``code`` 为稳定的机器码，CLI ``--json`` 与 Web API 错误响应原样输出，
  契约稳定不得改名。
- ``detail`` 为可选结构化上下文 dict（如 ``{"path": ..., "offset": 123}``）。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "IccToolkitError",
    "IccParseError",
    "CmmError",
    "ProfileLoadError",
    "IntentUnsupportedError",
    "ImagingError",
    "UnsupportedFormatError",
    "ProfileMismatchError",
    "MeasurementError",
    "CgatsParseError",
    "PdfAuditError",
    "VizError",
]


class IccToolkitError(Exception):
    """所有工具异常的基类。

    Attributes:
        code: 稳定的机器码（如 "ICC_PARSE", "PROFILE_NOT_FOUND"），CLI --json 与
              Web API 错误响应均原样输出此 code，契约稳定不得改名。
        detail: 可选结构化上下文 dict（如 {"path": ..., "offset": 123}）。
    """

    code: str = "ICC_TOOLKIT_ERROR"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = detail if detail is not None else {}


# ---- icc 层 ----
class IccParseError(IccToolkitError):
    """profile 结构无法解析：尺寸不足 128、魔数非 acsp、tag 表越界读不到等。"""

    code = "ICC_PARSE"


# ---- cmm 层 ----
class CmmError(IccToolkitError):
    """LittleCMS 调用失败；PIL.PyCMSError 一律包装为本异常（保留 __cause__）。"""

    code = "CMM"


class ProfileLoadError(CmmError):
    """profile 路径不存在/损坏。"""

    code = "PROFILE_LOAD"


class IntentUnsupportedError(CmmError):
    """请求的 rendering intent 在该 profile 的指定方向上不受支持。"""

    code = "INTENT_UNSUPPORTED"


# ---- imaging 层 ----
class ImagingError(IccToolkitError):
    """图像读写/像素处理失败。"""

    code = "IMAGING"


class UnsupportedFormatError(ImagingError):
    """不支持的图像格式（含 PSD 写出）。"""

    code = "UNSUPPORTED_FORMAT"


class ProfileMismatchError(ImagingError):
    """profile 色空间与图像模式不匹配（如 CMYK profile 配 RGB 图且无中间转换授权）。"""

    code = "PROFILE_MISMATCH"


# ---- measurement 层 ----
class MeasurementError(IccToolkitError):
    """测量验证层通用错误（参考表缺失、列缺失、Lab 形状非法等）。"""

    code = "MEASUREMENT"


class CgatsParseError(MeasurementError):
    """CGATS.17 文本无法解析（缺 END_DATA、列数不一致、数值解析失败、0 行等）。"""

    code = "CGATS_PARSE"


# ---- pdfaudit 层 ----
class PdfAuditError(IccToolkitError):
    """PDF 审计失败（打不开、加密且无口令）。"""

    code = "PDF_AUDIT"


# ---- viz 层 ----
class VizError(IccToolkitError):
    """可视化层错误（采样/绘图失败、色空间不匹配等）。"""

    code = "VIZ"
