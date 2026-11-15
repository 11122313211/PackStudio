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
