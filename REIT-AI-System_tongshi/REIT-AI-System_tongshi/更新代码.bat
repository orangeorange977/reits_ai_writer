@echo off
chcp 65001 >nul
title 从本机同步代码到服务器（只更新代码，不动数据）
setlocal

REM ============================================================
REM  用法：在【服务器】上，通过“远程桌面(mstsc)且映射了本机磁盘”连接时，
REM        双击本文件，即可把你本机改好的【代码】同步到服务器。
REM        它只更新代码，绝不覆盖服务器上大家生成的数据（摘要表/章节/账号等）。
REM
REM  按需修改下面两行：
REM   SRC = 你本机 AI test 的位置（\\tsclient\盘符\...，盘符=本机 AI test 所在盘）
REM   DST = 服务器上 AI test 的位置
REM ============================================================
set "SRC=\\tsclient\D\AI test"
set "DST=C:\AI test"

echo 源  (你本机)  ：%SRC%
echo 目标(服务器)  ：%DST%
echo.
echo 正在同步代码（自动跳过 drawio 组件、图片库，以及所有数据文件）...
echo.

robocopy "%SRC%" "%DST%" /E /R:1 /W:1 ^
 /XD .git .venv __pycache__ .vscode vendor cover_assets 图片库 data output ^
 /XF summary_saved.json write_config.json accounts.json .app_secret cover_saved.json model_setting.json project_meta.json .deps_installed ch1.json ch2.json ch3.json ch4.json ch5.json ch6.json ch7.json

echo.
echo ============================================================
echo  同步完成。
echo   - 只改了前端(.js/.css/.html) 或 skill(.md)：让同事按 Ctrl+F5 刷新即可；
echo   - 改了后端 .py：请关掉 run.bat 那个黑窗口，再重新双击 run.bat（重启后端）。
echo ============================================================
echo.
pause
