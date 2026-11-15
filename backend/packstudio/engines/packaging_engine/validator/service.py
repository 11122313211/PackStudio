"""Validation service: run the mechanical gate and emit a ValidationReport."""

from __future__ import annotations

from packstudio.engines.packaging_engine.models.ir import Dieline, StructureSpec, ValidationReport
from packstudio.engines.packaging_engine.validator.checks import checks

MANUAL_REVIEW_NOTES = (
    "结构模板为 DESIGN_TEMPLATE，需印厂复核后方可生产",
    "纸厚补偿与模切公差需按实际材料验证",
    "出血与安全区需结合印刷工艺确认",
)


def validate_dieline(dieline: Dieline, spec: StructureSpec | None = None) -> ValidationReport:
    results = checks(dieline, spec)
    failed = [c for c in results if not c["passed"]]
    return ValidationReport(
        passed=not failed,
        checks=results,
        manual_review=list(MANUAL_REVIEW_NOTES) + [f"未通过：{c['id']}" for c in failed],
    )


def summarize(report: ValidationReport) -> dict:
    return {
        "passed": report.passed,
        "total": len(report.checks),
        "passed_count": sum(1 for c in report.checks if c["passed"]),
        "failed": [c["id"] for c in report.checks if not c["passed"]],
    }
