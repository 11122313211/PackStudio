"""ICC 色彩管理工具集（TECH-DESIGN §2.5 顶层导出面）。

包结构::

    packstudio.engines.color_engine/
      errors.py        公共异常层级（稳定机器码 code / detail）
      icc/             纯 struct 二进制解析（零第三方依赖）
      cmm/             ImageCms 封装引擎与 profile 扫描
      imaging/         图像 ICC 处理流水线
      measurement/     CGATS 解析与 ΔE00 测量验证
      pdfaudit/        PDF 色彩审计
      viz/             色域采样与可视化
      cli.py           icct 命令行入口
      webui/           Flask 本机 Web UI（127.0.0.1）
      doctor.py        环境自诊断引擎（CLI icct doctor 与 WebUI /api/doctor 共用）
"""
from __future__ import annotations

__version__ = "0.3.30"

from packstudio.engines.color_engine.errors import IccToolkitError  # noqa: E402

__all__ = ["__version__", "IccToolkitError"]
