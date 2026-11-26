"""PackStudio Flask 应用工厂。

约定（沿用 ICC 工具集成熟方案）：
- 只绑 127.0.0.1（host 不对外暴露）；Flask 开发服务器，非生产用途；
- 响应统一 ``{"ok": true, "data": ...}`` / ``{"ok": false, "error": {...}}``；
- 两引擎的稳定错误码（IccToolkitError / PackagingError）原样透出；
- 开发期前端由 Vite dev server 代理 ``/api``；生产模式直接服务
  ``frontend/dist`` 静态产物。
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from packstudio import __version__
from packstudio.api.common import ApiError, fail

logger = logging.getLogger("packstudio.app")

_MAX_CONTENT_LENGTH = 64 * 1024 * 1024  # 64 MB

#: 两引擎错误基类 → 统一 422（稳定机器码透出）
_ENGINE_ERRORS: tuple[type[Exception], ...] = ()
try:  # 引擎错误基类逐个纳入，缺失不致命
    from packstudio.engines.color_engine.errors import IccToolkitError

    _ENGINE_ERRORS += (IccToolkitError,)
except ImportError:  # pragma: no cover
    pass
try:
    from packstudio.engines.packaging_engine.exceptions import PackagingError

    _ENGINE_ERRORS += (PackagingError,)
except ImportError:  # pragma: no cover
    pass


_LOCAL_ORIGIN_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = _MAX_CONTENT_LENGTH

    @app.before_request
    def _check_local_origin():
        """本机 CSRF 防护：变更型请求若携带 Origin，仅放行本机来源。"""
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        origin = request.headers.get("Origin")
        if not origin:
            return None
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in _LOCAL_ORIGIN_HOSTS:
            return fail("INVALID_ORIGIN", "禁止非本机来源的跨站写请求。", status=403)
        return None

    from packstudio.api import boxes, dieline, export, icc, pdf

    app.register_blueprint(boxes.bp)
    app.register_blueprint(dieline.bp)
    app.register_blueprint(icc.bp)
    app.register_blueprint(pdf.bp)
    app.register_blueprint(export.bp)

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "data": {"app": "packstudio", "version": __version__}})

    @app.errorhandler(ApiError)
    def on_api_error(exc: ApiError):
        return fail(exc.code, exc.message, status=exc.status, detail=exc.detail)

    if _ENGINE_ERRORS:

        def on_engine_error(exc: Exception):
            code = getattr(exc, "code", type(exc).__name__)
            detail = getattr(exc, "detail", None)
            return fail(code, str(exc), status=422, detail=detail if isinstance(detail, dict) else None)

        for exc_class in _ENGINE_ERRORS:
            app.register_error_handler(exc_class, on_engine_error)

    @app.errorhandler(HTTPException)
    def on_http_error(exc: HTTPException):
        return fail("HTTP_" + str(exc.code), exc.description, status=exc.code or 500)

    @app.errorhandler(Exception)
    def on_unexpected(exc: Exception):
        logger.exception("未处理异常：%s", exc)
        return fail("INTERNAL", f"内部错误：{exc}", status=500)

    _register_frontend(app)
    return app


def _register_frontend(app: Flask) -> None:
    """生产模式：服务 frontend/dist；开发模式该目录不存在，走 Vite。"""
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @app.get("/")
    def index():
        if (dist / "index.html").is_file():
            return send_from_directory(dist, "index.html")
        return (
            jsonify(
                {
                    "ok": True,
                    "data": {
                        "app": "packstudio",
                        "hint": "前端未构建：开发期请运行 `npm run dev`（Vite 端口 5173，/api 已代理）。",
                    },
                }
            ),
            200,
        )

    @app.get("/<path:subpath>")
    def assets(subpath: str):
        if subpath.startswith("api/") or subpath == "api":
            return fail("NOT_FOUND", "接口不存在。", status=404)
        target = dist / subpath
        if target.is_file():
            return send_from_directory(dist, subpath)
        # SPA 回退
        if (dist / "index.html").is_file():
            return send_from_directory(dist, "index.html")
        return fail("FRONTEND_NOT_BUILT", "前端产物不存在（frontend/dist）。", status=404)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app()
    # 仅本机绑定是安全硬约束
    app.run(host="127.0.0.1", port=8001, debug=False)


if __name__ == "__main__":
    main()
