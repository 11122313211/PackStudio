"""Content layout module: sourced packaging fields + safe-area placement."""

from packstudio.engines.packaging_engine.content.fields import (
    FIELD_MAP,
    PACKAGING_FIELDS,
    SCHEMA_VERSION,
    STATUS_MISSING,
    STATUS_USER_PROVIDED,
    FieldValue,
    PackagingField,
    build_fields,
    placeholder_for,
)
from packstudio.engines.packaging_engine.content.layout import (
    Panel,
    build_panels,
    choose_layout,
    layout_content,
    render_layout_svg,
)
from packstudio.engines.packaging_engine.content.models import ContentBrief, ContentLayoutResult

__all__ = [
    "FIELD_MAP",
    "PACKAGING_FIELDS",
    "SCHEMA_VERSION",
    "STATUS_MISSING",
    "STATUS_USER_PROVIDED",
    "FieldValue",
    "PackagingField",
    "build_fields",
    "placeholder_for",
    "Panel",
    "build_panels",
    "choose_layout",
    "layout_content",
    "render_layout_svg",
    "ContentBrief",
    "ContentLayoutResult",
]
