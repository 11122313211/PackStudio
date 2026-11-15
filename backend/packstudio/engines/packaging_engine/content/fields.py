"""Canonical packaging information fields with deterministic placeholders.

Rule: this module never invents regulatory, legal or commercial facts. A field
the user did not supply becomes an explicit placeholder such as
``[待提供：产品名称]`` or ``[待确认：许可证]`` instead of a guessed value.

Each field carries: ``key``, ``label_zh``, ``required`` and a placeholder
template. Optional alias/path lookups let a flat or nested product brief be
mapped onto the canonical fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STATUS_USER_PROVIDED = "user_provided"
STATUS_MISSING = "missing"

PLACEHOLDER_PROVIDE = "待提供"
PLACEHOLDER_CONFIRM = "待确认"
PLACEHOLDER_GENERATE = "待生成或粘贴"

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class PackagingField:
    """One canonical piece of packaging information."""

    key: str
    label_zh: str
    required: bool
    placeholder_kind: str
    sources: tuple[tuple[str, ...], ...]
    panel: str = "safe-area"

    @property
    def placeholder(self) -> str:
        return f"[{self.placeholder_kind}：{self.label_zh}]"


PACKAGING_FIELDS: tuple[PackagingField, ...] = (
    PackagingField(
        "product_name",
        "产品名称",
        True,
        PLACEHOLDER_PROVIDE,
        (
            ("product_name",),
            ("product", "name"),
            ("product_name_zh",),
            ("name",),
            ("产品名称",),
            ("产品名",),
            ("品名",),
        ),
    ),
    PackagingField(
        "brand",
        "品牌",
        True,
        PLACEHOLDER_PROVIDE,
        (
            ("brand",),
            ("brand_name",),
            ("品牌",),
            ("商标",),
        ),
    ),
    PackagingField(
        "manufacturer",
        "生产企业名称",
        True,
        PLACEHOLDER_PROVIDE,
        (
            ("manufacturer",),
            ("manufacturer_name",),
            ("business", "manufacturer"),
            ("producer",),
            ("生产企业",),
            ("生产企业名称",),
            ("生产商",),
        ),
    ),
    PackagingField(
        "license",
        "许可证",
        True,
        PLACEHOLDER_CONFIRM,
        (
            ("license",),
            ("license_number",),
            ("license_no",),
            ("standards", "license_number"),
            ("许可证",),
            ("许可证编号",),
            ("生产许可证",),
        ),
    ),
    PackagingField(
        "standard_no",
        "标准号",
        True,
        PLACEHOLDER_CONFIRM,
        (
            ("standard_no",),
            ("standard_number",),
            ("standards", "execution_standard"),
            ("execution_standard",),
            ("执行标准",),
            ("标准号",),
        ),
    ),
    PackagingField(
        "certification",
        "认证",
        False,
        PLACEHOLDER_CONFIRM,
        (
            ("certification",),
            ("certifications",),
            ("certs",),
            ("standards", "certifications"),
            ("认证",),
            ("认证信息",),
        ),
    ),
    PackagingField(
        "ingredients",
        "成分",
        True,
        PLACEHOLDER_PROVIDE,
        (
            ("ingredients",),
            ("ingredient",),
            ("product", "ingredients"),
            ("成分",),
            ("配料",),
            ("成分/配料",),
        ),
    ),
    PackagingField(
        "efficacy",
        "功效",
        False,
        PLACEHOLDER_PROVIDE,
        (
            ("efficacy",),
            ("efficacy_claims",),
            ("functions",),
            ("功效",),
            ("功效声明",),
        ),
    ),
    PackagingField(
        "net_weight",
        "净含量",
        True,
        PLACEHOLDER_PROVIDE,
        (
            ("net_weight",),
            ("net_content",),
            ("product", "net_content"),
            ("净含量",),
            ("净重",),
        ),
    ),
    PackagingField(
        "warnings",
        "警示语",
        True,
        PLACEHOLDER_PROVIDE,
        (
            ("warnings",),
            ("warning",),
            ("product", "warnings"),
            ("警示语",),
            ("警示",),
        ),
    ),
    PackagingField(
        "barcode",
        "条码",
        False,
        PLACEHOLDER_GENERATE,
        (
            ("barcode",),
            ("barcode_value",),
            ("product", "barcode"),
            ("条码",),
            ("商品条码",),
        ),
    ),
    PackagingField(
        "traceability",
        "追溯码",
        False,
        PLACEHOLDER_CONFIRM,
        (
            ("traceability",),
            ("traceability_code",),
            ("trace_code",),
            ("追溯码",),
            ("溯源码",),
        ),
    ),
    PackagingField(
        "date",
        "日期",
        False,
        PLACEHOLDER_PROVIDE,
        (
            ("date",),
            ("production_date",),
            ("batch",),
            ("batch_no",),
            ("生产日期",),
            ("批号",),
            ("日期",),
        ),
    ),
)

FIELD_MAP: dict[str, PackagingField] = {field.key: field for field in PACKAGING_FIELDS}


def get_field(key: str) -> PackagingField | None:
    return FIELD_MAP.get(key)


def field_keys() -> tuple[str, ...]:
    return tuple(FIELD_MAP)


@dataclass(frozen=True)
class FieldValue:
    """A resolved field: either the verbatim user text or an explicit placeholder."""

    key: str
    label_zh: str
    required: bool
    placeholder: str
    value: str
    status: str
    source: str
    panel: str = "safe-area"
    is_placeholder: bool = False

    @property
    def is_missing(self) -> bool:
        return self.is_placeholder

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label_zh": self.label_zh,
            "required": self.required,
            "placeholder": self.placeholder,
            "value": self.value,
            "status": self.status,
            "source": self.source,
            "panel": self.panel,
            "is_placeholder": self.is_placeholder,
        }


def _lookup(data: Any, path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def build_fields(raw: dict) -> tuple[FieldValue, ...]:
    """Map a user brief onto canonical fields without inventing facts."""
    resolved: list[FieldValue] = []
    for spec in PACKAGING_FIELDS:
        value = ""
        source = ""
        for path in spec.sources:
            candidate = _text(_lookup(raw, path))
            if candidate:
                value = candidate
                source = ".".join(path)
                break
        if value:
            resolved.append(
                FieldValue(
                    key=spec.key,
                    label_zh=spec.label_zh,
                    required=spec.required,
                    placeholder=spec.placeholder,
                    value=value,
                    status=STATUS_USER_PROVIDED,
                    source=source,
                    panel=spec.panel,
                    is_placeholder=False,
                )
            )
        else:
            resolved.append(
                FieldValue(
                    key=spec.key,
                    label_zh=spec.label_zh,
                    required=spec.required,
                    placeholder=spec.placeholder,
                    value=spec.placeholder,
                    status=STATUS_MISSING,
                    source="",
                    panel=spec.panel,
                    is_placeholder=True,
                )
            )
    return tuple(resolved)


def placeholder_for(key: str) -> str:
    spec = FIELD_MAP.get(key)
    if spec is None:
        return f"[{PLACEHOLDER_PROVIDE}：{key}]"
    return spec.placeholder
