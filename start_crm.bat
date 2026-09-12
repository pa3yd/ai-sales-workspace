@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Project virtual environment was not found: "%PY%"
    exit /b 1
)

cd /d "%ROOT%"
"%PY%" -m streamlit run workbench\app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
