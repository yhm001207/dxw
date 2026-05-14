@echo off
chcp 65001 >nul

:: Check if already admin
net session >nul 2>&1
if %errorlevel% == 0 goto :run

:: Request admin and relaunch
echo Requesting admin privileges...
powershell -Command "Start-Process '%~f0' -Verb RunAs"
exit /b

:run
setlocal EnableDelayedExpansion
set PYTHONIOENCODING=utf-8

echo ========================================
echo   Server - Admin Mode
echo   (CPU Temp Monitor Enabled)
echo ========================================
echo.

:: Kill old process on port 5000
echo [1/2] Checking port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo       Killing old process PID: %%a ...
    taskkill /F /PID %%a >nul 2>&1
)

:: Start server
echo [2/2] Starting server (admin mode)...
cd /d "%~dp0"
start "" python run_server.py

echo.
echo ========================================
echo   Server started (Admin Mode)
echo   URL: http://localhost:5000
echo   CPU temperature: ENABLED
echo ========================================
echo.
timeout /t 3 >nul
