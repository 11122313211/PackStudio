"""Retrospective template: 问题 → 根因 → 改进项 → 责任人 → 状态."""

from __future__ import annotations

from typing import Any, Iterable

from packstudio.engines.packaging_engine.bom.models import SCHEMA_VERSION, ReviewItem

HEADERS = ("问题", "根因", "改进项", "责任人", "状态")
STATUS_VALUES = ("open", "in_progress", "resolved", "verified")


def render_review_table(items: Iterable[ReviewItem] = ()) -> str:
    """Render the retrospective template as a Markdown table."""
    lines = [
        "| # | 问题 | 根因 | 改进项 | 责任人 | 状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(items, start=1):
        lines.append(
            f"| {index} | {item.problem} | {item.root_cause} | {item.improvement} "
            f"| {item.owner or '[待指定]'} | {item.status} |"
        )
    if not items:
        lines.append("| - | [待填写：问题] | [待填写：根因] | [待填写：改进项] | [待填写：责任人] | open |")
    lines.append("")
    return "\n".join(lines)


def build_review_template(items: Iterable[ReviewItem] = ()) -> str:
    """Return a full retrospective document with instructions and placeholders."""
    return "\n".join(
        [
            "# 包装生产复盘模板（Retrospective）",
            "",
            "流程：**问题 → 根因 → 改进项 → 责任人 → 状态**",
            "",
            "## 复盘步骤",
            "",
            "1. 记录问题（现象、影响范围、发生环节）。",
            "2. 定位根因（尽量区分直接原因与系统性原因）。",
            "3. 写出可执行的改进项（负责人与截止时间）。",
            "4. 指定责任人。",
            "5. 跟踪状态：`open` / `in_progress` / `resolved` / `verified`。",
            "",
            "## 记录表",
            "",
            render_review_table(items),
            "## 状态说明",
            "",
            "| 状态 | 含义 |",
            "| --- | --- |",
            "| open | 已记录，未开始处理。 |",
            "| in_progress | 正在处理。 |",
            "| resolved | 已处理，待验证。 |",
            "| verified | 已验证关闭。 |",
            "",
            "> 复盘记录仅用于内部改进，不替代正式质量报告。",
            "",
        ]
    )


def build_review_payload(items: Iterable[ReviewItem] = ()) -> dict[str, Any]:
    """Return the machine-readable retrospective payload."""
    row_list = list(items)
    return {
        "schema_version": SCHEMA_VERSION,
        "action": "review",
        "headers": list(HEADERS),
        "items": [item.to_dict() for item in row_list],
        "status_values": list(STATUS_VALUES),
        "summary": {
            "item_count": len(row_list),
            "open_count": sum(item.status == "open" for item in row_list),
        },
        "manual_review_required": True,
    }
