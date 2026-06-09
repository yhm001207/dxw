import subprocess
import os
import shutil

# PyInstaller 基础参数
common_args = [
    'pyinstaller',
    '--noconfirm',           # 覆盖已存在的构建目录
    '--clean',               # 清理缓存
    '--windowed',            # 创建无控制台窗口的 GUI 应用
    '--name', 'DxwSync',     # 应用程序名称
    # '--icon=icon.icns',    # macOS 图标文件 (.icns 格式)，取消注释并提供图标文件路径

    # 添加程序运行时需要的数据文件
    '--add-data=templates:templates',        # 使用冒号 : 分隔，Mac/Linux 语法
    '--add-data=users:users',
    '--add-data=shared:shared',

    # 隐式导入的模块
    '--hidden-import=psutil',
    '--hidden-import=requests',

    # 入口脚本
    'app.py'
]

# 运行 PyInstaller
print("开始构建 macOS 应用...")
subprocess.run(common_args, check=True)

print("\n✅ 构建成功！")
print("可执行文件位于: dist/DxwSync.app")
print("\n打包完成后，将 dist/DxwSync.app 文件夹压缩后可发送给其他 Mac 用户")
