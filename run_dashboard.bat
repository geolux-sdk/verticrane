@echo off
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=py -3"
) else (
    set "PYTHON_CMD=python"
)

%PYTHON_CMD% -m streamlit run dashboard.py
if errorlevel 1 (
    echo.
    echo Failed to start the dashboard.
    echo Run install_requirements.bat first to install dependencies.
    pause
    exit /b 1
)
