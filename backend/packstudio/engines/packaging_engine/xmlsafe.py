"""防御式 XML 解析助手：统一走 defusedxml，实体/DTD 一律拒绝。

标准库 ``xml.etree`` 的默认解析器会接受内部 DTD 实体扩展（billion laughs
等解析器放大攻击）。引擎生成的 SVG 虽不含 DTD，但作为纵深防御，本引擎
所有 XML/SVG 解析统一走 :func:`safe_fromstring`：

- 底层为 ``defusedxml.ElementTree``（``forbid_dtd/forbid_entities/
  forbid_external`` 全开）；
- defusedxml 的专用异常（``EntitiesForbidden`` 等）归一化为
  ``ET.ParseError`` 抛出，调用方既有的 ``except ET.ParseError`` 分支
  无需任何改动。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as DefusedET

__all__ = ["safe_fromstring", "ParseError"]

#: 与标准库同名的解析错误类型，便于调用方 ``except`` 写法保持不变
ParseError = ET.ParseError


def safe_fromstring(text: str) -> ET.Element:
    """解析 XML/SVG 文本；DOCTYPE / 实体声明 / 外部实体一律拒绝。

    行为与 ``ET.fromstring`` 兼容（含 ``ET.ParseError`` 异常语义），
    不可信输入的解析器放大攻击面被关闭。
    """
    try:
        return DefusedET.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise ET.ParseError(str(exc)) from exc
