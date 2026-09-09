@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 找不到 Python，请确认 WorkBuddy 内置 Python 路径
    pause
    exit /b 1
)

echo 正在启动 AI 询盘工作台...
echo 浏览器会自动打开 http://localhost:8501
echo 关闭本窗口即可停止工作台

:: 在后台启动 Streamlit（关闭本窗口也会停止服务）
start /B "" "%PY%" -m streamlit run app.py --server.headless true --server.port 8501

:: 等待 4 秒让服务启动完成
timeout /t 4 /nobreak >nul 2>nul
if %errorlevel% neq 0 (
    ping 127.0.0.1 -n 5 >nul
)

:: 自动打开默认浏览器
start "" "http://localhost:8501"

echo.
echo 工作台已在后台运行，浏览器应该已经弹出。如果没弹，请手动访问 http://localhost:8501
pause
