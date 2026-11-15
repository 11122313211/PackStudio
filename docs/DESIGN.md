# PackStudio 设计规范（色彩闭环工作流 · iOS 扁平）

交互原型：Photoshop 式三区工作台——左工具列 / 中画布（标尺+参考线）/ 右工作流面板。
信息架构：**五阶段色彩闭环工作流**（软门控 + 渐进式披露），替代旧的六区块平铺导航。
视觉语言：**iOS 扁平**——系统蓝、无边框填充控件、分组卡片、大圆角、SF 风格线性图标；
框架为磨砂玻璃悬浮（即 iOS 材质），画布全屏铺底。

## 一、五阶段工作流（信息架构）

| # | 阶段 | 内容 | 退出条件（done） | issue 条件 |
|---|------|------|------------------|------------|
| ① | 盒型结构 | 19 盒型选型 + 参数 → 刀版生成 + 14 项机械校验 + 3D 折叠 | `dieline && validation.passed` | 校验未通过 |
| ② | 素材与审计 | 导入 PDF/图片（**PDF 自动色彩审计**）→ 图层管理 → 逐图层 ICC 赋档/转换 | 有素材图层 | 审计有警告 |
| ③ | 色彩管理 | ICC 档案库（搜索/筛选/体检）+ 输出档案指定 + 软打样配置 + 取色 Lab | 已选输出档案 | — |
| ④ | 打样验证 | 软打样 → 越界统计 → **上传 CGATS → ΔE00 对标 Fogra39 判定** | 打样已跑且（无测量或 verify 通过） | verify 未通过 |
| ⑤ | 输出交付 | 刀版五格式 + 展平 PNG + 色彩闭环报告汇总 | 已导出 ≥1 次 | — |

**软门控规则**（`frontend/src/flow/stages.ts::deriveStageViews`，纯函数推导）：

- 五阶段全部可见；前置未满足的阶段状态点为灰，点入后面板顶部显示「前置未满足 — 原因」，主体按钮禁用；
- 前置：②③⑤ 需刀版（③配置型永远可用，⑤导出需刀版）；④ 需素材 + 输出档案；
- 已完成阶段随时可回跳修改；④ 失败时横幅给出「回 ② 修正素材 / 回 ③ 换档案或意图」行动按钮（**测量→反馈修正** 即闭环）；
- 状态点语义：灰=锁定 · 蓝=就绪 · 绿=完成 · 橙=异常；状态栏有「色彩闭环」胶囊（未开始/已模拟未测量/闭环通过/需修正）。

代码映射：阶段定义与门控 `flow/stages.ts`；步进器 `flow/FlowTracker.tsx`；
阶段容器 `panels/RightPanel.tsx`；阶段组件 `stages/*.tsx`。

## 二、视觉系统（iOS 扁平）

### 硬规则

1. **框架为磨砂玻璃**：顶栏/工具列/右面板/状态栏/标尺 = 半透明底
   `backdrop-filter: blur(28px) saturate(180%)`，朝画布边缘 1px 亮缘描边；
   **画布全屏铺底，框架悬浮其上**——禁止改回不透明并列布局（玻璃无内容可模糊）。
2. **控件无边框填充式**（iOS）：控件底 `--fill`（8px 圆角、无边框），hover 加深；
   主按钮 = 强调色胶囊；布尔量 = iOS 开关（checkbox 变体）；分隔 = 发丝线。
3. 圆角三档：卡片 12px / 控件 8px / 胶囊 999px。
4. 字号四档：11 / 12 / 14 / 15（`--fs-1..4`）；阶段标题 15 加粗。
5. 强调色 = iOS 系统蓝（深色 `#0A84FF` / 浅色 `#007AFF`）；语义色 = iOS 系统
   绿/橙/红（`--ok/--warn/--bad`）。刀线行业色**固定不随主题**：
   切线红 `#e5484d` 实线、压线青 `#12a594` 虚线、糊线蓝点线。
6. 图标 = SF 风格内联 SVG（1.8px 圆帽描边，`components/icons.tsx`），**禁止 emoji 字形**。
7. 动效：交互态 `160ms ease-out` 过渡；`prefers-reduced-motion` 时全部归零。

### 双主题 token（`styles/tokens.css`）

- 机制：`<html data-theme="dark|light">`，深色为默认；`main.tsx` 首帧前写入，
  localStorage（`packstudio.theme`）持久化；顶栏太阳/月亮图标切换。
