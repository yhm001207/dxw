# -*- coding: utf-8 -*-
"""
macOS 打包脚本
在 macOS 上运行：python build_mac.py
"""
import subprocess
import sys
import os

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
        '--windowed',               # 无终端窗口
        '--onefile',                # 单文件
        '--noconfirm',              # 覆盖不询问
        '--clean',
        # macOS 专属
        '--osx-bundle-identifier=com.dxw.syncclient',
        # 包含隐藏导入
        '--hidden-import=pystray._darwin',
        '--hidden-import=PIL._tkinter_finder',
        '--hidden-import=requests',
        '--hidden-import=psutil',
        # tkinter 是 macOS Python 自带的，不需要额外处理
        'sync_client.py',
    ]

    print("开始打包...")
    print(" ".join(cmd))
    ret = subprocess.call(cmd)
    if ret == 0:
        print("\n打包成功！")
        print("输出位置: dist/DWX同步客户端.app")
    else:
        print(f"\n打包失败，退出码: {ret}")
        sys.exit(1)

if __name__ == '__main__':
    main()
