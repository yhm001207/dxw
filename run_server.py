"""
使用 Waitress 生产服务器的启动脚本
性能远优于 Flask 自带开发服务器
"""
import os
import sys
from waitress import serve
from app import app
from terminal_server import start_terminal_server

if __name__ == '__main__':
    # Windows: 禁用终端 QuickEdit，防止点击终端导致服务器卡死
    if sys.platform == 'win32':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value & ~0x0040)
        except Exception:
            pass

    print("=" * 60)
    print("[Server] 服务器远程链接 (Waitress 生产模式)")
    print("=" * 60)
    print(f"监听地址: http://0.0.0.0:5000")
    print(f"终端服务: ws://0.0.0.0:5001")
    print(f"本机访问: http://localhost:5000")
    print(f"控制器: http://localhost:5000/controller")
    print(f"个人云盘: http://localhost:5000/files")
    print(f"上传页面: http://localhost:5000/upload")
    print("=" * 60)
    print("按 Ctrl+C 停止服务器")
    print()

    # 启动 WebSocket 终端服务器
    start_terminal_server(port=5001)
    
    # Waitress 配置
    # threads: 线程数（建议设置为 CPU 核心数 × 2）
    # connection_limit: 最大并发连接数
    # channel_timeout: 连接超时（秒）
    serve(
        app,
        host='0.0.0.0',
        port=5000,
        threads=16,  # 8 个线程
        connection_limit=100,
        channel_timeout=300,
        cleanup_interval=30,
        max_request_body_size=10 * 1024 * 1024 * 1024,  # 10GB
    )
