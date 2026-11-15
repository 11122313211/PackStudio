"""PackStudio REST API 蓝图包。

约定（沿用 ICC 工具集的成熟协议）：
- 响应统一 ``{"ok": true, "data": ...}`` / ``{"ok": false, "error": {"code", "message", "detail"}}``；
- 错误码原样输出两引擎的稳定机器码；
- 上传/输出文件放系统临时目录，按 id 白名单下载（防路径穿越）。
"""

from __future__ import annotations

from packstudio.api.common import ApiError, ok

__all__ = ["ApiError", "ok"]
