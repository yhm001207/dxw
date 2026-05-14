@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
set PYTHONIOENCODING=utf-8

echo ========================================
echo   Server Restart Tool
echo ========================================
echo.

echo [1/3] Checking port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo       Found process PID: %%a
    echo [2/3] Stopping process...
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo       [OK] Process %%a stopped
    ) else (
        echo       [WARN] Process %%a stop failed or already exited
    )
    goto :start
)

echo       [INFO] No running server found

:start
echo [3/3] Starting server...
cd /d "%~dp0"
start "" python run_server.py

echo.
echo [OK] Server started!
echo      URL: http://localhost:5000
echo.
timeout /t 3 >nul
