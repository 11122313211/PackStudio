@echo off
rem PackStudio 启动脚本：后端 Flask :8001 + 前端产物已预构建
rem 开发模式请另开终端运行 frontend 下的 `npm run dev`（Vite :5173）
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo [PackStudio] 未找到虚拟环境，正在创建...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -e ".[dev]" --quiet
)
if not exist frontend\dist\index.html (
  echo [PackStudio] 前端未构建，正在构建...
  cd frontend && npm install && npm run build && cd ..
)
echo [PackStudio] 启动 http://127.0.0.1:8001
start "" http://127.0.0.1:8001
.venv\Scripts\python.exe -m packstudio.app
