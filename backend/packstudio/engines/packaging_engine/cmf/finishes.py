"""印后 / 表面整饰工艺（CMF: Finish）分类表。

数据来源与用途
--------------
本表是**咨询性**的通用工艺参考，用于把用户输入的中文/英文工艺名规范化，
并给出典型参数、成本提示与注意事项。所有字段都是"行业常见做法"，
不是某个具体厂商的实测值：

- ``params`` 只描述可被厂商确认的通用工艺变量；
- ``cost_hint`` 是相对档位，不能当作报价；
- ``notes`` 列出失效 / 降级情形。

本地绝不根据这张表"证明"任何工艺可行或已打样。是否可施工、工艺参数、
成本都必须由厂商验证（见 ``knowledge/tobacco/process.md`` 的
"需厂商验证"说明）。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 规范化辅助
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {}


def _register_alias(key: str, *names: str) -> None:
    for name in names:
        _ALIASES[name.strip().lower()] = key


def _build_aliases() -> None:
    _register_alias("foil_gold", "烫金", "烫金色", "金箔", "hot foil gold", "foil gold")
    _register_alias("foil_silver", "烫银", "烫银色", "银箔", "hot foil silver", "foil silver")
    _register_alias("spot_uv", "局部uv", "局部uv光油", "uv局部", "spot uv", "spot-uv")
    _register_alias("emboss", "击凸", "凸压", "凸版", "emboss", "embossing")
    _register_alias("deboss", "压凹", "凹压", "凹版", "deboss", "debossing")
    _register_alias("gloss_lamination", "覆膜光", "光膜", "光面覆膜", "gloss lamination", "glossy lamination")
    _register_alias("matte_lamination", "覆膜哑", "哑膜", "哑面覆膜", "matte lamination", "matt lamination")
    _register_alias("soft_touch", "覆膜触感", "触感膜", "soft touch", "soft-touch lamination", "soft touch film")
    _register_alias("holographic", "镭射", "全息", "镭射烫印", "holographic", "holo", "hologram")
    _register_alias("embossing_die", "模压", "压纹", "模压压纹", "embossing die", "embossing_die", "pressing")
    _register_alias("screen_print", "丝印", "丝网印刷", "丝网印", "screen print", "screen printing", "silk screen")


_build_aliases()

# ---------------------------------------------------------------------------
# 工艺分类表
# ---------------------------------------------------------------------------

FINISH_TAXONOMY: dict[str, dict[str, Any]] = {
    "foil_gold": {
        "key": "foil_gold",
        "label_zh": "烫金",
        "en": "Hot foil stamping (gold)",
        "params": {
            "foil": "金色电化铝箔",
            "temperature": "约 90–120 °C（需厂商按箔号确认）",
            "pressure": "中等，需按承印物平整度调整",
            "line_count": "常规 120–150 lpi 铜模；精细线条可更高",
            "area_constraint": "大面积平铺易出现烫印不实或露底",
            "underbase": "深色油墨 / 粗糙面建议先打底",
        },
        "cost_hint": "中高：按烫印面积、套数与色数计",
        "notes": (
            "深色墨底或粗糙纸张附着力下降；同一版面多色箔需分版。"
            "冷烫（CRD）无金属层压延效果，见烟包工艺说明。"
        ),
    },
    "foil_silver": {
        "key": "foil_silver",
        "label_zh": "烫银",
        "en": "Hot foil stamping (silver)",
        "params": {
            "foil": "银色电化铝箔",
            "temperature": "约 90–120 °C（需厂商按箔号确认）",
            "pressure": "中等",
            "line_count": "常规 120–150 lpi 铜模",
            "area_constraint": "大面积平铺易露底",
            "underbase": "深色墨底建议先打底",
        },
        "cost_hint": "中高：与烫金同档",
        "notes": (
            "银箔在粗糙面与深色底上同样有附着力问题；"
            "容易受氧化/摩擦掉色，需评估耐磨要求。"
        ),
    },
    "spot_uv": {
        "key": "spot_uv",
        "label_zh": "局部UV",
        "en": "Spot UV (UV varnish, localized)",
        "params": {
            "varnish": "UV 光油",
            "thickness": "约 3–8 μm 湿膜（需厂商确认）",
            "registration": "需与图文套准，套印误差会外溢",
            "overprint": "与哑膜/触感膜叠印时需先做附着试验",
            "lead_time": "需制丝网版或柔性版",
        },
        "cost_hint": "中等：多一块网版/版辊成本",
        "notes": (
            "局部 UV 与哑面底色对比最强；透明 UV 在白卡上不明显，"
            "在深色底上才显亮。溢出和龟裂是常见缺陷。"
        ),
    },
    "emboss": {
        "key": "emboss",
        "label_zh": "击凸",
        "en": "Embossing (relief raising)",
        "params": {
            "direction": "凸面朝向观察者",
            "depth": "约 0.1–0.5 mm（需厂商按纸板克重确认）",
            "die": "黄铜/镁/树脂浮雕模",
            "paper_weight": "克重不足易破裂或压塌",
            "registration": "与印刷图文套准，套印误差可见",
        },
        "cost_hint": "中高：制模成本为主，适合中大批量",
        "notes": (
            "薄纸或已覆膜材料易裂；多层复合结构需评估分层风险。"
            "击凸与压凹不可在同一版面共用同一套模具方向。"
        ),
    },
    "deboss": {
        "key": "deboss",
        "label_zh": "压凹",
        "en": "Debossing (relief recessed)",
        "params": {
            "direction": "凹面朝向观察者",
            "depth": "约 0.1–0.5 mm（需厂商确认）",
            "die": "凹模（阴阳模对压更稳定）",
            "paper_weight": "克重不足易破裂",
            "registration": "与印刷图文套准",
        },
        "cost_hint": "中高：制模成本为主",
        "notes": (
            "压凹常与局部 UV/烫金叠合；叠合前需确认 UV 在凹槽内的附着。"
            "薄纸易出现透印（背面可见）。"
        ),
    },
    "gloss_lamination": {
        "key": "gloss_lamination",
        "label_zh": "覆膜光",
        "en": "Gloss lamination",
        "params": {
            "film": "光面 BOPP/PET 复合膜",
            "thickness": "约 12–20 μm（需厂商确认）",
            "adhesion": "与油墨附着力受墨层成分影响",
            "post_process": "覆膜后可进行击凸/烫金，但需降速评估",
        },
        "cost_hint": "中低：膜材+复合工时",
        "notes": (
            "光膜反光强，深色墨底易显划痕；覆膜后不可再进行未干油墨工艺。"
        ),
    },
    "matte_lamination": {
        "key": "matte_lamination",
        "label_zh": "覆膜哑",
        "en": "Matte lamination",
        "params": {
            "film": "哑面 BOPP/PET 复合膜",
            "thickness": "约 12–20 μm（需厂商确认）",
            "adhesion": "与油墨附着力受墨层成分影响",
            "post_process": "可叠印局部 UV 形成亮哑对比",
        },
        "cost_hint": "中低：膜材+复合工时",
        "notes": (
            "哑膜表面易留指纹与擦痕；层压气泡、起皱是常见缺陷。"
        ),
    },
    "soft_touch": {
        "key": "soft_touch",
        "label_zh": "覆膜触感",
        "en": "Soft-touch lamination",
        "params": {
            "film": "触感膜（silicone/feel-soft 涂层或薄膜）",
            "thickness": "约 15–30 μm（需厂商确认）",
            "adhesion": "触感涂层对 UV 油墨附着力需验证",
            "post_process": "可与局部 UV 叠合，需附着试验",
            "lead_time": "膜材供货周期较长",
        },
        "cost_hint": "高：膜材单价与复合损耗较高",
        "notes": (
            "触感膜抗刮性偏弱，运输/陈列易划伤；成本最高，需按定位确认。"
        ),
    },
    "holographic": {
        "key": "holographic",
        "label_zh": "镭射",
        "en": "Holographic foil / hologram",
        "params": {
            "foil": "镭射电化铝箔（全息膜）",
            "pattern": "光柱/网格/散点等，需指定版号",
            "registration": "图案有方向性，套印需定位",
            "area_constraint": "大面积镭射易显脏与色偏",
            "underbase": "深色底会压低全息对比",
        },
        "cost_hint": "高：镭射膜制版费用高，适合品牌防伪定位",
        "notes": (
            "镭射效果受观察角度影响大；不同批次膜色可能略有差异，"
            "对色要求高的项目需签样。防伪用途需另配拉线/编码。"
        ),
    },
    "embossing_die": {
        "key": "embossing_die",
        "label_zh": "模压",
        "en": "Embossing die / texture die",
        "params": {
            "tool": "模压版（黄铜/镁/激光雕刻）",
            "depth": "约 0.1–0.5 mm（需厂商确认）",
            "texture": "皮纹/布纹/几何纹等",
            "paper_weight": "克重不足易破裂",
            "registration": "与图文套准或整版压纹",
        },
        "cost_hint": "高：制模成本为主",
        "notes": (
            "模压与击凸工艺原理相近但目的不同：模压偏纹理，击凸偏图形。"
            "整版压纹会改变表面摩擦与后续烫印附着。"
        ),
    },
    "screen_print": {
        "key": "screen_print",
        "label_zh": "丝印",
        "en": "Screen printing",
        "params": {
            "mesh": "常规 150–300 目（按网点/线条选择）",
            "ink": "UV 丝印墨或溶剂墨（按承印物选）",
            "registration": "与底印套准",
            "curing": "UV 墨需固化，溶剂墨需干燥",
            "surface": "粗糙面需先处理或选高附着墨",
        },
        "cost_hint": "中低：制版+工时，适合中批量",
        "notes": (
            "丝印墨层厚，叠印在普通胶印墨上附着力需验证；"
            "精细四色不适用丝印，应走胶印/数码。"
        ),
    },
}

# 中文名 -> key（用于中文输入规范化）
CN_TO_KEY: dict[str, str] = {
    entry["label_zh"]: key
    for key, entry in FINISH_TAXONOMY.items()
}

# key -> 中文名
KEY_TO_CN: dict[str, str] = {
    key: entry["label_zh"]
    for key, entry in FINISH_TAXONOMY.items()
}


def _normalise(value: object) -> str:
    return " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())


def resolve_finish(value: object) -> str:
    """把用户输入的中文/英文/内部 key 归一为 ``FINISH_TAXONOMY`` 的 key。

    无法识别时返回 ``""``，由调用方决定报错策略。
    """
    raw = str(value).strip()
    if not raw:
        return ""
    if raw in FINISH_TAXONOMY:
        return raw
    key = _ALIASES.get(raw.lower())
    if key:
        return key
    key = _ALIASES.get(_normalise(raw))
    if key:
        return key
    key = CN_TO_KEY.get(raw)
    if key:
        return key
    # 反向：中文 label 去掉空格
    compact = raw.replace(" ", "")
    for label, key in CN_TO_KEY.items():
        if compact == label.replace(" ", ""):
            return key
    return ""


def known_finishes() -> list[str]:
    """返回所有已登记的工艺 key（按插入顺序）。"""
    return list(FINISH_TAXONOMY.keys())


def finish_names_zh() -> list[str]:
    return [entry["label_zh"] for entry in FINISH_TAXONOMY.values()]


def cost_rank(finish_key: str) -> str:
    """返回成本档位（low / medium / high）。仅供排序，不是报价。"""
    hint = str(FINISH_TAXONOMY.get(finish_key, {}).get("cost_hint", ""))
    if hint.startswith("高"):
        return "high"
    if hint.startswith("中高"):
        return "high"
    if hint.startswith("中低") or hint.startswith("低"):
        return "low"
    return "medium"
