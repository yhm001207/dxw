@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
set PYTHONIOENCODING=utf-8

echo ========================================
echo   Server Start (Debug Mode)
echo ========================================
echo.

:: Kill old process on port 5000
echo [1/2] Checking port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo       Killing old process PID: %%a ...
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo [2/2] Starting server in foreground...
echo.
echo ========================================
echo If there's an error, you'll see it below
echo Press Ctrl+C to stop the server
echo ========================================
echo.

cd /d "%~dp0"
python run_server.py

echo.
echo.
echo ========================================
echo Server stopped. Check output above for errors.
echo ========================================
echo.
pause
