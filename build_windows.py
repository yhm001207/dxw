# -*- coding: utf-8 -*-
"""
Windows 打包脚本
打包 sync_client.py 为 .exe，输出到桌面\打包 目录
图标使用 icon111.ico
"""
import subprocess
import sys
import os

OUTPUT_DIR = os.path.join(os.path.expanduser('~'), 'Desktop', '打包')
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon111.ico')
ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sync_client.py')

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 确保 PyInstaller 已安装
    try:
        import PyInstaller
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])

    # 确保依赖已安装
    deps = ['requests', 'pystray', 'Pillow', 'psutil']
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
        f'--icon={ICON_PATH}',
        f'--distpath={OUTPUT_DIR}',
        f'--add-data={ICON_PATH};.',
        '--hidden-import=pystray._windows',
        '--hidden-import=pystray._win32',
        '--hidden-import=PIL._tkinter_finder',
        '--hidden-import=requests',
        '--hidden-import=psutil',
        '--hidden-import=win32gui',
        '--hidden-import=win32con',
        '--hidden-import=win32api',
        ENTRY,
    ]

    print("开始打包...")
    print(" ".join(cmd))
    ret = subprocess.call(cmd)
    if ret == 0:
        exe_path = os.path.join(OUTPUT_DIR, 'DXW同步客户端.exe')
        print(f"\n打包成功！")
        print(f"输出位置: {exe_path}")
    else:
        print(f"\n打包失败，退出码: {ret}")
        sys.exit(1)

if __name__ == '__main__':
    main()
