@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

echo ========================================
echo   Server Restart Tool
echo ========================================
echo.

echo [1/3] Checking port 5000 and 5001...

:: Kill port 5000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo       Found Flask PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo       [OK] PID %%a stopped
    ) else (
        echo       [WARN] PID %%a stop failed
    )
)

:: Kill port 5001
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5001" ^| findstr "LISTENING"') do (
    echo       Found Terminal PID: %%a
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo       [OK] PID %%a stopped
    ) else (
        echo       [WARN] PID %%a stop failed
    )
)

echo [2/3] Starting server...
cd /d "%~dp0"
python app.py
