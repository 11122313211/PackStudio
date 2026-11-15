"""Tests for the contact sheet generator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from packstudio.engines.packaging_engine.contact_sheet import generate_contact_sheet


class ContactSheetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_generates_html_file(self) -> None:
        html = generate_contact_sheet(self.tmp_path)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertTrue((self.tmp_path / "contact-sheet.html").exists())

    def test_contains_all_box_types(self) -> None:
        from packstudio.engines.packaging_engine.structure.registry import BOX_TYPES

        implemented = [b for b in BOX_TYPES if b.implemented]
        html = generate_contact_sheet(self.tmp_path)
        # Each cell has a cell-header with the box name
        for b in implemented:
            name = b.name_zh or b.name_en
            self.assertIn(name, html, f"missing box type: {name}")

    def test_cell_count_matches_implemented_types(self) -> None:
        from packstudio.engines.packaging_engine.structure.registry import BOX_TYPES

        implemented = [b for b in BOX_TYPES if b.implemented]
        html = generate_contact_sheet(self.tmp_path)
        self.assertEqual(html.count('class="cell"'), len(implemented))

    def test_svgs_are_embedded(self) -> None:
        html = generate_contact_sheet(self.tmp_path)
        # Each cell should have an SVG
        self.assertEqual(html.count("<svg"), html.count('class="cell"'))

    def test_html_is_valid_utf8(self) -> None:
        html = generate_contact_sheet(self.tmp_path)
        # Should not contain replacement characters
        self.assertNotIn("\ufffd", html)

    def test_count_limit(self) -> None:
        html = generate_contact_sheet(self.tmp_path, count=3)
        self.assertEqual(html.count('class="cell"'), 3)


if __name__ == "__main__":
    unittest.main()
