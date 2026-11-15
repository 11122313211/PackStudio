"""Data models for the BOM / SOP module.

Kept stdlib-only and deterministic. Quantities are estimates derived from the
structure spec; every estimated value is flagged so a human reviewer can
override it before ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

MM2_PER_M2 = 1_000_000.0


def blank_area_mm2(spec: Any) -> float:
    """Unfolded blank area, measured from the engine-rendered dieline bounds.

    单一事实来源：直接渲染刀版并量外接矩形，保证与导出几何一致。仅当
    ``model_code`` 未实现（无法渲染）时退回名义公式估算，并把数量级偏差
    留给人工复核（BOM 输出本就要求 review）。
    """
    from packstudio.engines.packaging_engine.engine.render import render
    from packstudio.engines.packaging_engine.exceptions import StructureNotImplementedError

    try:
        dieline = render(spec)
    except StructureNotImplementedError:
        dims = spec.dimensions
        blank_w = 2.0 * (dims.length + dims.width) + max(spec.glue_width, 0.0)
        blank_h = 2.0 * dims.height + max(spec.tuck_height, 0.0)
        return max(blank_w * blank_h, 0.0)
    min_x, min_y, max_x, max_y = dieline.bounds
    return max((max_x - min_x) * (max_y - min_y), 0.0)


def _num(value: float) -> float:
    return round(float(value), 6)


@dataclass(frozen=True)
class BOMItem:
    """One line of the bill of materials."""

    name: str
    spec: str
    qty: float
    unit: str
    loss_rate: float = 0.0
    notes: str = ""
    source: str = "estimate"  # estimate | placeholder | user_input
    category: str = "material"  # material | consumable | tooling | labor | placeholder

    def effective_qty(self) -> float:
        return round(float(self.qty) * (1.0 + float(self.loss_rate)), 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "spec": self.spec,
            "qty": self.qty,
            "unit": self.unit,
            "loss_rate": self.loss_rate,
            "notes": self.notes,
            "source": self.source,
            "category": self.category,
            "effective_qty": self.effective_qty(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BOMItem":
        return cls(
            name=str(data.get("name", "")),
            spec=str(data.get("spec", "")),
            qty=float(data.get("qty", 0.0)),
            unit=str(data.get("unit", "")),
            loss_rate=float(data.get("loss_rate", 0.0)),
            notes=str(data.get("notes", "")),
            source=str(data.get("source", "estimate")),
            category=str(data.get("category", "material")),
        )


@dataclass(frozen=True)
class SOPStep:
    """One step of the manufacturing SOP."""

    step: int
    title: str
    detail: str
    equipment: str
    placeholder: bool = False
    source: str = "estimate"  # estimate | placeholder | user_input

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "title": self.title,
            "detail": self.detail,
            "equipment": self.equipment,
            "placeholder": self.placeholder,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SOPStep":
        return cls(
            step=int(data.get("step", 0)),
            title=str(data.get("title", "")),
            detail=str(data.get("detail", "")),
            equipment=str(data.get("equipment", "")),
            placeholder=bool(data.get("placeholder", False)),
            source=str(data.get("source", "estimate")),
        )


@dataclass
class ReviewItem:
    """A row of the retrospective template (复盘模板)."""

    problem: str
    root_cause: str
    improvement: str
    owner: str = ""
    status: str = "open"  # open | in_progress | resolved | verified

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "root_cause": self.root_cause,
            "improvement": self.improvement,
            "owner": self.owner,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewItem":
        return cls(
            problem=str(data.get("problem", "")),
            root_cause=str(data.get("root_cause", "")),
            improvement=str(data.get("improvement", "")),
            owner=str(data.get("owner", "")),
            status=str(data.get("status", "open")),
        )
