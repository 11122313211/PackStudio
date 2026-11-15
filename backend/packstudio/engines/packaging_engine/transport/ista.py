"""Simplified ISTA 3A stacking / compression estimate.

This module intentionally implements a **documented approximation**, not a
certified calculation:

    F = k * (H - h) * W * L      (N)

where ``H`` is the total stack height (m), ``h`` is the unit height (m),
``W`` and ``L`` are the box width and length (m) and ``k`` is a board
specific compression coefficient (N/m^2).  When ``k_factor`` is not supplied
the coefficient is estimated from the board grammage as
``k ~= 0.5 * board_gsm * 1e-3``.

.. warning::

    Every numeric value produced here is an engineering estimate.  Compressive
    strength depends on flute type, board grade, grammage, caliper, moisture,
    temperature, handling and palletisation.  All results carry
    ``requires_manufacturer_validation = True`` and must be validated against
    measured data (ISTA 3A, ISTA 3B, GB/T 4857.3, ...) before production.
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.exceptions import RequestValidationError

__all__ = ["stacking_estimate", "estimate_k_from_gsm"]

#: Simplified compressive-strength coefficient used when ``board_gsm`` is given.
#: ``k = 0.5 * board_gsm * 1e-3`` (N/m^2 per g/m^2).
GSM_TO_K_FACTOR = 0.5 * 1e-3

#: Standard gravitational acceleration used to convert mass to force (N/kg).
GRAVITY_N_PER_KG = 9.81


def estimate_k_from_gsm(board_gsm: float) -> float:
    """Return the simplified compression coefficient for a board grammage.

    ``board_gsm`` is the nominal board grammage in g/m^2.  The result is in
    N/m^2 and is a *placeholder* estimate only.
    """
    gsm = float(board_gsm)
    if gsm <= 0:
        raise RequestValidationError(
            "INVALID_BOARD_GSM",
            "纸板克重必须大于 0。",
            {"field": "board_gsm", "value": board_gsm},
        )
    return GSM_TO_K_FACTOR * gsm


def _positive_number(value: object, field: str) -> float:
    if value is None:
        raise RequestValidationError(
            "MISSING_REQUIRED_FIELD",
            f"缺少必填参数：{field}。",
            {"missing_fields": [field]},
        )
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(
            "INVALID_NUMERIC_FIELD",
            f"{field} 必须是数字。",
            {"field": field, "value": value},
        ) from exc
    return number


def stacking_estimate(
    length_mm: float,
    width_mm: float,
    height_mm: float,
    stack_height_m: float,
    unit_weight_kg: float,
    k_factor: float | None = None,
    board_gsm: float | None = None,
) -> dict:
    """Simplified ISTA 3A stacking / compression estimate.

    Parameters
    ----------
    length_mm, width_mm, height_mm:
        Finished box dimensions in millimetres.
    stack_height_m:
        Total stacked height in metres.
    unit_weight_kg:
        Weight of a single packed unit in kilograms.
    k_factor:
        Optional board compression coefficient in N/m^2.  When ``None`` the
        coefficient is estimated from ``board_gsm``.
    board_gsm:
        Board grammage in g/m^2 used to estimate ``k`` when ``k_factor`` is
        omitted.

    Returns
    -------
    dict
        A dict of estimates.  Every numeric value is explicitly an estimate
        requiring manufacturer validation (``requires_manufacturer_validation``
        is always ``True``).  ``safety_factor`` and ``recommended_stack_layers``
        are ``None`` whenever their inputs are missing.
    """
    length = _positive_number(length_mm, "length_mm")
    width = _positive_number(width_mm, "width_mm")
    height = _positive_number(height_mm, "height_mm")
    stack_height = _positive_number(stack_height_m, "stack_height_m")

    inputs = {
        "length_mm": length,
        "width_mm": width,
        "height_mm": height,
        "stack_height_m": stack_height,
        "unit_weight_kg": unit_weight_kg,
        "k_factor": k_factor,
        "board_gsm": board_gsm,
    }

    # Resolve the compression coefficient.
    if k_factor is not None:
        k = float(k_factor)
    elif board_gsm is not None:
        k = estimate_k_from_gsm(board_gsm)
    else:
        k = None
    inputs["k_estimate"] = k

    # F = k * (H - h) * W * L, all lengths in metres.
    length_m = length / 1000.0
    width_m = width / 1000.0
    height_m = height / 1000.0
    stack_height_above = stack_height - height_m

    estimated_compressive_strength_n: float | None = None
    if k is not None:
        estimated_compressive_strength_n = max(0.0, k * stack_height_above * width_m * length_m)

    bct_n = estimated_compressive_strength_n
    estimated_bct_kn = None if bct_n is None else bct_n / 1000.0
    estimated_bct_kg = None if bct_n is None else bct_n / GRAVITY_N_PER_KG

    # Stack load above the bottom unit and derived safety figures.
    safety_factor: float | None = None
    recommended_stack_layers: int | None = None
    if unit_weight_kg is not None and bct_n and bct_n > 0:
        unit_weight = float(unit_weight_kg)
        if unit_weight > 0:
            units_above = max(0.0, stack_height_above / height_m)
            stack_load_n = unit_weight * GRAVITY_N_PER_KG * units_above
            safety_factor = stack_load_n / bct_n
            recommended_stack_layers = int(bct_n // (unit_weight * GRAVITY_N_PER_KG)) + 1

    notes = (
        "简化 ISTA 3A 静态堆码估算：F ≈ k×(H−h)×W×L，k 由纸板克重近似。"
        "所有数值均为估算，需厂商验证：堆码层数、抗压强度与运输验证必须由厂商或认证实验室"
        "按 ISTA 3A/3B、GB/T 4857.3 实测完成，不得直接用于生产或合规判定。"
    )

    return {
        "method": "ISTA 3A simplified",
        "estimated_compressive_strength_n": estimated_compressive_strength_n,
        "estimated_bct_kn": estimated_bct_kn,
        "estimated_bct_kg": estimated_bct_kg,
        "safety_factor": safety_factor,
        "recommended_stack_layers": recommended_stack_layers,
        "requires_manufacturer_validation": True,
        "inputs": inputs,
        "notes": notes,
    }
