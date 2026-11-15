"""盒型目录 API：GET /api/boxes。"""

from __future__ import annotations

from flask import Blueprint

from packstudio.api.common import ok
from packstudio.engines.packaging_engine.structure.registry import model_choices, model_report

bp = Blueprint("boxes", __name__)


@bp.get("/api/boxes")
def list_boxes():
    """盒型目录：选择器条目 + 完整报告（ECMA/FEFCO/族别/实现状态）。"""
    return ok({"choices": model_choices(), "report": model_report()})
