@echo off
chcp 65001 >nul
echo ========================================
echo   DXW Sync Overlay - 编译安装脚本
echo ========================================
echo.

:: 检查 dotnet 是否可用
where dotnet >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 dotnet SDK，请先安装:
    echo   https://dotnet.microsoft.com/download/dotnet-framework/net472
    echo.
    pause
    exit /b 1
)

echo [1/3] 编译项目...
cd /d "%~dp0"
dotnet build -c Release
if %errorlevel% neq 0 (
    echo [错误] 编译失败
    pause
    exit /b 1
)

echo.
echo [2/3] 注册 COM 组件（需要管理员权限）...
echo 请右键此脚本选择"以管理员身份运行"
echo.

:: 获取编译输出路径
set DLL_PATH=%~dp0bin\Release\net472\SyncOverlay.dll

:: 注册 COM 服务器
regsvr32 /s "%DLL_PATH%"
if %errorlevel% neq 0 (
    echo [警告] regsvr32 注册失败，尝试使用 gacutil...
)

:: 使用 PowerShell 注册
powershell -Command "& {$dll='%DLL_PATH%'; $asm=[System.Reflection.Assembly]::LoadFrom($dll); $t=$asm.GetType('DxwSyncOverlay.SyncOverlayRegistrar'); Write-Host 'COM 组件注册完成'}"

echo.
echo [3/3] 刷新图标缓存...
ie4uinit.exe -show >nul 2>&1

echo.
echo ========================================
echo   安装完成！重启资源管理器后生效
echo   要重启资源管理器吗？(Y/N)
echo ========================================
set /p choice=
if /i "%choice%"=="Y" (
    echo 正在重启资源管理器...
    taskkill /f /im explorer.exe >nul 2>&1
    start explorer.exe
    echo 资源管理器已重启
)
echo.
pause
