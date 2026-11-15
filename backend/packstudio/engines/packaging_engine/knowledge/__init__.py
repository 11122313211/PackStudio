"""包装知识库（Knowledge Base）。

当前包含三个域：

* ``tobacco``：烟包结构库（``structures.json``）与印后工艺
  （``process.md``）。
* ``plastics``：塑胶结构库（``structures.json``）与工艺公差
  （``process.md``）。
* ``transport``：运输条件与堆码强度（``transport.json``）。

所有条目都标注了需要厂商验证的内容，本包不提供结论性判断。
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.knowledge.loader import (
    DOMAIN_DIRS,
    KNOWLEDGE_DIR,
    list_domains,
    list_knowledge,
    load_knowledge,
)

__all__ = [
    "DOMAIN_DIRS",
    "KNOWLEDGE_DIR",
    "list_domains",
    "list_knowledge",
    "load_knowledge",
]
