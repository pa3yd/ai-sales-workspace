@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Inquiry Agent - Analyze Inquiry

echo ============================================================
echo    AI Inquiry Agent  -  Analyze an Inquiry
echo ============================================================
echo.

rem ---- Step 1: find a working Python interpreter ---------------
set "PYEXE="

if exist "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PYEXE=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
    goto run_it
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -c "import sys" >nul 2>nul
    if %errorlevel%==0 set "PYEXE=python"
)

if not "%PYEXE%"=="" goto run_it

echo [ERROR] Python not found on this computer.
echo Please install Python 3 and tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:run_it
rem ---- Step 2: analyze ----------------------------------------
rem If a .txt file was dropped onto this .bat, analyze that file.
rem Otherwise run the 3 built-in demo inquiries.

if "%~1"=="" (
    echo No file dropped in - running the 3 built-in demo inquiries.
    echo.
    echo TIP: drag any inquiry .txt file onto this .bat to analyze it.
    echo.
    pause
    "%PYEXE%" main.py
) else (
    echo Analyzing file: %~nx1
    echo.
    "%PYEXE%" main.py "%~1"
)

echo.
echo ============================================================
echo    Done - full report saved to output\analysis_result.json
echo ============================================================
echo.
pause
