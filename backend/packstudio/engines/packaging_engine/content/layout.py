"""Deterministic content layout: write sourced fields into a dieline's safe areas.

Rules enforced here (see SKILL.md "安全边界"):
- Never invent regulatory/legal facts: only verbatim user text and explicit
  placeholders (``[待提供：…]`` / ``[待确认：…]``) are placed.
- Never modify the user's original text/logo/layout: the dieline layers are
  copied verbatim and only a new content layer is added.
- Never place text on cut lines (best effort: also avoid crease lines).
"""

from __future__ import annotations

import json
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from packstudio.engines.packaging_engine.content.fields import STATUS_MISSING, STATUS_USER_PROVIDED
from packstudio.engines.packaging_engine.content.models import ContentBrief, ContentLayoutResult
from packstudio.engines.packaging_engine.exceptions import RequestValidationError
from packstudio.engines.packaging_engine.xmlsafe import safe_fromstring
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_BLEED,
    LAYER_CUT,
    LAYER_CREASE,
    LAYER_GLUE,
    LAYER_GUIDE,
    LAYER_SAFETY,
    LAYER_TEXT,
    Dieline,
    StructureSpec,
)

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

SCHEMA_VERSION = "1.0"

FONT_CANDIDATES = (3.2, 2.8, 2.4, 2.0, 1.7)
CHAR_FACTOR = 0.85
LINE_HEIGHT_FACTOR = 1.45
GAP_FACTOR = 0.35

LAYER_STYLES: dict[str, dict[str, str]] = {
    LAYER_CUT: {"stroke": "#c0392b", "stroke-width": "0.5", "stroke-dasharray": "none"},
    LAYER_CREASE: {"stroke": "#1f618d", "stroke-width": "0.4", "stroke-dasharray": "3 2"},
    LAYER_BLEED: {"stroke": "#1e8449", "stroke-width": "0.3", "stroke-dasharray": "4 3"},
    LAYER_SAFETY: {"stroke": "#7f8c8d", "stroke-width": "0.3", "stroke-dasharray": "0.5 3"},
    LAYER_GLUE: {"stroke": "#b9770e", "stroke-width": "0.4", "stroke-dasharray": "6 3"},
    LAYER_GUIDE: {"stroke": "#95a5a6", "stroke-width": "0.2", "stroke-dasharray": "2 3"},
    LAYER_TEXT: {"stroke": "#111111", "stroke-width": "0"},
}

LAYER_ORDER = (
    LAYER_BLEED,
    LAYER_SAFETY,
    LAYER_GLUE,
    LAYER_CREASE,
    LAYER_GUIDE,
    LAYER_CUT,
)


@dataclass(frozen=True)
class Panel:
    """A rectangular region on the dieline where content may be written."""

    panel_id: str
    role: str
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "role": self.role,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


