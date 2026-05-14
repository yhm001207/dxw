@echo off
chcp 65001 >nul
echo ========================================
echo   DXW Sync Overlay - 卸载脚本
echo ========================================
echo.

set CLSID={A1B2C3D4-E5F6-7890-ABCD-EF1234567890}

echo [1/2] 注销 COM 组件...
regsvr32 /u /s "%~dp0bin\Release\net472\SyncOverlay.dll" >nul 2>&1

:: 清理注册表
reg delete "HKCR\CLSID\%CLSID%" /f >nul 2>&1
reg delete "HKCR\Wow6432Node\CLSID\%CLSID%" /f >nul 2>&1

echo [2/2] 刷新图标缓存...
ie4uinit.exe -show >nul 2>&1

echo.
echo 卸载完成！重启资源管理器后生效。
echo.
pause
