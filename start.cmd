@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not defined LABFLOW_PORT set "LABFLOW_PORT=43300"
set "LABFLOW_OPEN=1"

where python >nul 2>nul
if not errorlevel 1 (
  set "PY=python"
  goto checkpy
)
where py >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
  goto checkpy
)
goto nopython

:checkpy
%PY% --version >nul 2>nul
if errorlevel 1 goto nopython
goto run

:nopython
echo.
echo 没检测到可用的 Python，先给你打开「单机版」（不用安装，一个人用）。
if exist "直接打开-单机版.html" (
  start "" "直接打开-单机版.html"
) else (
  start "" "index.html"
)
echo.
echo 想多人共享的话：
echo   1. 打开 https://www.python.org/downloads/ 下载安装 Python
echo   2. 安装第一页勾选 "Add python.exe to PATH"
echo   3. 装完重新双击 start.cmd
echo.
pause
exit /b 0

:run
echo 实验画布共享服务启动中……
echo 本机：http://127.0.0.1:%LABFLOW_PORT%
echo 局域网：启动后日志里会打印本机 IP 地址
echo 关掉这个窗口就停止服务（浏览器会自动打开）。
%PY% server.py
pause