- 深色：底 `#000/#1C1C1E` 系、玻璃 `rgba(18,18,20,.68)`、文字 `#F5F5F7` + iOS 三级透明白；
  浅色：底 `#F2F2F7` 系、玻璃 `rgba(250,250,252,.66)`、文字近黑 + iOS 三级透明黑。
- 完整 token 表见 `tokens.css` `:root` 与 `:root[data-theme="light"]`（表面/文字/强调/
  语义/玻璃/结构六组）。
- **画布侧调色板在 `editor/theme.ts`**：井底（dark `#1b1b1d` / light `#ececf1`）、
  纸张描边、纸面网格、标尺刻度/文字、度量气泡、参考线强调色。
  **与 CSS `--well` 必须同步修改**。

### 画布主题化规则

- **纸面恒白**（`#ffffff`，它是印刷品本身），刀线行业色固定（行业规范）；
- 井底/标尺/度量提示/参考线随主题（`renderer.ts` 接收 `theme` 参数取 `getPalette()`）；
- 网格**只画在纸张盒内**（`drawGrid` 按内容盒 clip）——深色井底不出现浅色网格；
- 布局尺寸 token（`--topbar-h:48 / --toolbar-w:48 / --rightbar-w:312 /
  --statusbar-h:26 / --ruler-h:24`）与 `CanvasStage.tsx` 的 `SHELL_INSET`
  `{left:48, right:312, top:48+24, bottom:26}` **必须同步修改**。

## 三、色彩闭环 API 契约

| 环节 | 接口 | 说明 |
|---|---|---|
| ② 审计 | `POST /api/pdf/audit` | OutputIntent/内嵌档案/专色/各页色彩空间清单 |
| ② 赋档 | `POST /api/icc/convert` | assign/embed/convert，结果落下载文件回填新图层 |
| ③ 体检 | `GET/POST /api/icc/validate` | 19 条结构规则 + 必需 tag 表（`icc/validate.py`） |
| ④ 打样 | `POST /api/icc/proof` | 往返软打样 + OOG 掩图 + ΔE76 统计 |
| ④ 测量 | `POST /api/icc/verify` | CGATS.17 解析 → ΔE00 + ISO 12647-2/Fogra39 判定 → PASS/WARNING/FAIL（`measurement/verify.py`） |
| ⑤ 交付 | `POST /api/export` | svg/dxf/pdf/pdf_spot/cf2 |

后端保持**无状态**；工作流状态（当前阶段/审计/验证报告/主题）由前端 Zustand 持有。

## 四、布局

```
┌──────────────────────────────────────────────┐
│ 顶栏 48px 玻璃：品牌 · 文档名 · 导入 · 撤销重做 · 缩放 · 适配 · 主题 │
├──┬────────────────────────────────────────┬──┤
│工│ 24px 玻璃标尺                           │①②③│
│具│ ┌──────────────────────────┐           │④⑤ │
│列│ │ 画布（全屏铺底，可滑到玻璃下方） │           │步进│
│48│ └──────────────────────────┘           │器+ │
│px│                                        │阶段│
│  │                                        │312px│
├──┴────────────────────────────────────────┴──┤
│ 状态栏 26px 玻璃：后端 · 光标 · 单位 · 缩放 · 校验 · 闭环胶囊 │
└──────────────────────────────────────────────┘
```

- 右面板结构：**粘性步进器（5 段）→ 阶段标题行（序号徽标 + 标题 + 摘要）→
  锁定提示（软门控）→ 阶段内容**；一次只渲染当前阶段（渐进式披露）。
- 画布坐标：世界单位毫米（mm），y-down（与 SVG 导出一致）；mm/pt 状态栏切换。
- 快捷键：V 选择 · H 平移 · M 度量 · I 取色 · G 网格 · 空格临时平移 ·
  Ctrl+Z/Shift+Z 撤销重做 · Ctrl+0 适配 · Delete 删除选中图层 ·
  标尺拖出参考线 · 右键删除附近参考线。

## 五、代码映射

- token 与组件样式：`frontend/src/styles/tokens.css`
- 画布调色板：`frontend/src/editor/theme.ts`（与 CSS `--well` 同步）
- 刀线颜色（画布渲染）：`frontend/src/editor/renderer.ts` `LAYER_COLORS`（固定）
- 阶段定义/门控：`frontend/src/flow/stages.ts`；步进器：`frontend/src/flow/FlowTracker.tsx`
- 阶段组件：`frontend/src/stages/*.tsx`；阶段容器：`frontend/src/panels/RightPanel.tsx`
- 图标：`frontend/src/components/icons.tsx`
- 后端闭环路由：`backend/packstudio/api/icc.py`（validate / verify）
