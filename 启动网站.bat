@echo off
chcp 65001 >nul
cd /d "%~dp0"
title REIT-AI 申报材料生成系统

echo ============================================================
echo   REIT-AI 申报材料生成系统 - 启动器
echo ============================================================
echo.

REM ---- 1. 找 Python（优先 3.12）----
set "PYCMD="
py -3.12 --version >nul 2>nul && set "PYCMD=py -3.12"
if not defined PYCMD ( py -3 --version >nul 2>nul && set "PYCMD=py -3" )
if not defined PYCMD ( python --version >nul 2>nul && set "PYCMD=python" )
if not defined PYCMD (
  echo [错误] 没有检测到 Python。
  echo        请先到 https://www.python.org/downloads/ 下载安装 Python 3.12，
  echo        安装时务必勾选 "Add python.exe to PATH"，装完后再双击本文件。
  echo.
  pause
  exit /b 1
)
echo 使用 Python: %PYCMD%
echo.

REM ---- 2. 首次运行：创建独立运行环境（虚拟环境）----
if not exist ".venv\Scripts\python.exe" (
  echo 首次运行，正在创建独立运行环境（.venv）……
  %PYCMD% -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建运行环境失败，请确认 Python 安装正常。
    pause
    exit /b 1
  )
)
call ".venv\Scripts\activate.bat"

REM ---- 3. 安装依赖（默认走清华国内镜像，国内快且无需翻墙）----
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
echo 正在检查/安装依赖（国内镜像加速，首次需要几分钟，请耐心等待）……
python -m pip install --upgrade pip -i %PIP_MIRROR% >nul 2>nul
python -m pip install -r requirements.txt -i %PIP_MIRROR%
if errorlevel 1 (
  echo 国内镜像安装失败，改用官方源重试……
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)
echo.

REM ---- 4. 启动服务 + 打开浏览器 ----
echo 启动中……几秒后浏览器会自动打开 http://127.0.0.1:8000
echo 关闭本窗口即可停止网站。
echo.
start "" http://127.0.0.1:8000
python run_server.py

pause
