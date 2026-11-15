"""CF2 exporter: XML for CO2 laser-cutting machines.

CF2 is the de-facto interchange format used by desktop CO2 laser cutters
(Full Spectrum, Glowforge-style controllers, ...).  It is a small XML
dialect:

* a ``<CF2>`` root with a version attribute,
* a ``<Header>`` describing the coordinate system,
* one or more ``<Layer>`` elements, each with a name and a display colour,
* ``<Path>`` elements made of ``<Move>`` / ``<Line>`` primitives.

The exporter is deterministic and pure-stdlib.  Geometry is carried through
unchanged in millimetres, y-up: every ``(x1, y1)`` of a segment becomes a
``<Move>`` and every ``(x2, y2)`` becomes the matching ``<Line>``.

Layers are mapped onto the three canonical cutting groups:

===========  ======
CUT          red
CREASE       blue
GLUE         green
===========  ======

Any other IR layer (``BLEED``, ``SAFETY``, ``GUIDE``, ``TEXT``) is emitted on
the ``CUT`` layer so that no geometry is silently dropped.
"""

from __future__ import annotations

import xml.sax.saxutils as _sax

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    Dieline,
    StructureSpec,
)

__all__ = ["dieline_to_cf2"]

#: Canonical CF2 layer groups, in emission order.
CF2_LAYERS: tuple[tuple[str, str], ...] = (
    ("CUT", "red"),
    ("CREASE", "blue"),
    ("GLUE", "green"),
)

#: Mapping from IR layer to CF2 layer name.
LAYER_MAP: dict[str, str] = {
    LAYER_CUT: "CUT",
    LAYER_CREASE: "CREASE",
    LAYER_GLUE: "GLUE",
}


def _num(value: float) -> str:
    """Format a coordinate with four decimals, normalising negative zero."""
    number = round(float(value), 4)
    if number == 0:
        number = 0.0
    return f"{number:.4f}"


def _esc(text: str) -> str:
    return _sax.escape(str(text), {'"': "&quot;"})


def dieline_to_cf2(dieline: Dieline, spec: StructureSpec | None = None) -> bytes:
    """Serialise a :class:`Dieline` to a valid CF2 XML document (bytes).

    The ``spec`` argument is accepted for signature parity with the other
    exporters; it currently only contributes a non-semantic ``Metadata``
    comment describing the requested model.
    """
    # Group segments by CF2 layer, preserving engine order.
    grouped: dict[str, list] = {name: [] for name, _colour in CF2_LAYERS}
    for seg in dieline.segments:
        grouped[LAYER_MAP.get(seg.layer, "CUT")].append(seg)

    lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<CF2 version="1.0">')
    lines.append("  <Header>")
    lines.append("    <Unit>mm</Unit>")
    lines.append("    <Origin>bottom-left</Origin>")
    if spec is not None:
        lines.append(
            f"    <Metadata>model={_esc(spec.model_code)} "
            f"length_mm={_num(spec.dimensions.length)} "
            f"width_mm={_num(spec.dimensions.width)} "
            f"height_mm={_num(spec.dimensions.height)}</Metadata>"
        )
    lines.append("  </Header>")
    lines.append("  <Layers>")

    for name, colour in CF2_LAYERS:
        lines.append(f'    <Layer name="{_esc(name)}" color="{_esc(colour)}">')
        lines.append("      <Path>")
        for seg in grouped[name]:
            lines.append(
                f'        <Move x="{_num(seg.x1)}" y="{_num(seg.y1)}"/>'
            )
            lines.append(
                f'        <Line x="{_num(seg.x2)}" y="{_num(seg.y2)}"/>'
            )
        lines.append("      </Path>")
        lines.append("    </Layer>")

    lines.append("  </Layers>")
    lines.append("</CF2>")
    return ("\n".join(lines) + "\n").encode("utf-8")
