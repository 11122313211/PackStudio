"""DXF exporter: deterministic, millimetres, one layer per line type.

Produces an AutoCAD R12 (AC1009) ASCII DXF document that contains only
``LINE`` entities so every legacy CAD reader can consume it. Coordinates are
the exact millimetre, y-up values produced by the engine; nothing is scaled,
rotated or mirrored here.

Layers (ACI colour index):

===========  =====
CUT          7 (white/black)
CREASE       1 (red)
GLUE         5 (blue)
GUIDE        8 (gray, bounding-box margin frame)
===========  =====

Bleed/safety/text layers get their own canonical table entries; any layer not
in ``LAYER_COLORS`` falls back to the GUIDE layer.
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_BLEED,
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    LAYER_GUIDE,
    LAYER_SAFETY,
    LAYER_TEXT,
    Dieline,
    StructureSpec,
)

#: ACI colour index per canonical layer.
LAYER_COLORS: dict[str, int] = {
    LAYER_CUT: 7,
    LAYER_CREASE: 1,
    LAYER_GLUE: 5,
    LAYER_BLEED: 8,
    LAYER_SAFETY: 4,
    LAYER_GUIDE: 8,
    LAYER_TEXT: 7,
}

#: Layers that must always be present in the table, even when empty.
BASE_LAYERS = (LAYER_CUT, LAYER_CREASE, LAYER_GLUE)

#: Bounding-box margin in millimetres applied to the GUIDE frame.
MARGIN = 10.0


def _num(value: float) -> str:
    """Format a coordinate with four decimals, normalising negative zero."""
    number = round(float(value), 4)
    if number == 0:
        number = 0.0
    return f"{number:.4f}"


def _layer_for(layer: str) -> str:
    return layer if layer in LAYER_COLORS else LAYER_GUIDE


def _header() -> list[str]:
    return [
        "0", "SECTION",
        "2", "HEADER",
        "9", "$ACADVER",
        "1", "AC1009",
        "9", "$INSUNITS",
        "70", "4",
        "9", "$MEASUREMENT",
        "70", "1",
        "0", "ENDSEC",
    ]


def _tables(layers: list[str]) -> list[str]:
    lines = [
        "0", "SECTION",
        "2", "TABLES",
        "0", "TABLE",
        "2", "LAYER",
        "70", str(len(layers)),
    ]
    for layer in layers:
        lines.extend([
            "0", "LAYER",
            "2", layer,
            "70", "0",
            "62", str(LAYER_COLORS.get(layer, 7)),
            "6", "CONTINUOUS",
        ])
    lines.extend(["0", "ENDTAB", "0", "ENDSEC"])
    return lines


def _line_entity(x1: float, y1: float, x2: float, y2: float, layer: str) -> list[str]:
    return [
        "0", "LINE",
        "8", layer,
        "10", _num(x1), "20", _num(y1), "30", "0.0",
        "11", _num(x2), "21", _num(y2), "31", "0.0",
    ]


def _guide_frame(bounds: tuple[float, float, float, float], margin: float) -> list[str]:
    """Bounding-box margin frame on the GUIDE layer (so all geometry is visible)."""
    min_x, min_y, max_x, max_y = bounds
    x0 = min_x - margin
    y0 = min_y - margin
    x1 = max_x + margin
    y1 = max_y + margin
    edges = (
        (x0, y0, x1, y0),
        (x1, y0, x1, y1),
        (x1, y1, x0, y1),
        (x0, y1, x0, y0),
    )
    lines: list[str] = []
    for ax, ay, bx, by in edges:
        lines.extend(_line_entity(ax, ay, bx, by, LAYER_GUIDE))
    return lines


def dieline_to_dxf(dieline: Dieline, spec: StructureSpec | None = None) -> str:
    """Serialise a :class:`Dieline` to an AutoCAD R12 ASCII DXF string."""
    bounds = dieline.bounds
    present = {seg.layer for seg in dieline.segments}
    layers = sorted(set(BASE_LAYERS) | present | {LAYER_GUIDE})

    lines: list[str] = []
    lines.extend(_header())
    lines.extend(_tables(layers))
    lines.extend(["0", "SECTION", "2", "ENTITIES"])
    for seg in dieline.segments:
        lines.extend(_line_entity(seg.x1, seg.y1, seg.x2, seg.y2, _layer_for(seg.layer)))
    lines.extend(_guide_frame(bounds, MARGIN))
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(lines) + "\n"
