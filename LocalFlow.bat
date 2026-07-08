@echo off
rem Launch LocalFlow with a console window (useful for first runs / debugging).
cd /d "%~dp0"
py -3.13 localflow.py
pause
