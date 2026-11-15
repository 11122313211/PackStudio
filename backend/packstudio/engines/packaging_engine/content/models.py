"""Content-layer data models: ``ContentBrief`` and ``ContentLayoutResult``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packstudio.engines.packaging_engine.content.fields import (
    SCHEMA_VERSION,
    FieldValue,
    build_fields,
)
from packstudio.engines.packaging_engine.exceptions import RequestValidationError


@dataclass
class ContentBrief:
    """User-supplied product information, normalised onto canonical fields.

    ``from_dict`` accepts any mapping (flat or nested). Values are stored
    verbatim; nothing is invented or paraphrased.
    """

    data: dict
    fields: tuple[FieldValue, ...]

    @classmethod
    def from_dict(cls, data: object) -> "ContentBrief":
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise RequestValidationError("INVALID_BRIEF", "产品资料必须是对象。")
        raw = dict(data)
        return cls(data=raw, fields=build_fields(raw))

    def value_for(self, key: str) -> str:
        for item in self.fields:
            if item.key == key:
                return item.value
        return ""

    def field_for(self, key: str) -> FieldValue | None:
        for item in self.fields:
            if item.key == key:
                return item
        return None

    def is_provided(self, key: str) -> bool:
        item = self.field_for(key)
        return bool(item and not item.is_placeholder)

    @property
    def provided(self) -> tuple[FieldValue, ...]:
        return tuple(item for item in self.fields if not item.is_placeholder)

    @property
    def missing(self) -> tuple[FieldValue, ...]:
        return tuple(item for item in self.fields if item.is_placeholder)

    @property
    def missing_required(self) -> tuple[FieldValue, ...]:
        return tuple(item for item in self.missing if item.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "jurisdiction": str(self.data.get("jurisdiction", "CN") or "CN"),
            "product_category": str(self.data.get("product_category", "") or ""),
            "fields": [item.to_dict() for item in self.fields],
            "counts": {
                "fields": len(self.fields),
                "provided": len(self.provided),
                "missing": len(self.missing),
                "missing_required": len(self.missing_required),
            },
        }


@dataclass
class ContentLayoutResult:
    """Everything produced by ``layout_content`` for one dieline/brief pair."""

    dieline_model: str
    bounds: tuple[float, float, float, float]
    fields: tuple[FieldValue, ...]
    placements: tuple[dict[str, Any], ...]
    validation: dict[str, Any]
    paths: dict[str, str]
    missing_count: int
    warnings: tuple[str, ...] = ()
    safe_margin: float = 3.0

    @property
    def outputs(self) -> dict[str, str]:
        """Alias kept for CLI consumers (``result.outputs``)."""
        return dict(self.paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "dieline_model": self.dieline_model,
            "bounds": list(self.bounds),
            "safe_margin": self.safe_margin,
            "fields": [item.to_dict() for item in self.fields],
            "placements": [dict(item) for item in self.placements],
            "validation": self.validation,
            "paths": dict(self.paths),
            "missing_count": self.missing_count,
            "warnings": list(self.warnings),
        }
