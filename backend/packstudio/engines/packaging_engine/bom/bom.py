"""Deterministic BOM generation from a structure spec and content brief."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from packstudio.engines.packaging_engine.bom.models import SCHEMA_VERSION, BOMItem, blank_area_mm2
from packstudio.engines.packaging_engine.content.models import ContentBrief
from packstudio.engines.packaging_engine.models.ir import StructureSpec

PLACEHOLDER_BOARD = "[待确认：纸张品牌/克重/涂布]"
PLACEHOLDER_INK = "[待确认：油墨颜色与环保标准]"
PLACEHOLDER_COATING = "[待确认：表面处理工艺]"
PLACEHOLDER_DIE = "[待确认：模切刀版尺寸与供应商]"
PLACEHOLDER_GLUE = "[待确认：胶水型号]"
PLACEHOLDER_LABOR = "[待确认：人工单价与工时]"

MM2_PER_M2 = 1_000_000.0


def _num(value: float) -> float:
    return round(float(value), 6)


def build_items(spec: StructureSpec, brief: ContentBrief) -> list[BOMItem]:
    """Return the BOM lines: real estimates plus explicit placeholders."""
    area_m2 = _num(blank_area_mm2(spec) / MM2_PER_M2)
    board_spec = spec.material.strip() if spec.material else PLACEHOLDER_BOARD
    board_source = "estimate" if spec.material else "placeholder"

    items: list[BOMItem] = [
        BOMItem(
            name="纸板/卡纸",
            spec=board_spec,
            qty=_num(area_m2),
            unit="m²",
            loss_rate=0.03,
            notes="按展开空白面积估算，未含修边与拼版损耗；克重需人工确认。",
            source=board_source,
            category="material",
        ),
        BOMItem(
            name="印刷油墨",
            spec=PLACEHOLDER_INK,
            qty=_num(area_m2),
            unit="m²",
            loss_rate=0.02,
            notes="按印刷面积估算；颜色数、专色与环保认证需人工确认。",
            source="placeholder",
            category="consumable",
        ),
        BOMItem(
            name="表面处理/涂层",
            spec=PLACEHOLDER_COATING,
            qty=_num(area_m2),
            unit="m²",
            loss_rate=0.03,
            notes="覆膜/上光/UV 等工艺需人工确认，未含烫金面积。",
            source="placeholder",
            category="consumable",
        ),
        BOMItem(
            name="模切刀版",
            spec=PLACEHOLDER_DIE,
            qty=1.0,
            unit="套",
            loss_rate=0.0,
            notes="刀版为一次性工具，寿命与复用次数需人工确认。",
            source="placeholder",
            category="tooling",
        ),
        BOMItem(
            name="糊盒胶水",
            spec=PLACEHOLDER_GLUE,
            qty=_num(area_m2 * 0.05),
            unit="kg",
            loss_rate=0.05,
            notes="按糊口长度与面积估算；胶水型号需人工确认。",
            source="placeholder",
            category="consumable",
        ),
        BOMItem(
            name="人工工时",
            spec=PLACEHOLDER_LABOR,
            qty=1.0,
            unit="工时",
            loss_rate=0.0,
            notes="按单件工序估算；实际工时与单价需人工确认。",
            source="placeholder",
            category="labor",
        ),
    ]

    # Fold in user-provided packaging content so the BOM can flag legal fields.
    missing_required = [item.key for item in brief.missing_required]
    if missing_required:
        items.append(
            BOMItem(
                name="合规/法规信息",
                spec=f"[待确认：{'、'.join(missing_required)}]",
                qty=1.0,
                unit="项",
                loss_rate=0.0,
                notes="缺失的必填标签信息须在印前补齐，不能由系统推测。",
                source="placeholder",
                category="placeholder",
            )
        )
    return items


def _write_csv(path: Path, items: list[BOMItem]) -> None:
    header = [
        "name",
        "spec",
        "qty",
        "unit",
        "loss_rate",
        "effective_qty",
        "notes",
        "source",
        "category",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for item in items:
            writer.writerow(
                [
                    item.name,
                    item.spec,
                    item.qty,
                    item.unit,
                    item.loss_rate,
                    item.effective_qty(),
                    item.notes,
                    item.source,
                    item.category,
                ]
            )


def generate_bom(
    spec: StructureSpec,
    brief: ContentBrief,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Generate ``bom.csv`` (UTF-8) and ``bom.json`` and return the payload."""
    items = build_items(spec, brief)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    csv_path = target / "bom.csv"
    json_path = target / "bom.json"
    _write_csv(csv_path, items)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "bom",
        "model_code": spec.model_code,
        "material": spec.material,
        "dimensions_mm": {
            "length": spec.dimensions.length,
            "width": spec.dimensions.width,
            "height": spec.dimensions.height,
        },
        "board_thickness_mm": spec.board_thickness,
        "blank_area_mm2": _num(blank_area_mm2(spec)),
        "items": [item.to_dict() for item in items],
        "summary": {
            "item_count": len(items),
            "placeholder_count": sum(item.source == "placeholder" for item in items),
            "estimated_count": sum(item.source == "estimate" for item in items),
        },
        "warnings": [
            "BOM_ESTIMATE",
            "BOM_PLACEHOLDER_NEEDS_REVIEW",
        ],
        "manual_review_required": True,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    payload["files"] = {"bom_csv": str(csv_path), "bom_json": str(json_path)}
    return payload
