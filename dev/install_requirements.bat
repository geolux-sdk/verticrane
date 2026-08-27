@echo off
setlocal

set "REQUIREMENTS_FILE=%~dp0requirements.txt"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=py -3"
) else (
    set "PYTHON_CMD=python"
)

if not exist "%REQUIREMENTS_FILE%" (
    echo requirements.txt was not found.
    exit /b 1
)

if /I "%~1"=="--dry-run" (
    echo %PYTHON_CMD% -m pip install --upgrade pip
    echo %PYTHON_CMD% -m pip install -r "%REQUIREMENTS_FILE%"
    exit /b 0
)

%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 exit /b 1

%PYTHON_CMD% -m pip install -r "%REQUIREMENTS_FILE%"
if errorlevel 1 exit /b 1

echo Python libraries installed successfully.
