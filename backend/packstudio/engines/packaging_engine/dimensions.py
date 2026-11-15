"""Dimension conversions between finished and blank carton sizes.

The module normalizes three descriptions of a box:

``finished_inner``
    The clear inner space of the assembled box (the space the product sees).
``finished_outer``
    The external finished size, larger than the inner size by the board
    thickness on each wall.
``blank``
    The unfolded die-cut sheet, larger than the finished size by the
    production allowances (tuck tabs, dust flaps, glue tab).

All outputs are millimetres.  Every conversion is deterministic and every
value is an engineering approximation that a converter must validate against
the actual die.

The nominal rules implemented here are:

* ``finished_inner <-> finished_outer``: each dimension differs by
  ``2 * board_thickness``.
* ``finished <-> blank``:

  - ``blank_length = finished_length + 2 * tuck_height + glue_width``
  - ``blank_width = finished_width + 2 * dust_depth``
  - ``blank_height = finished_height`` (unchanged: it is the fold direction)
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.exceptions import RequestValidationError
from packstudio.engines.packaging_engine.models.ir import StructureSpec

__all__ = [
    "convert_dimensions",
    "blank_from_finished",
    "finished_from_blank",
    "material_usage",
    "DIMENSION_TYPES",
    "DEFAULT_TUCK_HEIGHT_MM",
    "DEFAULT_GLUE_WIDTH_MM",
    "DEFAULT_DUST_DEPTH_MM",
]

#: Supported dimension types.
DIMENSION_TYPES: tuple[str, ...] = ("finished_inner", "finished_outer", "blank")

DEFAULT_BOARD_THICKNESS_MM = 0.3
DEFAULT_TUCK_HEIGHT_MM = 12.0
DEFAULT_GLUE_WIDTH_MM = 11.0
DEFAULT_DUST_DEPTH_MM = 12.0

_REQUIRED_KEYS = ("length", "width", "height")


def _as_positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise RequestValidationError(
            "INVALID_DIMENSIONS",
            f"尺寸字段 {field} 必须是正数。",
            {"field": field, "value": value},
        )
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(
            "INVALID_DIMENSIONS",
            f"尺寸字段 {field} 必须是数字。",
            {"field": field, "value": value},
        ) from exc
    if number <= 0:
        raise RequestValidationError(
            "INVALID_DIMENSIONS",
            f"尺寸字段 {field} 必须大于 0。",
            {"field": field, "value": value},
        )
    return number


def _validate_type(dimension_type: str) -> str:
    normalized = str(dimension_type).strip()
    if normalized not in DIMENSION_TYPES:
        raise RequestValidationError(
            "INVALID_DIMENSION_TYPE",
            f"不支持的尺寸类型：{dimension_type}",
            {"supported": list(DIMENSION_TYPES)},
        )
    return normalized


def _read_dims(dims: object) -> dict[str, float]:
    if not isinstance(dims, dict):
        raise RequestValidationError("INVALID_DIMENSIONS", "尺寸必须是对象。")
    missing = [key for key in _REQUIRED_KEYS if key not in dims]
    if missing:
        raise RequestValidationError(
            "MISSING_REQUIRED_FIELD",
            "缺少结构尺寸。",
            {"missing_fields": missing},
        )
    return {key: _as_positive_float(dims[key], key) for key in _REQUIRED_KEYS}


def _allowances(
    tuck_height: float | None = None,
    glue_width: float | None = None,
    dust_depth: float | None = None,
) -> dict[str, float]:
    """Nominal production allowances added to length / width."""
    return {
        "length": 2.0 * float(DEFAULT_TUCK_HEIGHT_MM if tuck_height is None else tuck_height)
        + float(DEFAULT_GLUE_WIDTH_MM if glue_width is None else glue_width),
        "width": 2.0 * float(DEFAULT_DUST_DEPTH_MM if dust_depth is None else dust_depth),
        "height": 0.0,
    }


def _convert_type(
    dims: dict[str, float],
    from_type: str,
    to_type: str,
    board_thickness: float,
    allowances: dict[str, float],
) -> dict[str, float]:
    """Apply the additive delta between two supported dimension types.

    ``finished_inner <-> finished_outer`` differ by ``2 * board_thickness``
    on every axis.  ``finished <-> blank`` differ by the production
    allowances, applied directly to the supplied finished dimensions (so
    ``finished_outer -> blank`` and ``finished_inner -> blank`` differ by
    exactly ``2 * board_thickness``).
    """
    if from_type == to_type:
        return dict(dims)
    pair = {from_type, to_type}
    if pair == {"finished_inner", "finished_outer"}:
        sign = 1.0 if to_type == "finished_outer" else -1.0
        delta = 2.0 * board_thickness
        return {key: value + sign * delta for key, value in dims.items()}
    if "blank" in pair:
        sign = 1.0 if to_type == "blank" else -1.0
        return {key: value + sign * allowances[key] for key, value in dims.items()}
    # Unreachable: _validate_type already restricted the inputs.
    raise RequestValidationError(
        "INVALID_DIMENSION_TYPE",
        f"无法在 {from_type} 与 {to_type} 之间转换。",
        {"supported": list(DIMENSION_TYPES)},
    )


def convert_dimensions(
    dims: dict,
    from_type: str,
    to_type: str,
    board_thickness: float = DEFAULT_BOARD_THICKNESS_MM,
) -> dict:
    """Convert between dimension types.

    ``from_type`` / ``to_type`` are one of ``'finished_inner'``,
    ``'finished_outer'`` or ``'blank'``.  ``board_thickness`` is in
    millimetres.

    Returns ``{'length': mm, 'width': mm, 'height': mm, 'unit': 'mm',
    'dimension_type': to_type}``.

    Raises :class:`~packstudio.engines.packaging_engine.exceptions.RequestValidationError`
    for missing fields, non-positive dimensions or unknown dimension types.
    """
    if isinstance(board_thickness, bool) or board_thickness is None:
        raise RequestValidationError(
            "INVALID_BOARD_THICKNESS",
            "纸板厚度必须是正数。",
            {"value": board_thickness},
        )
    try:
        thickness = float(board_thickness)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(
            "INVALID_BOARD_THICKNESS",
            "纸板厚度必须是数字。",
            {"value": board_thickness},
        ) from exc
    if thickness <= 0:
        raise RequestValidationError(
            "INVALID_BOARD_THICKNESS",
            "纸板厚度必须大于 0。",
            {"value": board_thickness},
        )

    source_type = _validate_type(from_type)
    target_type = _validate_type(to_type)
    parsed = _read_dims(dims)
    allowances = _allowances()
    result = _convert_type(parsed, source_type, target_type, thickness, allowances)
    return {
        "length": result["length"],
        "width": result["width"],
        "height": result["height"],
        "unit": "mm",
        "dimension_type": target_type,
    }


def blank_from_finished(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    spec: StructureSpec,
) -> dict:
    """Calculate the blank (unfolded) dimensions from finished dimensions.

    Uses ``spec.tuck_height``, ``spec.glue_width`` and (via ``spec.extras``)
    ``dust_depth``; the height is unchanged because it is the fold direction.
    """
    dims = {"length": length_mm, "width": width_mm, "height": height_mm}
    parsed = _read_dims(dims)
    dust_depth = spec.extras.get("dust_depth", DEFAULT_DUST_DEPTH_MM)
    allowances = _allowances(spec.tuck_height, spec.glue_width, dust_depth)
    return {
        "length": parsed["length"] + allowances["length"],
        "width": parsed["width"] + allowances["width"],
        "height": parsed["height"],
        "unit": "mm",
        "dimension_type": "blank",
        "allowances_mm": {
            "length": allowances["length"],
            "width": allowances["width"],
            "height": allowances["height"],
        },
    }


def finished_from_blank(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    spec: StructureSpec | None = None,
) -> dict:
    """Inverse of :func:`blank_from_finished`."""
    dims = {"length": length_mm, "width": width_mm, "height": height_mm}
    parsed = _read_dims(dims)
    dust_depth = (
        spec.extras.get("dust_depth", DEFAULT_DUST_DEPTH_MM) if spec is not None else None
    )
    allowances = _allowances(None if spec is None else spec.tuck_height,
                             None if spec is None else spec.glue_width,
                             dust_depth)
    result = {key: value - allowances[key] for key, value in parsed.items()}
    return {
        "length": result["length"],
        "width": result["width"],
        "height": result["height"],
        "unit": "mm",
        "dimension_type": "finished_inner",
    }


def material_usage(length_mm: float, width_mm: float, board_gsm: float | None = None) -> dict:
    """Estimate material usage for a blank sheet.

    Returns the blank area in square millimetres and, when ``board_gsm``
    (g/m^2) is supplied, the approximate sheet mass in grams.
    """
    length = _as_positive_float(length_mm, "length_mm")
    width = _as_positive_float(width_mm, "width_mm")
    area_mm2 = length * width
    result: dict = {
        "blank_area_mm2": area_mm2,
        "unit": "mm2",
        "dimension_type": "blank",
    }
    if board_gsm is not None:
        gsm = _as_positive_float(board_gsm, "board_gsm")
        result["estimated_sheet_mass_g"] = area_mm2 * gsm / 1_000_000.0
    return result
