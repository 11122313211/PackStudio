"""Intent routing: maps a natural-language goal + request to a module action.

Routing uses evidence, not model self-confidence. Conflicting or
under-specified requests ask ONE key question at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packstudio.engines.packaging_engine.exceptions import RequestValidationError
from packstudio.engines.packaging_engine.models.ir import Request
from packstudio.engines.packaging_engine.structure.registry import model_choices, model_report

# action -> (module, expected outputs, required capabilities)
ACTION_MAP: dict[str, dict[str, Any]] = {
    "structure_template": {
        "module": "engine",
        "expected_outputs": ["template.svg", "structure-spec.json"],
        "required_capabilities": ["dieline_engine"],
    },
    "content_layout": {
        "module": "content",
        "expected_outputs": ["content-layout.svg", "content-spec.json", "source-report.md", "missing-fields.md", "review-checklist.md"],
        "required_capabilities": ["content_layout"],
    },
    "mockup_render": {
        "module": "cmf",
        "expected_outputs": ["mockup.png", "cmf-plan.json", "visual-qa.json"],
        "required_capabilities": ["cmf_provider"],
    },
    "validate": {
        "module": "validator",
        "expected_outputs": ["validation-report.json"],
        "required_capabilities": ["validator"],
    },
    "bom": {
        "module": "bom",
        "expected_outputs": ["bom.csv", "bom.json"],
        "required_capabilities": ["bom"],
    },
    "sop": {
        "module": "bom",
        "expected_outputs": ["sop.md"],
        "required_capabilities": ["sop"],
    },
    "knowledge": {
        "module": "knowledge",
        "expected_outputs": ["knowledge.json"],
        "required_capabilities": ["knowledge"],
    },
    "list_box_types": {
        "module": "structure",
        "expected_outputs": ["box-types.json"],
        "required_capabilities": ["structure_registry"],
    },
    "inspect": {
        "module": "core",
        "expected_outputs": ["inspect-report.json"],
        "required_capabilities": [],
    },
    "preview": {
        "module": "fold",
        "expected_outputs": ["preview.html", "fold-model.json"],
        "required_capabilities": ["fold_model"],
    },
    "contact_sheet": {
        "module": "contact_sheet",
        "expected_outputs": ["contact-sheet.html"],
        "required_capabilities": ["dieline_engine"],
    },
    "export_cf2": {
        "module": "exporters",
        "expected_outputs": ["dieline.cf2"],
        "required_capabilities": ["dieline_engine"],
    },
    "export_spot_pdf": {
        "module": "exporters",
        "expected_outputs": ["dieline-spot.pdf"],
        "required_capabilities": ["dieline_engine"],
    },
    "dimensions": {
        "module": "dimensions",
        "expected_outputs": ["dimensions.json"],
        "required_capabilities": ["dimension_converter"],
    },
    "health_check": {
        "module": "core",
        "expected_outputs": ["health-report.json"],
        "required_capabilities": [],
    },
}


@dataclass(frozen=True)
class RouteResult:
    action: str
    module: str
    implemented: bool
    confidence: float
    evidence: list[str]
    expected_outputs: list[str]
    missing_fields: list[str] = field(default_factory=list)
    choice_prompt: list[dict] | None = None
    clarification_question: str | None = None
    may_incur_cost: bool = False
    manual_review_items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "module": self.module,
            "implemented": self.implemented,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "expected_outputs": self.expected_outputs,
            "missing_fields": self.missing_fields,
            "choice_prompt": self.choice_prompt,
            "clarification_question": self.clarification_question,
            "may_incur_cost": self.may_incur_cost,
            "manual_review_items": self.manual_review_items,
        }


def _has_box_type(params: dict) -> bool:
    for key in ("model_code", "box_type", "type", "盒型"):
        if key in params and params[key]:
            return True
    return False


def _has_dimensions(params: dict) -> bool:
    dim = params.get("dimensions")
    if isinstance(dim, dict) and all(k in dim for k in ("length", "width", "height")):
        return True
    return any(k in params for k in ("length", "width", "height", "size"))


def _is_dry_run(params: dict) -> bool:
    return bool(params.get("dry_run")) or bool(params.get("dry-run"))


def route(request: Request) -> RouteResult:
    action = request.action
    params = request.parameters

    if action == "health_check":
        return RouteResult("health_check", "core", True, 1.0, ["explicit_action:health_check"], ["health-report.json"])

    if action == "list_box_types":
        return RouteResult(
            "list_box_types", "structure", True, 1.0, ["explicit_action:list_box_types"],
            ["box-types.json"], choice_prompt=model_choices(),
        )

    if action == "inspect":
        return RouteResult("inspect", "core", True, 1.0, ["explicit_action:inspect"], ["inspect-report.json"])

    if action == "validate":
        return RouteResult("validate", "validator", True, 1.0, ["explicit_action:validate"], ["validation-report.json"])

    if action == "structure_template":
        missing = []
        if not _has_box_type(params):
            missing.append("model_code")
        if not _has_dimensions(params):
            missing.append("dimensions")
        implemented = True
        evidence = ["explicit_action:structure_template"]
        choice_prompt = None
        clarification = None
        if not _has_box_type(params):
            implemented = True
            choice_prompt = model_choices()
            clarification = "请选择盒型（或回复盒型名称/序号）："
        if not _has_dimensions(params):
            missing.append("dimensions")
            clarification = "请提供成品长、宽、高与单位："
        return RouteResult(
            action, "engine", implemented,
            1.0 if not missing else 0.5,
            evidence, ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing, choice_prompt=choice_prompt,
            clarification_question=clarification,
        )

    if action == "content_layout":
        missing = [k for k in ("template", "brief") if k not in params]
        return RouteResult(
            action, "content", True, 0.5 if missing else 1.0,
            ["explicit_action:content_layout"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing,
            clarification_question="请提供模板（template）与产品资料（brief）：" if missing else None,
        )

    if action == "mockup_render":
        missing = [k for k in ("artwork", "dimensions") if k not in params]
        return RouteResult(
            action, "cmf", True, 0.5 if missing else 1.0,
            ["explicit_action:mockup_render"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing, may_incur_cost=True,
            clarification_question="请提供完成稿（artwork）与真实物理尺寸（dimensions）：" if missing else None,
            manual_review_items=["CMF 效果图不能替代打样、正式印前文件或印厂确认"],
        )

    if action in ("bom", "sop"):
        return RouteResult(
            action, "bom", True, 1.0, [f"explicit_action:{action}"],
            ACTION_MAP[action]["expected_outputs"],
        )

    if action == "knowledge":
        missing = [] if params.get("domain") else ["domain"]
        return RouteResult(
            action, "knowledge", True, 0.5 if missing else 1.0,
            ["explicit_action:knowledge"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing,
            clarification_question="请指定知识域（domain），如 tobacco / plastics / transport：" if missing else None,
        )

    if action == "preview":
        missing = []
        if not _has_box_type(params):
            missing.append("model_code")
        if not _has_dimensions(params):
            missing.append("dimensions")
        return RouteResult(
            action, "fold", True, 0.5 if missing else 1.0,
            ["explicit_action:preview"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing,
            choice_prompt=model_choices() if not _has_box_type(params) else None,
            clarification_question="请选择盒型并提供成品长、宽、高：" if missing else None,
        )

    if action == "contact_sheet":
        return RouteResult(
            action, "contact_sheet", True, 1.0,
            ["explicit_action:contact_sheet"],
            ACTION_MAP[action]["expected_outputs"],
        )

    if action == "export_cf2":
        missing = []
        if not _has_box_type(params):
            missing.append("model_code")
        if not _has_dimensions(params):
            missing.append("dimensions")
        return RouteResult(
            action, "exporters", True, 0.5 if missing else 1.0,
            ["explicit_action:export_cf2"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing,
            clarification_question="请提供盒型与成品长、宽、高：" if missing else None,
        )

    if action == "export_spot_pdf":
        missing = []
        if not _has_box_type(params):
            missing.append("model_code")
        if not _has_dimensions(params):
            missing.append("dimensions")
        return RouteResult(
            action, "exporters", True, 0.5 if missing else 1.0,
            ["explicit_action:export_spot_pdf"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing,
            clarification_question="请提供盒型与成品长、宽、高：" if missing else None,
        )

    if action == "dimensions":
        missing = [k for k in ("length", "width", "height") if k not in params]
        return RouteResult(
            action, "dimensions", True, 0.5 if missing else 1.0,
            ["explicit_action:dimensions"],
            ACTION_MAP[action]["expected_outputs"],
            missing_fields=missing,
            clarification_question="请提供长、宽、高与换算方向（from_type / to_type）：" if missing else None,
        )

    raise RequestValidationError(
        "UNKNOWN_ACTION",
        f"不支持的 action：{action}",
        {"supported_actions": sorted(ACTION_MAP)},
    )


def supported_actions() -> list[str]:
    return sorted(ACTION_MAP)


def capability_manifest() -> dict:
    """Static capability manifest (module-level)."""
    return {
        "version": "0.1.0",
        "box_types": model_report(),
        "actions": supported_actions(),
        "notes": "几何由确定性代码生成；LLM 仅做意图识别与参数抽取。",
    }
