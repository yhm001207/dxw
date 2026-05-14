@echo off
chcp 65001 >nul
echo ========================================
echo   Stopping Server
echo ========================================

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    echo Found server PID: %%a, killing...
    taskkill /F /PID %%a >nul 2>&1
)

echo Server stopped.
pause
