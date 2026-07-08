@echo off
rem Launch LocalFlow with a console window (useful for debugging).
cd /d "%~dp0"

if exist .venv goto :venv_ok
echo [ERROR] Virtual environment .venv not found.
echo Please run setup.bat first to install dependencies!
echo.
pause
exit /b 1

:venv_ok
.venv\Scripts\python.exe localflow.py
pause
