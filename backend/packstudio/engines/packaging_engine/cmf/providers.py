"""CMF 工艺 Provider 抽象与实现。

设计约束
--------
1. **绝不本地伪造工艺证据。** 任何真实效果图/成本/合规结论都必须来自
   外部 Provider 的返回值；没有可用 Provider 时直接抛 ``ProviderError``。
2. **密钥只从环境变量读取。** ``api_key_env`` 只保存环境变量名，实际
   取值在运行时用 ``os.environ`` 读取；本模块不出现任何硬编码密钥。
3. **Mock 输出必须显式标注。** ``MockProvider`` 返回的每个 output 都带
   ``mock=True``、``manual_review=True``、``label="mock"`` 与告警，
   调用方不得把它当作真实 CMF 结果。

真实集成说明
------------
``OpenAICompatibleProvider`` 与 ``CustomRESTProvider`` 在本模块中是
**显式桩**：配置缺失时抛 ``PROVIDER_NOT_CONFIGURED``，配置齐全时抛
``PROVIDER_STUB_NOT_IMPLEMENTED``。这样做是为了避免"看起来像接了 API，
实际是假的"。要真正接入，请替换这两个类的 ``invoke``，保留同样的
错误码约定。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from packstudio.engines.packaging_engine.exceptions import ProviderError

# ---------------------------------------------------------------------------
# 能力常量
# ---------------------------------------------------------------------------

CAP_IMAGE_GENERATION = "image_generation"
CAP_VISION = "vision"
CAP_TEXT = "text"
CAP_STRUCTURED = "structured_output"

# ---------------------------------------------------------------------------
# 环境变量名（只保存名字，绝不保存值）
# ---------------------------------------------------------------------------

ENV_OPENAI_ENDPOINT = "CMF_OPENAI_ENDPOINT"
#: 存放 OpenAI 兼容端点密钥的环境变量名（只存名字，值运行时读取）
ENV_OPENAI_KEY_NAME = "CMF_OPENAI_API_KEY"
ENV_OPENAI_MODEL = "CMF_OPENAI_MODEL"
ENV_CUSTOM_ENDPOINT = "CMF_CUSTOM_ENDPOINT"
#: 存放自定义 REST 端点密钥的环境变量名（只存名字，值运行时读取）
ENV_CUSTOM_KEY_NAME = "CMF_CUSTOM_API_KEY"
ENV_DEFAULT_PROVIDER = "CMF_PROVIDER"

# A fixed 1x1 transparent PNG, used only as a provider-contract fixture.
MOCK_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)

MOCK_WARNING = "MOCK_OUTPUT_NOT_A_REAL_CMF_RENDER"


# ---------------------------------------------------------------------------
# 基础数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderDescriptor:
    """用于输出记录的非密钥 Provider 描述。"""

    name: str
    provider_type: str
    capabilities: frozenset[str] = frozenset()
    model: str = ""
    endpoint: str = ""
    may_incur_cost: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.provider_type,
            "capabilities": sorted(self.capabilities),
            "model": self.model,
            "endpoint": self.endpoint,
            "may_incur_cost": self.may_incur_cost,
        }


@dataclass
class ProviderCall:
    """一次 Provider 调用记录（不含任何密钥）。"""

    operation: str
    provider: str
    success: bool = False
    error_code: str = ""
    mock: bool = False
    manual_review: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "provider": self.provider,
            "success": self.success,
            "error_code": self.error_code,
            "mock": self.mock,
            "manual_review": self.manual_review,
            "attempts": self.attempts,
        }


# ---------------------------------------------------------------------------
# BaseProvider
# ---------------------------------------------------------------------------


class BaseProvider(ABC):
    """CMF 工艺 Provider 的抽象基类。"""

    name: str = "base"
    provider_type: str = "base"
    capabilities: frozenset[str] = frozenset()
    may_incur_cost: bool = False

    def supports(self, capability: str) -> bool:
        """是否具备某项能力。"""
        return capability in self.capabilities

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            provider_type=self.provider_type,
            capabilities=self.capabilities,
        )

    @abstractmethod
    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """执行一次调用并返回输出。失败必须抛 ``ProviderError``。"""
        raise NotImplementedError

    # 便于在 output 中统一标注
    def _labelled(self, output: dict[str, Any], *, mock: bool) -> dict[str, Any]:
        merged = dict(output)
        merged.setdefault("provider", self.name)
        merged["mock"] = mock
        merged["manual_review"] = True
        merged["label"] = "mock" if mock else "external"
        if mock and MOCK_WARNING not in merged.get("warnings", []):
            warnings = list(merged.get("warnings", []))
            warnings.append(MOCK_WARNING)
            merged["warnings"] = warnings
        return merged


# ---------------------------------------------------------------------------
# MockProvider
# ---------------------------------------------------------------------------


class MockProvider(BaseProvider):
    """确定性 Mock，仅用于测试 / 流程演练。

    返回的所有输出都显式标注 ``mock=True``、``manual_review=True``，
    并附带 ``MOCK_OUTPUT_NOT_A_REAL_CMF_RENDER`` 告警。绝不产生真实
    CMF 证据。
    """

    name = "mock"
    provider_type = "mock"
    capabilities = frozenset({CAP_IMAGE_GENERATION, CAP_VISION, CAP_TEXT, CAP_STRUCTURED})
    may_incur_cost = False

    def __init__(self, scripted: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._scripted = dict(scripted or {})

    @staticmethod
    def image_bytes() -> bytes:
        return base64.b64decode(MOCK_PNG_BASE64)

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"operation": operation, "payload": payload})
        if operation in self._scripted:
            output = dict(self._scripted[operation])
        elif operation == "image_generation":
            output = {
                "image_base64": MOCK_PNG_BASE64,
                "mime_type": "image/png",
                "request_digest": self._digest(operation, payload),
            }
        elif operation == "visual_qa":
            # Mock 不能真正判过：显式走 manual_review，绝不 fake accept。
            output = {
                "passed": True,
                "score": 1.0,
                "issues": [],
                "recommended_action": "manual_review",
                "request_digest": self._digest(operation, payload),
            }
        elif operation == "cost_estimate":
            output = {"estimates": [], "request_digest": self._digest(operation, payload)}
        else:
            output = {"request_digest": self._digest(operation, payload)}
        output.setdefault("recommended_action", "manual_review")
        return self._labelled(output, mock=True)

    @staticmethod
    def _digest(operation: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{operation}:{canonical}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 真实 Provider 的显式桩
# ---------------------------------------------------------------------------


class _StubRESTProvider(BaseProvider):
    """真实 REST 集成的显式桩基类。"""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        api_key_env: str,
        model: str = "",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.name = name
        self.endpoint = endpoint
        self.api_key_env = api_key_env
        self.model = model
        self.timeout_seconds = timeout_seconds

    def api_key(self) -> str:
        """只从环境变量读取密钥；未配置返回空串。"""
        return os.environ.get(self.api_key_env, "") or ""

    def is_configured(self) -> bool:
        return bool(self.endpoint) and bool(self.api_key())

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            provider_type=self.provider_type,
            capabilities=self.capabilities,
            model=self.model,
            endpoint=self.endpoint,
            may_incur_cost=self.may_incur_cost,
        )

    def _missing(self) -> None:
        raise ProviderError(
            "PROVIDER_NOT_CONFIGURED",
            f"Provider {self.name} 未配置：需要环境变量 {self.api_key_env} 与 endpoint。",
            {
                "provider": self.name,
                "missing_env": self.api_key_env,
                "endpoint_configured": bool(self.endpoint),
            },
        )


class OpenAICompatibleProvider(_StubRESTProvider):
    """OpenAI 兼容端点的显式桩。

    未配置（缺 endpoint 或环境变量）时抛 ``PROVIDER_NOT_CONFIGURED``；
    配置齐全时抛 ``PROVIDER_STUB_NOT_IMPLEMENTED``。绝不伪造调用结果。
    """

    provider_type = "openai_compatible"
    capabilities = frozenset({CAP_IMAGE_GENERATION, CAP_VISION, CAP_TEXT, CAP_STRUCTURED})
    may_incur_cost = True

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key_env: str = ENV_OPENAI_KEY_NAME,
        model: str | None = None,
        name: str = "openai_compatible",
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            name=name,
            endpoint=endpoint if endpoint is not None else os.environ.get(ENV_OPENAI_ENDPOINT, ""),
            api_key_env=api_key_env,
            model=model if model is not None else os.environ.get(ENV_OPENAI_MODEL, ""),
            timeout_seconds=timeout_seconds,
        )

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured():
            self._missing()
        raise ProviderError(
            "PROVIDER_STUB_NOT_IMPLEMENTED",
            f"{self.name} 是显式桩，尚未实现真实 HTTP 调用；请替换 invoke。",
            {"provider": self.name, "operation": operation},
        )


class CustomRESTProvider(_StubRESTProvider):
    """自定义 REST 端点的显式桩。行为同 ``OpenAICompatibleProvider``。"""

    provider_type = "custom_rest"
    capabilities = frozenset({CAP_IMAGE_GENERATION, CAP_VISION, CAP_TEXT, CAP_STRUCTURED})
    may_incur_cost = True

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key_env: str = ENV_CUSTOM_KEY_NAME,
        model: str = "",
        name: str = "custom_rest",
        timeout_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            name=name,
            endpoint=endpoint if endpoint is not None else os.environ.get(ENV_CUSTOM_ENDPOINT, ""),
            api_key_env=api_key_env,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured():
            self._missing()
        raise ProviderError(
            "PROVIDER_STUB_NOT_IMPLEMENTED",
            f"{self.name} 是显式桩，尚未实现真实 HTTP 调用；请替换 invoke。",
            {"provider": self.name, "operation": operation},
        )


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """按名字注册 / 解析 Provider，并保证"无可用 Provider 即报错"。"""

    def __init__(self, providers: list[BaseProvider] | None = None) -> None:
        self._providers: dict[str, BaseProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: BaseProvider) -> BaseProvider:
        if not provider.name:
            raise ProviderError("PROVIDER_INVALID", "Provider 必须提供 name。")
        self._providers[provider.name] = provider
        return provider

    def get(self, name: str) -> BaseProvider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return list(self._providers.keys())

    def all(self) -> list[BaseProvider]:
        return list(self._providers.values())

    def describe(self) -> list[dict[str, Any]]:
        return [provider.describe().to_dict() for provider in self._providers.values()]

    def supports(self, capability: str, provider: BaseProvider | None = None) -> BaseProvider | None:
        candidates = [provider] if provider is not None else self._providers.values()
        for candidate in candidates:
            if candidate is not None and candidate.supports(capability):
                return candidate
        return None

    def resolve(
        self,
        capability: str,
        provider: BaseProvider | str | None = None,
        *,
        required: bool = True,
    ) -> BaseProvider | None:
        """解析支持指定能力的 Provider。

        - 传入实例：直接使用（并校验能力）。
        - 传入名字：从注册表查找。
        - 传入 ``None``：按注册表顺序查找第一个支持的。
        - 找不到且 ``required`` 为真时抛 ``ProviderError``。
        """
        candidate: BaseProvider | None = None
        if isinstance(provider, BaseProvider):
            candidate = provider
        elif isinstance(provider, str):
            candidate = self._providers.get(provider)
            if candidate is None:
                if required:
                    raise ProviderError("PROVIDER_NOT_FOUND", f"未注册的 Provider：{provider}")
                return None
        elif provider is not None:
            raise ProviderError("PROVIDER_INVALID", "provider 必须是 BaseProvider 或名字字符串。")

        if candidate is not None and not candidate.supports(capability):
            if required:
                raise ProviderError(
                    "PROVIDER_CAPABILITY_MISSING",
                    f"Provider {candidate.name} 不支持 {capability}。",
                    {"provider": candidate.name, "capability": capability},
                )
            return None

        if candidate is not None:
            return candidate

        found = self.supports(capability)
        if found is not None:
            return found
        if required:
            raise ProviderError(
                "PROVIDER_UNAVAILABLE",
                f"没有支持 {capability} 的已配置 Provider；本地不会伪造 CMF 证据。",
                {"capability": capability, "registered": self.names()},
            )
        return None


_DEFAULT_REGISTRY: ProviderRegistry | None = None


def build_default_registry() -> ProviderRegistry:
    """根据环境变量构造默认注册表；未配置时返回空注册表。"""
    registry = ProviderRegistry()
    requested = os.environ.get(ENV_DEFAULT_PROVIDER, "").strip().lower()

    if requested == "mock":
        registry.register(MockProvider())
    elif requested == "openai_compatible":
        registry.register(OpenAICompatibleProvider())
    elif requested == "custom_rest":
        registry.register(CustomRESTProvider())
    elif requested and requested not in {"none", "auto"}:
        raise ProviderError(
            "PROVIDER_NOT_FOUND",
            f"未知的 {ENV_DEFAULT_PROVIDER}：{requested}",
            {"supported": ["mock", "openai_compatible", "custom_rest", "none"]},
        )

    # 未显式指定时，自动探测已配置的外部端点。
    if requested in {"", "auto", "openai_compatible"} and (
        os.environ.get(ENV_OPENAI_ENDPOINT, "").strip() or os.environ.get(ENV_OPENAI_KEY_NAME, "").strip()
    ):
        registry.register(OpenAICompatibleProvider())
    if requested in {"", "auto", "custom_rest"} and (
        os.environ.get(ENV_CUSTOM_ENDPOINT, "").strip() or os.environ.get(ENV_CUSTOM_KEY_NAME, "").strip()
    ):
        registry.register(CustomRESTProvider())
    return registry


def get_default_registry() -> ProviderRegistry:
    """返回进程级默认注册表（惰性构造）。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = build_default_registry()
    return _DEFAULT_REGISTRY
