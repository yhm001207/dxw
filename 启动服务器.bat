@echo off
cd /d "%~dp0"
echo ========================================
echo   DXW Server Starting...
echo   Flask:    http://192.168.31.196:5000
echo   Terminal: ws://192.168.31.196:5001
echo ========================================
python app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Server exited with error
    pause
)
