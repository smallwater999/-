@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title 高考志愿智能规划师

echo =========================================
echo   高考志愿智能规划师 v2.0.0
echo   Gaokao Volunteer Planning Agent
echo =========================================
echo.

:: Check Python installation
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found!
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Display Python version
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PY_VER=%%i
echo [INFO] %PY_VER%
echo.

:: Set project root to script directory
cd /d "%~dp0"

:: Set environment variables
set PORT=8000
set HOST=0.0.0.0

:: Create config from example if not exists
if not exist ".env" (
    echo [INFO] .env not found, creating from .env.example...
    copy ".env.example" ".env" >nul
    echo [WARN] Please edit .env and set your LLM_API_KEY before using!
)

:: Create virtual environment if not exists
if not exist "win_venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    python -m venv win_venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate virtual environment
call win_venv\Scripts\activate.bat

:: Install/update dependencies
echo [INFO] Installing dependencies...
pip install -r requirements.txt -q --disable-pip-version-check
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install dependencies.
    echo Try running: pip install -r requirements.txt
    pause
    exit /b 1
)

:: Ensure data directories exist
if not exist "data\charts" mkdir "data\charts"
if not exist "data\reports" mkdir "data\reports"
if not exist "data\checkpoints" mkdir "data\checkpoints"

echo.
echo =========================================
echo   Starting service...
echo   Open http://localhost:8000 in browser
echo   Press Ctrl+C to stop
echo =========================================
echo.

:: Start the service
python -m uvicorn src.main:app --host %HOST% --port %PORT% --log-level info

:: If uvicorn exits, pause
echo.
echo [INFO] Service stopped.
pause
