# -*- coding: utf-8 -*-
"""
服务器远程链接 Web 控制面板
============================
精简入口：创建 Flask app，注册 Blueprint，启动中间件。
"""

import os, sys, time
from pathlib import Path

from flask import Flask, request, jsonify
from auth import init_db, get_current_user, is_whitelisted, sync_shared_folders_from_disk
from config import WORK_DIR, TEMPLATE_DIR, UPLOAD_DIR, SHARED_DIR
import state

app = Flask(__name__)
app.secret_key = 'code-editor-secret-key-2026'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 10GB

# 初始化数据库
init_db()

# 同步磁盘共享文件夹到数据库
sync_shared_folders_from_disk(SHARED_DIR)

# 注册所有 Blueprint
from routes import register_all
register_all(app)


# ==================== 中间件 ====================

@app.before_request
def log_visitor():
    """记录访问者信息"""
    from datetime import datetime
    path = request.path
    # 跳过静态资源和输出文件
    if path.startswith('/output/') or path.startswith('/uploads/') or path.startswith('/static/'):
        return
    try:
        user = get_current_user()
        username = user['username'] if user else '-'
    except Exception:
        username = '-'
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()
    entry = {
        'ip': ip or '-',
        'path': path,
        'username': username,
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'method': request.method,
    }
    with state.VISITORS_LOCK:
        state.VISITORS.append(entry)
        if len(state.VISITORS) > 5000:
            state.VISITORS[:] = state.VISITORS[-2000:]


@app.before_request
def track_traffic():
    """统计用户流量"""
    user = None
    try:
        user = get_current_user()
    except Exception:
        pass
    if not user:
        return
    username = user['username']
    content_len = request.content_length or 0
    with state._USER_TRAFFIC_LOCK:
        if username not in state._USER_TRAFFIC:
            state._USER_TRAFFIC[username] = {'upload': 0, 'download': 0, 'requests': 0}
        state._USER_TRAFFIC[username]['upload'] += content_len
        state._USER_TRAFFIC[username]['requests'] += 1


@app.after_request
def after_request_track_download(response):
    """统计下载流量"""
    try:
        user = get_current_user()
        if user:
            cl = response.content_length
            if cl and cl > 0:
                with state._USER_TRAFFIC_LOCK:
                    if user['username'] in state._USER_TRAFFIC:
                        state._USER_TRAFFIC[user['username']]['download'] += cl
    except Exception:
        pass
    return response


@app.before_request
def check_server_status():
    """服务器暂停检查"""
    with state.SERVER_LOCK:
        if not state.SERVER_STOPPED:
            return
    allowed = ['/api/start', '/api/status', '/controller']
    if request.path in allowed or request.path.startswith('/output/') or request.path.startswith('/uploads/'):
        return
    if request.path.startswith('/api/'):
        return jsonify({'error': 'server stopped', 'stopped': True}), 503
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>服务器已暂停</title>'
        '<style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;'
        'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}'
        '.box{background:#1e293b;padding:48px;border-radius:16px;text-align:center;}'
        'button{padding:12px 32px;font-size:16px;background:#22c55e;color:#fff;'
        'border:none;border-radius:8px;cursor:pointer;margin-top:16px;}'
        'button:hover{background:#16a34a;}</style></head><body>'
        '<div class="box"><h1>⚠️ 服务器已暂停</h1>'
        '<p style="color:#94a3b8;margin:12px 0;">Flask 服务器已暂停，点击下方按钮恢复</p>'
        '<button onclick="startServer()">▶ 启动服务器</button>'
        '<p id="msg" style="margin-top:12px;color:#22d3ee;"></p></div>'
        '<script>function startServer(){'
        'fetch("/api/start",{method:"POST"}).then(r=>r.json()).then(d=>{'
        'document.getElementById("msg").textContent="已启动！正在刷新...";'
        'setTimeout(()=>location.reload(),1000);'
        '}).catch(e=>{'
        'document.getElementById("msg").textContent="启动失败："+e;'
        '});}</script></body></html>'
    )
    return html, 503


# ==================== 错误处理 ====================

@app.errorhandler(413)
def request_entity_too_large(e):
    max_mb = app.config.get('MAX_CONTENT_LENGTH', 0) / 1024 / 1024
    return jsonify({'error': f'文件太大，服务器限制 {max_mb:.0f}MB'}), 413


@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': f'请求无效：{e.description}'}), 400


@app.errorhandler(500)
def internal_error(e):
    import traceback
    traceback.print_exc()
    return jsonify({'error': f'服务器内部错误：{e.description}'}), 500


# ==================== 启动 ====================

if __name__ == '__main__':
    TEMPLATE_DIR.mkdir(exist_ok=True)

    print('启动 DXW 服务器 ...')
    print('访问地址: http://192.168.31.196:5000')

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
        _log_f = open('dxw_server.log', 'a', encoding='utf-8')
        sys.stdout = _log_f
        sys.stderr = _log_f

    import logging
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                        handlers=[logging.FileHandler('dxw_server.log', encoding='utf-8')])
    log = logging.getLogger('dxw')
    log.info('启动 DXW 服务器 (waitress) ...')
    log.info('访问地址: http://192.168.31.196:5000')
    log.info('日志文件: dxw_server.log')

    # 启动 WebSocket 终端服务器（端口 5001）
    try:
        from terminal_server import start_terminal_server
        start_terminal_server(port=5001)
        log.info('终端服务器已启动 (ws://0.0.0.0:5001)')
    except Exception as e:
        log.warning(f'终端服务器启动失败: {e}')

    serve(app, host='0.0.0.0', port=5000, threads=16, channel_timeout=300, max_request_body_size=10*1024*1024*1024)
