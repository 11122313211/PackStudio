"""Deterministic dieline render dispatcher.

Renders a StructureSpec into a Dieline. The LLM never produces geometry.
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.engine.base import SegmentBuilder
from packstudio.engines.packaging_engine.engine.cartons import BUILDERS
from packstudio.engines.packaging_engine.exceptions import StructureNotImplementedError
from packstudio.engines.packaging_engine.models.ir import Dieline, StructureSpec
from packstudio.engines.packaging_engine.structure.registry import resolve_box_type


def render(spec: StructureSpec) -> Dieline:
    box = resolve_box_type(spec.model_code)
    if box is None or not box.implemented:
        raise StructureNotImplementedError(
            "STRUCTURE_NOT_IMPLEMENTED",
            f"盒型未实现：{spec.model_code}",
            {"model_code": spec.model_code, "status": "NOT_IMPLEMENTED"},
        )
    builder = BUILDERS.get(box.code)
    if builder is None:
        raise StructureNotImplementedError(
            "STRUCTURE_NOT_IMPLEMENTED",
            f"盒型未实现：{box.code}",
            {"model_code": box.code, "status": "NOT_IMPLEMENTED"},
        )

    cut_builder, crease_builder, glue_layer, panels, bounds = builder(spec)
    segments = list(cut_builder.segments) + list(crease_builder.segments)

    # Emit the glue fold line (where glue is applied) in the GLUE layer.
    # The glue tab sits to the left of x=0; the fold is the vertical at x=0.
    glue_builder = SegmentBuilder()
    glue_builder.line(0.0, 0.0, 0.0, spec.dimensions.height, glue_layer, "glue", 0.25, "GLUE_FOLD")
    segments.extend(glue_builder.segments)

    metadata = {
        "box_type": box.code,
        "box_name_zh": box.name_zh,
        "ecma": box.ecma,
        "fefco": box.fefco,
        "family": box.family,
        "dimensions_mm": {
            "length": spec.dimensions.length,
            "width": spec.dimensions.width,
            "height": spec.dimensions.height,
        },
        "parameters_mm": {
            "board_thickness": spec.board_thickness,
            "bleed": spec.bleed,
            "safe_margin": spec.safe_margin,
            "shrink": spec.shrink,
            "tuck_height": spec.tuck_height,
            "glue_width": spec.glue_width,
        },
        "panels": panels,
        "output_mode": spec.output_mode,
        "review_required": "REQUIRES_MANUFACTURER_REVIEW",
    }
    return Dieline(
        model_code=box.code,
        segments=segments,
        bounds=bounds,
        metadata=metadata,
    )


def render_from_dict(data: dict) -> Dieline:
    return render(StructureSpec.from_dict(data))
