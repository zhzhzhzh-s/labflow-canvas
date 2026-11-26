@echo off
chcp 65001 >nul
echo ================= 实验画布 · 环境诊断 =================
echo.
echo [1] Python
where python 2>nul
if not errorlevel 1 python --version
where py 2>nul
if not errorlevel 1 py -3 --version
echo.
echo [2] 系统
ver
echo.
echo [3] 本机 IP
ipconfig | findstr /i "IPv4"
echo.
echo [4] 43300 端口占用
netstat -ano | findstr ":43300"
echo.
echo [5] 当前目录文件
dir /b
echo.
echo =====================================================
echo 把以上全部内容复制给 AI，它就能判断问题在哪。
pause
