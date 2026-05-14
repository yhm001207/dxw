# -*- coding: utf-8 -*-
"""
macOS 打包脚本（新 UI PySide6 版本）
在 macOS 上运行：python build_mac.py
"""
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(ROOT, 'sync_client_ui', 'main.py')
RESOURCES = os.path.join(ROOT, 'sync_client_ui', 'resources')
ICON = os.path.join(ROOT, 'icon111.ico')

def main():
    os.chdir(ROOT)

    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

    deps = ['PySide6', 'requests']
    for dep in deps:
        try:
            __import__(dep.lower().replace('-', '_').split('[')[0])
        except ImportError:
            print(f"正在安装 {dep}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', dep])

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name=DXW同步客户端',
        '--windowed',
        '--onefile',
        '--noconfirm',
        '--clean',
        '--osx-bundle-identifier=com.dxw.syncclient',
        '--hidden-import=PySide6',
        '--hidden-import=PySide6.QtWidgets',
        '--hidden-import=PySide6.QtCore',
        '--hidden-import=PySide6.QtGui',
        f'--add-data={RESOURCES}:resources',
    ]
    if os.path.exists(ICON):
        cmd.append(f'--icon={ICON}')
    cmd.append(ENTRY)

    print("开始打包...")
    print(" ".join(cmd))
    ret = subprocess.call(cmd)
    if ret == 0:
        print("\n打包成功！")
        print("输出位置: dist/DXW同步客户端.app")
    else:
        print(f"\n打包失败，退出码: {ret}")
        sys.exit(1)

if __name__ == '__main__':
    main()
