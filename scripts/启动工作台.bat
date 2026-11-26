@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
rem PackStudio 启动脚本：环境健康自检 + 前端按需构建 + 后端 Flask :8001
rem 用法：
rem   scripts\启动工作台.bat           常规启动
rem   scripts\启动工作台.bat --build   强制重新构建前端后启动
rem 开发模式请另开终端在 frontend 目录运行 `npm run dev`（Vite :5173）

cd /d "%~dp0.."

set "FORCE_BUILD=0"
if /I "%~1"=="--build" set "FORCE_BUILD=1"

rem ---- 1. 选择并校验 Python 运行环境 ----
set "PY_CMD="

if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -c "import packstudio, PIL._imaging" >nul 2>&1
  if !errorlevel! equ 0 (
    set "PY_CMD=.venv\Scripts\python.exe"
  ) else (
    echo [PackStudio] 检测到 .venv 已失效（常见于项目目录迁移），正在尝试重建...
    rmdir /s /q .venv >nul 2>&1
  )
)

if not defined PY_CMD (
  rem 优先尝试以 Python 3.12 创建 .venv，失败则回退默认 python
  where py >nul 2>&1
  if !errorlevel! equ 0 (
    py -3.12 -m venv .venv >nul 2>&1
  )
  if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv >nul 2>&1
  )
  if exist ".venv\Scripts\python.exe" (
    echo [PackStudio] 正在向 .venv 安装项目依赖...
    .venv\Scripts\python.exe -m pip install -e ".[dev]" --quiet >nul 2>&1
    .venv\Scripts\python.exe -c "import packstudio, PIL._imaging" >nul 2>&1
    if !errorlevel! equ 0 (
      set "PY_CMD=.venv\Scripts\python.exe"
    )
  )
)

if not defined PY_CMD (
  rem 回退至已装好依赖的系统 Python
  python -c "import packstudio, PIL._imaging" >nul 2>&1
  if !errorlevel! equ 0 (
    echo [PackStudio] .venv 不可用，自动回退至系统 Python 运行。
    set "PY_CMD=python"
  ) else (
    echo [PackStudio] 错误：未找到可用的 Python 运行环境（需 Python ^>=3.12 且安装 pip install -e ".[dev]"）。
    pause
    exit /b 1
  )
)

rem ---- 2. 检查/构建前端产物 ----
if "%FORCE_BUILD%"=="1" (
  echo [PackStudio] 正在重新构建前端...
  pushd frontend
  call npm run build
  set "BUILD_ERR=!errorlevel!"
  popd
  if not "!BUILD_ERR!"=="0" (
    echo [PackStudio] 前端构建失败，请检查 Node/npm 输出。
    pause
    exit /b !BUILD_ERR!
  )
) else if not exist "frontend\dist\index.html" (
  echo [PackStudio] 前端未构建，正在安装依赖并构建...
  pushd frontend
  call npm install && call npm run build
  set "BUILD_ERR=!errorlevel!"
  popd
  if not "!BUILD_ERR!"=="0" (
    echo [PackStudio] 前端构建失败，请检查 Node/npm 输出。
    pause
    exit /b !BUILD_ERR!
  )
)

rem ---- 3. 检查 8001 端口占用并启动服务 ----
netstat -ano | findstr /R /C:":8001 .*LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
  echo [PackStudio] 端口 8001 已在运行，直接打开工作台 http://127.0.0.1:8001
  start "" http://127.0.0.1:8001
  exit /b 0
)

echo [PackStudio] 使用 !PY_CMD! 启动 http://127.0.0.1:8001
start "" http://127.0.0.1:8001
!PY_CMD! -m packstudio.app
endlocal
