# PackStudio — 包装印前工作台

把 **ICC工具集**（色彩引擎）与 **aiskill-packaging**（刀版引擎）合并升级成的统一
「包装印前工作台」：PS 式三区界面 + 分区块功能面板 + 浅色极简纯白设计。

![流程](docs/DESIGN.md)

## 核心能力

| 分区块 | 能力 |
|---|---|
| ① 盒型与参数 | 18 类参数化盒型（直线/锁底/飞机盒/纸箱/屋顶盒…）确定性生成刀版 |
| ② 图层 | PDF 逐页/图片导入为位图图层，移动/吸附/不透明度/锁定/排序 |
| ③ 尺寸与校验 | 展开尺寸（引擎几何真值）、面板清单、14 项机械校验（含川崎/前泽折叠判定） |
| ④ 色彩管理 ICC | 系统 profile 扫描、输出指定、取色 Lab 换算、PDF 印前审计（OutputIntent/专色） |
| ⑤ 打样与色域 | 画布快照软打样模拟（sRGB→设备→sRGB 往返）、ΔE 越界掩图与统计 |
| ⑥ 导出 | 刀版 SVG / DXF / PDF / 专色分色 PDF / CF2 + 画布展平 PNG（按 DPI） |
| 3D 折叠预览 | 修好山谷推断后的折叠动画（Three.js 本地化，离线可用） |

## 快速开始

```bat
scripts\启动工作台.bat     :: 一键：建环境(首次) + 构建前端(首次) + 启动 + 开浏览器
```

手动方式：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
.venv\Scripts\python.exe -m packstudio.app     :: http://127.0.0.1:8001
```

开发模式（前端热更）：

```bat
.venv\Scripts\python.exe -m packstudio.app    :: 后端 :8001
cd frontend && npm run dev                    :: Vite :5173，/api 自动代理
```

## 架构

```
PackStudio/
├── backend/packstudio/
│   ├── app.py              # Flask 工厂：{ok,data|error} 协议、稳定错误码、127.0.0.1
│   ├── api/                # boxes / dieline / icc / pdf / export 五蓝图
│   └── engines/
│       ├── packaging_engine/   # 刀版引擎（自 aiskill-packaging 迁移+修复）
│       └── color_engine/       # ICC 引擎（自 ICC工具集 迁移，裁剪 webui/cli）
├── frontend/               # Vite + React 18 + TS + Zustand（零 UI 库，自写设计系统）
│   ├── src/editor/         # 与框架解耦的画布引擎：视口/渲染/交互/SVG解析/PDF导入
│   ├── src/panels/         # 六大分区块
│   └── src/store/          # 文档/视图/历史状态
├── docs/DESIGN.md          # 设计规范（纯白平面 token）
└── scripts/启动工作台.bat
```

- 世界坐标毫米（mm），刀版 SVG 由后端输出 **y-down**（SVG 媒介原生方向），PDF/DXF 保持 y-up。
- 渲染：切线红实线 / 压线青虚线 / 糊线蓝点线（行业惯例），见 `docs/DESIGN.md`。
- 安全：仅绑定 127.0.0.1；上传 64MB 上限；下载按 id+文件名白名单防穿越；SVG 解析拒绝 DOCTYPE/ENTITY。

## 引擎修复记录（相对旧项目的 bug 修复）

| 缺陷 | 修复 |
|---|---|
| RTL 底耳圆角弧扫 270° 挖穿 12 类盒型 | `engine/base.py` 改 `0, -90`，镜像+包围盒回归测试 |
| PDF/专色 PDF 导出上下颠倒 | 保留 y-up（PDF 原生方向），出血改 even-odd 环形区 |
| SVG 显示上下镜像 | 导出统一翻转为 y-down |
| 三套矛盾展开尺寸公式 | 校验器/BOM 统一为引擎几何真值（±1mm 紧公差） |
| 出血/安全区恒 True 空壳校验 | 实现真实几何检查 + 明示跳过语义 |
| 粘口折线两层重合（全部盒型） | render 统一 GLUE 层输出 GLUE_FOLD |
| crash_lock 缺背支撑板（既切又折） | 补标准深度支撑板 |
| mailer 耳深错误挂 H | 改 crash-lock 同族公式 `(W+tuck+shrink)/2` |
| 山谷方向全判谷折 | 翻板/粘口标签→山折；GLUE 折线纳入折叠铰链 |

回归测试：`backend/tests/packaging/test_regression_packstudio.py`（62 项）。
全量：`pytest backend/tests`（683+ 通过）。

## 已知边界

- 屋顶盒（gable_top）屋面为近似展示级结构，生产前需印厂复核（输出协议即
  `DESIGN_TEMPLATE + REQUIRES_MANUFACTURER_REVIEW`）。
- 打样覆盖层是画布静态快照，修改素材后需重新打样。
- CMYK 图像导出仅 TIFF（PNG 不支持 CMYK，Pillow 限制）。