def _fmt(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0")
    if text.endswith("."):
        text = text[:-1]
    if text in ("", "-", "-0"):
        text = "0"
    return text


def _wrap(value: str, max_chars: int) -> list[str]:
    return textwrap.wrap(
        value,
        width=max(6, max_chars),
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
    ) or [""]


def _segment_intersects_rect(
    x1: float, y1: float, x2: float, y2: float,
    rx: float, ry: float, rw: float, rh: float,
) -> bool:
    """Liang-Barsky segment/rectangle intersection (inclusive of boundaries)."""
    dx = x2 - x1
    dy = y2 - y1
    p = [-dx, dx, -dy, dy]
    q = [rx - x1, x1 - (rx + rw), ry - y1, y1 - (ry + rh)]
    u1 = 0.0
    u2 = 1.0
    for i in range(4):
        if abs(p[i]) < 1e-12:
            if q[i] < 0:
                return False
        else:
            t = q[i] / p[i]
            if p[i] < 0:
                if t > u2:
                    return False
                if t > u1:
                    u1 = t
            else:
                if t < u1:
                    return False
                if t < u2:
                    u2 = t
    return u1 <= u2


def _panels_from_metadata(dieline: Dieline) -> list[Panel]:
    raw = dieline.metadata.get("panels")
    panels: list[Panel] = []
    if not isinstance(raw, Iterable):
        return panels
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            panels.append(
                Panel(
                    panel_id=str(item["panel_id"]),
                    role=str(item.get("role", "panel")),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    width=float(item["width"]),
                    height=float(item["height"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return panels


def _fallback_panels(dieline: Dieline, safe_margin: float) -> list[Panel]:
    """Derive candidate panels from the dieline bounds when no metadata exists."""
    minx, miny, maxx, maxy = dieline.bounds
    width = maxx - minx
    height = maxy - miny
    inset = safe_margin + 1.0
    total_w = width - 2 * inset
    total_h = height - 2 * inset
    if total_w <= 0 or total_h <= 0:
        return []
    columns = 3 if width >= 3 * (2 * inset + 12) else (2 if width >= 2 * (2 * inset + 10) else 1)
    gap = 0.0
    col_w = (total_w - (columns - 1) * gap) / columns
    panels: list[Panel] = []
    for index in range(columns):
        panels.append(
            Panel(
                panel_id=f"panel-{index + 1}",
                role="panel",
                x=minx + inset + index * (col_w + gap),
                y=miny + inset,
                width=col_w,
                height=total_h,
            )
        )
    return panels


def _safe_panels(
    panels: list[Panel],
    dieline: Dieline,
    safe_margin: float,
) -> list[Panel]:
    min_area = (2 * safe_margin + 4) * (2 * safe_margin + 4)
    structural = [s for s in dieline.segments if s.layer in (LAYER_CUT, LAYER_CREASE)]
    cuts_only = [s for s in dieline.segments if s.layer == LAYER_CUT]
    for layers in (structural, cuts_only):
        safe: list[Panel] = []
        for panel in panels:
            if panel.width <= 0 or panel.height <= 0:
                continue
            if panel.width * panel.height < min_area:
                continue
            if any(
                _segment_intersects_rect(
                    s.x1, s.y1, s.x2, s.y2, panel.x, panel.y, panel.width, panel.height
                )
                for s in layers
            ):
                continue
            safe.append(panel)
        if safe:
            return safe
    return []


def build_panels(dieline: Dieline, spec: StructureSpec, safe_margin: float) -> list[Panel]:
    """Return the panels available for content, excluding cut/crease lines."""
    candidates = _panels_from_metadata(dieline) or _fallback_panels(dieline, safe_margin)
    safe = _safe_panels(candidates, dieline, safe_margin)
    return safe


def _attempt(
    fields,
    panels: list[Panel],
    font_size: float,
    safe_margin: float,
) -> tuple[list[dict[str, Any]], list[Any]]:
    line_height = font_size * LINE_HEIGHT_FACTOR
    gap = font_size * GAP_FACTOR
    cursors = {panel.panel_id: panel.y + safe_margin for panel in panels}
    placed: list[dict[str, Any]] = []
    deferred: list[Any] = []
    last_id = panels[0].panel_id
    for field in fields:
        order = [p for p in panels if p.panel_id == last_id] + [
            p for p in panels if p.panel_id != last_id
        ]
        chosen = None
        for panel in order:
            usable_w = panel.width - 2 * safe_margin
            chars = max(6, int(usable_w / (font_size * CHAR_FACTOR)))
            lines = _wrap(field.value, chars)
            last_baseline = cursors[panel.panel_id] + (len(lines) - 1) * line_height
            bottom = panel.y + panel.height - safe_margin
            if last_baseline <= bottom:
                chosen = (panel, lines, last_baseline, usable_w)
                break
        if chosen is None:
            deferred.append(field)
            continue
        panel, lines, last_baseline, usable_w = chosen
        x = panel.x + safe_margin
        y = cursors[panel.panel_id]
        placed.append(
            {
                "field_id": field.key,
                "label_zh": field.label_zh,
                "value": field.value,
                "status": field.status,
                "is_placeholder": field.is_placeholder,
                "panel": panel.panel_id,
                "panel_role": panel.role,
                "x": x,
                "y": y,
                "width": usable_w,
                "line_count": len(lines),
                "first_baseline_y": y,
                "last_baseline_y": last_baseline,
                "text_box": [x, y - font_size * 0.85, usable_w, len(lines) * line_height],
            }
        )
        cursors[panel.panel_id] = last_baseline + line_height + gap
        last_id = panel.panel_id
    return placed, deferred


def choose_layout(
    fields,
    panels: list[Panel],
    safe_margin: float,
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for font_size in FONT_CANDIDATES:
        placed, deferred = _attempt(fields, panels, font_size, safe_margin)
        score = (len(placed), -len(deferred), font_size)
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "font_size": font_size,
                "placed": placed,
                "deferred": deferred,
            }
    assert best is not None
    return best


def render_layout_svg(
    dieline: Dieline,
    panels: list[Panel],
    placed: list[dict[str, Any]],
    font_size: float,
    safe_margin: float,
    spec: StructureSpec,
) -> str:
    minx, miny, maxx, maxy = dieline.bounds
    width = maxx - minx
    height = maxy - miny
    root = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "version": "1.1",
            "width": f"{width:.3f}",
            "height": f"{height:.3f}",
            "viewBox": f"{_fmt(minx)} {_fmt(miny)} {_fmt(width)} {_fmt(height)}",
            "data-generator": "aiskill-packaging.content",
            "data-model": dieline.model_code,
            "data-schema-version": SCHEMA_VERSION,
        },
    )
    title = ET.SubElement(root, f"{{{SVG_NS}}}title")
    title.text = f"{dieline.model_code} content layout"
    desc = ET.SubElement(root, f"{{{SVG_NS}}}desc")
    desc.text = (
        "Dieline structure copied verbatim; only an added content layer places "
        "sourced fields or explicit placeholders in safe areas."
    )
    metadata = ET.SubElement(root, f"{{{SVG_NS}}}metadata", {"id": "packaging-content-metadata"})
    metadata.text = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "model_code": dieline.model_code,
            "material": spec.material,
            "board_thickness": spec.board_thickness,
            "safe_margin": safe_margin,
            "font_size": font_size,
            "panel_count": len(panels),
            "placed_count": len(placed),
            "manual_review_required": True,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for layer in LAYER_ORDER:
        group = ET.SubElement(
            root, f"{{{SVG_NS}}}g", {"id": f"layer-{layer.lower()}", "data-layer": layer}
        )
        style = LAYER_STYLES[layer]
        for segment in dieline.segments:
            if segment.layer != layer:
                continue
            ET.SubElement(
                group,
                f"{{{SVG_NS}}}path",
                {
                    "d": f"M {_fmt(segment.x1)} {_fmt(segment.y1)} L {_fmt(segment.x2)} {_fmt(segment.y2)}",
                    "fill": "none",
                    "stroke": style["stroke"],
                    "stroke-width": style["stroke-width"],
                    "stroke-dasharray": style["stroke-dasharray"],
                    "data-label": segment.label,
                },
            )
    guides = ET.SubElement(
        root, f"{{{SVG_NS}}}g", {"id": "layer-content-guides", "data-layer": "GUIDE"}
    )
    for panel in panels:
        ET.SubElement(
            guides,
            f"{{{SVG_NS}}}rect",
            {
                "x": _fmt(panel.x),
                "y": _fmt(panel.y),
                "width": _fmt(panel.width),
                "height": _fmt(panel.height),
                "fill": "none",
                "stroke": "#cccccc",
                "stroke-width": "0.15",
                "stroke-dasharray": "1 2",
                "data-panel": panel.panel_id,
                "data-role": panel.role,
            },
        )
    content = ET.SubElement(
        root, f"{{{SVG_NS}}}g", {"id": "layer-content", "data-layer": LAYER_TEXT}
    )
    line_height = font_size * LINE_HEIGHT_FACTOR
    for item in placed:
        group = ET.SubElement(
            content,
            f"{{{SVG_NS}}}g",
            {
                "id": f"field-{item['field_id']}",
                "data-field-id": item["field_id"],
                "data-status": item["status"],
                "data-panel": item["panel"],
                "data-placeholder": "true" if item["is_placeholder"] else "false",
            },
        )
        text = ET.SubElement(
            group,
            f"{{{SVG_NS}}}text",
            {
                "x": _fmt(item["x"]),
                "y": _fmt(item["y"]),
                "font-family": "sans-serif",
                "font-size": _fmt(font_size),
                "fill": "#111111",
            },
        )
        lines = _wrap(item["value"], max(6, int(item["width"] / (font_size * CHAR_FACTOR))))
        for index, line in enumerate(lines):
            tspan = ET.SubElement(
                text,
                f"{{{SVG_NS}}}tspan",
                {
                    "x": _fmt(item["x"]),
                    "dy": "0" if index == 0 else _fmt(line_height),
                },
            )
            tspan.text = line
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _validate(
    svg_text: str,
    dieline: Dieline,
    placed: list[dict[str, Any]],
    deferred: list[Any],
    panels: list[Panel],
    fields,
    safe_margin: float,
) -> dict[str, Any]:
    cut_segments = [s for s in dieline.segments if s.layer == LAYER_CUT]
    expected_cuts = [
        f"M {_fmt(s.x1)} {_fmt(s.y1)} L {_fmt(s.x2)} {_fmt(s.y2)}" for s in cut_segments
    ]
    cut_layer_untouched = all(exp in svg_text for exp in expected_cuts)

    try:
        parsed = safe_fromstring(svg_text)  # 防御式解析：拒绝 DTD/实体（见 xmlsafe）
        ids = [el.attrib.get("id") for el in parsed.iter() if el.attrib.get("id")]
        unique_ids = len(ids) == len(set(ids))
    except ET.ParseError:
        parsed = None
        ids = []
        unique_ids = False

    text_on_cut: list[str] = []
    within_safe: list[str] = []
    panel_by_id = {panel.panel_id: panel for panel in panels}
    for item in placed:
        box = item["text_box"]
        panel = panel_by_id.get(item["panel"])
        inside = False
        if panel is not None:
            inside = (
                box[0] >= panel.x - 1e-6
                and box[1] >= panel.y - 1e-6
                and box[0] + box[2] <= panel.x + panel.width + 1e-6
                and box[1] + box[3] <= panel.y + panel.height + 1e-6
            )
        for segment in cut_segments:
            if _segment_intersects_rect(
                segment.x1, segment.y1, segment.x2, segment.y2, *box
            ):
                text_on_cut.append(item["field_id"])
                break
        if inside and item["field_id"] not in text_on_cut:
            within_safe.append(item["field_id"])

    missing_count = sum(1 for field in fields if field.is_placeholder)
    checks = {
        "svg_xml_valid": parsed is not None,
        "cut_layer_untouched": cut_layer_untouched,
        "unique_ids": unique_ids,
        "no_text_on_cut_lines": not text_on_cut,
        "text_within_safe_area": bool(within_safe),
        "all_fields_have_status": all(bool(field.status) for field in fields),
        "all_fields_have_source": all(
            bool(field.source) or field.is_placeholder for field in fields
        ),
        "all_fields_placed_or_deferred": len(placed) + len(deferred) == len(fields),
        "no_text_on_cut_lines_details": text_on_cut,
        "deferred_fields": [field.key for field in deferred],
    }
    warnings = ["REQUIRES_COMPLIANCE_REVIEW"]
    if missing_count:
        warnings.append("MISSING_CONTENT_FIELDS")
    if deferred:
        warnings.append("CONTENT_DEFERRED_OVERFLOW")
    warnings.append("REVIEW_SOP_AND_BOM_UNITS")
    if text_on_cut:
        warnings.append("TEXT_ON_CUT_LINES")
    return {
        "valid": not text_on_cut,
        "checks": checks,
        "counts": {
            "fields": len(fields),
            "placed": len(placed),
            "deferred": len(deferred),
            "missing_fields": missing_count,
            "panels": len(panels),
        },
        "warnings": warnings,
        "manual_review_required": True,
        "safe_margin": safe_margin,
    }


def _source_report(brief: ContentBrief, dieline: Dieline, spec: StructureSpec) -> str:
    provided = brief.provided
    lines = [
        "# 内容来源报告（Source Report）",
        "",
        "本报告区分「用户提供」与「占位符」两类内容。系统未执行外部法规检索，",
        "也不会替用户补写任何法规、认证或企业信息。",
        "",
        f"- 刀模型号：`{dieline.model_code}`",
        f"- 尺寸（mm）：长 {spec.dimensions.length:g} × 宽 {spec.dimensions.width:g} × 高 {spec.dimensions.height:g}",
        f"- 纸厚（mm）：{spec.board_thickness:g}",
        "",
        "## 用户提供字段（user_provided）",
        "",
    ]
    if provided:
        for item in provided:
            lines.append(f"- `{item.key}`（{item.label_zh}）← `{item.source}`：{item.value}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 占位符字段（placeholder）",
            "",
        ]
    )
    if brief.missing:
        for item in brief.missing:
            lines.append(f"- `{item.key}`（{item.label_zh}）→ {item.value}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 合规边界",
            "",
            "- 本报告不是法律审核或上市许可。",
            "- 执行标准、许可证、认证、企业信息、功效与警示必须由用户及有资质人员复核。",
            "- 未提供的数据保留占位符，不作推测或补写。",
            "- 条码、追溯码等需现场生成或粘贴后更新。",
            "",
        ]
    )
    return "\n".join(lines)


def _missing_report(brief: ContentBrief) -> str:
    missing = brief.missing
    by_kind: dict[str, list[Any]] = {}
    for item in missing:
        kind = item.placeholder.split("：", 1)[0].strip("[]")
        by_kind.setdefault(kind, []).append(item)
    lines = [
        "# 缺失字段清单（Missing Fields）",
        "",
        f"共 {len(missing)} 项待提供或待确认。",
        "",
    ]
    for kind in ("待提供", "待确认", "待生成或粘贴"):
        items = by_kind.get(kind, [])
        lines.append(f"## {kind}")
        lines.append("")
        if items:
            for item in items:
                lines.append(f"- `{item.key}`（{item.label_zh}）：{item.value}")
        else:
            lines.append("- 无")
        lines.append("")
    if not missing:
        lines.append("无缺失字段。")
        lines.append("")
    return "\n".join(lines)


def _review_checklist(brief: ContentBrief, dieline: Dieline) -> str:
    missing = brief.missing
    missing_required = brief.missing_required
    return "\n".join(
        [
            "# 人工复核清单（Review Checklist）",
            "",
            "- [ ] 核对品牌名、产品名、规格与净含量是否与正式资料一致。",
            "- [ ] 核对生产企业、地址与联系方式是否真实完整。",
            "- [ ] 由有资质人员确认执行标准、许可证与认证的适用性。",
            "- [ ] 审核功效、警示、使用方法与贮存表述。",
            "- [ ] 由设计人员检查字号、可读性、条码净空区与最终面板排版。",
            "- [ ] 确认文字未进入刀线、压痕线、糊口或安全区之外。",
            "- [ ] 印前输出前完成转曲、出血、分色与供应商复核。",
            "- [ ] 与 BOM/SOP 单元核对纸张克重、工艺与数量单位。",
            "",
            f"刀模型号：`{dieline.model_code}`",
            f"字段总数：{len(brief.fields)}；待补充：{len(missing)}；必填缺失：{len(missing_required)}。",
            "",
        ]
    )


def _write_outputs(
    output_dir: str | Path,
    svg_text: str,
    spec_payload: dict[str, Any],
    source_report: str,
    missing_report: str,
    checklist: str,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    files = {
        "content-layout.svg": svg_text,
        "content-spec.json": json.dumps(
            spec_payload, ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        "source-report.md": source_report,
        "missing-fields.md": missing_report,
        "review-checklist.md": checklist,
    }
    paths: dict[str, str] = {}
    for name, content in files.items():
        path = target / name
        path.write_text(content, encoding="utf-8")
        paths[name] = str(path)
    return paths


def layout_content(
    dieline: Dieline,
    brief: ContentBrief,
    spec: StructureSpec,
    output_dir: str | Path,
) -> ContentLayoutResult:
    """Place sourced fields into safe areas and write all content artifacts."""
    if not isinstance(dieline, Dieline):
        raise RequestValidationError("INVALID_DIELINE", "dieline 必须是 Dieline 实例。")
    if not isinstance(spec, StructureSpec):
        raise RequestValidationError("INVALID_SPEC", "spec 必须是 StructureSpec 实例。")
    if not isinstance(brief, ContentBrief):
        raise RequestValidationError("INVALID_BRIEF", "brief 必须是 ContentBrief 实例。")

    safe_margin = float(spec.safe_margin)
    if safe_margin < 0:
        safe_margin = 0.0
    if not dieline.bounds or dieline.bounds[2] <= dieline.bounds[0] or dieline.bounds[3] <= dieline.bounds[1]:
        raise RequestValidationError("INVALID_DIELINE", "dieline.bounds 无效。")

    panels = build_panels(dieline, spec, safe_margin)
    if not panels:
        raise RequestValidationError(
            "MISSING_SAFE_AREA",
            "刀模中没有可写入内容的安全区。",
            {"bounds": list(dieline.bounds)},
        )

    best = choose_layout(brief.fields, panels, safe_margin)
    placed = best["placed"]
    deferred = best["deferred"]
    font_size = best["font_size"]

    svg_text = render_layout_svg(dieline, panels, placed, font_size, safe_margin, spec)
    validation = _validate(svg_text, dieline, placed, deferred, panels, brief.fields, safe_margin)

    spec_payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "content_layout",
        "dieline_model": dieline.model_code,
        "bounds": list(dieline.bounds),
        "safe_margin": safe_margin,
        "font_size": font_size,
        "material": spec.material,
        "panels": [panel.to_dict() for panel in panels],
        "fields": [item.to_dict() for item in brief.fields],
        "placements": placed,
        "validation": validation,
        "manual_review_required": True,
    }

    paths = _write_outputs(
        output_dir,
        svg_text,
        spec_payload,
        _source_report(brief, dieline, spec),
        _missing_report(brief),
        _review_checklist(brief, dieline),
    )

    warnings = tuple(validation["warnings"])
    return ContentLayoutResult(
        dieline_model=dieline.model_code,
        bounds=tuple(dieline.bounds),
        fields=brief.fields,
        placements=tuple(placed),
        validation=validation,
        paths=paths,
        missing_count=len(brief.missing),
        warnings=warnings,
        safe_margin=safe_margin,
    )
