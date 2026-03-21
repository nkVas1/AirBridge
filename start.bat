@echo off
chcp 65001 >nul 2>&1
title AirBridge

echo ============================================
echo   AirBridge — Wireless File Transfer
echo ============================================
echo.

:: Check Python availability
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Verify Python version (3.10+)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [INFO] Python %PYVER% detected

:: Create virtual environment if it doesn't exist
if not exist "venv\" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Install/update dependencies
echo [INFO] Installing dependencies...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies. Run "pip install -r requirements.txt" manually to see details.
    pause
    exit /b 1
)
echo [OK] Dependencies ready

echo.
echo [START] Launching AirBridge...
echo [INFO] Press Ctrl+C to stop
echo.

python -m airbridge

pause
