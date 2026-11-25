@echo off
title Altium Library Merger
echo ========================================================
echo             ALTIUM LIBRARY MERGER LAUNCHER
echo ========================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in your PATH!
    echo Please install Python 3.10+ from python.org and try again.
    pause
    exit /b 1
)

echo Starting Altium Library Merger...
python run_app.py
pause
