"""CMF 服务层：生成 ``cmf-plan.json`` 与独立视觉 QA。

关键规则
--------
* ``plan_cmf`` 与 ``visual_qa`` 都要求存在一个支持
  ``image_generation`` 的已配置 Provider；没有就抛 ``ProviderError``，
  绝不本地伪造效果图或工艺结论。
* Mock Provider 的输出一律标注 ``mock=True`` / ``manual_review=True``，
  本模块会把它透传并在结果里给出告警。
* 本模块只做**前置兼容性检查**与**结果整理**；真实可行性、成本、
  合规都必须回到厂商验证流程。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packstudio.engines.packaging_engine.exceptions import ProviderError, RequestValidationError
from packstudio.engines.packaging_engine.models import Dimensions

from .compatibility import (
    MATERIAL_TAXONOMY,
    compatibility_plan,
    resolve_material,
)
from .finishes import FINISH_TAXONOMY, resolve_finish
from .providers import (
    CAP_IMAGE_GENERATION,
    BaseProvider,
    MockProvider,
    ProviderRegistry,
    get_default_registry,
)

MODULE_NAME = "cmf"
PLAN_SCHEMA_VERSION = "1.0"
PLAN_FILENAME = "cmf-plan.json"

ARTWORK_SUFFIXES = {".svg", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}

DISCLAIMER = (
    "本文件由 CMF 模块生成的计划，仅为设计沟通与厂商询价准备，"
    "不是生产文件、打样结果或合规证明。工艺可行性、参数与成本需厂商验证。"
)

VISUAL_QA_CHECKS: tuple[tuple[str, str], ...] = (
    ("structure_proportion", "结构与物理比例未改变"),
    ("artwork_main_visual", "主视觉未偏移或重绘"),
    ("logo_geometry", "Logo 几何未被破坏"),
    ("chinese_text", "中文字体未被重绘或乱码"),
    ("panel_mapping", "版面映射（正/反/侧）正确"),
    ("finish_region", "工艺只附着在指定表面且无浮空/发光/溢出"),
    ("dieline_annotation", "刀模线、标注、安全区未进入成品图"),
    ("color_fidelity", "颜色未发生严重偏移"),
    ("count", "产品数量正确"),
    ("photo_realism", "光影/背景与包装定位一致"),
)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_artwork(artwork_path: object) -> tuple[Path, bytes, dict[str, Any]]:
    if not isinstance(artwork_path, (str, Path)) or not str(artwork_path).strip():
        raise RequestValidationError("MISSING_REQUIRED_FIELD", "缺少完成设计稿路径。")
    path = Path(artwork_path).expanduser()
    if not path.is_file():
        raise RequestValidationError("INPUT_NOT_FOUND", f"完成稿不存在：{path}")
    if path.suffix.lower() not in ARTWORK_SUFFIXES:
        raise RequestValidationError(
            "UNSUPPORTED_ASSET",
            "完成稿必须是 SVG、PDF、PNG、JPG/JPEG 或 WEBP。",
        )
    raw = path.read_bytes()
    return path, raw, {
        "path": str(path),
        "name": path.name,
        "suffix": path.suffix.lower(),
        "bytes": len(raw),
        "sha256": _sha256(raw),
    }


def _normalise_dimensions(dimensions: object) -> Dimensions:
    if isinstance(dimensions, Dimensions):
        return dimensions
    return Dimensions.from_dict(dimensions)


def _normalise_finishes(finishes: object) -> tuple[list[dict[str, Any]], list[str]]:
    if finishes is None:
        raw_items: list[Any] = []
    elif isinstance(finishes, (str,)):
        raw_items = [finishes]
    elif isinstance(finishes, (list, tuple)):
        raw_items = list(finishes)
    else:
        raise RequestValidationError("INVALID_PARAMETER", "finishes 必须是数组或字符串。")

    resolved: list[dict[str, Any]] = []
    unrecognized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        key = resolve_finish(item)
        if not key:
            unrecognized.append(str(item))
            continue
        if key in seen:
            continue
        seen.add(key)
        entry = FINISH_TAXONOMY[key]
        resolved.append(
            {
                "key": entry["key"],
                "label_zh": entry["label_zh"],
                "en": entry["en"],
                "params": dict(entry["params"]),
                "cost_hint": entry["cost_hint"],
                "notes": entry["notes"],
            }
        )
    return resolved, unrecognized


def _resolve_provider(
    provider: BaseProvider | str | None,
    registry: ProviderRegistry,
) -> BaseProvider:
    return registry.resolve(CAP_IMAGE_GENERATION, provider, required=True)


def _provider_info(provider: BaseProvider) -> dict[str, Any]:
    descriptor = provider.describe().to_dict()
    return {
        **descriptor,
        "mock": isinstance(provider, MockProvider),
        "manual_review": True,
    }


def _write_json(target: Path, payload: dict[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def plan_cmf(
    artwork_path: object,
    dimensions: object,
    material: object,
    finishes: object,
    provider: BaseProvider | str | None = None,
    *,
    output_dir: object | None = None,
    structure: str = "",
    camera: str = "",
    background: str = "",
    registry: ProviderRegistry | None = None,
) -> dict[str, Any]:
    """生成 CMF 计划并写出 ``cmf-plan.json``。

    必须提供支持 ``image_generation`` 的 Provider；否则抛
    ``ProviderError``。返回的计划是**咨询性**文件，不是生产文件。
    """
    art_path, raw, artwork = _load_artwork(artwork_path)
    dims = _normalise_dimensions(dimensions)

    material_key = resolve_material(material)
    if not material_key:
        raise RequestValidationError(
            "UNKNOWN_MATERIAL",
            f"未知材质：{material}；可选：{sorted(MATERIAL_TAXONOMY)}",
            {"supported": sorted(MATERIAL_TAXONOMY)},
        )

    finish_items, unrecognized = _normalise_finishes(finishes)
    compat = compatibility_plan(material_key, [item["key"] for item in finish_items])

    active_registry = registry or get_default_registry()
    active_provider = _resolve_provider(provider, active_registry)

    warnings: list[str] = []
    if unrecognized:
        warnings.append("UNRECOGNIZED_FINISH_INPUT")
    if not compat["all_compatible"]:
        warnings.append("INCOMPATIBLE_FINISH_MATERIAL")
    if compat["requires_manufacturer_validation"]:
        warnings.append("REQUIRES_MANUFACTURER_VALIDATION")
    if isinstance(active_provider, MockProvider):
        warnings.append("MOCK_PROVIDER_USED_OUTPUT_IS_NOT_PRODUCTION_EVIDENCE")

    material_entry = MATERIAL_TAXONOMY[material_key]
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "module": MODULE_NAME,
        "generated_at": _now(),
        "artwork": artwork,
        "dimensions": {
            "length": dims.length,
            "width": dims.width,
            "height": dims.height,
            "unit": dims.unit,
            "dimension_type": dims.dimension_type,
        },
        "structure": str(structure or ""),
        "material": {
            "key": material_key,
            "label_zh": material_entry["label_zh"],
            "en": material_entry["en"],
            "notes": material_entry["notes"],
        },
        "finishes": finish_items,
        "compatibility": compat,
        "provider": _provider_info(active_provider),
        "camera": camera,
        "background": background,
        "production_file": False,
        "disclaimer": DISCLAIMER,
        "warnings": warnings,
    }

    target_dir = Path(output_dir).expanduser() if output_dir is not None else Path.cwd()
    plan["outputs"] = {"cmf_plan": str(_write_json(target_dir / PLAN_FILENAME, plan))}
    return plan


def visual_qa(
    artwork_path: object,
    generated_image_path: object,
    dimensions: object,
    material: object,
    finishes: object = None,
    provider: BaseProvider | str | None = None,
    *,
    registry: ProviderRegistry | None = None,
) -> dict[str, Any]:
    """对生成图执行独立视觉 QA，返回结构化 checks。

    与 ``plan_cmf`` 一样要求支持 ``image_generation`` 的 Provider；
    没有可用 Provider 时抛 ``ProviderError``，不伪造通过结论。
    """
    art_path, _raw, artwork = _load_artwork(artwork_path)

    if not isinstance(generated_image_path, (str, Path)) or not str(generated_image_path).strip():
        raise RequestValidationError("MISSING_REQUIRED_FIELD", "缺少待检查的生成图路径。")
    image_path = Path(generated_image_path).expanduser()
    if not image_path.is_file():
        raise RequestValidationError("INPUT_NOT_FOUND", f"生成图不存在：{image_path}")
    image_raw = image_path.read_bytes()
    generated_image = {
        "path": str(image_path),
        "name": image_path.name,
        "bytes": len(image_raw),
        "sha256": _sha256(image_raw),
    }

    dims = _normalise_dimensions(dimensions)
    finish_items, _unrecognized = _normalise_finishes(finishes)

    active_registry = registry or get_default_registry()
    active_provider = _resolve_provider(provider, active_registry)

    payload = {
        "operation": "visual_qa",
        "artwork": artwork,
        "generated_image": generated_image,
        "dimensions": {
            "length": dims.length,
            "width": dims.width,
            "height": dims.height,
            "unit": dims.unit,
        },
        "checks": [{"id": check_id, "label_zh": label} for check_id, label in VISUAL_QA_CHECKS],
    }
    output = active_provider.invoke("visual_qa", payload)
    if not isinstance(output, dict):
        raise ProviderError("INVALID_PROVIDER_RESPONSE", "visual_qa Provider 必须返回对象。")

    is_mock = bool(output.get("mock", isinstance(active_provider, MockProvider)))
    issues = [item for item in output.get("issues", []) if isinstance(item, dict)]
    checks = [
        {
            "id": check_id,
            "label_zh": label,
            "status": "passed",
            "detail": "",
        }
        for check_id, label in VISUAL_QA_CHECKS
    ]
    for issue in issues:
        check_id = str(issue.get("check_id") or issue.get("type") or "")
        for check in checks:
            if check_id and check["id"] == check_id:
                check["status"] = "failed"
                check["detail"] = str(issue.get("message", ""))
                break

    provider_passed = bool(output.get("passed"))
    raw_action = str(output.get("recommended_action", ""))
    if raw_action not in {"accept", "retry", "manual_review", "reject"}:
        raw_action = "manual_review"

    warnings: list[str] = list(output.get("warnings", []) or [])
    if is_mock:
        warnings.append("MOCK_VISUAL_QA_REQUIRES_MANUAL_REVIEW")

    # Mock 或未真正判过时，绝不声称通过。
    passed = bool(provider_passed and raw_action == "accept" and not is_mock)
    if is_mock:
        action = "manual_review"
    elif passed:
        action = "accept"
    else:
        action = raw_action or "manual_review"

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "module": MODULE_NAME,
        "generated_at": _now(),
        "artwork": artwork,
        "generated_image": generated_image,
        "provider": _provider_info(active_provider),
        "mock": is_mock,
        "manual_review": True,
        "passed": passed,
        "score": max(0.0, min(1.0, float(output.get("score", 0.0) or 0.0))),
        "recommended_action": action,
        "checks": checks,
        "issues": issues,
        "warnings": warnings,
        "production_file": False,
        "disclaimer": DISCLAIMER,
    }
