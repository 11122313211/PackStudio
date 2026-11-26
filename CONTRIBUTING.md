# 参与贡献（Contributing）

感谢你关注并参与 **PackStudio** 的建设！以下是本地开发、代码规范与提交 PR 的标准流程。

## 1. 开发环境准备

- **Python** `>= 3.12`
- **Node.js** `>= 20`（含 npm）

```bash
# 1. 创建并激活虚拟环境，安装后端及测试依赖
python -m venv .venv
# Windows:
.venv\Scripts\python.exe -m pip install -e ".[dev]"
# macOS / Linux:
.venv/bin/python -m pip install -e ".[dev]"

# 2. 安装前端依赖
cd frontend && npm install
```

开发模式启动（前后端热更新）：

```bash
# 终端 1：启动后端 API（127.0.0.1:8001）
python -m packstudio.app

# 终端 2：启动前端 Vite（5173，/api 自动代理到 :8001）
cd frontend && npm run dev
```

## 2. 设计与工程约定

- **前端设计规范**：请先阅读 [docs/DESIGN.md](docs/DESIGN.md)，严格遵循 iOS 扁平磨砂玻璃双主题、画布纸面恒白、刀线行业色固定与**禁止 emoji 图标**等约定。
- **后端协议约定**：API 统一返回 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": {"code": "...", "message": "...", "detail": {...}}}`；保持后端无状态，仅监听 `127.0.0.1`。
- **代码格式**：遵循根目录 [.editorconfig](.editorconfig) 配置（UTF-8、Python 4 空格、TS/CSS/YAML 2 空格）。

## 3. 提交前质量检查

提交 Pull Request 前请确保以下命令全部通过：

```bash
# 后端全量测试
pytest backend/tests -q

# 前端类型检查、单元测试与生产构建
cd frontend
npm run typecheck
npm test
npm run build
```

## 4. Commit 与 PR 规范

推荐使用语义化提交前缀：

- `feat:` 新功能（如新增盒型、色彩工具）
- `fix:` Bug 修复
- `refactor:` 重构（不改变对外行为）
- `perf:` 性能优化
- `test:` 测试补充与修复
- `docs:` 文档与 README 更新
- `chore:` 构建、CI、依赖与仓库维护
