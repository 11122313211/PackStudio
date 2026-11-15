"""ICC 闭环两条新路由的 API 测试：/api/icc/validate 与 /api/icc/verify。"""

from __future__ import annotations

import io

import pytest
from PIL import ImageCms

from packstudio.app import create_app
from packstudio.engines.color_engine.measurement.verify import REFERENCE_TABLES


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _srgb_profile_bytes() -> bytes:
    """lcms 合成的标准 v2 mntr profile（含必需 tag，结构应当 valid）。"""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _cgats_from_reference(delta: float) -> bytes:
    """由内置 Fogra39 参考表构造 CGATS.17 实测文件（每 patch Lab 偏移 delta）。

    delta=0.4 → ΔE00 远小于容差（PASS）；delta=20 → 全面超差（FAIL）。
    SAMPLE_ID 用参考表 target 名，走名称对齐路径（无需 profile）。
    """
    table = REFERENCE_TABLES["Fogra39"]
    fields = ["SAMPLE_ID", *table.device_fields, "LAB_L", "LAB_A", "LAB_B"]
    lines = [
        "CGATS.17",
        'ORIGINATOR\t"PackStudio API test"',
        f"NUMBER_OF_FIELDS\t{len(fields)}",
        "BEGIN_DATA_FORMAT",
        "\t".join(fields),
        "END_DATA_FORMAT",
        f"NUMBER_OF_SETS\t{len(table.patches)}",
        "BEGIN_DATA",
    ]
    for patch in table.patches:
        device = "\t".join(f"{v:.0f}" for v in patch.device)
        lab = "\t".join(f"{v + delta:.4f}" for v in patch.lab)
        lines.append(f"{patch.target}\t{device}\t{lab}")
    lines.append("END_DATA")
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# /api/icc/validate
# ---------------------------------------------------------------------------


def test_validate_get_with_profile_file(client, tmp_path):
    profile = tmp_path / "srgb.icc"
    profile.write_bytes(_srgb_profile_bytes())
    resp = client.get("/api/icc/validate", query_string={"path": str(profile)})
    assert resp.status_code == 200
    report = resp.get_json()["data"]["report"]
    assert report["valid"] is True
    assert report["counts"]["error"] == 0
    assert isinstance(report["issues"], list)


def test_validate_get_requires_path(client):
    resp = client.get("/api/icc/validate")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "MISSING_PROFILE"


def test_validate_get_missing_file_maps_to_422(client, tmp_path):
    resp = client.get("/api/icc/validate", query_string={"path": str(tmp_path / "nope.icc")})
    assert resp.status_code == 422
    assert resp.get_json()["ok"] is False


def test_validate_post_upload_valid_profile(client):
    resp = client.post(
        "/api/icc/validate",
        data={"file": (io.BytesIO(_srgb_profile_bytes()), "srgb.icc")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["filename"] == "srgb.icc"
    assert data["report"]["valid"] is True


def test_validate_post_garbage_bytes_reported_not_raised(client):
    """垃圾字节不抛 500：结构问题以 issues 形式返回（valid=False）。"""
    resp = client.post(
        "/api/icc/validate",
        data={"file": (io.BytesIO(b"not-an-icc-profile-at-all" * 10), "bad.icc")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    report = resp.get_json()["data"]["report"]
    assert report["valid"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert "HDR_MAGIC" in codes or "HDR_SHORT_FILE" in codes


def test_validate_post_requires_file_or_path(client):
    resp = client.post("/api/icc/validate", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "MISSING_PROFILE"


# ---------------------------------------------------------------------------
# /api/icc/verify
# ---------------------------------------------------------------------------


def test_verify_pass_with_reference_table(client):
    resp = client.post(
        "/api/icc/verify",
        data={"file": (io.BytesIO(_cgats_from_reference(delta=0.4)), "meas.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["verdict"] == "PASS"
    assert data["passed"] is True
    assert data["reference"] == "Fogra39"
    assert data["tolerance"] == pytest.approx(5.0)
    report = data["report"]
    assert report["stats"]["count"] == 9
    assert len(report["iso"]) == 9
    assert all(item["passed"] for item in report["iso"])
    assert report["stats"]["mean"] < 1.0


def test_verify_fail_when_far_off(client):
    resp = client.post(
        "/api/icc/verify",
        data={"file": (io.BytesIO(_cgats_from_reference(delta=20.0)), "meas.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["verdict"] == "FAIL"
    assert data["passed"] is False
    assert not all(item["passed"] for item in data["report"]["iso"])
    # 通道诊断：CMYK device 数据齐备 → 应给出 hints
    assert data["report"]["channel_diagnostics"] is not None


def test_verify_tolerance_override_forces_fail(client):
    resp = client.post(
        "/api/icc/verify",
        data={"file": (io.BytesIO(_cgats_from_reference(delta=2.0)), "meas.txt"), "tolerance": "0.5"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["tolerance"] == pytest.approx(0.5)
    # 超出容差 1.0 以上（工程警示带之外）→ FAIL；0.5~1.0 区间是 WARNING
    assert data["verdict"] == "FAIL"


def test_verify_missing_lab_columns_maps_to_422(client):
    """缺 LAB 列 → 引擎 NO_LAB 稳定码透出（闭环反馈可定位原因）。"""
    body = "\n".join(
        [
            "CGATS.17",
            "NUMBER_OF_FIELDS\t5",
            "BEGIN_DATA_FORMAT",
            "SAMPLE_ID\tCMYK_C\tCMYK_M\tCMYK_Y\tCMYK_K",
            "END_DATA_FORMAT",
            "NUMBER_OF_SETS\t1",
            "BEGIN_DATA",
            "paper\t0\t0\t0\t0",
            "END_DATA",
        ]
    )
    resp = client.post(
        "/api/icc/verify",
        data={"file": (io.BytesIO(body.encode("utf-8")), "nolab.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "NO_LAB"


def test_verify_garbage_cgats_maps_to_422(client):
    resp = client.post(
        "/api/icc/verify",
        data={"file": (io.BytesIO(b"totally not cgats"), "bad.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 422
    assert resp.get_json()["ok"] is False


def test_verify_missing_file(client):
    resp = client.post("/api/icc/verify", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "MISSING_FILE"


def test_verify_rejects_unknown_reference(client):
    resp = client.post(
        "/api/icc/verify",
        data={"file": (io.BytesIO(_cgats_from_reference(0.4)), "meas.txt"), "reference": "NoSuch"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "NO_REFERENCE"
