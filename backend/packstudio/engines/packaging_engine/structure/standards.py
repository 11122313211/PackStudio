"""ECMA / FEFCO structural standard library (Phase 2).

Data-driven mapping of industry-standard packaging codes to structural
metadata: human-readable descriptions, default build parameters, compatible
engine box types and engineering rules.

This module is deliberately **data-only** — it does not import the geometry
engine or the validator, so the CLI, the MCP bridge and the validator can all
consume it without pulling in the renderer.

All physical numbers are engineering placeholders. They are useful for
ideation and for sanity-checking a request, but they are *not* a substitute for
a manufacturer's validated dieline, and every entry is flagged as such.
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.exceptions import KnowledgeMissingError
from packstudio.engines.packaging_engine.structure.registry import resolve_box_type

__all__ = [
    "ECMA_STANDARDS",
    "FEFCO_STANDARDS",
    "STANDARD_LOOKUP",
    "resolve_standard",
    "standards_report",
    "compatible_standards",
    "dimension_rules",
    "stacking_estimate",
]

# Error code raised when a requested standard code is not in the library.
STANDARD_MISSING = "STANDARD_MISSING"


def _entry(
    code: str,
    name_en: str,
    name_zh: str,
    description: str,
    defaults: dict,
    compatible: list[str],
    notes: str,
) -> dict:
    """Build a single standard entry with a normalised key order."""
    return {
        "code": code,
        "name_en": name_en,
        "name_zh": name_zh,
        "description": description,
        "default_parameters": dict(defaults),
        "compatible_box_types": list(compatible),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Default build parameters
# ---------------------------------------------------------------------------
# Folding cartons (ECMA A20/A01): thin board, small tucks, narrow glue tab.
_DEFAULTS_TUCK = {
    "shrink": 0.5,
    "tuck_height": 12.0,
    "glue_width": 10.0,
    "bleed": 3.0,
    "safe_margin": 3.0,
    "board_thickness": 0.3,
}

_DEFAULTS_SNAP = {
    "shrink": 0.5,
    "tuck_height": 15.0,
    "glue_width": 6.0,
    "bleed": 3.0,
    "safe_margin": 3.0,
    "board_thickness": 0.4,
}

_DEFAULTS_AUTOLOCK = {
    "shrink": 0.5,
    "tuck_height": 16.0,
    "glue_width": 8.0,
    "bleed": 3.0,
    "safe_margin": 3.0,
    "board_thickness": 0.5,
}

# Corrugated / heavier board (FEFCO): bigger glue tab, deeper allowances.
_DEFAULTS_RSC = {
    "shrink": 1.0,
    "tuck_height": 0.0,
    "glue_width": 35.0,
    "bleed": 5.0,
    "safe_margin": 3.0,
    "board_thickness": 3.0,
}

_DEFAULTS_FOLDER = {
    "shrink": 1.0,
    "tuck_height": 12.0,
    "glue_width": 20.0,
    "bleed": 5.0,
    "safe_margin": 3.0,
    "board_thickness": 1.5,
}

_DEFAULTS_TELESCOPIC = {
    "shrink": 1.0,
    "tuck_height": 0.0,
    "glue_width": 30.0,
    "bleed": 5.0,
    "safe_margin": 3.0,
    "board_thickness": 2.5,
}


# ---------------------------------------------------------------------------
# ECMA standards
# ---------------------------------------------------------------------------
ECMA_STANDARDS: dict[str, dict] = {
    "A20.20.01.01": _entry(
        "A20.20.01.01",
        "Straight Tuck End",
        "直线插口盒",
        "A straight tuck end folding carton: the top and bottom tuck flaps are "
        "cut on the front panel and fold into the box from the same direction. "
        "Common retail carton for dry goods.",
        _DEFAULTS_TUCK,
        ["carton.box_v2.straight", "carton.box_v2.hang_tab", "carton.box_v2.carry_handle"],
        "ECMA A20.20 family. The tuck height must exceed the board caliper to "
        "avoid re-opening. Values here are placeholders for ideation only.",
    ),
    "A20.20.03.01": _entry(
        "A20.20.03.01",
        "Reverse Tuck End",
        "反插口盒",
        "A reverse tuck end folding carton: the top tuck flap folds in from the "
        "front while the bottom tuck folds in from the back, giving a clean "
        "outer surface with no exposed tuck edge on the front face.",
        _DEFAULTS_TUCK,
        ["carton.box_v2.same_direction_tuck", "carton.box_v2.top_cover", "carton.box_v2.glue_bottom"],
        "ECMA A20.20 family. Reverse tuck needs a slightly deeper glue tab than "
        "a straight tuck to keep the corner rigid. Values are placeholders.",
    ),
    "A01.55.00.01": _entry(
        "A01.55.00.01",
        "Auto-lock Bottom / Crash-lock",
        "自动锁底/锁扣盒",
        "A crash-lock / auto-lock bottom carton: the bottom panels carry "
        "interlocking tabs that snap together without adhesive, so the box "
        "self-erects on the packaging line.",
        _DEFAULTS_AUTOLOCK,
        ["carton.box_v2.lock_bottom", "carton.box_v2.mailer"],
        "ECMA A01.55 family. Crash-lock tabs need a heavier board than a plain "
        "tuck end; defaults below are the lightest workable starting point and "
        "must be validated for the chosen board grade.",
    ),
    "A01.50.00.00": _entry(
        "A01.50.00.00",
        "Snap Closure",
        "卡口盖盒",
        "A snap-closure carton: a friction/snap lid that retains the contents "
        "without glue or tuck tabs, typically used for reusable retail packs.",
        _DEFAULTS_SNAP,
        ["carton.box_v2.top_cover", "carton.box_v2.mailer"],
        "ECMA A01.50 family. Snap retention depends on board caliper tolerance; "
        "shrink and tuck_height are starting values, not production settings.",
    ),
}


# ---------------------------------------------------------------------------
# FEFCO standards
# ---------------------------------------------------------------------------
FEFCO_STANDARDS: dict[str, dict] = {
    "0201": _entry(
        "0201",
        "Regular Slotted Container (RSC)",
        "常规开槽箱",
        "The most common corrugated shipping container: one piece with a "
        "manufacturer's joint on one edge and four flaps, the outer flaps being "
        "half the width so they meet at the centre of the box.",
        _DEFAULTS_RSC,
        ["carton.box_v2.shipping_carton"],
        "FEFCO 02 slotted family. The 0201 is the industry default shipping "
        "carton; the slot width default is 2 x caliper + 1 mm and is a converter "
        "choice, not a FEFCO figure.",
    ),
    "0412": _entry(
        "0412",
        "One Piece Folder",
        "一片式文件夹盒",
        "A one-piece folder (FEFCO 04 folder family): a single die-cut blank "
        "folds into a box with a bottom and a lid whose end flaps meet; only a "
        "single closure is required where the flaps collide.",
        _DEFAULTS_FOLDER,
        ["carton.box_v2.top_cover"],
        "FEFCO 04 folder family. Folders carry no manufacturer's joint by "
        "default, so glue_width is a converter choice for the optional edge "
        "tab; the default is a placeholder.",
    ),
    "0301": _entry(
        "0301",
        "Full Telescopic Box",
        "全套式天地盖箱",
        "A two-piece telescopic box from the FEFCO 0300 series: a base tray and "
        "a full-depth lid that slides over the sides, creating a double-wall "
        "perimeter for high stacking strength.",
        _DEFAULTS_TELESCOPIC,
        [],
        "FEFCO 0300 telescopic family. Telescopic boxes are inherently "
        "two-piece, so no single-piece carton.box_v2 code is listed as "
        "compatible. Represented by the engine as a base tray plus a separate "
        "lid rather than one blank.",
    ),
}


# ---------------------------------------------------------------------------
# Combined lookup
# ---------------------------------------------------------------------------
def _build_lookup() -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for standard_type, table in (("ECMA", ECMA_STANDARDS), ("FEFCO", FEFCO_STANDARDS)):
        for code, entry in table.items():
            lookup[code] = {**entry, "standard_type": standard_type}
    return lookup


STANDARD_LOOKUP: dict[str, dict] = _build_lookup()


def _lookup_key(code: str) -> str:
    return str(code).strip().lower()


def resolve_standard(code: str) -> dict:
    """Look up a standard by code (case-insensitive; ECMA and FEFCO).

    Raises :class:`KnowledgeMissingError` when the code is absent or empty.
    """
    if not isinstance(code, str) or not code.strip():
        raise KnowledgeMissingError(
            STANDARD_MISSING, "标准编码不能为空。", {"code": code}
        )
    key = _lookup_key(code)
    for canonical, entry in STANDARD_LOOKUP.items():
        if canonical.lower() == key:
            return dict(entry)
    raise KnowledgeMissingError(
        STANDARD_MISSING,
        f"未收录的标准编码：{code}",
        {"code": code},
    )


def standards_report() -> list[dict]:
    """Return every standard as a list, ECMA entries first then FEFCO."""
    report: list[dict] = []
    for standard_type, table in (("ECMA", ECMA_STANDARDS), ("FEFCO", FEFCO_STANDARDS)):
        for entry in table.values():
            report.append({**entry, "standard_type": standard_type})
    return report


def compatible_standards(box_code: str) -> list[dict]:
    """Return standards compatible with a given box type code.

    Matching combines two signals: the ``ecma``/``fefco`` code recorded on the
    box type in the registry, plus any standard that explicitly lists the box
    code in ``compatible_box_types``. Unknown box codes yield an empty list.
    """
    box = resolve_box_type(box_code)
    if box is None:
        return []

    wanted: set[str] = set()
    for field in ("ecma", "fefco"):
        value = getattr(box, field, "")
        if value:
            for canonical in STANDARD_LOOKUP:
                if canonical.lower() == value.strip().lower():
                    wanted.add(canonical)
    for entry in STANDARD_LOOKUP.values():
        if box.code in entry["compatible_box_types"]:
            wanted.add(entry["code"])

    return [STANDARD_LOOKUP[code] for code in sorted(wanted) if code in STANDARD_LOOKUP]


# ---------------------------------------------------------------------------
# Dimension rules
# ---------------------------------------------------------------------------
# Engineering limits per standard. All ratios are length / width, whichever
# side is treated as the opening. Values are starting-point placeholders.
_DIMENSION_RULES: dict[str, dict] = {
    "A20.20.01.01": {
        "min_aspect_ratio": 0.5,
        "max_aspect_ratio": 4.0,
        "recommended_board_thickness_mm": [0.2, 0.6],
        "typical_tuck_height_mm": 12.0,
        "typical_glue_width_mm": 10.0,
        "clearance_ratio": 0.02,
        "max_height_to_length_ratio": 3.0,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Folding carton; keep the tuck inside 1/3 of the short side.",
    },
    "A20.20.03.01": {
        "min_aspect_ratio": 0.5,
        "max_aspect_ratio": 4.0,
        "recommended_board_thickness_mm": [0.2, 0.6],
        "typical_tuck_height_mm": 12.0,
        "typical_glue_width_mm": 10.0,
        "clearance_ratio": 0.02,
        "max_height_to_length_ratio": 3.0,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Reverse tuck adds a small glue-tab allowance at the corner.",
    },
    "A01.55.00.01": {
        "min_aspect_ratio": 0.5,
        "max_aspect_ratio": 4.0,
        "recommended_board_thickness_mm": [0.4, 1.2],
        "typical_tuck_height_mm": 16.0,
        "typical_glue_width_mm": 8.0,
        "clearance_ratio": 0.03,
        "max_height_to_length_ratio": 3.0,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Crash-lock needs a heavier board to hold the snap; verify tab depth.",
    },
    "A01.50.00.00": {
        "min_aspect_ratio": 0.5,
        "max_aspect_ratio": 4.0,
        "recommended_board_thickness_mm": [0.3, 0.8],
        "typical_tuck_height_mm": 15.0,
        "typical_glue_width_mm": 6.0,
        "clearance_ratio": 0.02,
        "max_height_to_length_ratio": 3.0,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Snap retention depends on caliper tolerance; validate slip fit.",
    },
    "0201": {
        "min_aspect_ratio": 0.6,
        "max_aspect_ratio": 3.5,
        "recommended_board_thickness_mm": [1.5, 6.0],
        "typical_tuck_height_mm": 0.0,
        "typical_glue_width_mm": 35.0,
        "clearance_ratio": 0.03,
        "max_height_to_length_ratio": 3.0,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Corrugated RSC; slot width is 2 x caliper + 1 mm by convention.",
    },
    "0412": {
        "min_aspect_ratio": 0.6,
        "max_aspect_ratio": 4.0,
        "recommended_board_thickness_mm": [1.0, 3.0],
        "typical_tuck_height_mm": 12.0,
        "typical_glue_width_mm": 20.0,
        "clearance_ratio": 0.03,
        "max_height_to_length_ratio": 3.0,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Folder: the meeting lid flaps must clear the end wall.",
    },
    "0301": {
        "min_aspect_ratio": 0.6,
        "max_aspect_ratio": 3.5,
        "recommended_board_thickness_mm": [1.5, 5.0],
        "typical_tuck_height_mm": 0.0,
        "typical_glue_width_mm": 30.0,
        "clearance_ratio": 0.05,
        "max_height_to_length_ratio": 2.5,
        "typical_flap_overlap_mm": 0.0,
        "notes": "Two-piece telescope; lid overhang (3-8 mm) must be validated.",
    },
}

_DEFAULT_RULES: dict = {
    "min_aspect_ratio": 0.5,
    "max_aspect_ratio": 4.0,
    "recommended_board_thickness_mm": [0.2, 3.0],
    "typical_tuck_height_mm": 12.0,
    "typical_glue_width_mm": 10.0,
    "clearance_ratio": 0.03,
    "max_height_to_length_ratio": 3.0,
    "typical_flap_overlap_mm": 0.0,
    "notes": "No dedicated rule set; using generic carton limits.",
}


def dimension_rules(standard_code: str) -> dict:
    """Return engineering dimension rules for a standard code.

    Raises :class:`KnowledgeMissingError` for an unknown code.
    """
    standard = resolve_standard(standard_code)
    code = standard["code"]
    rules = dict(_DIMENSION_RULES.get(code, _DEFAULT_RULES))
    defaults = standard.get("default_parameters", {})
    # Overlay the standard's own defaults so the reported values stay in sync
    # with the library entry.
    rules["typical_tuck_height_mm"] = float(rules.get("typical_tuck_height_mm", defaults.get("tuck_height", 0.0)))
    rules["typical_glue_width_mm"] = float(rules.get("typical_glue_width_mm", defaults.get("glue_width", 0.0)))
    return {
        "standard_code": code,
        "standard_type": standard["standard_type"],
        "name_en": standard["name_en"],
        "name_zh": standard["name_zh"],
        **rules,
        "requires_manufacturer_validation": True,
    }


# ---------------------------------------------------------------------------
# Stacking / compression estimate
# ---------------------------------------------------------------------------
def stacking_estimate(
    length: float,
    width: float,
    height: float,
    material_gsm: float,
    stack_height: float,
) -> dict:
    """Simplified ISTA-style stacking/compression estimate.

    This is a thin, backward-compatible adapter over
    :func:`packstudio.engines.packaging_engine.transport.ista.stacking_estimate`.  The old
    five-argument signature is preserved: ``stack_height`` is in millimetres
    and is converted to the transport module's metre-based input, while
    ``material_gsm`` becomes ``board_gsm``.

    .. warning::

        Every numeric result is an engineering estimate. Corrugated
        compressive strength depends on the flute, board grade, moisture,
        handling and palletisation; a manufacturer or a certifying lab must
        validate the stack. The result always carries
        ``requires_manufacturer_validation = True``.
    """
    from packstudio.engines.packaging_engine.transport.ista import stacking_estimate as _ista

    return _ista(
        length_mm=length,
        width_mm=width,
        height_mm=height,
        stack_height_m=float(stack_height) / 1000.0,
        unit_weight_kg=None,
        board_gsm=material_gsm,
    )
