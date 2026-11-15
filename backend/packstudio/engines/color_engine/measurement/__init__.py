"""measurement 包：CGATS.17 解析/写出与测量验证（ΔE00 统计、ISO 判定）。

导出面按 TECH-DESIGN §2.5；``summarize`` / ``ISO12647_2_TOLERANCES`` /
``overall_verdict`` / ``ReferencePatch`` 为设计 §4.9 与 §6.1 定义/引用的
附加公开项（additive，未在 §2.5 清单中但服务于 CLI/WebUI 集成）。
"""
from __future__ import annotations

from packstudio.engines.color_engine.measurement.cgats import CgatsData, parse_cgats, write_cgats, cgats_from_lab
from packstudio.engines.color_engine.measurement.verify import (
    REFERENCE_TABLES,
    ISO12647_2_TOLERANCES,
    ReferencePatch,
    ReferenceSet,
    PatchVerdict,
    DeltaEStats,
    VerifyResult,
    IsoVerdict,
    delta_e_00,
    verify_cgats,
    verify_reference,
    reference_table,
    summarize,
    overall_verdict,
    verify_report_to_cgats,
    channel_diagnostics,
)

__all__ = [
    "CgatsData",
    "parse_cgats",
    "write_cgats",
    "cgats_from_lab",
    "ReferenceSet",
    "ReferencePatch",
    "PatchVerdict",
    "DeltaEStats",
    "VerifyResult",
    "IsoVerdict",
    "delta_e_00",
    "verify_cgats",
    "verify_reference",
    "reference_table",
    "REFERENCE_TABLES",
    "ISO12647_2_TOLERANCES",
    "summarize",
    "overall_verdict",
    "verify_report_to_cgats",
    "channel_diagnostics",
]
