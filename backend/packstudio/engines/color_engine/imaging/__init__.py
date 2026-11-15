"""imaging 包：图像 ICC 处理流水线（TECH-DESIGN §2.5 导出面）。"""
from __future__ import annotations

from packstudio.engines.color_engine.imaging.pipeline import (
    SUPPORTED_READ,
    SUPPORTED_WRITE,
    FileResult,
    ProfileOp,
    assign_profile,
    convert_image,
    dynamic_workers,
    embed_profile,
    get_embedded_profile,
    process_batch,
    process_file,
    read_image,
    strip_profile,
)

__all__ = [
    "ProfileOp",
    "FileResult",
    "read_image",
    "get_embedded_profile",
    "assign_profile",
    "embed_profile",
    "strip_profile",
    "convert_image",
    "process_file",
    "process_batch",
    "dynamic_workers",
    "SUPPORTED_READ",
    "SUPPORTED_WRITE",
]
