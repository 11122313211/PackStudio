"""SVG exporter: deterministic, mm units, 1:1 scale, one group per layer."""

from __future__ import annotations

import html
import json

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_BLEED,
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    LAYER_SAFETY,
    Dieline,
    StructureSpec,
)

LAYER_ORDER = (LAYER_CUT, LAYER_CREASE, LAYER_GLUE, LAYER_BLEED, LAYER_SAFETY)

_LAYER_STYLE = {
    LAYER_CUT: ("#000000", "solid", 0.35),
    LAYER_CREASE: ("#c00000", "dashed", 0.25),
    LAYER_GLUE: ("#00a0e9", "dotted", 0.25),
    LAYER_BLEED: ("#888888", "solid", 0.2),
    LAYER_SAFETY: ("#00a0e9", "solid", 0.15),
}


def _n(value: float) -> str:
    rounded = round(value, 4)
    if abs(rounded) < 0.00005:
        rounded = 0.0
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _dash(style: str) -> str:
    if style == "dashed":
        return "4 2"
    if style == "dotted":
        return "1 2"
    if style == "glue":
        return "2 2"
    return "none"


def dieline_to_svg(dieline: Dieline, spec: StructureSpec | None = None) -> str:
    min_x, min_y, max_x, max_y = dieline.bounds
    margin = 10.0
    # SVG 是 y-down 媒介：输出前把 y-up 内容翻转为 y' = -y，保证显示方向正确。
    view_x = min_x - margin
    view_y = -max_y - margin
    view_w = max_x - min_x + 2 * margin
    view_h = max_y - min_y + 2 * margin

    by_layer: dict[str, list[str]] = {layer: [] for layer in LAYER_ORDER}
    for seg in dieline.segments:
        color, _style, width = _LAYER_STYLE.get(seg.layer, ("#000000", "solid", 0.3))
        dash = _dash(seg.style)
        label = html.escape(seg.label) if seg.label else ""
        attrs = (
            f'class="line-{seg.layer.lower()}" '
            f'data-layer="{seg.layer}" '
            f'x1="{_n(seg.x1)}" y1="{_n(-seg.y1)}" x2="{_n(seg.x2)}" y2="{_n(-seg.y2)}" '
            f'stroke="{color}" stroke-width="{_n(width)}" stroke-dasharray="{dash}"'
        )
        if label:
            attrs += f' data-label="{label}"'
        by_layer.setdefault(seg.layer, []).append(f'<line {attrs} />')

    panels = dieline.metadata.get("panels", [])
    panel_guides = []
    for panel in panels:
        panel_guides.append(
            f'<rect class="panel-guide" x="{_n(panel["x"])}" y="{_n(-panel["y"] - panel["h"])}" '
            f'width="{_n(panel["w"])}" height="{_n(panel["h"])}" '
            f'data-panel-name="{html.escape(str(panel["name"]))}" />'
        )

    groups = [
        '<g id="panel_guides" inkscape:groupmode="layer" inkscape:label="PANEL_GUIDES">',
        "\n    ".join(panel_guides),
        "</g>",
    ]
    for layer in LAYER_ORDER:
        lines = by_layer.get(layer, [])
        groups.append(
            f'<g id="{layer.lower()}" inkscape:groupmode="layer" inkscape:label="{layer}">'
            + ("\n    " + "\n    ".join(lines) + "\n  " if lines else "")
            + "</g>"
        )

    metadata = {
        "schema_version": "1.0",
        "model_code": dieline.model_code,
        "dimensions_mm": dieline.metadata.get("dimensions_mm", {}),
        "parameters_mm": dieline.metadata.get("parameters_mm", {}),
        "output_mode": "DESIGN_TEMPLATE",
        "review_required": "REQUIRES_MANUFACTURER_REVIEW",
    }
    metadata_json = html.escape(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="{_n(view_w)}mm" height="{_n(view_h)}mm" viewBox="{_n(view_x)} {_n(view_y)} {_n(view_w)} {_n(view_h)}">
  <title>Packaging dieline {html.escape(dieline.model_code)}</title>
  <metadata id="PACKAGING_STRUCTURE_METADATA">{metadata_json}</metadata>
  <style>
    .panel-guide {{ fill: none; stroke: #00a0e9; stroke-width: 0.08; stroke-dasharray: 1 1; }}
  </style>
{chr(10).join(groups)}
</svg>
'''
