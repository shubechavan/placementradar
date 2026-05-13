@echo off
title PlacementRadar — KJSIM
color 0A

echo.
echo  ================================
echo   PlacementRadar — KJSIM
echo   Starting server...
echo  ================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Please install Python from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit
)

REM Install all dependencies from requirements.txt
echo  Checking dependencies...
pip install -r requirements.txt --quiet --exists-action i
echo  Dependencies ready.
echo.

REM Start Flask server in background
echo  Launching PlacementRadar...
start /B python app.py

REM Wait for server to boot
timeout /t 3 /nobreak >nul

REM Open in Chrome, fall back to default browser
start chrome http://localhost:5000 2>nul
if errorlevel 1 (
    start http://localhost:5000
)

echo  Server is running at http://localhost:5000
echo.
echo  Keep this window open while using PlacementRadar.
echo  Close this window to shut down the server.
echo.
pause
