"""Reconcile a generated BOM against a generated SOP.

Detects:
- name mismatches: items referenced by one document but not the other
  (BOM-only / SOP-only);
- spec mismatches: a concrete BOM spec that the SOP does not reflect;
- qty mismatches: a BOM quantity that disagrees with the SOP quantity map.

Writes ``bom-sop-review.md`` next to the BOM outputs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packstudio.engines.packaging_engine.bom.models import SCHEMA_VERSION

TOPICS: dict[str, dict[str, Any]] = {
    "paper": {
        "label": "纸张/纸板",
        "bom_keywords": ("纸板", "卡纸", "纸张", "克重"),
        "sop_keywords": ("纸张", "纸板", "卡纸", "克重"),
        "unit": "m²",
    },
    "ink": {
        "label": "油墨",
        "bom_keywords": ("油墨", "专色"),
        "sop_keywords": ("油墨", "专色"),
        "unit": "m²",
    },
    "coating": {
        "label": "表面处理",
        "bom_keywords": ("表面处理", "涂层", "覆膜", "上光", "UV"),
        "sop_keywords": ("表面处理", "涂层", "覆膜", "上光", "UV"),
        "unit": "m²",
    },
    "die": {
        "label": "模切/刀版",
        "bom_keywords": ("刀版", "模切"),
        "sop_keywords": ("刀版", "模切"),
        "unit": "套",
    },
    "glue": {
        "label": "胶水",
        "bom_keywords": ("胶水", "糊口"),
        "sop_keywords": ("胶水", "糊口"),
        "unit": "kg",
    },
    "labor": {
        "label": "人工",
        "bom_keywords": ("人工", "工时"),
        "sop_keywords": ("人工", "工时"),
        "unit": "工时",
    },
    "regulatory": {
        "label": "合规/法规",
        "bom_keywords": ("法规", "合规", "许可证", "认证"),
        "sop_keywords": ("法规", "合规", "标签"),
        "unit": "项",
    },
}

PLACEHOLDER_MARKERS = ("待确认", "待提供", "待生成")


def is_placeholder(text: str) -> bool:
    text = str(text or "")
    return "[" in text and any(marker in text for marker in PLACEHOLDER_MARKERS)


def _normalize(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def _matches(keywords: tuple[str, ...], text: str) -> bool:
    normalized = _normalize(text)
    return any(_normalize(keyword) in normalized for keyword in keywords)


def _bom_items(bom: dict[str, Any]) -> list[dict[str, Any]]:
    items = bom.get("items")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _sop_steps(sop: dict[str, Any]) -> list[dict[str, Any]]:
    steps = sop.get("steps")
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _bom_topic(item: dict[str, Any]) -> str | None:
    name = str(item.get("name", ""))
    for topic, meta in TOPICS.items():
        if _matches(meta["bom_keywords"], name):
            return topic
    return None


def _sop_topics(step: dict[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(step.get("title", "")),
            str(step.get("detail", "")),
            str(step.get("equipment", "")),
        ]
    )
    return [topic for topic, meta in TOPICS.items() if _matches(meta["sop_keywords"], text)]


def _resolve_output_dir(bom: dict[str, Any], sop: dict[str, Any]) -> Path:
    for payload, key in ((bom, "bom_json"), (bom, "bom_csv"), (sop, "sop_md")):
        files = payload.get("files")
        if isinstance(files, dict) and key in files:
            return Path(files[key]).parent
    return Path(".")


def reconcile_bom_sop(bom: dict[str, Any], sop: dict[str, Any]) -> dict[str, Any]:
    """Compare BOM and SOP payloads and write ``bom-sop-review.md``."""
    if not isinstance(bom, dict) or not isinstance(sop, dict):
        raise ValueError("bom 与 sop 必须是 dict。")

    items = _bom_items(bom)
    steps = _sop_steps(sop)
    quantities = sop.get("quantities") if isinstance(sop.get("quantities"), dict) else {}

    bom_by_topic: dict[str, list[dict[str, Any]]] = {topic: [] for topic in TOPICS}
    bom_unmatched: list[dict[str, Any]] = []
    for item in items:
        topic = _bom_topic(item)
        if topic is None:
            bom_unmatched.append(item)
        else:
            bom_by_topic[topic].append(item)

    sop_by_topic: dict[str, list[dict[str, Any]]] = {topic: [] for topic in TOPICS}
    for step in steps:
        for topic in _sop_topics(step):
            sop_by_topic[topic].append(step)

    matched: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    bom_only: list[dict[str, Any]] = []
    sop_only: list[dict[str, Any]] = []

    for topic, meta in TOPICS.items():
        bom_items = bom_by_topic[topic]
        sop_steps = sop_by_topic[topic]
        if bom_items and sop_steps:
            matched.append(
                {
                    "topic": topic,
                    "label": meta["label"],
                    "bom_items": [item.get("name", "") for item in bom_items],
                    "sop_steps": [f"{step.get('step')}. {step.get('title', '')}" for step in sop_steps],
                }
            )
            bom_text = " ".join(
                str(item.get("spec", "")) + " " + str(item.get("notes", ""))
                for item in bom_items
            )
            sop_text = " ".join(
                str(step.get("detail", "")) + " " + str(step.get("equipment", ""))
                for step in sop_steps
            )
            for item in bom_items:
                spec = str(item.get("spec", ""))
                if spec and not is_placeholder(spec) and _normalize(spec) not in _normalize(sop_text):
                    mismatches.append(
                        {
                            "kind": "spec",
                            "topic": topic,
                            "detail": f"BOM 规格 `{spec}` 未在 SOP 中体现。",
                            "bom_value": spec,
                            "sop_value": sop_text,
                        }
                    )
                topic_qty = quantities.get(topic)
                if topic_qty is not None:
                    try:
                        diff = abs(float(item.get("qty", 0.0)) - float(topic_qty))
                    except (TypeError, ValueError):
                        diff = float("inf")
                    if diff > 1e-6:
                        mismatches.append(
                            {
                                "kind": "qty",
                                "topic": topic,
                                "detail": (
                                    f"数量不一致：BOM `{item.get('qty')} {item.get('unit', '')}` "
                                    f"vs SOP `{topic_qty}`。"
                                ),
                                "bom_value": item.get("qty"),
                                "sop_value": topic_qty,
                            }
                        )
        elif bom_items and not sop_steps:
            bom_only.append(
                {
                    "topic": topic,
                    "label": meta["label"],
                    "items": [item.get("name", "") for item in bom_items],
                }
            )
            mismatches.append(
                {
                    "kind": "name",
                    "topic": topic,
                    "detail": f"BOM 项 `{'、'.join(str(i.get('name','')) for i in bom_items)}` 未在 SOP 中出现。",
                }
            )
        elif sop_steps and not bom_items:
            sop_only.append(
                {
                    "topic": topic,
                    "label": meta["label"],
                    "steps": [f"{step.get('step')}. {step.get('title', '')}" for step in sop_steps],
                }
            )
            mismatches.append(
                {
                    "kind": "name",
                    "topic": topic,
                    "detail": f"SOP 工步 `{'、'.join(str(s.get('step')) for s in sop_steps)}` 未在 BOM 中出现。",
                }
            )

    for item in bom_unmatched:
        bom_only.append(
            {"topic": None, "label": "未识别", "items": [item.get("name", "")]}
        )
        mismatches.append(
            {
                "kind": "name",
                "topic": None,
                "detail": f"BOM 项 `{item.get('name', '')}` 未匹配到任何主题。",
            }
        )

    ok = not mismatches
    summary = {
        "bom_item_count": len(items),
        "sop_step_count": len(steps),
        "matched_topics": len(matched),
        "bom_only_count": len(bom_only),
        "sop_only_count": len(sop_only),
        "mismatch_count": len(mismatches),
        "ok": ok,
    }

    output_dir = _resolve_output_dir(bom, sop)
    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / "bom-sop-review.md"
    review_path.write_text(_render_markdown(summary, matched, bom_only, sop_only, mismatches), encoding="utf-8")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "action": "bom_sop_review",
        "ok": ok,
        "matched": matched,
        "bom_only": bom_only,
        "sop_only": sop_only,
        "mismatches": mismatches,
        "summary": summary,
        "warnings": ["BOM_SOP_MISMATCH"] if mismatches else ["BOM_SOP_CONSISTENT"],
        "manual_review_required": True,
        "files": {"bom_sop_review_md": str(review_path)},
    }
    return payload


def _render_markdown(
    summary: dict[str, Any],
    matched: list[dict[str, Any]],
    bom_only: list[dict[str, Any]],
    sop_only: list[dict[str, Any]],
    mismatches: list[dict[str, Any]],
) -> str:
    lines = [
        "# BOM / SOP 一致性复核（Reconciliation）",
        "",
        "## 结论",
        "",
        f"- 是否一致：{'✅ 一致' if summary['ok'] else '❌ 存在差异'}",
        f"- BOM 项数：{summary['bom_item_count']}",
        f"- SOP 工步数：{summary['sop_step_count']}",
        f"- 匹配主题数：{summary['matched_topics']}",
        f"- BOM-only：{summary['bom_only_count']}",
        f"- SOP-only：{summary['sop_only_count']}",
        f"- 差异项：{summary['mismatch_count']}",
        "",
        "## 匹配主题",
        "",
    ]
    if matched:
        for item in matched:
            lines.append(
                f"- {item['label']}（{item['topic']}）："
                f"BOM `{'、'.join(item['bom_items'])}` ↔ SOP `{'、'.join(item['sop_steps'])}`"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## BOM-only（SOP 未覆盖）", ""])
    if bom_only:
        for item in bom_only:
            label = item["label"]
            lines.append(f"- {label}：{'、'.join(item['items'])}")
    else:
        lines.append("- 无")
    lines.extend(["", "## SOP-only（BOM 未覆盖）", ""])
    if sop_only:
        for item in sop_only:
            label = item["label"]
            lines.append(f"- {label}：{'、'.join(item['steps'])}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 差异明细", ""])
    if mismatches:
        for item in mismatches:
            lines.append(f"- [{item['kind']}] {item['detail']}")
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 建议",
            "",
            "- [ ] 逐项确认规格、数量与单位是否与供应商报价一致。",
            "- [ ] 补齐 BOM-only / SOP-only 的对应项或删除多余项。",
            "- [ ] 复核后更新 bom.json 与 sop.md。",
            "",
        ]
    )
    return "\n".join(lines)
