"""API 公共设施：统一响应、错误类型、上传/输出文件管理。"""

from __future__ import annotations

import re
import secrets
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
#: 孤儿上传清理阈值
_UPLOAD_TTL_SECONDS = 6 * 3600


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
