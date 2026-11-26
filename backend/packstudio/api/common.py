"""API 公共设施：统一响应、错误类型、上传/输出文件管理。"""

from __future__ import annotations

import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from flask import jsonify


class ApiError(Exception):
    """带稳定机器码的 API 层错误（HTTP 语义由 status 承载）。"""

    def __init__(self, code: str, message: str, *, status: int = 400, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}


def ok(data):
    return jsonify({"ok": True, "data": data})


def fail(code: str, message: str, *, status: int = 400, detail: dict | None = None):
    return (
        jsonify({"ok": False, "error": {"code": code, "message": message, "detail": detail or {}}}),
        status,
    )


_ID_PATTERN = re.compile(r"^[a-z0-9]{8,32}$")
_UPLOAD_DIR = Path(tempfile.gettempdir()) / "packstudio_uploads"
_OUTPUT_DIR = Path(tempfile.gettempdir()) / "packstudio_outputs"
#: 孤儿上传与导出生成物的清理阈值（6 小时）
_UPLOAD_TTL_SECONDS = 6 * 3600
_OUTPUT_TTL_SECONDS = 6 * 3600
#: 输出目录清理节流间隔（避免高频写入重复遍历目录）
_SWEEP_INTERVAL_SECONDS = 300.0
_last_output_sweep: float = 0.0

#: 图像转换接口允许的输出扩展名白名单
ALLOWED_IMAGE_OUT_FORMATS: frozenset[str] = frozenset({"tif", "tiff", "png", "jpg", "jpeg"})


def validate_out_format(raw: str | None, default: str = "tif") -> str:
    """校验并返回规范化的图像输出格式（小写，无前导点）。"""
    fmt = (raw or default).strip().lower().lstrip(".")
    if fmt not in ALLOWED_IMAGE_OUT_FORMATS:
        raise ApiError(
            "INVALID_FORMAT",
            f"不支持的图像输出格式：{raw}",
            detail={"supported": sorted(ALLOWED_IMAGE_OUT_FORMATS)},
        )
    return fmt


def _new_id() -> str:
    return secrets.token_hex(8)  # 16 个小写 hex 字符


def save_upload(file_storage, suffix: str = "") -> Path:
    """把上传文件落盘到 uploads/<id><suffix>，返回路径。"""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_uploads()
    path = _UPLOAD_DIR / f"{_new_id()}{_safe_suffix(suffix)}"
    file_storage.save(path)
    return path


def save_bytes(data: bytes, suffix: str = "") -> Path:
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_uploads()
    path = _UPLOAD_DIR / f"{_new_id()}{_safe_suffix(suffix)}"
    path.write_bytes(data)
    return path


def store_output(name: str, data: bytes) -> str:
    """生成物落盘 outputs/<id>/<name>，返回下载 id。"""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_outputs()
    output_id = _new_id()
    target = _output_dir_for(output_id, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return output_id


def output_path(output_id: str, name: str) -> Path:
    """按 id + 名字解析输出路径；非法 id / 名字一律拒绝（防穿越）。"""
    if not _ID_PATTERN.match(output_id or ""):
        raise ApiError("INVALID_DOWNLOAD_ID", "非法的下载 id。", status=404)
    return _output_dir_for(output_id, name)


def _output_dir_for(output_id: str, name: str) -> Path:
    directory = _OUTPUT_DIR / output_id
    # 名字只允许安全文件名（禁分隔符与 ..）
    safe = Path(name).name
    if safe != name or name in ("", ".", ".."):
        raise ApiError("INVALID_DOWNLOAD_NAME", "非法的下载文件名。", status=404)
    return directory / safe


def _safe_suffix(suffix: str) -> str:
    if not suffix:
        return ""
    safe = Path(suffix).suffix or Path(suffix).name
    return safe if re.match(r"^\.[A-Za-z0-9]{1,8}$", safe) else ""


def _sweep_uploads() -> None:
    """尽力清理过期孤儿上传（不抛错）。"""
    now = time.time()
    try:
        for p in _UPLOAD_DIR.iterdir():
            try:
                if p.is_file() and now - p.stat().st_mtime > _UPLOAD_TTL_SECONDS:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _sweep_outputs(*, force: bool = False, ttl_seconds: float | None = None) -> int:
    """尽力清理过期的生成物目录（不抛错），返回本次删除的目录数。"""
    global _last_output_sweep
    now = time.time()
    if not force and now - _last_output_sweep < _SWEEP_INTERVAL_SECONDS:
        return 0
    _last_output_sweep = now
    ttl = _OUTPUT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    removed = 0
    try:
        if not _OUTPUT_DIR.is_dir():
            return 0
        for entry in _OUTPUT_DIR.iterdir():
            try:
                if now - entry.stat().st_mtime > ttl:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed
