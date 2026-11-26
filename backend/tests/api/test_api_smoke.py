"""PackStudio REST API 冒烟测试（Flask test client，不落真实服务）。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from packstudio.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _png_bytes(color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 基础与盒型
# ---------------------------------------------------------------------------


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["app"] == "packstudio"


def test_boxes_catalog(client):
    resp = client.get("/api/boxes")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    codes = {item["value"] for item in data["choices"]}
    assert "carton.box_v2.straight" in codes
    assert any(item["status"] == "not_implemented" for item in data["choices"])


# ---------------------------------------------------------------------------
# 刀版渲染与校验
# ---------------------------------------------------------------------------

_SPEC = {
    "model_code": "carton.box_v2.straight",
    "dimensions": {"length": 120.0, "width": 60.0, "height": 90.0, "unit": "mm"},
}


def test_dieline_render_ok(client):
    resp = client.post("/api/dieline", json=dict(_SPEC))
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["svg"].lstrip().startswith("<?xml")
    assert "viewBox" in data["svg"]
    assert data["net_size_mm"]["width"] > 0
    assert data["metadata"]["box_type"] == "carton.box_v2.straight"
    assert data["validation"]["passed"] is True
    ids = {c["id"] for c in data["validation"]["checks"]}
    assert "NET_DIMENSION_CONSISTENCY" in ids


def test_dieline_render_rejects_unknown_code(client):
    resp = client.post(
        "/api/dieline",
        json={"model_code": "carton.box_v2.no_such", "dimensions": _SPEC["dimensions"]},
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "STRUCTURE_NOT_IMPLEMENTED"


def test_dieline_render_rejects_bad_dimensions(client):
    resp = client.post(
        "/api/dieline",
        json={"model_code": "carton.box_v2.straight", "dimensions": {"length": -1, "width": 10, "height": 10}},
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "INVALID_DIMENSIONS"


# ---------------------------------------------------------------------------
# 导出与下载
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt,mime", [("svg", "image/svg+xml"), ("pdf", "application/pdf"), ("dxf", "application/dxf"), ("cf2", "application/xml"), ("pdf_spot", "application/pdf")])
def test_export_and_download_roundtrip(client, fmt, mime):
    resp = client.post("/api/export", json=dict(_SPEC, format=fmt))
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["mime"] == mime
    assert data["size"] > 0
    dl = client.get(f"/api/download/{data['id']}/{data['filename']}")
    assert dl.status_code == 200
    assert len(dl.data) == data["size"]


def test_download_rejects_bad_id(client):
    resp = client.get("/api/download/../../etc/passwd/x.svg")
    assert resp.status_code in (404, 405)


def test_export_rejects_unknown_format(client):
    resp = client.post("/api/export", json=dict(_SPEC, format="docx"))
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_FORMAT"


# ---------------------------------------------------------------------------
# ICC
# ---------------------------------------------------------------------------


def test_icc_profiles_endpoint(client):
    resp = client.get("/api/icc/profiles")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert isinstance(data["profiles"], list)


def test_icc_lab_builtin_srgb(client):
    resp = client.post("/api/icc/lab", json={"rgb": [200, 40, 40]})
    assert resp.status_code == 200
    lab = resp.get_json()["data"]["lab"]
    assert 0.0 <= lab[0] <= 100.0
    assert len(lab) == 3


def test_icc_proof_roundtrip(client):
    resp = client.post(
        "/api/icc/proof",
        data={"file": (io.BytesIO(_png_bytes()), "snap.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["proof"]["id"]
    assert data["out_of_gamut"]["id"]
    assert "oog_ratio" in data["stats"]
    dl = client.get(f"/api/download/{data['proof']['id']}/proof.png")
    assert dl.status_code == 200
    assert dl.data.startswith(b"\x89PNG")


def test_icc_proof_oog_mask_transparent_rgba(client):
    """掩图必须是透明底 RGBA（红色告警像素），叠加不刷白画布。"""
    resp = client.post(
        "/api/icc/proof",
        data={"file": (io.BytesIO(_png_bytes()), "snap.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    dl = client.get(f"/api/download/{data['out_of_gamut']['id']}/{data['out_of_gamut']['filename']}")
    assert dl.status_code == 200
    im = Image.open(io.BytesIO(dl.data))
    assert im.mode == "RGBA"
    rgba = im.convert("RGBA")
    # 默认设备是内置 sRGB：往返恒等 → 无越界 → 全图透明
    assert data["stats"]["oog_ratio"] == 0.0
    corner = rgba.getpixel((0, 0))
    assert corner[3] == 0


def test_icc_proof_oog_mask_flags_out_of_gamut(client):
    """高饱和青色（sRGB→sRGB 恒等应为 0 越界）；这里验证掩图像素与统计一致。"""
    resp = client.post(
        "/api/icc/proof",
        data={"file": (io.BytesIO(_png_bytes((0, 255, 0))), "snap.png"), "oog_threshold": "0.5"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    stats = data["stats"]
    assert stats["threshold"] == 0.5
    assert 0.0 <= stats["oog_ratio"] <= 1.0
    dl = client.get(f"/api/download/{data['out_of_gamut']['id']}/{data['out_of_gamut']['filename']}")
    im = Image.open(io.BytesIO(dl.data)).convert("RGBA")
    red_px = sum(1 for p in im.getdata() if p[3] == 255)
    assert red_px == stats["oog_count"]


# ---------------------------------------------------------------------------
# PDF 审计
# ---------------------------------------------------------------------------


def test_pdf_audit_rejects_garbage(client):
    resp = client.post(
        "/api/pdf/audit",
        data={"file": (io.BytesIO(b"not a pdf"), "bad.pdf")},
        content_type="multipart/form-data",
    )
    assert resp.status_code in (400, 422)
    assert resp.get_json()["ok"] is False


def test_pdf_audit_missing_file(client):
    resp = client.post("/api/pdf/audit", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "MISSING_FILE"


# ---------------------------------------------------------------------------
# 前端静态回退
# ---------------------------------------------------------------------------


def test_index_serves_frontend_or_hint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # frontend/dist 已构建 → HTML；未构建 → JSON 提示。两者都合法。
    if resp.content_type.startswith("text/html"):
        assert b"PackStudio" in resp.data
    else:
        assert resp.get_json()["ok"] is True


def test_unknown_api_404(client):
    resp = client.get("/api/no_such_endpoint")
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False


# ---------------------------------------------------------------------------
# 回归测试：工艺参数透传、CMYK 色彩链路、ΔE00 打样、安全防护与输出清理
# ---------------------------------------------------------------------------


def test_dieline_process_params_affect_geometry(client):
    """前端顶层工艺参数（tuck_height/glue_width/shrink/bleed）必须真实影响展开几何，且兼容旧 parameters_mm。"""
    base = client.post("/api/dieline", json=dict(_SPEC)).get_json()["data"]
    bigger = client.post(
        "/api/dieline",
        json=dict(_SPEC, tuck_height=28.0, glue_width=24.0, shrink=0.5, bleed=5.0),
    ).get_json()["data"]
    assert bigger["net_size_mm"]["width"] > base["net_size_mm"]["width"] + 5.0
    assert bigger["net_size_mm"]["height"] > base["net_size_mm"]["height"] + 5.0
    assert bigger["metadata"]["parameters_mm"]["tuck_height"] == pytest.approx(28.0)
    assert bigger["metadata"]["parameters_mm"]["glue_width"] == pytest.approx(24.0)
    assert bigger["metadata"]["parameters_mm"]["bleed"] == pytest.approx(5.0)

    # 向后兼容旧 parameters_mm 嵌套形式
    nested = client.post(
        "/api/dieline",
        json=dict(_SPEC, parameters_mm={"tuck_height": 28.0, "glue_width": 24.0}),
    ).get_json()["data"]
    assert nested["net_size_mm"]["width"] == pytest.approx(bigger["net_size_mm"]["width"], abs=0.5)
    assert nested["metadata"]["parameters_mm"]["tuck_height"] == pytest.approx(28.0)


def test_fold_preview_forwards_params_and_preserves_error_code(client):
    resp = client.get(
        "/api/dieline/fold",
        query_string={
            "model_code": "carton.box_v2.straight",
            "length": "120",
            "width": "60",
            "height": "90",
            "tuck_height": "20",
            "glue_width": "18",
        },
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert b"/vendor/three.min.js" in resp.data

    bad = client.get(
        "/api/dieline/fold",
        query_string={"model_code": "carton.box_v2.no_such", "length": "100", "width": "50", "height": "80"},
    )
    assert bad.status_code == 422
    assert bad.get_json()["error"]["code"] == "STRUCTURE_NOT_IMPLEMENTED"


def test_icc_proof_defaults_to_de00_and_supports_de76(client):
    r_default = client.post(
        "/api/icc/proof",
        data={"file": (io.BytesIO(_png_bytes((20, 220, 40))), "snap.png")},
        content_type="multipart/form-data",
    )
    assert r_default.status_code == 200
    assert r_default.get_json()["data"]["stats"]["metric"] == "de00"

    r_de76 = client.post(
        "/api/icc/proof",
        data={"file": (io.BytesIO(_png_bytes((20, 220, 40))), "snap.png"), "metric": "de76"},
        content_type="multipart/form-data",
    )
    assert r_de76.status_code == 200
    assert r_de76.get_json()["data"]["stats"]["metric"] == "de76"

    r_bad = client.post(
        "/api/icc/proof",
        data={"file": (io.BytesIO(_png_bytes()), "snap.png"), "metric": "de99"},
        content_type="multipart/form-data",
    )
    assert r_bad.status_code == 400
    assert r_bad.get_json()["error"]["code"] == "INVALID_METRIC"


def _find_system_cmyk_profile() -> str | None:
    from packstudio.engines.color_engine.cmm import discovery

    for p in discovery.scan_profiles():
        if p.color_space.strip().upper() == "CMYK":
            return p.path
    return None


def test_icc_lab_and_convert_with_cmyk_profile(client):
    """CMYK 输出档案下取色与图层转档均应成功（取色返回 CMYK/TAC+Lab，转档输出 TIFF + sRGB 预览）。"""
    cmyk_path = _find_system_cmyk_profile()
    if not cmyk_path:
        pytest.skip("系统未安装 CMYK ICC 档案，跳过 CMYK 集成测试")

    # 1) 取色：sRGB → CMYK → Lab
    lab_resp = client.post("/api/icc/lab", json={"rgb": [220, 40, 40], "profile": cmyk_path})
    assert lab_resp.status_code == 200
    lab_data = lab_resp.get_json()["data"]
    assert lab_data["color_space"] == "CMYK"
    assert len(lab_data["device_values"]) == 4
    assert lab_data["tac"] is not None and 0.0 <= lab_data["tac"] <= 400.0
    assert len(lab_data["lab"]) == 3

    # 2) 转档：无嵌入 ICC 的 RGB PNG → CMYK 自动切换 TIFF + 返回 sRGB 预览 PNG
    conv_resp = client.post(
        "/api/icc/convert",
        data={
            "file": (io.BytesIO(_png_bytes((220, 40, 40))), "layer.png"),
            "op": "convert",
            "profile_path": cmyk_path,
            "intent": "relative",
            "out_format": "png",
        },
        content_type="multipart/form-data",
    )
    assert conv_resp.status_code == 200
    conv_data = conv_resp.get_json()["data"]
    assert conv_data["assumed_src_srgb"] is True
    assert conv_data["color_space"] == "CMYK"
    assert conv_data["filename"] == "converted.tif"
    assert conv_data["preview"] is not None

    tif_dl = client.get(f"/api/download/{conv_data['id']}/{conv_data['filename']}")
    assert tif_dl.status_code == 200
    assert Image.open(io.BytesIO(tif_dl.data)).mode == "CMYK"

    prev_dl = client.get(f"/api/download/{conv_data['preview']['id']}/{conv_data['preview']['filename']}")
    assert prev_dl.status_code == 200
    assert prev_dl.data.startswith(b"\x89PNG")


def test_icc_path_whitelist_and_local_origin_guard(client):
    """阻断非 ICC 后缀与非白名单目录探测，并阻断跨站非本机 Origin 写请求。"""
    from pathlib import Path

    resp_ext = client.get("/api/icc/validate", query_string={"path": "not_an_icc.ini"})
    assert resp_ext.status_code == 403
    assert resp_ext.get_json()["error"]["code"] == "INVALID_PROFILE_PATH"

    outside_icc = Path(__file__).resolve().parent / "fake.icc"
    resp_dir = client.get("/api/icc/validate", query_string={"path": str(outside_icc)})
    assert resp_dir.status_code == 403
    assert resp_dir.get_json()["error"]["code"] == "INVALID_PROFILE_PATH"

    resp_csrf = client.post(
        "/api/icc/lab",
        json={"rgb": [10, 20, 30]},
        headers={"Origin": "https://evil.example"},
    )
    assert resp_csrf.status_code == 403
    assert resp_csrf.get_json()["error"]["code"] == "INVALID_ORIGIN"

    resp_local = client.post(
        "/api/icc/lab",
        json={"rgb": [10, 20, 30]},
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert resp_local.status_code == 200


def test_sweep_outputs_removes_expired_dirs(tmp_path, monkeypatch):
    """验证 _sweep_outputs 清理过期生成物目录而保留新鲜目录。"""
    import os
    import time

    from packstudio.api import common

    monkeypatch.setattr(common, "_OUTPUT_DIR", tmp_path)
    old_id = common.store_output("old.svg", b"<svg/>")
    new_id = common.store_output("new.svg", b"<svg/>")

    old_dir = tmp_path / old_id
    past = time.time() - 7200
    os.utime(old_dir, (past, past))

    removed = common._sweep_outputs(force=True, ttl_seconds=3600)
    assert removed == 1
    assert not old_dir.exists()
    assert (tmp_path / new_id).exists()
