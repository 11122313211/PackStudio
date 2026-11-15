"""Spot-colour separation PDF exporter.

Produces a PDF 1.4 document that uses the PDF ``/Separation`` colour space
with four named colorants (spot plates):

============  ==========  ======
Colorant      Appearance  Used for
============  ==========  ======
CutContour    black       cut lines
CreaseValley  blue        valley folds (and ``auto``/``none`` creases)
CreaseMountain green      mountain folds
Bleed         red         bleed area (and glue guide lines)
============  ==========  ======

The document is assembled by hand (objects, xref table, trailer, ``%%EOF``)
so the project needs no third-party PDF library.  Geometry is emitted as
stroked vector paths in millimetre coordinates converted to PDF points
(1 mm = 72/25.4 pt), with the dieline's y-up convention preserved on screen
by mapping the dieline's ``max_y`` to the top of the page.

Each segment is drawn exactly once, on the plate selected by its IR layer
(and, for creases, its ``fold_direction``).  Segments whose direction is
``auto`` or ``none`` are treated as valley folds and drawn on the
``CreaseValley`` plate.
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    Dieline,
    StructureSpec,
)

__all__ = ["dieline_to_spot_pdf", "SPOT_COLORANTS"]

PT_PER_MM = 72.0 / 25.4
MARGIN_MM = 10.0
DEFAULT_BLEED_MM = 3.0

#: The four named colorants, in resource order ``CS0`` ... ``CS3``.
SPOT_COLORANTS: tuple[str, ...] = (
    "CutContour",
    "CreaseValley",
    "CreaseMountain",
    "Bleed",
)

#: CMYK for the ``C1`` endpoint of each colorant's tint transform.
_COLORANT_CMYK: dict[str, tuple[float, float, float, float]] = {
    "CutContour": (0.0, 0.0, 0.0, 1.0),  # black
    "CreaseValley": (1.0, 0.0, 0.0, 0.0),  # blue
    "CreaseMountain": (1.0, 0.0, 1.0, 0.0),  # green
    "Bleed": (0.0, 1.0, 1.0, 0.0),  # red
}


def _fmt(value: float) -> str:
    return f"{value:.6f}"


def _colorant_for(layer: str, fold_direction: str) -> str:
    """Map an IR layer (plus fold sense) onto one of the four plates.

    ``CUT`` and ``GLUE`` are die-produced production lines and go to
    ``CutContour`` (black); ``CREASE`` folds split between
    ``CreaseValley`` and ``CreaseMountain``.  ``BLEED`` goes to the
    ``Bleed`` plate; every other guidance layer (``GUIDE``, ``SAFETY``,
    ``TEXT``, unknown) is drawn on the ``Bleed`` plate so no geometry is
    silently dropped while no fifth colorant is invented.
    """
    if layer == LAYER_CREASE:
        if fold_direction == "mountain":
            return "CreaseMountain"
        # valley, auto, none (and anything unexpected) -> valley plate
        return "CreaseValley"
    if layer in (LAYER_CUT, LAYER_GLUE):
        return "CutContour"
    return "Bleed"


def _assemble_pdf(objects: list[bytes]) -> bytes:
    """Write uncompressed PDF objects with byte-exact offsets and xref."""
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    start = len(data)
    size = len(objects) + 1
    data.extend(f"xref\n0 {size}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode("ascii")
    )
    return bytes(data)


def _separation_object(colorant: str) -> bytes:
    cmyk = _COLORANT_CMYK[colorant]
    c1 = " ".join(f"{value:g}" for value in cmyk)
    return (
        f"[/Separation /{colorant} /DeviceCMYK << /FunctionType 2 "
        f"/Domain [0 1] /C0 [0 0 0 0] /C1 [{c1}] /N 1 >>]"
    ).encode("ascii")


def dieline_to_spot_pdf(dieline: Dieline, spec: StructureSpec | None = None) -> bytes:
    """Serialise a :class:`Dieline` to a spot-colour separation PDF (bytes)."""
    min_x, min_y, max_x, max_y = dieline.bounds
    margin = MARGIN_MM
    page_w_mm = (max_x - min_x) + 2 * margin
    page_h_mm = (max_y - min_y) + 2 * margin
    page_w = page_w_mm * PT_PER_MM
    page_h = page_h_mm * PT_PER_MM

    def to_pdf(x: float, y: float) -> tuple[float, float]:
        # PDF 原生 y-up（原点左下）：保留刀版 y-up 方向，仅平移到页内边距。
        px = (x - min_x + margin) * PT_PER_MM
        py = (y - min_y + margin) * PT_PER_MM
        return px, py

    ops: list[str] = [f"{0.35 * PT_PER_MM:.4f} w"]

    # Group segments by colorant, preserving engine order.
    grouped: dict[str, list] = {name: [] for name in SPOT_COLORANTS}
    for seg in dieline.segments:
        grouped[_colorant_for(seg.layer, seg.fold_direction)].append(seg)

    for index, colorant in enumerate(SPOT_COLORANTS):
        resource = f"CS{index}"
        segments = grouped[colorant]
        if not segments:
            continue
        ops.append(f"/{resource} CS 1 SCN")
        for seg in segments:
            x1, y1 = to_pdf(seg.x1, seg.y1)
            x2, y2 = to_pdf(seg.x2, seg.y2)
            ops.append(f"{_fmt(x1)} {_fmt(y1)} m {_fmt(x2)} {_fmt(y2)} l")
        ops.append("S")

    # Bleed area: the ring between the cut bounds and the bounds expanded by
    # the bleed margin, drawn as an even-odd fill on the ``Bleed`` plate so
    # the artwork area itself stays clear.
    bleed = DEFAULT_BLEED_MM
    if spec is not None:
        bleed = float(spec.bleed)
    if bleed > 0:
        ox, oy = to_pdf(min_x - bleed, min_y - bleed)
        ow = (max_x - min_x + 2 * bleed) * PT_PER_MM
        oh = (max_y - min_y + 2 * bleed) * PT_PER_MM
        ix, iy = to_pdf(min_x, min_y)
        iw = (max_x - min_x) * PT_PER_MM
        ih = (max_y - min_y) * PT_PER_MM
        ops.append("/CS3 CS 1 SCN")
        ops.append(f"{_fmt(ox)} {_fmt(oy)} {_fmt(ow)} {_fmt(oh)} re")
        ops.append(f"{_fmt(ix)} {_fmt(iy)} {_fmt(iw)} {_fmt(ih)} re")
        ops.append("f*")

    title = f"Dieline: {dieline.model_code}".replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    ops.append(
        f"BT /F1 9 Tf {_fmt(margin * PT_PER_MM)} {_fmt(margin * PT_PER_MM - 12)} Tm "
        f"({title}) Tj ET"
    )

    stream = ("\n".join(ops) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_fmt(page_w)} {_fmt(page_h)}] "
            "/Resources << /ColorSpace << /CS0 6 0 R /CS1 7 0 R /CS2 8 0 R /CS3 9 0 R >> "
            "/Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
        f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _separation_object("CutContour"),
        _separation_object("CreaseValley"),
        _separation_object("CreaseMountain"),
        _separation_object("Bleed"),
    ]
    return _assemble_pdf(objects)
