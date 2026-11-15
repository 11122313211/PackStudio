"""CMF（Color / Material / Finish）模块。

包含：
- ``finishes``：印后工艺分类表（``FINISH_TAXONOMY``）。
- ``compatibility``：材质 → 工艺兼容性矩阵与可解释判定。
- ``providers``：Provider 抽象、MockProvider、显式桩、注册表。
- ``service``：``plan_cmf`` 与 ``visual_qa``。

安全与诚实约束
--------------
Mock 输出必须显式标注 ``mock`` / ``manual_review``；没有可用 Provider 时
抛 ``ProviderError``，绝不本地伪造工艺证据。详见各子模块文档。
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.cmf.compatibility import (
    MATERIAL_TAXONOMY,
    compatibility_plan,
    compatibility_report,
    compatible,
    finish_options,
    material_options,
    resolve_material,
)
from packstudio.engines.packaging_engine.cmf.finishes import (
    FINISH_TAXONOMY,
    CN_TO_KEY,
    KEY_TO_CN,
    finish_names_zh,
    known_finishes,
    resolve_finish,
)
from packstudio.engines.packaging_engine.cmf.providers import (
    BaseProvider,
    CustomRESTProvider,
    MockProvider,
    OpenAICompatibleProvider,
    ProviderCall,
    ProviderDescriptor,
    ProviderRegistry,
    build_default_registry,
    get_default_registry,
)
from packstudio.engines.packaging_engine.cmf.service import plan_cmf, visual_qa

__all__ = [
    "FINISH_TAXONOMY",
    "CN_TO_KEY",
    "KEY_TO_CN",
    "finish_names_zh",
    "known_finishes",
    "resolve_finish",
    "MATERIAL_TAXONOMY",
    "compatible",
    "compatibility_report",
    "compatibility_plan",
    "material_options",
    "finish_options",
    "resolve_material",
    "BaseProvider",
    "MockProvider",
    "OpenAICompatibleProvider",
    "CustomRESTProvider",
    "ProviderCall",
    "ProviderDescriptor",
    "ProviderRegistry",
    "build_default_registry",
    "get_default_registry",
    "plan_cmf",
    "visual_qa",
]
