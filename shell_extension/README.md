# DXW Sync Shell Extension

Windows 文件管理器同步状态覆盖图标

## 功能

在 Windows 资源管理器中为同步文件夹内的文件显示状态图标：

- ✅ 绿色对勾 — 已同步
- 🔄 蓝色循环 — 同步中
- ❌ 红色叉 — 同步出错
- ⚠️ 黄色感叹号 — 冲突

## 安装

### 前置要求

- .NET Framework 4.7.2 或更高版本
- Windows 10/11

### 编译安装

1. **右键** `build_and_install.bat` → **以管理员身份运行**
2. 等待编译完成
3. 按 Y 重启资源管理器

### 手动编译

```shell
cd shell_extension
dotnet build -c Release
```

然后用管理员权限运行注册脚本。

## 卸载

右键 `uninstall.bat` → 以管理员身份运行

## 技术原理

1. Shell Extension 是一个 COM 组件，注册到 Windows 注册表
2. 当资源管理器显示文件时，会查询所有注册的覆盖图标处理器
3. 本扩展读取 `~/.dxw_sync_state.db`（同步客户端的本地状态数据库）
4. 根据文件的同步状态返回对应的图标

## 文件说明

- `SyncOverlay.cs` — C# 主程序
- `SyncOverlay.csproj` — 项目文件
- `build_and_install.bat` — 编译+安装脚本
- `uninstall.bat` — 卸载脚本
