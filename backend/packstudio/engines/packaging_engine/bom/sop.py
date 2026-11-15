"""Deterministic SOP generation from a structure spec."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packstudio.engines.packaging_engine.bom.models import (
    SCHEMA_VERSION,
    SOPStep,
    blank_area_mm2,
)
from packstudio.engines.packaging_engine.models.ir import StructureSpec

PLACEHOLDER_EQUIPMENT = "[待确认：设备型号]"
PLACEHOLDER_PAPER = "[待确认：纸张克重/品牌]"
PLACEHOLDER_INK = "[待确认：油墨/专色]"
PLACEHOLDER_FINISH = "[待确认：印后工艺]"
PLACEHOLDER_DIECUT = "[待确认：模切机/刀版]"
PLACEHOLDER_GLUE = "[待确认：糊盒机/胶水]"
PLACEHOLDER_QC = "[待确认：检测仪器/标准]"


def build_steps(spec: StructureSpec) -> list[SOPStep]:
    """Return the 7-step SOP: 印前 → 排版 → 印刷 → 印后工艺 → 模切 → 糊盒 → 质检."""
    material = spec.material.strip() or PLACEHOLDER_PAPER
    dims = spec.dimensions
    dimensions_text = (
        f"{dims.length:g} × {dims.width:g} × {dims.height:g} mm"
    )
    steps = [
        SOPStep(
            step=1,
            title="印前",
            detail=(
                f"核对刀模与成品尺寸（{dimensions_text}）、纸张（{material}）、"
                f"出血 {spec.bleed:g} mm、安全边距 {spec.safe_margin:g} mm，"
                "完成转曲、分色与印前打样。"
            ),
            equipment=PLACEHOLDER_EQUIPMENT,
            placeholder=spec.material == "",
        ),
        SOPStep(
            step=2,
            title="排版",
            detail=(
                "按内容布局把背标字段放入安全区，确保文字不落在刀线、压痕线或"
                "糊口；条码保留净空区。"
            ),
            equipment=PLACEHOLDER_EQUIPMENT,
            placeholder=True,
        ),
        SOPStep(
            step=3,
            title="印刷",
            detail=(
                f"上机印刷 {dims.length:g}×{dims.width:g} mm 幅面，按 {PLACEHOLDER_INK} 调色，"
                f"控制套准与色差，纸厚补偿 {spec.board_thickness:g} mm。"
            ),
            equipment=PLACEHOLDER_EQUIPMENT,
            placeholder=True,
        ),
        SOPStep(
            step=4,
            title="印后工艺",
            detail=f"实施表面处理（{PLACEHOLDER_FINISH}），注意套准与蹭脏。",
            equipment=PLACEHOLDER_EQUIPMENT,
            placeholder=True,
        ),
        SOPStep(
            step=5,
            title="模切",
            detail=(
                f"安装刀版（{spec.model_code}），模切并去废边；检查压痕深度与"
                "切线一致性。"
            ),
            equipment=PLACEHOLDER_DIECUT,
            placeholder=True,
        ),
        SOPStep(
            step=6,
            title="糊盒",
            detail=(
                f"糊口涂胶宽度约 {spec.glue_width:g} mm，折合压实；"
                "控制胶水开放时间与初粘力。"
            ),
            equipment=PLACEHOLDER_GLUE,
            placeholder=True,
        ),
        SOPStep(
            step=7,
            title="质检",
            detail=(
                "检查尺寸、印刷、成型、条码可读性与标签法规字段；"
                "抽检比例与AQL需人工确认。"
            ),
            equipment=PLACEHOLDER_QC,
            placeholder=True,
        ),
    ]
    return steps


def render_markdown(spec: StructureSpec, steps: list[SOPStep]) -> str:
    lines = [
        "# 标准作业程序（SOP）",
        "",
        f"- 刀模型号：`{spec.model_code}`",
        f"- 成品尺寸（mm）：{spec.dimensions.length:g} × {spec.dimensions.width:g} × {spec.dimensions.height:g}",
        f"- 纸厚（mm）：{spec.board_thickness:g}",
        f"- 出血（mm）：{spec.bleed:g}",
        f"- 安全边距（mm）：{spec.safe_margin:g}",
        f"- 糊口宽度（mm）：{spec.glue_width:g}",
        f"- 纸张：{spec.material.strip() or '[待确认：纸张克重/品牌]'}",
        "",
        "> 以下工步顺序为默认流程；设备、参数与检验标准均为占位符，须由印厂确认。",
        "",
        "## 工步",
        "",
    ]
    for step in steps:
        lines.extend(
            [
                f"### {step.step}. {step.title}",
                "",
                step.detail,
                "",
                f"- 设备：{step.equipment}",
                "",
            ]
        )
    lines.extend(
        [
            "## 设备清单（占位符）",
            "",
            "| 工步 | 设备 | 状态 |",
            "| --- | --- | --- |",
        ]
    )
    for step in steps:
        lines.append(f"| {step.step}. {step.title} | {step.equipment} | 待确认 |")
    lines.extend(
        [
            "",
            "## 人工复核项",
            "",
            "- [ ] 纸张克重、品牌与供应商确认。",
            "- [ ] 油墨、专色与环保认证确认。",
            "- [ ] 表面处理工艺与成本确认。",
            "- [ ] 刀版、模切机与糊盒机参数确认。",
            "- [ ] 质检抽样方案与AQL确认。",
            "",
        ]
    )
    return "\n".join(lines)


def generate_sop(spec: StructureSpec, output_dir: str | Path) -> dict[str, Any]:
    """Generate ``sop.md`` and return the SOP payload."""
    steps = build_steps(spec)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(spec, steps)
    sop_path = target / "sop.md"
    sop_path.write_text(markdown, encoding="utf-8")

    # Deterministic quantities mirrored from the BOM so the review can compare
    # the two documents for spec/qty mismatches.
    area_m2 = round(blank_area_mm2(spec) / 1_000_000.0, 6)
    quantities = {
        "paper": area_m2,
        "ink": area_m2,
        "coating": area_m2,
        "die": 1.0,
        "glue": round(area_m2 * 0.05, 6),
        "labor": 1.0,
        "regulatory": 1.0,
    }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "sop",
        "model_code": spec.model_code,
        "steps": [step.to_dict() for step in steps],
        "quantities": quantities,
        "summary": {
            "step_count": len(steps),
            "placeholder_count": sum(step.placeholder for step in steps),
        },
        "warnings": ["SOP_PLACEHOLDER_NEEDS_REVIEW", "REVIEW_SOP_AND_BOM_UNITS"],
        "manual_review_required": True,
    }
    payload["files"] = {"sop_md": str(sop_path)}
    return payload
