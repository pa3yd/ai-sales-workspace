@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI Inquiry Agent - Verification

echo ============================================================
echo    AI Inquiry Agent  -  One-Click Verification
echo ============================================================
echo.

rem ---- Step 1: find a working Python interpreter ---------------
set "PYEXE="

rem (a) Prefer the fixed absolute path (always works on this PC)
if exist "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PYEXE=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
    goto run_it
)

rem (b) Fall back to whatever "python" is on PATH
where python >nul 2>nul
if %errorlevel%==0 (
    python -c "import sys" >nul 2>nul
    if %errorlevel%==0 set "PYEXE=python"
)

if not "%PYEXE%"=="" goto run_it

rem (c) Nothing found
echo [ERROR] Python not found on this computer.
echo.
echo Fix: install Python 3 from https://www.python.org/downloads/
echo      and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:run_it
echo Using Python: %PYEXE%
echo Running verification, please wait (about 30-60 seconds)...
echo.

rem ---- Step 2: run the verification ---------------------------
"%PYEXE%" verify.py
set EXITCODE=%errorlevel%

echo.
echo ============================================================
if "%EXITCODE%"=="0" (
    echo    ALL CHECKS PASSED - the agent is healthy
) else (
    echo    SOME CHECKS FAILED - read the table above
)
echo ============================================================
echo.
pause
