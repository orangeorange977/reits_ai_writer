@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title REIT-AI 法律文件生成系统 v1.0
cd /d "%~dp0"

echo.
echo ═══════════════════════════════════════════════
echo     REIT-AI 法律文件生成系统 v1.0
echo ═══════════════════════════════════════════════
echo.

:: ============================================
:: [1/3] 检测Python环境
:: ============================================
echo [1/3] 检测Python环境...

set PYTHON_CMD=
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=python
    goto :python_found
)

python3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=python3
    goto :python_found
)

echo.
echo ╔═══════════════════════════════════════════╗
echo ║  [错误] 未检测到Python环境！             ║
echo ╠═══════════════════════════════════════════╣
echo ║  请安装 Python 3.10 或更高版本           ║
echo ║  下载地址:                               ║
echo ║  https://www.python.org/downloads/       ║
echo ║                                          ║
echo ║  安装时请勾选 "Add Python to PATH"       ║
echo ╚═══════════════════════════════════════════╝
echo.
pause
exit /b 1

:python_found
for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do set PYTHON_VER=%%v
echo        已找到 %PYTHON_VER%
echo.

:: ============================================
:: [2/3] 检查并安装依赖（以“能否真正 import”为准，不依赖标记文件，
::       这样即使从别人电脑拷来带着旧标记也不会误判为已装好）
:: ============================================
echo [2/3] 检查依赖...

set "DEP_CHECK=import fastapi,uvicorn,docx,fitz,openpyxl,openai,dotenv,lxml,multipart,aiosqlite"
%PYTHON_CMD% -c "%DEP_CHECK%" >nul 2>&1
if %errorlevel% equ 0 (
    echo        依赖已就绪
    echo.
    goto :start_service
)

echo        检测到缺少依赖，正在安装，请稍候（首次需要几分钟，黑窗口里有进度）...
echo.
set "PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
%PYTHON_CMD% -m pip install --upgrade pip -i %PIP_MIRROR% >nul 2>&1
echo        使用国内镜像安装（清华源，快且无需翻墙）...
%PYTHON_CMD% -m pip install -r requirements.txt -i %PIP_MIRROR%
if %errorlevel% neq 0 (
    echo.
    echo        国内镜像安装失败，改用官方源重试...
    %PYTHON_CMD% -m pip install -r requirements.txt
)

:: 装完再核对一次，确认真的能 import（网络中断/版本不对时能明确报错，而不是启动才崩）
%PYTHON_CMD% -c "%DEP_CHECK%" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ╔═══════════════════════════════════════════╗
    echo ║  [错误] 依赖安装失败！                   ║
    echo ╠═══════════════════════════════════════════╣
    echo ║  可能的原因：                            ║
    echo ║  1. 网络不可用（安装依赖需要联网）       ║
    echo ║  2. Python 不是 3.12.x（PyMuPDF 等装不上）║
    echo ║  3. 权限不足，请右键“以管理员身份运行”   ║
    echo ║                                          ║
    echo ║  可手动执行（清华镜像）：                ║
    echo ║  python -m pip install -r requirements.txt ║
    echo ║   -i https://pypi.tuna.tsinghua.edu.cn/simple ║
    echo ╚═══════════════════════════════════════════╝
    echo.
    pause
    exit /b 1
)

echo.
echo        依赖安装完成！
echo.

:: ============================================
:: [3/3] 启动服务
:: ============================================
:start_service
echo [3/3] 启动服务...
echo.
echo ───────────────────────────────────────────────
echo   服务地址: http://127.0.0.1:8000
echo   API文档:  http://127.0.0.1:8000/docs
echo.
echo   浏览器将自动打开，请稍候...
echo   按 Ctrl+C 可停止服务
echo ───────────────────────────────────────────────
echo.

:: 延迟2秒后自动打开浏览器（后台执行）
start /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"

:: 启动服务（通过 run_server.py；监听地址由环境变量 REIT_HOST 控制：
::   本地不设 → 只听 127.0.0.1；服务器上设 REIT_HOST=0.0.0.0 → 对外开放）
%PYTHON_CMD% run_server.py

echo.
echo ═══════════════════════════════════════════════
echo   服务已停止
echo ═══════════════════════════════════════════════
echo.
pause
endlocal
