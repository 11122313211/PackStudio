"""材质 → 工艺（Finish）兼容性矩阵。

用途
----
把"这种纸能不能做这个工艺"这种经验判断，整理成**可查询、可解释**的矩阵，
供 ``cmf.service.plan_cmf`` 在生成计划前做前置检查。

状态含义
--------
``supported``
    该材质常规可施工，无额外前提。
``precoat_required``
    理论上可施工，但必须先做预涂/底涂/复合等前处理；是否可行与成本
    必须由厂商验证。
``limited``
    仅在限定条件下可行（如克重、楞型、版深、后道套准等），属于
    "需厂商验证"的边界情形。
``unsupported``
    该材质通常不做此工艺，或工艺会破坏结构/外观。

重要边界
--------
本矩阵是**咨询性**参考，不是供应商认证结论。矩阵的"unsupported"
只表示"通常不做"，不构成对任何厂商能力的保证；任何进入生产的决定
都必须回到 ``knowledge/*/process.md`` 的"需厂商验证"流程。
"""

from __future__ import annotations

from typing import Any

from .finishes import FINISH_TAXONOMY, resolve_finish

# ---------------------------------------------------------------------------
# 材质分类
# ---------------------------------------------------------------------------

MATERIAL_TAXONOMY: dict[str, dict[str, Any]] = {
    "white_card": {
        "key": "white_card",
        "label_zh": "白卡",
        "en": "White card / white board",
        "notes": "表面平滑、白度高，是最常见的印后工艺载体。",
    },
    "coated_paper": {
        "key": "coated_paper",
        "label_zh": "铜版纸",
        "en": "Coated paper",
        "notes": "涂层平滑但克重通常低于白卡，击凸/压凹深度受限。",
    },
    "kraft": {
        "key": "kraft",
        "label_zh": "牛皮纸",
        "en": "Kraft paper",
        "notes": "表面粗糙、颜色偏深，浅色工艺需底涂，深色工艺对比差。",
    },
    "corrugated": {
        "key": "corrugated",
        "label_zh": "瓦楞纸",
        "en": "Corrugated board",
        "notes": "表面不平整，印后工艺需穿透到楞峰，常见需预涂或改用丝印。",
    },
    "plastic": {
        "key": "plastic",
        "label_zh": "塑胶",
        "en": "Plastic (PET / PVC / PP / PS)",
        "notes": "表面能低，需先做附着/电晕处理；成型方式决定能否压凸。",
    },
}

_MATERIAL_ALIASES: dict[str, str] = {}


def _register_material_alias(key: str, *names: str) -> None:
    for name in names:
        _MATERIAL_ALIASES[name.strip().lower()] = key


_register_material_alias("white_card", "白卡", "白卡纸", "白板纸", "white card", "white cardboard", "ivory board", "ivory")
_register_material_alias("coated_paper", "铜版纸", "涂布纸", "coated paper", "art paper")
_register_material_alias("kraft", "牛皮纸", "kraft paper", "kraft")
_register_material_alias("corrugated", "瓦楞纸", "瓦楞板", "corrugated", "corrugated board", "corrugate", "flute")
_register_material_alias("plastic", "塑胶", "塑料", "塑片", "plastic", "pet", "pvc", "pp", "ps", "abs", "acrylic", "polystyrene")

# ---------------------------------------------------------------------------
# 兼容性矩阵
# ---------------------------------------------------------------------------

# material -> {finish_key -> status}
COMPATIBILITY: dict[str, dict[str, str]] = {
    "white_card": {
        "foil_gold": "supported",
        "foil_silver": "supported",
        "spot_uv": "supported",
        "emboss": "supported",
        "deboss": "supported",
        "gloss_lamination": "supported",
        "matte_lamination": "supported",
        "soft_touch": "supported",
        "holographic": "supported",
        "embossing_die": "supported",
        "screen_print": "supported",
    },
    "coated_paper": {
        "foil_gold": "supported",
        "foil_silver": "supported",
        "spot_uv": "supported",
        "emboss": "limited",
        "deboss": "limited",
        "gloss_lamination": "supported",
        "matte_lamination": "supported",
        "soft_touch": "supported",
        "holographic": "supported",
        "embossing_die": "limited",
        "screen_print": "supported",
    },
    "kraft": {
        "foil_gold": "precoat_required",
        "foil_silver": "precoat_required",
        "spot_uv": "precoat_required",
        "emboss": "limited",
        "deboss": "limited",
        "gloss_lamination": "supported",
        "matte_lamination": "supported",
        "soft_touch": "limited",
        "holographic": "precoat_required",
        "embossing_die": "limited",
        "screen_print": "supported",
    },
    "corrugated": {
        "foil_gold": "precoat_required",
        "foil_silver": "precoat_required",
        "spot_uv": "precoat_required",
        "emboss": "limited",
        "deboss": "limited",
        "gloss_lamination": "unsupported",
        "matte_lamination": "unsupported",
        "soft_touch": "unsupported",
        "holographic": "precoat_required",
        "embossing_die": "limited",
        "screen_print": "supported",
    },
    "plastic": {
        "foil_gold": "supported",
        "foil_silver": "supported",
        "spot_uv": "supported",
        "emboss": "supported",  # 通过模具/压纹实现，需厂商验证
        "deboss": "supported",
        "gloss_lamination": "limited",
        "matte_lamination": "limited",
        "soft_touch": "supported",
        "holographic": "supported",
        "embossing_die": "supported",
        "screen_print": "supported",
    },
}

