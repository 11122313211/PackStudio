"""PDF exporter: deterministic, pure-stdlib PDF 1.4 with vector lines.

The document is assembled by hand (objects, xref table, trailer, ``%%EOF``) so
the project needs no third-party PDF library. Geometry is emitted as stroked
vector paths in millimetre coordinates converted to PDF points (1 mm =
72/25.4 pt). The dieline's y-up convention is preserved on screen by mapping
the dieline's ``max_y`` to the top of the page.

Line colours follow the same conventions as the DXF/SVG exporters:

* CUT   -> black
* CREASE-> red
* GLUE  -> blue
* BLEED/SAFETY/GUIDE/TEXT -> sensible fallbacks

A GUIDE-coloured bounding box is drawn with the PDF ``re`` operator and a
page margin frames every edge of the dieline.
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

PT_PER_MM = 72.0 / 25.4
MARGIN_MM = 10.0

LAYER_RGB: dict[str, tuple[float, float, float]] = {
    LAYER_CUT: (0.0, 0.0, 0.0),
    LAYER_CREASE: (0.75, 0.0, 0.0),
    LAYER_GLUE: (0.0, 0.0, 1.0),
    LAYER_BLEED: (0.5, 0.5, 0.5),
    LAYER_SAFETY: (0.0, 0.0, 0.8),
    LAYER_GUIDE: (0.6, 0.6, 0.6),
    LAYER_TEXT: (0.0, 0.0, 0.0),
}


def _fmt(value: float) -> str:
    return f"{value:.6f}"


def _color(rgb: tuple[float, float, float]) -> str:
    """Compact ``r g b rg`` operator, e.g. ``0.75 0 0 rg``."""
    return f"{rgb[0]:g} {rgb[1]:g} {rgb[2]:g} rg"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _ascii(text: str) -> str:
    """Keep only printable ASCII so a base-14 font renders it correctly."""
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)


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


def dieline_to_pdf(dieline: Dieline, spec: StructureSpec | None = None) -> bytes:
    """Serialise a :class:`Dieline` to a valid PDF 1.4 document (bytes)."""
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

    # Bounding frame: the ``re`` operator outlines the dieline on the page.
    fx, fy = to_pdf(min_x, max_y)
    fw = (max_x - min_x) * PT_PER_MM
    fh = (max_y - min_y) * PT_PER_MM
    ops.append(_color((0.6, 0.6, 0.6)))
    ops.append(f"{_fmt(fx)} {_fmt(fy - fh)} {_fmt(fw)} {_fmt(fh)} re")
    ops.append("S")

    for seg in dieline.segments:
        rgb = LAYER_RGB.get(seg.layer, LAYER_RGB[LAYER_CUT])
        ops.append(_color(rgb))
        x1, y1 = to_pdf(seg.x1, seg.y1)
        x2, y2 = to_pdf(seg.x2, seg.y2)
        ops.append(f"{_fmt(x1)} {_fmt(y1)} m {_fmt(x2)} {_fmt(y2)} l S")

    title = _ascii(f"Dieline: {dieline.model_code}")
    ops.append(
        f"BT /F1 9 Tf {_fmt(margin * PT_PER_MM)} {_fmt(margin * PT_PER_MM - 12)} Tm "
        f"({_pdf_escape(title)}) Tj ET"
    )

    stream = ("\n".join(ops) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_fmt(page_w)} {_fmt(page_h)}] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
        f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _assemble_pdf(objects)
