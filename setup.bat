@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo =======================================================
echo              LocalFlow Installer Setup
echo =======================================================
echo.
echo [Setup] Searching for a compatible Python installation...

set "PYTHON_CMD="

py -3.13 -c "import sys; exit(0 if sys.version_info.major > 3 or sys.version_info.major == 3 and sys.version_info.minor >= 10 else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3.13"
    goto :found
)

py -3.12 -c "import sys; exit(0 if sys.version_info.major > 3 or sys.version_info.major == 3 and sys.version_info.minor >= 10 else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3.12"
    goto :found
)

py -3.11 -c "import sys; exit(0 if sys.version_info.major > 3 or sys.version_info.major == 3 and sys.version_info.minor >= 10 else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3.11"
    goto :found
)

py -3 -c "import sys; exit(0 if sys.version_info.major > 3 or sys.version_info.major == 3 and sys.version_info.minor >= 10 else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
    goto :found
)

python -c "import sys; exit(0 if sys.version_info.major > 3 or sys.version_info.major == 3 and sys.version_info.minor >= 10 else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :found
)

echo [ERROR] Python 3.10+ was not found on your system.
echo Please download and install Python from: https://www.python.org/downloads/
echo Make sure to check the option "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:found
echo [Setup] Found Python command: %PYTHON_CMD%

if exist .venv goto :venv_exists
echo [Setup] Creating virtual environment (.venv)...
%PYTHON_CMD% -m venv .venv
if %errorlevel% neq 0 goto :venv_fail
goto :venv_exists

:venv_fail
echo [ERROR] Failed to create virtual environment.
pause
exit /b 1

:venv_exists
echo [Setup] Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1

echo [Setup] Running setup installer helper...
.venv\Scripts\python.exe setup.py
if %errorlevel% neq 0 goto :setup_fail

echo.
echo =======================================================
echo          LocalFlow Installed Successfully!
echo =======================================================
echo.
pause
exit /b 0

:setup_fail
echo.
echo [ERROR] Installation failed during dependency setup.
pause
exit /b 1
