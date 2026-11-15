"""Tests for the SVG / DXF / PDF dieline exporters.

All exporters are pure-stdlib and deterministic: coordinates are absolute
millimetres, y up, and are carried through unchanged.
"""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET

from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.exporters import (
    dieline_to_cf2,
    dieline_to_dxf,
    dieline_to_pdf,
    dieline_to_spot_pdf,
    dieline_to_svg,
)
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    CreaseSegment,
    Dieline,
    StructureSpec,
)

SVG_NS = "http://www.w3.org/2000/svg"
PT_PER_MM = 72.0 / 25.4


def _parse_svg(xml_text):
    """解析引擎生成的 SVG/CF2 输出。

    输出均为本引擎确定性生成（可信），仍显式拒绝 DOCTYPE/内部实体，
    防止 XML 实体扩展（XXE/billion laughs）。
    """
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", "replace")
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise ValueError("untrusted XML: DOCTYPE/ENTITY not allowed")
    return ET.fromstring(xml_text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_simple_dieline() -> Dieline:
    """A tiny three-layer dieline with exact, hand-checkable bounds."""
    segments = [
        CreaseSegment(0.0, 0.0, 100.0, 0.0, LAYER_CUT),
        CreaseSegment(50.0, 0.0, 50.0, 100.0, LAYER_CREASE, "dashed", 0.25),
        CreaseSegment(0.0, 100.0, 0.0, 200.0, LAYER_GLUE, "glue", 0.25),
    ]
    return Dieline("test.simple", tuple(segments), (0.0, 0.0, 100.0, 200.0), {})


def make_lock_bottom_dieline() -> tuple[Dieline, StructureSpec]:
    spec = StructureSpec.from_dict(
        {
            "model_code": "carton.box_v2.lock_bottom",
            "dimensions": {"length": 80.0, "width": 40.0, "height": 120.0, "unit": "mm"},
        }
    )
    return render(spec), spec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dxf(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    pairs: list[tuple[int, str]] = []
    index = 0
    while index + 1 < len(lines):
        code = lines[index].strip()
        value = lines[index + 1].strip()
        if code.lstrip("-").isdigit():
            pairs.append((int(code), value))
        index += 2
    return pairs


def _layer_records(pairs: list[tuple[int, str]]) -> list[dict]:
    records: list[dict] = []
    for i, (code, value) in enumerate(pairs):
        if code == 0 and value == "LAYER":
            record: dict = {}
            j = i + 1
            while j < len(pairs) and pairs[j][0] != 0:
                record[pairs[j][0]] = pairs[j][1]
                j += 1
            records.append(record)
    return records


def _count(pairs: list[tuple[int, str]], code: int, value: str) -> int:
    return sum(1 for c, v in pairs if c == code and v == value)


def _media_box(pdf: bytes) -> tuple[float, float]:
    match = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*\]", pdf)
    assert match is not None, "PDF has no MediaBox"
    return float(match.group(1)), float(match.group(2))


def _validate_pdf_xref(pdf: bytes) -> None:
    """Check that every xref offset really points at its ``N 0 obj`` header."""
    start = pdf.rfind(b"startxref")
    assert start != -1, "PDF has no startxref"
    offset = int(pdf[start + len(b"startxref"):].split()[0])
    assert pdf[offset:offset + 4] == b"xref", "startxref does not point at xref"
    header = re.match(rb"xref\s*\n0\s+(\d+)\s*\n", pdf[offset:])
    assert header is not None, "malformed xref header"
    count = int(header.group(1))
    entries = re.findall(rb"(\d{10})\s+(\d{5})\s+([nf])\s", pdf[offset + header.end():])
    assert len(entries) == count, f"xref declares {count} entries but {len(entries)} found"
    for entry_offset, _generation, kind in entries:
        if kind == b"f":
            continue
        position = int(entry_offset)
        assert re.match(rb"\d+ 0 obj", pdf[position:]), f"bad xref offset {position}"


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

class SvgExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dieline = make_simple_dieline()
        self.svg = dieline_to_svg(self.dieline)

    def test_valid_xml_and_svg_root(self) -> None:
        root = _parse_svg(self.svg)
        self.assertEqual(root.tag, f"{{{SVG_NS}}}svg")
        self.assertIn("<svg", self.svg)

    def test_layers_present(self) -> None:
        ids = {el.get("id") for el in _parse_svg(self.svg).iter()}
        for layer in ("cut", "crease", "glue"):
            self.assertIn(layer, ids)

    def test_exact_mm_scale_and_margin(self) -> None:
        root = _parse_svg(self.svg)
        # bounds (0,0,100,200) + 10 mm margin -> 120 x 220 mm view。
        # SVG 为 y-down 媒介：导出时 y 翻转为 y'=-y，内容占 [-200, 0]。
        self.assertEqual(root.get("width"), "120mm")
        self.assertEqual(root.get("height"), "220mm")
        self.assertEqual(root.get("viewBox"), "-10 -210 120 220")

    def test_coordinates_unchanged(self) -> None:
        root = _parse_svg(self.svg)
        cut = [el for el in root.iter() if el.get("data-layer") == LAYER_CUT]
        self.assertEqual(cut[0].get("x1"), "0")
        self.assertEqual(cut[0].get("y1"), "0")
        self.assertEqual(cut[0].get("x2"), "100")
        self.assertEqual(cut[0].get("y2"), "0")

    def test_accepts_spec(self) -> None:
        spec = StructureSpec.from_dict(
            {"model_code": "test.simple", "dimensions": {"length": 1, "width": 1, "height": 1}}
        )
        self.assertIn("<svg", dieline_to_svg(self.dieline, spec))


# ---------------------------------------------------------------------------
# DXF
# ---------------------------------------------------------------------------

class DxfExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dieline = make_simple_dieline()
        self.dxf = dieline_to_dxf(self.dieline)
        self.pairs = _parse_dxf(self.dxf)

    def test_sections_present(self) -> None:
        section_names = [v for c, v in self.pairs if c == 2]
        for section in ("HEADER", "TABLES", "ENTITIES"):
            self.assertIn(section, section_names)
        self.assertEqual(_count(self.pairs, 0, "SECTION"), 3)

    def test_header_r12_and_units(self) -> None:
        values = {v for c, v in self.pairs if c == 9}
        self.assertIn("$ACADVER", values)
        self.assertIn("$INSUNITS", values)
        self.assertIn("$MEASUREMENT", values)
        acadver = self.pairs[self.pairs.index((9, "$ACADVER")) + 1]
        self.assertEqual(acadver, (1, "AC1009"))
        insunits = self.pairs[self.pairs.index((9, "$INSUNITS")) + 1]
        self.assertEqual(insunits, (70, "4"))

    def test_layer_table_has_cut_crease_glue(self) -> None:
        names = [record.get(2) for record in _layer_records(self.pairs)]
        for layer in (LAYER_CUT, LAYER_CREASE, LAYER_GLUE):
            self.assertIn(layer, names)

    def test_layer_colors(self) -> None:
        records = {record.get(2): record for record in _layer_records(self.pairs)}
        for layer, color in ((LAYER_CUT, 7), (LAYER_CREASE, 1), (LAYER_GLUE, 5)):
            self.assertIn(layer, records)
            self.assertEqual(records[layer].get(62), str(color))

    def test_line_entities_only(self) -> None:
        # Only LINE is allowed as an entity (plus the guide frame).
        entity_kinds = {
            value for (code, value) in self.pairs
            if code == 0 and value not in ("SECTION", "ENDSEC", "TABLE", "ENDTAB", "EOF", "LAYER")
        }
        self.assertEqual(entity_kinds, {"LINE"})
        # 3 dieline segments + 4 guide-frame edges.
        self.assertEqual(_count(self.pairs, 0, "LINE"), 7)

    def test_entity_coordinates(self) -> None:
        index = next(i for i, pair in enumerate(self.pairs) if pair == (0, "LINE"))
        start = self.pairs[index]
        self.assertEqual(start, (0, "LINE"))
        self.assertEqual(self.pairs[index + 1], (8, LAYER_CUT))
        fields: dict[int, str] = {}
        j = index + 1
        while self.pairs[j][0] != 0:
            fields[self.pairs[j][0]] = self.pairs[j][1]
            j += 1
        # First cut segment is (0,0) -> (100,0); DXF coords use 4 decimals.
        self.assertEqual(fields[10], "0.0000")
        self.assertEqual(fields[20], "0.0000")
        self.assertEqual(fields[11], "100.0000")
        self.assertEqual(fields[21], "0.0000")

    def test_guidance_frame_present(self) -> None:
        self.assertEqual(_count(self.pairs, 8, "GUIDE"), 4)

    def test_accepts_spec(self) -> None:
        spec = StructureSpec.from_dict(
            {"model_code": "test.simple", "dimensions": {"length": 1, "width": 1, "height": 1}}
        )
        self.assertIn("ENTITIES", dieline_to_dxf(self.dieline, spec))


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

class PdfExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dieline = make_simple_dieline()
        self.pdf = dieline_to_pdf(self.dieline)

    def test_returns_bytes_with_header_and_eof(self) -> None:
        self.assertIsInstance(self.pdf, bytes)
        self.assertTrue(self.pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(self.pdf.rstrip().endswith(b"%%EOF"))

    def test_has_object_streams_and_xref(self) -> None:
        for token in (b"obj", b"endobj", b"xref", b"trailer", b"startxref"):
            self.assertIn(token, self.pdf)

    def test_xref_offsets_are_valid(self) -> None:
        _validate_pdf_xref(self.pdf)

    def test_contains_rectangle_and_line_operators(self) -> None:
        self.assertIn(b" re", self.pdf)
        self.assertIn(b" m", self.pdf)
        self.assertIn(b" l S", self.pdf)

    def test_line_colors_present(self) -> None:
        self.assertIn(b"0 0 0 rg", self.pdf)  # cut
        self.assertIn(b"0.75 0 0 rg", self.pdf)  # crease
        self.assertIn(b"0 0 1 rg", self.pdf)  # glue

    def test_page_size_fits_dieline_with_margin(self) -> None:
        width, height = _media_box(self.pdf)
        span_x = 100.0
        span_y = 200.0
        margin = 10.0
        self.assertAlmostEqual(width, (span_x + 2 * margin) * PT_PER_MM, places=3)
        self.assertAlmostEqual(height, (span_y + 2 * margin) * PT_PER_MM, places=3)

    def test_accepts_spec(self) -> None:
        spec = StructureSpec.from_dict(
            {"model_code": "test.simple", "dimensions": {"length": 1, "width": 1, "height": 1}}
        )
        self.assertTrue(dieline_to_pdf(self.dieline, spec).startswith(b"%PDF"))


# ---------------------------------------------------------------------------
# Integration: real rendered dieline through all three exporters
# ---------------------------------------------------------------------------

class RealDielineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dieline, self.spec = make_lock_bottom_dieline()
        self.assertGreater(len(self.dieline.segments), 0)

    def test_svg_output(self) -> None:
        svg = dieline_to_svg(self.dieline, self.spec)
        root = _parse_svg(svg)
        self.assertEqual(root.tag, f"{{{SVG_NS}}}svg")
        data = self.dieline.metadata
        self.assertEqual(data["box_type"], "carton.box_v2.lock_bottom")

    def test_dxf_layers_match_dieline_layers(self) -> None:
        dxf = dieline_to_dxf(self.dieline, self.spec)
        pairs = _parse_dxf(dxf)
        declared = {record.get(2) for record in _layer_records(pairs)}
        for layer in {seg.layer for seg in self.dieline.segments}:
            self.assertIn(layer, declared)
        # The three canonical layers are always declared.
        for layer in (LAYER_CUT, LAYER_CREASE, LAYER_GLUE):
            self.assertIn(layer, declared)

    def test_dxf_line_count_matches_segments(self) -> None:
        dxf = dieline_to_dxf(self.dieline, self.spec)
        pairs = _parse_dxf(dxf)
        self.assertEqual(_count(pairs, 0, "LINE"), len(self.dieline.segments) + 4)

    def test_pdf_output(self) -> None:
        pdf = dieline_to_pdf(self.dieline, self.spec)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        width, height = _media_box(pdf)
        min_x, min_y, max_x, max_y = self.dieline.bounds
        margin = 10.0
        self.assertAlmostEqual(width, (max_x - min_x + 2 * margin) * PT_PER_MM, places=3)
        self.assertAlmostEqual(height, (max_y - min_y + 2 * margin) * PT_PER_MM, places=3)

    def test_all_exporters_run_without_error(self) -> None:
        svg = dieline_to_svg(self.dieline, self.spec)
        dxf = dieline_to_dxf(self.dieline, self.spec)
        pdf = dieline_to_pdf(self.dieline, self.spec)
        self.assertIsInstance(svg, str)
        self.assertIsInstance(dxf, str)
        self.assertIsInstance(pdf, bytes)
        self.assertGreater(len(svg), 0)
        self.assertGreater(len(dxf), 0)
        self.assertGreater(len(pdf), 0)


# ---------------------------------------------------------------------------
# CF2
# ---------------------------------------------------------------------------

class Cf2ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dieline = make_simple_dieline()
        self.cf2 = dieline_to_cf2(self.dieline)
        self.root = _parse_svg(self.cf2)

    def test_returns_utf8_bytes_with_xml_declaration(self) -> None:
        self.assertIsInstance(self.cf2, bytes)
        self.assertTrue(self.cf2.startswith(b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>"))

    def test_root_is_cf2(self) -> None:
        self.assertEqual(self.root.tag, "CF2")
        self.assertEqual(self.root.get("version"), "1.0")

    def test_header_units_and_origin(self) -> None:
        header = self.root.find("Header")
        self.assertIsNotNone(header)
        self.assertEqual(header.findtext("Unit"), "mm")
        self.assertEqual(header.findtext("Origin"), "bottom-left")

    def test_three_layer_groups_with_colours(self) -> None:
        layers = {layer.get("name"): layer for layer in self.root.iter("Layer")}
        self.assertEqual(set(layers), {"CUT", "CREASE", "GLUE"})
        self.assertEqual(layers["CUT"].get("color"), "red")
        self.assertEqual(layers["CREASE"].get("color"), "blue")
        self.assertEqual(layers["GLUE"].get("color"), "green")

    def test_coordinates_are_1to1_mm(self) -> None:
        moves = {layer.get("name"): layer.findall(".//Move") for layer in self.root.iter("Layer")}
        lines = {layer.get("name"): layer.findall(".//Line") for layer in self.root.iter("Layer")}
        # CUT has one segment: (0,0) -> (100,0).
        self.assertEqual(moves["CUT"][0].get("x"), "0.0000")
        self.assertEqual(moves["CUT"][0].get("y"), "0.0000")
        self.assertEqual(lines["CUT"][0].get("x"), "100.0000")
        self.assertEqual(lines["CUT"][0].get("y"), "0.0000")
        # CREASE has one segment: (50,0) -> (50,100).
        self.assertEqual(moves["CREASE"][0].get("x"), "50.0000")
        self.assertEqual(lines["CREASE"][0].get("y"), "100.0000")
        # GLUE has one segment: (0,100) -> (0,200).
        self.assertEqual(moves["GLUE"][0].get("y"), "100.0000")
        self.assertEqual(lines["GLUE"][0].get("y"), "200.0000")

    def test_every_segment_emitted_once(self) -> None:
        total = 0
        for layer in self.root.iter("Layer"):
            total += len(layer.findall(".//Move"))
        self.assertEqual(total, len(self.dieline.segments))

    def test_accepts_spec(self) -> None:
        spec = StructureSpec.from_dict(
            {"model_code": "test.simple", "dimensions": {"length": 1, "width": 1, "height": 1}}
        )
        parsed = _parse_svg(dieline_to_cf2(self.dieline, spec))
        self.assertEqual(parsed.findtext("Header/Metadata").split()[0], "model=test.simple")

    def test_real_dieline_is_valid_xml(self) -> None:
        dieline, _ = make_lock_bottom_dieline()
        parsed = _parse_svg(dieline_to_cf2(dieline))
        names = {layer.get("name") for layer in parsed.iter("Layer")}
        self.assertEqual(names, {"CUT", "CREASE", "GLUE"})


# ---------------------------------------------------------------------------
# Spot-colour PDF
# ---------------------------------------------------------------------------

class SpotPdfExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dieline = make_simple_dieline()
        self.pdf = dieline_to_spot_pdf(self.dieline)

    def test_returns_bytes_with_header_and_eof(self) -> None:
        self.assertIsInstance(self.pdf, bytes)
        self.assertTrue(self.pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(self.pdf.rstrip().endswith(b"%%EOF"))

    def test_has_object_streams_and_xref(self) -> None:
        for token in (b"obj", b"endobj", b"xref", b"trailer", b"startxref"):
            self.assertIn(token, self.pdf)

    def test_xref_offsets_are_valid(self) -> None:
        _validate_pdf_xref(self.pdf)

    def test_uses_separation_colour_space(self) -> None:
        self.assertIn(b"/Separation", self.pdf)
        self.assertIn(b"/DeviceCMYK", self.pdf)

    def test_all_four_colorants_present(self) -> None:
        for colorant in (b"/CutContour", b"/CreaseValley", b"/CreaseMountain", b"/Bleed"):
            self.assertIn(colorant, self.pdf)

    def test_separation_objects_are_referenced(self) -> None:
        for resource in (b"/CS0 6 0 R", b"/CS1 7 0 R", b"/CS2 8 0 R", b"/CS3 9 0 R"):
            self.assertIn(resource, self.pdf)

    def test_fold_direction_selects_plate(self) -> None:
        valley = CreaseSegment(0.0, 0.0, 50.0, 0.0, LAYER_CREASE, "dashed", 0.25, fold_direction="valley")
        mountain = CreaseSegment(
            50.0, 0.0, 100.0, 0.0, LAYER_CREASE, "dashed", 0.25, fold_direction="mountain"
        )
        dieline = Dieline("test.folds", (valley, mountain), (0.0, 0.0, 100.0, 0.0), {})
        pdf = dieline_to_spot_pdf(dieline)
        self.assertIn(b"/CS1 CS 1 SCN", pdf)  # CreaseValley selected
        self.assertIn(b"/CS2 CS 1 SCN", pdf)  # CreaseMountain selected

    def test_auto_fold_direction_uses_valley_plate(self) -> None:
        seg = CreaseSegment(0.0, 0.0, 10.0, 0.0, LAYER_CREASE, fold_direction="auto")
        dieline = Dieline("test.auto", (seg,), (0.0, 0.0, 10.0, 0.0), {})
        pdf = dieline_to_spot_pdf(dieline)
        self.assertIn(b"/CS1 CS 1 SCN", pdf)
        self.assertNotIn(b"/CS2 CS 1 SCN", pdf)

    def test_accepts_spec(self) -> None:
        spec = StructureSpec.from_dict(
            {"model_code": "test.simple", "dimensions": {"length": 1, "width": 1, "height": 1}}
        )
        self.assertTrue(dieline_to_spot_pdf(self.dieline, spec).startswith(b"%PDF"))

    def test_real_dieline_is_valid(self) -> None:
        dieline, _ = make_lock_bottom_dieline()
        pdf = dieline_to_spot_pdf(dieline)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        _validate_pdf_xref(pdf)
        self.assertIn(b"/Separation", pdf)
        for colorant in (b"/CutContour", b"/CreaseValley", b"/CreaseMountain", b"/Bleed"):
            self.assertIn(colorant, pdf)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
