"""Unified request / dimension / spec IR shared by every module.

Contract:
- LLM only extracts parameters; deterministic code owns geometry.
- Dimensions are always normalised to millimetres internally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from packstudio.engines.packaging_engine.exceptions import RequestValidationError

MM_PER_UNIT = {"mm": 1.0, "cm": 10.0, "in": 25.4}

# Canonical line layers. Cut/crease must never share a layer.
LAYER_CUT = "CUT"
LAYER_CREASE = "CREASE"
LAYER_BLEED = "BLEED"
LAYER_SAFETY = "SAFETY"
LAYER_GLUE = "GLUE"
LAYER_TEXT = "TEXT"
LAYER_GUIDE = "GUIDE"


@dataclass(frozen=True)
class Dimensions:
    """Normalised physical dimensions in millimetres."""

    length: float
    width: float
    height: float
    unit: str = "mm"
    dimension_type: str = "unspecified"  # finished_inner | finished_outer | unspecified

    @classmethod
    def from_dict(cls, data: object) -> "Dimensions":
        if not isinstance(data, dict):
            raise RequestValidationError(
                "INVALID_DIMENSIONS", "dimensions 必须是对象。", {"field": "dimensions"}
            )
        missing = [k for k in ("length", "width", "height") if k not in data]
        if missing:
            raise RequestValidationError(
                "MISSING_REQUIRED_FIELD",
                "缺少结构尺寸。",
                {"missing_fields": missing},
            )
        unit = str(data.get("unit", "mm")).lower()
        if unit not in MM_PER_UNIT:
            raise RequestValidationError(
                "UNSUPPORTED_UNIT",
                f"不支持的尺寸单位：{unit}",
                {"supported_units": sorted(MM_PER_UNIT)},
            )
        try:
            scale = MM_PER_UNIT[unit]
            length = float(data["length"]) * scale
            width = float(data["width"]) * scale
            height = float(data["height"]) * scale
        except (TypeError, ValueError) as exc:
            raise RequestValidationError(
                "INVALID_DIMENSIONS", "长、宽、高必须是数字。", {"field": "dimensions"}
            ) from exc
        if min(length, width, height) <= 0:
            raise RequestValidationError(
                "INVALID_DIMENSIONS", "长、宽、高必须大于 0。", {"field": "dimensions"}
            )
        return cls(
            length=length,
            width=width,
            height=height,
            unit="mm",
            dimension_type=str(data.get("dimension_type", "unspecified")),
        )


@dataclass(frozen=True)
class StructureSpec:
    """Input to the dieline engine: box type + dimensions + process allowances."""

    model_code: str
    dimensions: Dimensions
    board_thickness: float = 0.3
    bleed: float = 3.0
    safe_margin: float = 3.0
    shrink: float = 0.5
    tuck_height: float = 12.0
    glue_width: float = 11.0
    material: str = ""
    output_mode: str = "DESIGN_TEMPLATE"
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: object) -> "StructureSpec":
        if not isinstance(data, dict):
            raise RequestValidationError("INVALID_SPEC", "结构参数必须是对象。")
        model_code = str(data.get("model_code", "")).strip()
        if not model_code:
            raise RequestValidationError(
                "MISSING_REQUIRED_FIELD", "缺少 model_code。", {"missing_fields": ["model_code"]}
            )
        if "dimensions" not in data:
            raise RequestValidationError(
                "MISSING_REQUIRED_FIELD", "缺少 dimensions。", {"missing_fields": ["dimensions"]}
            )
        known = {
            "model_code",
            "dimensions",
            "board_thickness",
            "bleed",
            "safe_margin",
            "shrink",
            "tuck_height",
            "glue_width",
            "material",
            "output_mode",
        }
        extras = {k: v for k, v in data.items() if k not in known}
        return cls(
            model_code=model_code,
            dimensions=Dimensions.from_dict(data["dimensions"]),
            board_thickness=float(data.get("board_thickness", 0.3)),
            bleed=float(data.get("bleed", 3.0)),
            safe_margin=float(data.get("safe_margin", 3.0)),
            shrink=float(data.get("shrink", 0.5)),
            tuck_height=float(data.get("tuck_height", 12.0)),
            glue_width=float(data.get("glue_width", 11.0)),
            material=str(data.get("material", "")),
            output_mode=str(data.get("output_mode", "DESIGN_TEMPLATE")),
            extras=extras,
        )


@dataclass(frozen=True)
class CreaseSegment:
    """A single geometry primitive in a dieline.

    ``fold_direction`` records the intended fold sense for crease lines:
    ``mountain`` (山折), ``valley`` (谷折), ``none`` (no fold) or ``auto``
    (engine will infer from geometry). Defaults to ``auto`` for backward
    compatibility with existing builders that do not set it.
    """

    x1: float
    y1: float
    x2: float
    y2: float
    layer: str = LAYER_GUIDE
    style: str = "solid"  # solid | dashed | dotted | glue
    width: float = 0.35
    label: str = ""
    fold_direction: str = "auto"  # mountain | valley | none | auto

    def to_dict(self) -> dict:
        return {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "layer": self.layer,
            "style": self.style,
            "width": self.width,
            "label": self.label,
            "fold_direction": self.fold_direction,
        }


@dataclass(frozen=True)
class Dieline:
    """Deterministic output of the dieline engine."""

    model_code: str
    segments: list[CreaseSegment]
    bounds: tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "model_code": self.model_code,
            "bounds": self.bounds,
            "segment_count": len(self.segments),
            "segments": [s.to_dict() for s in self.segments],
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class Request:
    """Unified request envelope consumed by the MCP bridge / CLI."""

    action: str
    request_id: str = ""
    parameters: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: object) -> "Request":
        if not isinstance(data, dict):
            raise RequestValidationError("INVALID_REQUEST", "请求必须是对象。")
        action = str(data.get("action", "")).strip()
        if not action:
            raise RequestValidationError(
                "MISSING_REQUIRED_FIELD", "缺少 action。", {"missing_fields": ["action"]}
            )
        return cls(
            action=action,
            request_id=str(data.get("request_id", "")),
            parameters=dict(data.get("parameters", {})),
        )

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "request_id": self.request_id,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ValidationReport:
    """Result of the mechanical validation gate."""

    passed: bool
    checks: list[dict]
    manual_review: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "manual_review": self.manual_review,
        }
