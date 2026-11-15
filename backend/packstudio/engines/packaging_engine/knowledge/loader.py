"""包装知识库（Knowledge Base）加载器。

布局::

    packstudio.engines.packaging_engine/knowledge/
        loader.py
        tobacco/
            structures.json
            process.md
        plastics/
            structures.json
            process.md
        transport.json

用法::

    from packstudio.engines.packaging_engine.knowledge import load_knowledge, list_knowledge

    data = load_knowledge("tobacco", "structures")
    entries = list_knowledge("tobacco")

约定
----
* ``load_knowledge`` 返回 ``dict``；Markdown 条目包装为
  ``{"name", "domain", "format": "markdown", "path", "content"}``。
* 缺失条目抛 ``KnowledgeMissingError``，绝不返回假数据。
* 本模块只做"读文件 + 校验"，不做任何工艺/合规判断。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packstudio.engines.packaging_engine.exceptions import KnowledgeMissingError

KNOWLEDGE_DIR = Path(__file__).resolve().parent

# domain -> 子目录（空串表示直接位于 knowledge 根目录）
DOMAIN_DIRS: dict[str, str] = {
    "tobacco": "tobacco",
    "plastics": "plastics",
    "transport": "",
}

# 每个条目允许的扩展名
KNOWN_SUFFIXES = (".json", ".md")


def _domain_dir(domain: str) -> Path:
    subdir = DOMAIN_DIRS.get(domain)
    if subdir is None:
        raise KnowledgeMissingError(
            "KNOWLEDGE_DOMAIN_UNKNOWN",
            f"未知知识域：{domain}；可用：{sorted(DOMAIN_DIRS)}",
            {"supported": sorted(DOMAIN_DIRS)},
        )
    return KNOWLEDGE_DIR / subdir


def list_domains() -> list[str]:
    """返回所有可用知识域。"""
    return list(DOMAIN_DIRS)


def list_knowledge(domain: str) -> list[str]:
    """列出某知识域下所有条目名（不含扩展名）。"""
    directory = _domain_dir(domain)
    if not directory.is_dir():
        return []
    names: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in KNOWN_SUFFIXES:
            names.append(path.stem)
    return names


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KnowledgeMissingError(
            "KNOWLEDGE_INVALID",
            f"知识条目无法解析：{path}",
            {"path": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise KnowledgeMissingError(
            "KNOWLEDGE_INVALID",
            f"知识条目根节点必须是对象：{path}",
            {"path": str(path)},
        )
    return payload


def _load_markdown(path: Path) -> dict[str, Any]:
    return {
        "name": path.stem,
        "format": "markdown",
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


def load_knowledge(domain: str, name: str) -> dict[str, Any]:
    """加载某个知识条目。

    ``name`` 可以是 ``"structures"``、``"process"``，也可以带扩展名。
    先找 ``.json``，再找 ``.md``；都找不到抛 ``KnowledgeMissingError``。
    """
    if not domain:
        raise KnowledgeMissingError("KNOWLEDGE_DOMAIN_UNKNOWN", "必须提供知识域。")
    if not name:
        raise KnowledgeMissingError("KNOWLEDGE_NAME_UNKNOWN", "必须提供条目名。")

    directory = _domain_dir(domain)
    candidate = Path(name)
    if candidate.suffix.lower() in KNOWN_SUFFIXES:
        stem = candidate.stem
    else:
        stem = str(name)

    # 依次尝试：显式文件名、stem.json、stem.md
    for filename in _candidate_filenames(stem):
        path = directory / filename
        if path.is_file():
            if path.suffix.lower() == ".json":
                payload = _load_json(path)
                payload.setdefault("domain", domain)
                payload.setdefault("name", path.stem)
                payload.setdefault("path", str(path))
                return payload
            return _load_markdown(path)

    available = list_knowledge(domain)
    raise KnowledgeMissingError(
        "KNOWLEDGE_MISSING",
        f"知识条目不存在：{domain}/{name}",
        {"domain": domain, "name": name, "available": available},
    )


def _candidate_filenames(stem: str) -> list[str]:
    safe = stem.strip()
    if not safe:
        return []
    return [f"{safe}.json", f"{safe}.md", safe]
