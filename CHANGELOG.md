# 变更日志（Changelog）

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范与 [语义化版本](https://semver.org/lang/zh-CN/) 约定。

## [Unreleased]

### 新增（Added）
- **离屏高清渲染引擎**：新增 `renderOffscreen()` 与 `sampleArtworkPixel()`，软打样与 96/150/300 DPI 展平导出完全脱离屏幕视口裁切，支持勾选是否包含矢量刀版线。
- **CMYK 完整印前链路**：
  - 取色接口 `/api/icc/lab` 支持 CMYK / Gray 设备空间转换，同步返回设备通道值与总墨量 `TAC%`；
  - 图层转换 `/api/icc/convert` 针对 CMYK 自动写出无损 TIFF 并生成画布 sRGB 预览图，图层面板可直接下载真实 CMYK 产物。
- **色彩闭环过期联动（Stale）**：修改刀版、图层或色彩配置后自动将旧打样与旧测量报告标为「已过期」，避免误放行出片。
- **画布与工作台交互增强**：支持画布直接拖放导入 PDF/图片、导入图层居中落位、旋转图层命中检测与同步选框、批量导入合并单次撤销、顶栏撤销/重做禁用态、状态栏断线自动重连、系统 ICC 强制刷新（`?refresh=1`）。
- **后端安全与生命周期治理**：临时生成物 6 小时 TTL 自动清理（`_sweep_outputs`）、ICC 路径系统目录与后缀白名单校验、本机 `Origin` CSRF 防护。
- **工程质量与开源维护体系**：前端引入 Vitest 核心单元测试与 `typecheck` 脚本；补充后端回归测试至 **704 项**；新增 GitHub Actions CI、MIT `LICENSE`、`.editorconfig`、`CONTRIBUTING.md` 及 Issue/PR 模板。

### 修复（Fixed）
- 修复前端 4 项刀版工艺参数（插耳深/糊口宽/缩收量/出血）因嵌套在 `parameters_mm` 导致生成、3D 折叠预览与导出时静默失效的问题（前后端双向修复并兼容旧载荷）。
- 修复 `/api/icc/convert` 因小写操作名与大写枚举键比对导致全部 ICC 操作报 400 `INVALID_OP` 的潜伏缺陷，以及无嵌入 ICC 的 RGB 图层转换报 `NO_SOURCE_PROFILE` 的问题。
- 修复 Windows 下生成 TIFF 预览导致临时文件删除触发 `WinError 32` 的文件句柄锁问题。
- 修复 `CanvasStage` 渲染循环缺少依赖数组与每帧双绘 `cleanCanvas` 的性能浪费，及切换 `mm/pt` 单位时标尺角标不同步的问题。
- 修复替换打样 `ImageBitmap` 时未调用 `.close()` 导致的 GPU 显存泄漏，以及 `ColorStage` 中残留的 emoji 图标规范违规。
- 升级 `scripts/启动工作台.bat` 增加虚拟环境健康自检、目录迁移自动重建与系统 Python 智能回退。

### 变更（Changed）
- 软打样越界掩图与统计默认口径从 `ΔE76` 升级为 ISO 标准 `ΔE00`（CIEDE2000，与 ④ 测量验证对齐），保留 `metric=de76` 兼容切换，并在默认相对比色意图下消除 1/3 冗余 CMM 变换耗时。

---

## [0.1.0] - 2026-11-15

### 新增（Added）
- 初始版本发布：融合参数化刀版引擎（18 类盒型 + 14 项机械校验 + 离线 3D 折叠 + 五格式导出）与 ICC 色彩引擎（档案扫描/体检 + 软打样 + PDF 审计 + CGATS ΔE00 验证）。
- 实现五阶段色彩闭环工作流与 iOS 扁平磨砂玻璃深浅双主题 UI。