STATUS_CN: dict[str, str] = {
    "supported": "可直接施工",
    "precoat_required": "需预涂/底涂后施工",
    "limited": "有条件可行（需厂商验证）",
    "unsupported": "通常不适用",
}


def resolve_material(value: object) -> str:
    """把中文/英文/内部 key 归一为 ``MATERIAL_TAXONOMY`` 的 key。"""
    raw = str(value).strip()
    if not raw:
        return ""
    if raw in MATERIAL_TAXONOMY:
        return raw
    lowered = raw.lower()
    if lowered in _MATERIAL_ALIASES:
        return _MATERIAL_ALIASES[lowered]
    compact = lowered.replace(" ", "").replace("-", "")
    for alias, key in _MATERIAL_ALIASES.items():
        if compact == alias.replace(" ", "").replace("-", ""):
            return key
    return ""


def _status(material_key: str, finish_key: str) -> str:
    return COMPATIBILITY.get(material_key, {}).get(finish_key, "unsupported")


def compatible(material: object, finish: object, *, allow_preconditions: bool = True) -> bool:
    """返回该材质是否可施工该工艺。

    默认 ``allow_preconditions=True``，即 ``precoat_required`` 与
    ``limited`` 视为"有条件可行"并返回 ``True``；``unsupported`` 或
    无法识别的输入返回 ``False``。需要严格判定时传入
    ``allow_preconditions=False``。
    """
    return bool(compatibility_report(material, finish, allow_preconditions=allow_preconditions)["compatible"])


def compatibility_report(
    material: object,
    finish: object,
    *,
    allow_preconditions: bool = True,
) -> dict[str, Any]:
    """返回可解释的兼容性判定结果。"""
    material_key = resolve_material(material)
    finish_key = resolve_finish(finish)

    if not material_key:
        return {
            "material": str(material),
            "material_key": "",
            "finish": str(finish),
            "finish_key": finish_key,
            "status": "unknown",
            "compatible": False,
            "reason": f"未知材质：{material}；无法判定兼容性。",
            "preconditions": [],
        }
    if not finish_key:
        return {
            "material": str(material),
            "material_key": material_key,
            "finish": str(finish),
            "finish_key": "",
            "status": "unknown",
            "compatible": False,
            "reason": f"未知工艺：{finish}；无法判定兼容性。",
            "preconditions": [],
        }

    status = _status(material_key, finish_key)
    material_label = MATERIAL_TAXONOMY[material_key]["label_zh"]
    finish_label = FINISH_TAXONOMY[finish_key]["label_zh"]

    preconditions: list[str] = []
    if status == "precoat_required":
        preconditions.append(
            f"{material_label} 上做{finish_label}前需先做预涂/底涂，具体配方与成本需厂商验证。"
        )
    elif status == "limited":
        preconditions.append(
            f"{material_label} 上做{finish_label}受克重/楞型/版深/套准等条件限制，需厂商验证。"
        )

    is_compatible = status == "supported" or (allow_preconditions and status in {"precoat_required", "limited"})
    reason = (
        f"{material_label} 与 {finish_label}：{STATUS_CN.get(status, status)}。"
    )
    if preconditions:
        reason = "；".join([reason, *preconditions])

    return {
        "material": str(material),
        "material_key": material_key,
        "material_label": material_label,
        "finish": str(finish),
        "finish_key": finish_key,
        "finish_label": finish_label,
        "status": status,
        "status_zh": STATUS_CN.get(status, status),
        "compatible": bool(is_compatible),
        "reason": reason,
        "preconditions": preconditions,
        "notes": FINISH_TAXONOMY[finish_key].get("notes", ""),
    }


def compatibility_plan(material: object, finishes: list[object]) -> dict[str, Any]:
    """对一个方案做整体兼容性体检。"""
    material_key = resolve_material(material)
    reports = [compatibility_report(material, value) for value in finishes]
    blocking = [r for r in reports if not r["compatible"]]
    return {
        "material": str(material),
        "material_key": material_key,
        "finishes": [
            {
                "finish": report["finish"],
                "finish_key": report["finish_key"],
                "status": report["status"],
                "status_zh": report["status_zh"],
                "compatible": report["compatible"],
                "reason": report["reason"],
                "preconditions": report["preconditions"],
            }
            for report in reports
        ],
        "all_compatible": not blocking,
        "blocking": [report["finish_key"] for report in blocking],
        "requires_manufacturer_validation": any(
            report["status"] in {"precoat_required", "limited"} for report in reports
        ),
    }


def material_options() -> list[dict[str, str]]:
    """返回可供 UI / LLM 选择的材质列表。"""
    return [
        {"value": key, "label": entry["label_zh"]}
        for key, entry in MATERIAL_TAXONOMY.items()
    ]


def finish_options() -> list[dict[str, str]]:
    """返回可供 UI / LLM 选择的工艺列表。"""
    return [
        {"value": key, "label": entry["label_zh"], "en": entry["en"]}
        for key, entry in FINISH_TAXONOMY.items()
    ]
