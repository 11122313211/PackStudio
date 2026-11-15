"""Box-type registry — the contract the dieline engine implements against.

A box type is "implemented" only when `engine.render` produces deterministic
geometry. Anything not implemented MUST return NOT_IMPLEMENTED, never an
approximation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoxType:
    code: str
    name_zh: str
    aliases: tuple[str, ...]
    implemented: bool
    ecma: str = ""
    fefco: str = ""
    family: str = "carton"  # carton | shipping | tray | sleeve | custom


BOX_TYPES: tuple[BoxType, ...] = (
    BoxType("carton.box_v2.straight", "直线盒", ("直线盒", "直线", "straight"), True, "A20.20.01.01"),
    BoxType("carton.box_v2.lock_bottom", "锁底盒", ("锁底盒", "锁底", "lock bottom", "auto lock bottom"), True, "A01.55.00.01"),
    BoxType("carton.box_v2.mailer", "飞机盒", ("飞机盒", "mailer"), True),
    BoxType("carton.box_v2.top_cover", "上盖盒", ("上盖盒", "top cover"), True),
    BoxType("carton.box_v2.same_direction_tuck", "同向盖", ("同向盖", "同向插口", "same direction tuck"), True, "A20.20.03.01"),
    BoxType("carton.box_v2.glue_bottom", "粘底盒", ("粘底盒", "粘底", "glue bottom"), True),
    BoxType("carton.box_v2.hang_tab", "挂耳盒", ("挂耳盒", "挂耳", "hang tab"), True),
    BoxType("carton.box_v2.carry_handle", "手提盒", ("手提盒", "carry handle"), True),
    BoxType("carton.box_v2.shipping_carton", "纸箱", ("纸箱", "shipping carton"), True, fefco="0201"),
    BoxType("carton.box_v2.crash_lock", "插口锁底盒", ("插口盒", "crash lock", "auto bottom"), True, "A01.55.00.01"),
    BoxType("carton.box_v2.gable_top", "屋顶提手盒", ("屋顶盒", "gable top"), True, fefco="0217"),
    BoxType("carton.box_v2.pillow", "枕式盒", ("枕式盒", "pillow box"), True),
    BoxType("carton.box_v2.tray", "托盘盒", ("托盘盒", "tray"), True, family="tray"),
    BoxType("carton.box_v2.sleeve_tray", "套筒托盘", ("套筒+托盘", "sleeve tray"), True, family="sleeve"),
    BoxType("carton.box_v2.food_box", "翻盖食品盒", ("食品盒", "food box"), True),
    BoxType("carton.box_v2.fefco_0427", "卷边托盘锁扣盒", ("锁扣盒", "roll end tray", "fefco 0427"), True, fefco="0427"),
    BoxType("carton.box_v2.diamond", "菱形折面盒", ("菱形盒", "diamond box"), True),
    BoxType("carton.box_v2.rectangular_blank", "四壁托盘带角扣", ("角扣托盘", "rectangular blank"), True, family="tray"),
    BoxType("carton.box_v2.custom", "其它", ("其它", "其他", "custom"), False),
)


def _key(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def resolve_box_type(value: str) -> BoxType | None:
    target = _key(value)
    for box in BOX_TYPES:
        if target == _key(box.code) or target == _key(box.name_zh):
            return box
        if target in {_key(a) for a in box.aliases}:
            return box
    return None


def is_implemented(code: str) -> bool:
    box = resolve_box_type(code)
    return bool(box and box.implemented)


def model_report() -> list[dict]:
    return [
        {
            "code": box.code,
            "name_zh": box.name_zh,
            "aliases": list(box.aliases),
            "implemented": box.implemented,
            "ecma": box.ecma,
            "fefco": box.fefco,
            "family": box.family,
        }
        for box in BOX_TYPES
    ]


def model_choices() -> list[dict]:
    ordered = sorted(enumerate(BOX_TYPES), key=lambda item: (not item[1].implemented, item[0]))
    return [
        {
            "value": box.code,
            "label": box.name_zh,
            "status": "available" if box.implemented else "not_implemented",
        }
        for _, box in ordered
    ]
