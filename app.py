# -*- coding: utf-8 -*-
"""
服务器远程链接 Web 控制面板
============================
通过浏览器远程访问服务器，运行程序，查看实时输出和结果。
支持：脚本运行、访问者追踪、服务器暂停/恢复、文件上传运行。
"""

import os
import sys
import time
import subprocess
import signal
import json
import threading
import queue
import collections
import shutil
import platform
import zipfile
import io
import codecs
import uuid
from pathlib import Path
from datetime import datetime

import psutil

import flask
from flask import (
    Flask, Response, request, send_from_directory, send_file,
    render_template, render_template_string, jsonify,
    session, redirect, url_for
)

# 导入用户认证模块
from auth import (
    init_db, register_user, verify_user, login_required, get_current_user,
    get_user_dir, get_user_uploads_dir, get_user_profile_dir,
    get_user_profile, save_user_profile, get_user_avatar_path,
    is_whitelisted, get_whitelist, add_to_whitelist, remove_from_whitelist,
    get_all_users, get_all_admins, is_admin, is_super_admin, set_user_role, get_user_role,
    create_application, get_my_application, get_pending_applications,
    submit_approval, count_pending_for_admin, get_application_approvals,
    send_message, get_inbox, get_sent, mark_message_read, get_unread_message_count,
    delete_message, get_message,
    create_notification, get_notifications, get_unread_notification_count,
    mark_notification_read, mark_all_notifications_read,
    delete_notification, delete_all_notifications,
    create_shared_folder, add_shared_folder_member,
    get_accessible_shared_folders, get_shared_folder_members, delete_shared_folder,
    sync_shared_folders_from_disk,
    invite_to_shared_folder, accept_shared_invitation, reject_shared_invitation,
    get_pending_invitations,
    delete_user,
    record_shared_file_meta, get_shared_files_meta,
    create_moment, get_moments, get_user_moments, delete_moment,
    toggle_moment_like, get_moment_likes, get_moments_like_status,
    SUPER_ADMIN_USER,
    DB_PATH,
    USERS_DIR,
)

app = Flask(__name__)
app.secret_key = 'code-editor-secret-key-2026'  # 用于 Session 加密
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024  # 最大上传 10GB

# 初始化用户数据库
init_db()

WORK_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WORK_DIR / 'templates'
UPLOAD_DIR = WORK_DIR / 'uploads'  # 保留全局上传目录（兼容性）
UPLOAD_DIR.mkdir(exist_ok=True)
SHARED_DIR = WORK_DIR / 'shared'
SHARED_DIR.mkdir(exist_ok=True)
(SHARED_DIR / 'public').mkdir(exist_ok=True)
(SHARED_DIR / 'private').mkdir(exist_ok=True)
# 同步磁盘上的共享文件夹到数据库
sync_shared_folders_from_disk(SHARED_DIR)

# PyTorch 环境路径
TORCH_PYTHON = None
for _p in [
    Path(r'D:\apps\anaconda\envs\torch\python.exe'),
    Path(r'D:\apps\anaconda3\envs\torch\python.exe'),
]:
    if _p.exists():
        TORCH_PYTHON = str(_p)
        break

# 脚本映射
SCRIPTS = {
    'cascade': {
        'file': 'lcvr_cascade_3lc.py',
        'title': '三级级联调制',
        'desc': '三级级联偏振调制仿真，生成 30 张调制矩阵子图',
        'images': ['lcvr_triple_cascade.png'],
        'needs_torch': False,
    },
    'simple': {
        'file': 'lcvr_simple.py',
        'title': '二级简单级联',
        'desc': '两级级联调制仿真，展示调制矩阵随电压变化',
        'images': ['lcvr_modulation_matrices_final.png'],
        'needs_torch': False,
    },
    'load_pt': {
        'file': 'load_pt_web.py',
        'title': '加载 .pt 相位文件',
        'desc': '加载当前目录下所有 .pt 相位文件，显示相位分布图',
        'images': [],
        'needs_torch': True,
    },
    'sgd_dfft': {
        'file': 'SGD_of_MM_ADAM_DFFT_v5.py',
        'title': 'SGD 全息优化 (DFFT)',
        'desc': '多平面相位全息图优化（PyTorch 自动求导 + Adam），运行较慢',
        'images': ['loss_curve.png', 'phase_hologram.png', 'reconstructions.png'],
        'needs_torch': True,
    },
    'sgd_siren': {
        'file': 'SGD_of_MM_ADAM_SIREN_v6.py',
        'title': 'SGD 全息优化 (SIREN)',
        'desc': 'SIREN 神经网络参数化的相位全息图优化，结果更平滑',
        'images': ['loss_curve_siren.png', 'phase_hologram_siren.png', 'reconstructions_siren.png'],
        'needs_torch': True,
    },
}

_running_processes = {}
_running_lock = threading.Lock()

_upload_running = {}
_upload_lock = threading.Lock()

_file_running = {}  # 跟踪通过 /api/run_file 运行的进程
_file_lock = threading.Lock()

# 用户进程运行状态和输出缓冲（用于断线重连）
_user_processes = {}  # {username: {filename: {proc, buffer, start_time, path}}}
_user_proc_lock = threading.Lock()
BUFFER_MAX_LINES = 500  # 每个进程最多保留最近500行输出

VISITORS = []
VISITORS_LOCK = threading.Lock()

# 用户流量统计 {username: {'upload': bytes, 'download': bytes, 'requests': int}}
_USER_TRAFFIC = {}
_USER_TRAFFIC_LOCK = threading.Lock()

SERVER_STOPPED = False
SERVER_LOCK = threading.Lock()

_SKIP_LOG_PATHS = ('/output/', '/api/', '/static/', '/uploads/')


def get_env(gpu_id=None):
    env = os.environ.copy()
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    if gpu_id is not None and gpu_id != '' and gpu_id != 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = str(gpu_id)
    elif gpu_id == 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = '-1'
    return env


def detect_gpus():
    """通过 nvidia-smi 检测所有 GPU"""
    gpus = []
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 7:
                    gpus.append({
                        'id': int(parts[0]),
                        'name': parts[1],
                        'mem_total': int(parts[2]),
                        'mem_used': int(parts[3]),
                        'mem_free': int(parts[4]),
                        'temp': int(parts[5]),
                        'util': int(parts[6]),
                    })
    except Exception:
        pass
    return gpus


# ==================== 用户设置 ====================

def get_user_settings(username):
    """读取用户设置，不存在则返回空字典"""
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / 'settings.json'
    if settings_path.exists():
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_settings(username, settings):
    """保存用户设置"""
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / 'settings.json'
    with open(settings_path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ==================== 用户认证 ====================

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """登录页面"""
    if request.method == 'GET':
        return render_template('login.html')
    
    # POST - 处理登录
    data = request.get_json() if request.is_json else request.form
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': '请输入用户名和密码'}), 400
    
    user = verify_user(username, password)
    if user is None:
        return jsonify({'error': '用户名或密码错误'}), 401

    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user.get('role', 'user')
    session['login_time'] = time.time()
    return jsonify({'ok': True, 'username': user['username']})


@app.route('/register', methods=['GET', 'POST'])
def register_page():
    """注册页面"""
    if request.method == 'GET':
        return render_template('register.html')
    
    # POST - 处理注册
    data = request.get_json() if request.is_json else request.form
    username = data.get('username', '').strip()
    password = data.get('password', '')
    confirm = data.get('confirm', '')
    
    if not username or not password:
        return jsonify({'error': '请输入用户名和密码'}), 400
    
    if password != confirm:
        return jsonify({'error': '两次密码不一致'}), 400
    
    result = register_user(username, password)
    if 'error' in result:
        return jsonify(result), 400
    
    # 自动登录
    user = verify_user(username, password)
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user.get('role', 'user')
    return jsonify({'ok': True, 'username': user['username']})


@app.route('/logout', methods=['POST'])
def logout():
    """登出"""
    session.clear()
    return jsonify({'ok': True})


# ==================== 大厅页面 ====================

@app.route('/')
@login_required
def lobby():
    """大厅页面（登录后默认页面）"""
    user = get_current_user()
    role = get_user_role(user['username'])
    is_wl = is_whitelisted(user['username'])
    return render_template('lobby.html', username=user['username'], user_role=role, user_is_whitelisted=is_wl)

@app.route('/index')
@login_required
def index():
    """功能选择页面（兼容旧链接）"""
    user = get_current_user()
    return render_template('index.html', scripts=SCRIPTS, username=user['username'])


# ==================== 脚本运行 API ====================

@app.route('/api/scripts')
def list_scripts():
    info = {}
    for name, cfg in SCRIPTS.items():
        info[name] = {
            'title': cfg['title'],
            'desc': cfg['desc'],
            'images': cfg['images'],
            'running': name in _running_processes,
        }
    return jsonify(info)


@app.route('/api/run/<script_name>', methods=['GET'])
def run_script(script_name):
    if script_name not in SCRIPTS:
        return jsonify({'error': f'未知脚本: {script_name}'}), 404
    cfg = SCRIPTS[script_name]
    script_path = WORK_DIR / cfg['file']
    if not script_path.exists():
        return jsonify({'error': f'文件不存在: {script_path}'}), 404
    with _running_lock:
        if script_name in _running_processes:
            return jsonify({'error': '该脚本正在运行中，请等待完成'}), 409

    def generate():
        try:
            python_exe = TORCH_PYTHON if cfg.get('needs_torch') else sys.executable
            if cfg.get('needs_torch') and not TORCH_PYTHON:
                yield 'data: ' + json.dumps({'type': 'error', 'msg': '未找到 PyTorch 环境'}) + '\n\n'
                return

            proc = subprocess.Popen(
                [python_exe, str(script_path)],
                cwd=str(WORK_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=get_env(),
                text=True,
                encoding='utf-8', errors='replace',
                bufsize=1,
            )
            with _running_lock:
                _running_processes[script_name] = proc

            yield 'data: ' + json.dumps({'type': 'status', 'msg': 'started'}) + '\n\n'

            for line in proc.stdout:
                data = json.dumps({'type': 'stdout', 'msg': line.rstrip()})
                yield f'data: {data}\n\n'

            proc.wait()
            return_code = proc.returncode

            result_images = list(cfg['images'])
            if script_name == 'load_pt':
                for f in sorted(WORK_DIR.glob('pt_phase_*.png')):
                    fn = f.name
                    if fn not in result_images:
                        result_images.append(fn)

            existing_images = [img for img in result_images if (WORK_DIR / img).exists()]
            done_data = json.dumps({
                'type': 'done',
                'return_code': return_code,
                'images': existing_images,
            })
            yield f'data: {done_data}\n\n'

        except Exception as e:
            err_data = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err_data}\n\n'
        finally:
            with _running_lock:
                _running_processes.pop(script_name, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/stop/<script_name>', methods=['POST'])
def stop_script(script_name):
    with _running_lock:
        proc = _running_processes.get(script_name)
        if proc is None:
            return jsonify({'error': '脚本未在运行'}), 404
        if sys.platform == 'win32':
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        _running_processes.pop(script_name, None)
    return jsonify({'status': 'stopped'})


# ==================== 文件输出 ====================

@app.route('/output/<path:filename>')
def serve_output(filename):
    return send_from_directory(str(WORK_DIR), filename)


# ==================== 访问者追踪 ====================

@app.before_request
def log_visitor():
    if any(request.path.startswith(p) for p in _SKIP_LOG_PATHS):
        return
    visitor = {
        'ip': request.remote_addr,
        'username': session.get('username', '-'),
        'method': request.method,
        'path': request.path,
        'ua': request.headers.get('User-Agent', ''),
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    with VISITORS_LOCK:
        VISITORS.append(visitor)
        if len(VISITORS) > 200:
            VISITORS[:] = VISITORS[-200:]

@app.after_request
def track_traffic(response):
    """统计用户流量"""
    username = session.get('username')
    if not username:
        return response
    # 只用 content_length 统计，绝不能调用 get_data()，否则会把 SSE 流和大文件全部读进内存
    download_bytes = 0
    try:
        cl = response.content_length
        if cl:
            download_bytes = cl
    except Exception:
        pass
    upload_bytes = 0
    try:
        if request.content_length:
            upload_bytes = request.content_length
    except Exception:
        pass
    with _USER_TRAFFIC_LOCK:
        t = _USER_TRAFFIC.setdefault(username, {'upload': 0, 'download': 0, 'requests': 0})
        t['upload'] += upload_bytes
        t['download'] += download_bytes
        t['requests'] += 1
    return response


# ==================== 控制器页面 ====================

@app.route('/controller')
@login_required
def controller():
    """后台监管页面 — 所有登录用户均可访问，内容按角色区分"""
    username = session.get('username')
    role = get_user_role(username)
    is_adm = is_admin(username)
    is_sadm = (role == 'super_admin')
    is_wl = is_whitelisted(username)
    return render_template(
        'controller.html',
        username=username,
        user_role=role,
        is_admin=is_adm,
        is_super_admin=is_sadm,
        user_is_whitelisted=is_wl,  # 避免与函数名冲突
    )


# ==================== 白名单 API ====================

@app.route('/api/whitelist')
@login_required
def api_get_whitelist():
    """获取白名单和所有用户列表（含磁盘用户）"""
    user = get_current_user()
    if not is_admin(user['username']):
        return jsonify({'error': '无权限'}), 403
    # 数据库用户
    db_users = get_all_users()
    db_names = {u['username'] for u in db_users}
    db_map = {u['username']: u for u in db_users}
    # 磁盘用户（users 目录下有文件夹但不在数据库中的）
    users_dir = Path(__file__).resolve().parent / 'users'
    all_users = list(db_users)
    if users_dir.exists():
        for d in sorted(users_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.') and d.name not in db_names:
                all_users.append({
                    'username': d.name,
                    'created_at': '-',
                    'role': 'user',
                })
    return jsonify({
        'whitelist': get_whitelist(),
        'all_users': all_users,
    })


@app.route('/api/whitelist/add', methods=['POST'])
@login_required
def api_whitelist_add():
    """添加用户到白名单"""
    user = get_current_user()
    if not is_admin(user['username']):
        return jsonify({'error': '无权限'}), 403
    data = request.get_json() or {}
    username = data.get('username', '')
    if not username:
        return jsonify({'error': '缺少用户名'}), 400
    if add_to_whitelist(username):
        return jsonify({'status': 'ok'})
    return jsonify({'error': '添加失败'}), 500


@app.route('/api/whitelist/remove', methods=['POST'])
@login_required
def api_whitelist_remove():
    """从白名单移除用户"""
    user = get_current_user()
    if not is_admin(user['username']):
        return jsonify({'error': '无权限'}), 403
    data = request.get_json() or {}
    username = data.get('username', '')
    if not username:
        return jsonify({'error': '缺少用户名'}), 400
    if remove_from_whitelist(username):
        return jsonify({'status': 'ok'})
    return jsonify({'error': '移除失败'}), 500


# 白名单变更日志（通知管理员用）
_WHITELIST_CHANGE_LOG = []
_WHITELIST_CHANGE_LOCK = threading.Lock()


@app.route('/api/whitelist/batch', methods=['POST'])
@login_required
def api_whitelist_batch():
    """批量设置白名单（仅超级管理员可操作）"""
    user = get_current_user()
    if not is_super_admin(user['username']):
        return jsonify({'error': '仅超级管理员有权修改白名单'}), 403
    data = request.get_json() or {}
    usernames = data.get('usernames', [])
    new_set = set(usernames)
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    # 获取旧的 whitelist 以便计算变化
    c.execute('SELECT username FROM whitelist')
    old_set = set(r[0] for r in c.fetchall())
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    # 先清空再批量添加
    c.execute('DELETE FROM whitelist')
    for u in usernames:
        c.execute('INSERT OR IGNORE INTO whitelist (username) VALUES (?)', (u,))
    conn.commit()
    conn.close()
    # 记录变更（通知其他管理员）
    with _WHITELIST_CHANGE_LOCK:
        _WHITELIST_CHANGE_LOG.append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'by': user['username'],
            'count': len(usernames),
            'added': added,
            'removed': removed,
        })
        # 只保留最近20条记录
        if len(_WHITELIST_CHANGE_LOG) > 20:
            _WHITELIST_CHANGE_LOG.pop(0)
    # 通知其他管理员
    details = ''
    if added:
        details += '✅ 加入：' + '、'.join(added)
    if removed:
        if details: details += '  '
        details += '❌ 移除：' + '、'.join(removed)
    if not details:
        details = '无变化'
    admins = get_all_admins()
    for admin_name in admins:
        if admin_name != user['username']:
            create_notification(admin_name, 'whitelist_change', '白名单已更新',
                f'{user["username"]} 修改了白名单 — {details}', '/controller')
    return jsonify({'status': 'ok', 'count': len(usernames)})


@app.route('/api/whitelist/changes', methods=['GET'])
@login_required
def api_whitelist_changes():
    """管理员查看白名单变更通知"""
    user = get_current_user()
    if not is_admin(user['username']):
        return jsonify({'error': '无权限'}), 403
    with _WHITELIST_CHANGE_LOCK:
        # 如果请求来自超级管理员，只返回非自己操作的变更
        if is_super_admin(user['username']):
            filtered = [c for c in _WHITELIST_CHANGE_LOG if c['by'] != user['username']]
        else:
            filtered = list(_WHITELIST_CHANGE_LOG)
        return jsonify(filtered[-5:])  # 最近5条


@app.route('/api/scan_users')
@login_required
def api_scan_users():
    """扫描 users 目录，返回所有存在的用户（含数据库中有的和磁盘上有的）"""
    user = get_current_user()
    if not is_admin(user['username']):
        return jsonify({'error': '无权限'}), 403

    users_dir = Path(__file__).resolve().parent / 'users'
    disk_users = []
    if users_dir.exists():
        for d in sorted(users_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.'):
                disk_users.append(d.name)

    db_users = get_all_users()
    db_names = {u['username'] for u in db_users}
    db_map = {u['username']: u['created_at'] for u in db_users}

    # 合并：磁盘上有但数据库没有的用户
    all_names = list(db_names)
    for u in disk_users:
        if u not in db_names:
            all_names.append(u)

    result = []
    for name in all_names:
        result.append({
            'username': name,
            'created_at': db_map.get(name, '-'),
            'on_disk': name in disk_users,
            'in_db': name in db_names,
        })

    return jsonify({'users': result, 'disk_count': len(disk_users), 'db_count': len(db_users)})


# ==================== 管理员角色管理 API（仅 super_admin） ====================

@app.route('/api/admin/list_roles')
@login_required
def api_list_roles():
    """获取所有用户及其角色（仅 super_admin，含磁盘用户）"""
    user = get_current_user()
    if not is_super_admin(user['username']):
        return jsonify({'error': '无权限，仅超级管理员可操作'}), 403
    db_users = get_all_users()
    db_names = {u['username'] for u in db_users}
    all_users = list(db_users)
    # 合并磁盘用户
    users_dir = Path(__file__).resolve().parent / 'users'
    if users_dir.exists():
        for d in sorted(users_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.') and d.name not in db_names:
                all_users.append({
                    'username': d.name,
                    'created_at': '-',
                    'role': 'user',
                })
    return jsonify({'users': all_users})


@app.route('/api/admin/set_role', methods=['POST'])
@login_required
def api_set_role():
    """设置用户角色（仅 super_admin 可操作，角色限 admin/user）"""
    user = get_current_user()
    if not is_super_admin(user['username']):
        return jsonify({'error': '无权限，仅超级管理员可操作'}), 403
    data = request.get_json() or {}
    target = data.get('username', '').strip()
    role = data.get('role', '').strip()
    if not target:
        return jsonify({'error': '缺少目标用户名'}), 400
    if role not in ('admin', 'user'):
        return jsonify({'error': '角色只能是 admin 或 user'}), 400
    if target == user['username']:
        return jsonify({'error': '不能修改自己的角色'}), 400
    if set_user_role(target, role):
        action_desc = '被设为管理员' if role == 'admin' else '被撤销管理员权限'
        # 通知所有管理员
        admins = get_all_admins()
        for admin_name in admins:
            if admin_name != user['username']:
                create_notification(admin_name, 'admin_change', '管理员变动',
                    f'{user["username"]} 已将 {target} {action_desc}', '/controller')
        # 通知目标用户
        create_notification(target, 'admin_change', '权限变更',
            f'您已被 {user["username"]} {action_desc}', '/controller')
        return jsonify({'ok': True, 'username': target, 'role': role})
    return jsonify({'error': '操作失败，用户不存在或角色不可更改'}), 400


@app.route('/api/admin/list')
@login_required
def api_admin_list():
    """获取管理员名录（所有登录用户可查看）"""
    admin_names = get_all_admins()
    result = []
    for name in admin_names:
        role = get_user_role(name)
        result.append({'username': name, 'role': role})
    return jsonify(result)


@app.route('/api/admin/delete_user', methods=['POST'])
@login_required
def api_admin_delete_user():
    """删除用户（仅超级管理员可操作）"""
    user = get_current_user()
    if not is_super_admin(user['username']):
        return jsonify({'error': '无权限，仅超级管理员可操作'}), 403
    data = request.get_json() or {}
    target = data.get('username', '').strip()
    keep_files = data.get('keep_files', False)
    if not target:
        return jsonify({'error': '缺少目标用户名'}), 400
    if target == SUPER_ADMIN_USER:
        return jsonify({'error': '不能删除超级管理员账号'}), 400
    ok, msg = delete_user(target, keep_files)
    if ok:
        # 通知所有管理员
        admins = get_all_admins()
        for admin_name in admins:
            if admin_name != user['username']:
                create_notification(admin_name, 'admin_change', '用户已删除',
                    f'{user["username"]} 删除了用户 {target}', '/controller')
        return jsonify({'ok': True, 'msg': msg})
    return jsonify({'error': msg}), 400


# ==================== 白名单审批 API ====================

@app.route('/api/whitelist/apply', methods=['POST'])
@login_required
def api_whitelist_apply():
    """普通用户提交白名单申请"""
    user = get_current_user()
    if is_whitelisted(user["username"]):
        return jsonify({'error': '您已在白名单中'}), 400
    data = request.get_json() or {}
    reason = (data.get("reason") or "").strip()
    app_id, err = create_application(user["username"], reason)
    if err:
        return jsonify({"error": err}), 400
    admins = get_all_admins()
    # 通知所有管理员有新申请
    for admin_name in admins:
        if admin_name != user["username"]:
            create_notification(admin_name, 'whitelist_apply', '新白名单申请',
                f'用户 {user["username"]} 提交了白名单申请', '/controller')
    return jsonify({"ok": True, "application_id": app_id,
                        "admin_count": len(admins),
                        "msg": f"申请已提交，需 {len(admins)} 位管理员审批通过后即可加入白名单"})

@app.route('/api/whitelist/my_application')
@login_required
def api_my_application():
    """查看当前用户自己的申请状态"""
    user = get_current_user()
    app = get_my_application(user["username"])
    if app is None:
        return jsonify({'status': 'none'})
    approvals = get_application_approvals(app["id"])
    return jsonify({"status": app["status"],
                        "reason": app.get("reason", ""),
                        "created_at": app.get("created_at", ""),
                        "resolved_at": app.get("resolved_at", ""),
                        "approvals": approvals,
                        "is_whitelisted": is_whitelisted(user["username"]),})

@app.route('/api/whitelist/pending')
@login_required
def api_whitelist_pending():
    """管理员查看待审批列表"""
    user = get_current_user()
    if not is_admin(user["username"]):
        return jsonify({'error': '无权限'}), 403
    apps = get_pending_applications()
    admin_count = len(get_all_admins())
    result = []
    for a in apps:
        a["admin_count"] = admin_count
        a["approvals"] = get_application_approvals(a["id"])
        result.append(a)
    return jsonify({"applications": result})

@app.route('/api/whitelist/approve', methods=['POST'])
@login_required
def api_whitelist_approve():
    """管理员同意白名单申请"""
    user = get_current_user()
    if not is_admin(user["username"]):
        return jsonify({'error': '无权限'}), 403
    data = request.get_json() or {}
    app_id = data.get("application_id")
    if not app_id:
        return jsonify({'error': '缺少 application_id'}), 400
    ok, msg = submit_approval(int(app_id), user["username"], "approve")
    return jsonify({"ok": ok, "msg": msg})

@app.route('/api/whitelist/reject', methods=['POST'])
@login_required
def api_whitelist_reject():
    """管理员拒绝白名单申请"""
    user = get_current_user()
    if not is_admin(user["username"]):
        return jsonify({'error': '无权限'}), 403
    data = request.get_json() or {}
    app_id = data.get("application_id")
    if not app_id:
        return jsonify({'error': '缺少 application_id'}), 400
    ok, msg = submit_approval(int(app_id), user["username"], "reject")
    return jsonify({"ok": ok, "msg": msg})


# ==================== 站内消息 API ====================

@app.route('/messages')
@login_required
def messages_page():
    """消息中心页面"""
    return render_template('messages.html', username=session.get('username'))


@app.route('/api/messages/send', methods=['POST'])
@login_required
def api_message_send():
    """发送站内消息"""
    user = get_current_user()
    data = request.get_json() or {}
    recipient = data.get('recipient', '').strip()
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()
    attachment_name = data.get('attachment_name', '').strip()
    attachment_path = data.get('attachment_path', '').strip()
    if not recipient or not body:
        return jsonify({'error': '收件人和内容不能为空'}), 400
    if recipient == user['username']:
        return jsonify({'error': '不能给自己发消息'}), 400
    # 验证附件路径安全性
    if attachment_path:
        abs_path = Path(attachment_path).resolve()
        if not str(abs_path).startswith(str(_ATTACHMENTS_DIR.resolve())):
            return jsonify({'error': '附件路径不合法'}), 400
    msg_id, err = send_message(user['username'], recipient, subject, body,
                               attachment_name, attachment_path)
    if err:
        return jsonify({'error': err}), 500
    # 通知收件人
    notif_msg = f'{user["username"]} 给您发送了一条消息'
    if attachment_name:
        notif_msg += f'（附带文件：{attachment_name}）'
    create_notification(recipient, 'message', '新消息', notif_msg, '/messages')
    return jsonify({'ok': True, 'msg_id': msg_id})


@app.route('/api/messages/upload_attachment', methods=['POST'])
@login_required
def api_upload_attachment():
    """上传私信附件到隐藏目录"""
    user = get_current_user()
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': '未选择文件'}), 400
    # UUID 重命名，保留原始扩展名
    ext = Path(file.filename).suffix
    stored_name = f'{uuid.uuid4().hex}{ext}'
    stored_path = _ATTACHMENTS_DIR / stored_name
    file.save(str(stored_path))
    return jsonify({
        'ok': True,
        'filename': file.filename,
        'stored_path': str(stored_path),
    })


@app.route('/api/messages/attachment_download')
@login_required
def api_attachment_download():
    """下载私信附件（仅发送者和接收者）"""
    user = get_current_user()
    msg_id = request.args.get('msg_id', type=int)
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    msg = get_message(msg_id, user['username'])
    if not msg:
        return jsonify({'error': '消息不存在'}), 404
    if not msg.get('attachment_path') or not msg.get('attachment_name'):
        return jsonify({'error': '该消息无附件'}), 400
    abs_path = Path(msg['attachment_path']).resolve()
    if not abs_path.exists():
        return jsonify({'error': '附件已被领取或已过期'}), 404
    if not str(abs_path).startswith(str(_ATTACHMENTS_DIR.resolve())):
        return jsonify({'error': '路径不合法'}), 400
    return send_from_directory(
        str(abs_path.parent), abs_path.name,
        as_attachment=True, download_name=msg['attachment_name']
    )


@app.route('/api/messages/attachment_move', methods=['POST'])
@login_required
def api_attachment_move():
    """将私信附件移动到用户网盘（源文件消失）"""
    user = get_current_user()
    data = request.get_json() or {}
    msg_id = data.get('msg_id')
    dest_dir = data.get('dest_dir', '').strip()
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    msg = get_message(int(msg_id), user['username'])
    if not msg:
        return jsonify({'error': '消息不存在'}), 404
    if not msg.get('attachment_path') or not msg.get('attachment_name'):
        return jsonify({'error': '该消息无附件'}), 400
    src_path = Path(msg['attachment_path']).resolve()
    if not src_path.exists():
        return jsonify({'error': '附件已被领取或已过期'}), 404
    if not str(src_path).startswith(str(_ATTACHMENTS_DIR.resolve())):
        return jsonify({'error': '路径不合法'}), 400
    # 目标目录：默认移到用户网盘根目录
    if not dest_dir:
        dest_dir = str(get_user_dir(user['username']))
    dest_path = Path(dest_dir).resolve()
    if not str(dest_path).startswith(str(get_user_dir(user['username']).resolve())):
        return jsonify({'error': '只能移动到自己的网盘'}), 403
    # 确保目标目录存在
    dest_path.mkdir(parents=True, exist_ok=True)
    target_file = dest_path / msg['attachment_name']
    # 如果同名文件已存在，加序号
    if target_file.exists():
        stem = target_file.stem
        suffix = target_file.suffix
        i = 1
        while target_file.exists():
            target_file = dest_path / f'{stem}_{i}{suffix}'
            i += 1
    shutil.move(str(src_path), str(target_file))
    # 更新消息中的附件路径为空（标记为已领取）
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("UPDATE messages SET attachment_path = '' WHERE id = ?", (int(msg_id),))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'dest': str(target_file)})


@app.route('/api/messages/inbox')
@login_required
def api_messages_inbox():
    """收件箱"""
    user = get_current_user()
    msgs = get_inbox(user['username'])
    return jsonify(msgs)


@app.route('/api/messages/sent')
@login_required
def api_messages_sent():
    """已发送"""
    user = get_current_user()
    msgs = get_sent(user['username'])
    return jsonify(msgs)


@app.route('/api/messages/unread_count')
@login_required
def api_messages_unread_count():
    """未读消息数"""
    user = get_current_user()
    cnt = get_unread_message_count(user['username'])
    return jsonify({'count': cnt})


@app.route('/api/messages/read', methods=['POST'])
@login_required
def api_message_read():
    """标记消息已读"""
    user = get_current_user()
    data = request.get_json() or {}
    msg_id = data.get('msg_id')
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    ok = mark_message_read(int(msg_id), user['username'])
    return jsonify({'ok': ok})


@app.route('/api/messages/delete', methods=['POST'])
@login_required
def api_message_delete():
    """删除消息"""
    user = get_current_user()
    data = request.get_json() or {}
    msg_id = data.get('msg_id')
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    try:
        ok = delete_message(int(msg_id), user['username'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': ok})


# ==================== 系统通知 API ====================

@app.route('/api/notifications')
@login_required
def api_notifications():
    """获取通知列表"""
    user = get_current_user()
    notifs = get_notifications(user['username'])
    return jsonify(notifs)


@app.route('/api/notifications/unread_count')
@login_required
def api_notifications_unread_count():
    """未读通知数"""
    user = get_current_user()
    cnt = get_unread_notification_count(user['username'])
    return jsonify({'count': cnt})


@app.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notification_read():
    """标记通知已读"""
    user = get_current_user()
    data = request.get_json() or {}
    notif_id = data.get('notif_id')
    if not notif_id:
        return jsonify({'error': '缺少 notif_id'}), 400
    ok = mark_notification_read(int(notif_id), user['username'])
    return jsonify({'ok': ok})


@app.route('/api/notifications/read_all', methods=['POST'])
@login_required
def api_notifications_read_all():
    """全部已读"""
    user = get_current_user()
    ok = mark_all_notifications_read(user['username'])
    return jsonify({'ok': ok})


@app.route('/api/notifications/delete', methods=['POST'])
@login_required
def api_notification_delete():
    """删除单条通知"""
    user = get_current_user()
    data = request.get_json() or {}
    notif_id = data.get('notif_id')
    if not notif_id:
        return jsonify({'error': '缺少 notif_id'}), 400
    try:
        ok = delete_notification(int(notif_id), user['username'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': ok})


@app.route('/api/notifications/delete_all', methods=['POST'])
@login_required
def api_notifications_delete_all():
    """删除所有通知"""
    user = get_current_user()
    try:
        deleted = delete_all_notifications(user['username'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'deleted': deleted})


@app.route('/api/users/list')
@login_required
def api_users_list():
    """获取所有用户列表（供发消息选择收件人）"""
    user = get_current_user()
    admins = get_all_admins()
    all_users = get_all_users()
    result = []
    for u in all_users:
        if u['username'] == user['username']:
            continue  # 排除自己
        result.append({
            'username': u['username'],
            'role': u['role'],
        })
    return jsonify(result)


@app.route('/api/visitors')
@login_required
def api_visitors():
    """访问者记录 - 仅白名单用户可查看"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    with VISITORS_LOCK:
        data = list(reversed(VISITORS[-100:]))
    return jsonify(data)


@app.route('/api/traffic')
@login_required
def api_traffic():
    """用户流量统计 - 仅白名单用户可查看"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    with _USER_TRAFFIC_LOCK:
        result = []
        for username, t in _USER_TRAFFIC.items():
            result.append({
                'username': username,
                'upload': t['upload'],
                'download': t['download'],
                'upload_fmt': format_size(t['upload']),
                'download_fmt': format_size(t['download']),
                'total_fmt': format_size(t['upload'] + t['download']),
                'requests': t['requests'],
            })
    result.sort(key=lambda x: x['upload'] + x['download'], reverse=True)
    return jsonify(result)


# ==================== 服务器启停 ====================

@app.before_request
def check_server_status():
    with SERVER_LOCK:
        if not SERVER_STOPPED:
            return
    allowed = ['/api/start', '/api/status', '/controller']
    if request.path in allowed or request.path.startswith('/output/') or request.path.startswith('/uploads/'):
        return
    if request.path.startswith('/api/'):
        return jsonify({'error': 'server stopped', 'stopped': True}), 503
    # 返回简单的暂停提示页（内联，不依赖模板文件）
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


@app.route('/api/shutdown', methods=['POST'])
def shutdown():
    global SERVER_STOPPED
    with SERVER_LOCK:
        SERVER_STOPPED = True
    return jsonify({'status': 'stopped'})


@app.route('/api/start', methods=['POST'])
def start_server():
    global SERVER_STOPPED
    with SERVER_LOCK:
        SERVER_STOPPED = False
    return jsonify({'status': 'running'})


@app.route('/api/status')
def api_status():
    with SERVER_LOCK:
        return jsonify({'stopped': SERVER_STOPPED})


# ==================== 文件上传与运行 ====================

@app.route('/api/showcase_status')
def api_showcase_status():
    """公开的服务器性能展示接口（无需登录）"""
    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    if not hasattr(api_showcase_status, '_cpu_name'):
        api_showcase_status._cpu_name = ''
        try:
            r = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip() and l.strip() != 'Name']
                if lines:
                    api_showcase_status._cpu_name = lines[0]
        except Exception:
            try:
                api_showcase_status._cpu_name = platform.processor() or ''
            except Exception:
                pass
    cpu_name = api_showcase_status._cpu_name

    mem = psutil.virtual_memory()

    gpus = detect_gpus()
    for g in gpus:
        g['mem_percent'] = round(g['mem_used'] / g['mem_total'] * 100, 1) if g['mem_total'] > 0 else 0

    return jsonify({
        'cpu': {
            'name': cpu_name,
            'percent': cpu_percent,
            'count': cpu_count,
            'freq': f"{cpu_freq.current:.0f}MHz" if cpu_freq else 'N/A',
        },
        'memory': {
            'percent': mem.percent,
            'total': format_size(mem.total),
            'used': format_size(mem.used),
            'available': format_size(mem.available),
        },
        'gpus': gpus,
    })


def _whitelist_denied():
    """白名单未通过的拒绝页面 — 服务器性能展示"""
    return render_template('showcase.html')


@app.route('/showcase')
@login_required
def showcase():
    """性能展示页面（非白名单用户可见）"""
    return render_template('showcase.html')


@app.route('/upload')
@login_required
def upload_page():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return _whitelist_denied()
    user_dir = get_user_dir(user['username'])
    return render_template(
        'upload.html',
        sys_exec=sys.executable,
        torch_env=TORCH_PYTHON or '',
        work_dir=str(WORK_DIR.resolve()),
        user_dir=str(user_dir.resolve()),
        root_dir=str(WORK_DIR.resolve()),
        username=user['username']
    )


@app.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    user = get_current_user()
    user_uploads_dir = get_user_uploads_dir(user['username'])
    user_uploads_dir.mkdir(parents=True, exist_ok=True)

    if 'file' not in request.files:
        return jsonify({'error': '请求中没有文件字段，请检查表单提交方式'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择任何文件'}), 400

    # 可选：指定上传目标目录（支持相对路径，相对于用户目录）
    target_dir = request.form.get('target_dir', '')
    if target_dir:
        user_dir = get_user_dir(user['username']).resolve()
        candidate = (user_dir / target_dir).resolve()
        if str(candidate).startswith(str(user_dir)) and is_path_allowed(str(candidate), user['username']):
            save_dir = candidate
        elif is_path_allowed(target_dir, user['username']):
            save_dir = Path(target_dir)
        else:
            return jsonify({'error': f'无权访问目录：{target_dir}'}), 403
    else:
        save_dir = user_uploads_dir

    try:
        save_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return jsonify({'error': f'创建目录失败：{save_dir} — {e}'}), 500

    # 支持文件夹上传：relative_path 包含文件夹结构
    relative_path = request.form.get('relative_path', '')
    if relative_path:
        # relative_path 形如 "folder/subfolder/file.txt"
        # 去掉文件名，只保留目录部分
        rel_dir = os.path.dirname(relative_path)
        if rel_dir:
            save_dir = save_dir / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)

    safe_name = os.path.basename(file.filename)
    save_path = save_dir / safe_name
    content_length = request.content_length or 0
    max_size = app.config.get('MAX_CONTENT_LENGTH', 0)

    print(f'[UPLOAD] 用户={user["username"]} 文件={safe_name} 大小={content_length} 限制={max_size} 目标={save_path}')

    if max_size and content_length > max_size:
        return jsonify({'error': f'文件太大：{content_length / 1024 / 1024:.1f}MB，超过限制 {max_size / 1024 / 1024:.0f}MB'}), 413

    # 写入文件（file.save 内部会流式写入）
    written = 0
    try:
        file.save(str(save_path))
        written = save_path.stat().st_size
    except IOError as e:
        print(f'[UPLOAD] 写入失败：{e}')
        return jsonify({'error': f'磁盘写入失败：{e}'}), 500
    except Exception as e:
        print(f'[UPLOAD] 未知错误：{type(e).__name__}: {e}')
        return jsonify({'error': f'上传异常：{type(e).__name__}: {e}'}), 500

    print(f'[UPLOAD] 完成：{safe_name}，写入 {written / 1024 / 1024:.1f}MB')
    return jsonify({
        'status': 'ok',
        'filename': safe_name,
        'file_path': str(save_path.resolve()),
        'size': written,
        'size_fmt': f'{written / 1024 / 1024:.1f}MB',
    })


# ==================== 切片上传（大文件） ====================
_UPLOAD_CHUNKS_DIR = WORK_DIR / '_upload_chunks'
_UPLOAD_CHUNKS_DIR.mkdir(exist_ok=True)

# ==================== 私信附件目录 ====================
_ATTACHMENTS_DIR = WORK_DIR / '_message_attachments'
_ATTACHMENTS_DIR.mkdir(exist_ok=True)

@app.route('/api/upload_chunk', methods=['POST'])
@login_required
def upload_chunk():
    """接收单个切片"""
    user = get_current_user()
    chunk = request.files.get('chunk')
    if not chunk:
        return jsonify({'error': '没有切片数据'}), 400

    upload_id = request.form.get('upload_id', '')
    chunk_index = request.form.get('chunk_index', '')
    filename = request.form.get('filename', 'unknown')
    if not upload_id or chunk_index == '':
        return jsonify({'error': '缺少 upload_id 或 chunk_index'}), 400

    # 每个用户的切片临时目录
    chunk_dir = _UPLOAD_CHUNKS_DIR / user['username'] / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunk_path = chunk_dir / f'{chunk_index}.part'
    try:
        chunk.save(str(chunk_path))
    except Exception as e:
        return jsonify({'error': f'切片 {chunk_index} 写入失败：{e}'}), 500

    size = chunk_path.stat().st_size
    print(f'[CHUNK] 用户={user["username"]} 文件={filename} 切片={chunk_index} 大小={size / 1024 / 1024:.1f}MB')
    return jsonify({'status': 'ok', 'chunk_index': int(chunk_index), 'size': size})


@app.route('/api/upload_complete', methods=['POST'])
@login_required
def upload_complete():
    """合并所有切片为最终文件"""
    user = get_current_user()
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效请求'}), 400

    upload_id = data.get('upload_id', '')
    filename = data.get('filename', 'unknown')
    total_chunks = data.get('total_chunks', 0)
    target_dir = data.get('target_dir', '')

    if not upload_id or total_chunks == 0:
        return jsonify({'error': '缺少参数'}), 400

    chunk_dir = _UPLOAD_CHUNKS_DIR / user['username'] / upload_id
    if not chunk_dir.exists():
        return jsonify({'error': '切片目录不存在'}), 400

    # 目标目录
    if target_dir:
        user_dir = get_user_dir(user['username']).resolve()
        candidate = (user_dir / target_dir).resolve()
        if str(candidate).startswith(str(user_dir)) and is_path_allowed(str(candidate), user['username']):
            save_dir = candidate
        elif is_path_allowed(target_dir, user['username']):
            save_dir = Path(target_dir)
        else:
            return jsonify({'error': f'无权访问目录：{target_dir}'}), 403
    else:
        save_dir = get_user_uploads_dir(user['username'])
    save_dir.mkdir(parents=True, exist_ok=True)

    safe_name = os.path.basename(filename)
    save_path = save_dir / safe_name

    # 支持文件夹上传：relative_path 包含文件夹结构
    relative_path = data.get('relative_path', '')
    if relative_path:
        rel_dir = os.path.dirname(relative_path)
        if rel_dir:
            save_dir = save_dir / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / safe_name

    # 合并切片
    written = 0
    try:
        with open(str(save_path), 'wb') as out:
            for i in range(total_chunks):
                part = chunk_dir / f'{i}.part'
                if not part.exists():
                    return jsonify({'error': f'缺少切片 {i}，上传不完整'}), 400
                with open(str(part), 'rb') as inp:
                    while True:
                        buf = inp.read(4 * 1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
                        written += len(buf)
    except Exception as e:
        return jsonify({'error': f'合并失败：{e}'}), 500

    # 清理切片
    try:
        shutil.rmtree(str(chunk_dir))
    except Exception:
        pass

    print(f'[UPLOAD] 切片合并完成：{safe_name}，共 {written / 1024 / 1024:.1f}MB，{total_chunks} 个切片')
    return jsonify({
        'status': 'ok',
        'filename': safe_name,
        'file_path': str(save_path.resolve()),
        'size': written,
        'size_fmt': f'{written / 1024 / 1024:.1f}MB',
    })


@app.route('/api/run_upload/<filename>')
def run_upload(filename):
    safe_name = os.path.basename(filename)
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists():
        return jsonify({'error': '文件不存在'}), 404

    env_choice = request.args.get('env', 'system')
    python_exe = sys.executable
    use_conda = False
    if env_choice == 'torch':
        if TORCH_PYTHON:
            python_exe = TORCH_PYTHON
        else:
            use_conda = True

    # 新增：允许用户选择工作目录
    work_dir_choice = request.args.get('work_dir', 'project')  # 'project' 或 'upload'
    if work_dir_choice == 'project':
        run_cwd = str(WORK_DIR)
    else:
        run_cwd = str(UPLOAD_DIR)

    def generate():
        try:
            if use_conda:
                cmd = ['conda', 'run', '-n', 'torch', 'python', str(file_path)]
            else:
                cmd = [python_exe, str(file_path)]

            proc = subprocess.Popen(
                cmd,
                cwd=run_cwd,  # 使用用户选择的工作目录
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=get_env(),
                text=True,
                encoding='utf-8', errors='replace',
                bufsize=1,
            )
            with _upload_lock:
                _upload_running[safe_name] = proc

            yield 'data: ' + json.dumps({'type': 'status', 'msg': 'started'}) + '\n\n'

            for line in proc.stdout:
                data = json.dumps({'type': 'stdout', 'msg': line.rstrip()})
                yield f'data: {data}\n\n'

            proc.wait()
            rc = proc.returncode

            generated = []
            for f in sorted(UPLOAD_DIR.iterdir()):
                if f.suffix in ('.png', '.jpg', '.jpeg', '.bmp',
                                  '.mat', '.pt', '.csv', '.txt', '.pdf'):
                    generated.append(f.name)

            done = json.dumps({
                'type': 'done',
                'return_code': rc,
                'files': generated,
            })
            yield f'data: {done}\n\n'

        except Exception as e:
            err = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err}\n\n'
        finally:
            with _upload_lock:
                _upload_running.pop(safe_name, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/upload_files')
@login_required
def api_upload_files():
    user = get_current_user()
    user_uploads_dir = get_user_uploads_dir(user['username'])
    user_dir = get_user_dir(user['username'])

    # 只返回进入编辑器后（since时间戳之后）修改/创建的文件
    since = request.args.get('since', type=float, default=0)
    # 可选：指定要扫描的目录
    scan_path = request.args.get('path', '')

    files = []
    seen_names = set()

    # 如果指定了目录，扫描该目录下的新文件
    if scan_path and is_path_allowed(scan_path, user['username']):
        scan_dir = Path(scan_path)
        if scan_dir.exists() and scan_dir.is_dir():
            for f in sorted(scan_dir.iterdir()):
                if f.name.startswith('.') or f.name.startswith('__'):
                    continue
                if f.is_file():
                    if since and f.stat().st_mtime < since:
                        continue
                    if f.name not in seen_names:
                        seen_names.add(f.name)
                        files.append({
                            'name': f.name,
                            'size': f.stat().st_size,
                            'mtime': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                            'location': 'scan',
                            'path': str(f),
                        })

    # 用户上传目录中的文件
    if user_uploads_dir.exists():
        for f in sorted(user_uploads_dir.iterdir()):
            if f.name.endswith('.py'):
                continue
            if since and f.stat().st_mtime < since:
                continue
            if f.name not in seen_names:
                seen_names.add(f.name)
                files.append({
                    'name': f.name,
                    'size': f.stat().st_size,
                    'mtime': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'location': 'upload'
                })
    # 用户目录中的图片/数据文件（排除脚本和隐藏文件）
    if user_dir.exists():
        for f in sorted(user_dir.iterdir()):
            if f.suffix in ('.py', '.pyc', '.bat', '.ipynb') or f.name.startswith('.'):
                continue
            if f.is_file():
                if since and f.stat().st_mtime < since:
                    continue
                if f.name not in seen_names:
                    seen_names.add(f.name)
                    files.append({
                        'name': f.name,
                        'size': f.stat().st_size,
                        'mtime': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'location': 'user'
                    })
    return jsonify(files)


@app.route('/uploads/<path:filename>')
def serve_upload_file(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)


@app.route('/api/user_file/<path:filename>')
@login_required
def serve_user_file(filename):
    """提供用户目录下的文件"""
    user = get_current_user()
    user_dir = get_user_dir(user['username'])
    file_path = user_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return jsonify({'error': '文件不存在'}), 404
    return send_from_directory(str(user_dir), filename)


@app.route('/api/stop_upload/<filename>', methods=['POST'])
def stop_upload(filename):
    safe = os.path.basename(filename)
    with _upload_lock:
        proc = _upload_running.get(safe)
        if proc is None:
            return jsonify({'error': '未在运行'}), 404
        if sys.platform == 'win32':
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        _upload_running.pop(safe, None)
    return jsonify({'status': 'stopped'})

# ==================== Python 环境检测 ====================

_env_cache = {'data': None, 'time': 0}
_env_cache_lock = threading.Lock()
_ENV_CACHE_TTL = 60  # 缓存有效期（秒）

def _refresh_env_cache():
    """后台刷新环境缓存"""
    try:
        result = _detect_python_environments_full()
        with _env_cache_lock:
            _env_cache['data'] = result
            _env_cache['time'] = time.time()
    except Exception:
        pass

def detect_python_environments():
    """检测系统中所有可用的 Python 环境（带缓存）"""
    now = time.time()
    with _env_cache_lock:
        cached = _env_cache['data']
        age = now - _env_cache['time']
    if cached is not None and age < _ENV_CACHE_TTL:
        return cached
    # 缓存过期或不存在：先返回快检测结果，后台刷新完整结果
    if cached is not None:
        threading.Thread(target=_refresh_env_cache, daemon=True).start()
        return cached
    # 首次调用：阻塞获取完整结果
    result = _detect_python_environments_full()
    with _env_cache_lock:
        _env_cache['data'] = result
        _env_cache['time'] = time.time()
    return result

def _detect_python_environments_full():
    """检测系统中所有可用的 Python 环境"""
    environments = []
    
    # 1. 当前系统 Python
    environments.append({
        'name': '系统 Python',
        'path': sys.executable,
        'type': 'system',
        'description': f'当前系统 Python ({sys.version_info.major}.{sys.version_info.minor})'
    })
    
    # 2. 检查常见的 Python 安装路径
    common_paths = [
        Path(r'C:\Python39\python.exe'),
        Path(r'C:\Python310\python.exe'),
        Path(r'C:\Python311\python.exe'),
        Path(r'C:\Python312\python.exe'),
        Path(r'C:\Python313\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python39\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python310\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python311\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python312\python.exe'),
    ]
    
    for path in common_paths:
        if path.exists() and str(path) != sys.executable:
            try:
                result = subprocess.run(
                    [str(path), '--version'],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=0x08000000 if os.name == 'nt' else 0,
                )
                version = result.stdout.strip() or result.stderr.strip()
                environments.append({
                    'name': f'Python ({path.parent.parent.name})',
                    'path': str(path),
                    'type': 'system',
                    'description': version
                })
            except Exception:
                pass
    
    # 3. PyTorch 环境
    if TORCH_PYTHON:
        try:
            result = subprocess.run(
                [TORCH_PYTHON, '--version'],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000 if os.name == 'nt' else 0,
            )
            version = result.stdout.strip() or result.stderr.strip()
            environments.append({
                'name': 'PyTorch 环境',
                'path': TORCH_PYTHON,
                'type': 'torch',
                'description': f'PyTorch 环境 - {version}'
            })
        except Exception:
            environments.append({
                'name': 'PyTorch 环境',
                'path': TORCH_PYTHON,
                'type': 'torch',
                'description': 'PyTorch 环境 (torch)'
            })
    
    # 4. 检查 conda 环境（如果 conda 可用）
    try:
        result = subprocess.run(
            ['conda', 'env', 'list', '--json'],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        if result.returncode == 0:
            conda_envs = json.loads(result.stdout)
            for env_path in conda_envs.get('envs', []):
                python_path = Path(env_path) / 'python.exe'
                if python_path.exists():
                    env_name = Path(env_path).name
                    try:
                        ver_result = subprocess.run(
                        [str(python_path), '--version'],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        creationflags=0x08000000 if os.name == 'nt' else 0,
                    )
                        version = ver_result.stdout.strip() or ver_result.stderr.strip()
                    except Exception:
                        version = 'unknown'
                    
                    environments.append({
                        'name': f'Conda: {env_name}',
                        'path': str(python_path),
                        'type': 'conda',
                        'description': f'Conda 环境 {env_name} - {version}'
                    })
    except Exception:
        pass

    # 5. WSL Python 环境
    try:
        result = subprocess.run(
            ['wsl.exe', 'bash', '-c', 'which python3 python 2>/dev/null | head -1'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            wsl_python = result.stdout.strip()
            ver_result = subprocess.run(
                ['wsl.exe', 'bash', '-c', f'{wsl_python} --version 2>&1'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
                creationflags=0x08000000 if platform.system() == 'Windows' else 0,
            )
            version = ver_result.stdout.strip() or 'WSL Python'
            environments.append({
                'name': 'WSL Python',
                'path': 'wsl:' + wsl_python,
                'type': 'wsl',
                'description': f'WSL - {version}'
            })
        # 检查 WSL conda 环境
        conda_result = subprocess.run(
            ['wsl.exe', 'bash', '-c', 'conda env list --json 2>/dev/null'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
        )
        if conda_result.returncode == 0 and conda_result.stdout.strip():
            try:
                conda_data = json.loads(conda_result.stdout)
                for env_path in conda_data.get('envs', []):
                    env_name = env_path.rstrip('/').split('/')[-1]
                    py_path = env_path + '/bin/python'
                    environments.append({
                        'name': f'WSL Conda: {env_name}',
                        'path': 'wsl:' + py_path,
                        'type': 'wsl',
                        'description': f'WSL Conda 环境 {env_name}'
                    })
            except Exception:
                pass
    except Exception:
        pass

    # 6. MATLAB / Octave 环境
    for cmd, name in [('matlab', 'MATLAB'), ('octave', 'GNU Octave')]:
        try:
            # Windows 上用 where 查找，Linux/Mac 用 which
            find_cmd = 'where' if platform.system() == 'Windows' else 'which'
            result = subprocess.run(
                [find_cmd, cmd],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace',
                creationflags=0x08000000 if platform.system() == 'Windows' else 0,
            )
            if result.returncode == 0 and result.stdout.strip():
                exe_path = result.stdout.strip().split('\n')[0].strip().strip('"')
                # 获取版本：从安装路径推断，避免启动 MATLAB 导致超时/崩溃
                version = 'installed'
                if cmd == 'matlab':
                    # 尝试从路径中提取版本号，如 R2022a
                    import re as _re
                    ver_match = _re.search(r'R\d{4}[ab]', exe_path)
                    if ver_match:
                        version = ver_match.group(0)
                elif cmd == 'octave':
                    try:
                        ver_result = subprocess.run(
                            [exe_path, '--version'],
                            capture_output=True, text=True, timeout=5,
                            encoding='utf-8', errors='replace',
                            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
                        )
                        if ver_result.returncode == 0 and ver_result.stdout.strip():
                            version = ver_result.stdout.strip().split('\n')[0]
                    except Exception:
                        pass
                environments.append({
                    'name': name,
                    'path': exe_path,
                    'type': 'matlab' if cmd == 'matlab' else 'octave',
                    'description': f'{name} - {version}'
                })
        except Exception:
            pass

    return environments


@app.route('/api/python_environments')
def api_python_environments():
    """返回所有检测到的 Python 环境"""
    environments = detect_python_environments()
    return jsonify({'environments': environments})


def format_size(b):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


def format_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f'{h}小时{m}分钟'
    return f'{m}分钟'


@app.route('/api/system_status')
@login_required
def api_system_status():
    """返回系统状态信息"""
    user = get_current_user()
    username = user['username']

    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    # 内存
    mem = psutil.virtual_memory()

    # 磁盘
    disk = psutil.disk_usage('/')

    # GPU 信息（通过 torch 环境检测）
    gpu_info = None
    try:
        result = subprocess.run(
            [TORCH_PYTHON or sys.executable, '-c',
             'import torch;print(torch.cuda.is_available());'
             'print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "");'
             'print(torch.cuda.get_device_properties(0).total_memory if torch.cuda.is_available() else 0);'
             'print(torch.cuda.memory_allocated(0) if torch.cuda.is_available() else 0);'
             'print(torch.cuda.memory_reserved(0) if torch.cuda.is_available() else 0)'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if lines[0] == 'True':
                gpu_info = {
                    'name': lines[1],
                    'vram_total': format_size(int(lines[2])),
                    'vram_used': format_size(int(lines[3])),
                    'vram_cached': format_size(int(lines[4])),
                    'vram_percent': round(int(lines[3]) / int(lines[2]) * 100, 1) if int(lines[2]) > 0 else 0,
                }
    except Exception:
        pass

    # 用户目录大小
    user_dir = get_user_dir(username)
    user_dir_size = 0
    file_count = 0
    if user_dir.exists():
        for f in user_dir.rglob('*'):
            if f.is_file():
                try:
                    user_dir_size += f.stat().st_size
                    file_count += 1
                except Exception:
                    pass

    # 运行中的进程数
    running_count = 0
    with _user_proc_lock:
        procs = _user_processes.get(username, {})
        running_count = sum(1 for p in procs.values() if not p['finished'])

    # Python 环境
    environments = detect_python_environments()

    # 服务器进程信息
    server_proc = psutil.Process(os.getpid())
    server_mem = server_proc.memory_info()
    server_start = server_proc.create_time()
    server_uptime = time.time() - server_start

    # 当前用户在线时长
    login_time = session.get('login_time', time.time())
    online_seconds = time.time() - login_time

    # ==================== 排行榜数据 ====================
    # 扫描所有用户目录
    users_dir = WORK_DIR / 'users'
    leaderboard = []
    if users_dir.exists():
        for user_folder in users_dir.iterdir():
            if not user_folder.is_dir():
                continue
            uname = user_folder.name
            u_size = 0
            u_files = 0
            u_py_count = 0
            for f in user_folder.rglob('*'):
                if f.is_file():
                    try:
                        u_size += f.stat().st_size
                        u_files += 1
                        if f.suffix == '.py':
                            u_py_count += 1
                    except Exception:
                        pass
            leaderboard.append({
                'username': uname,
                'disk_bytes': u_size,
                'disk': format_size(u_size),
                'file_count': u_files,
                'py_count': u_py_count,
            })

    # 排序生成各排行榜
    disk_rank = sorted(leaderboard, key=lambda x: x['disk_bytes'], reverse=True)
    file_rank = sorted(leaderboard, key=lambda x: x['file_count'], reverse=True)
    py_rank = sorted(leaderboard, key=lambda x: x['py_count'], reverse=True)

    return jsonify({
        'cpu': {
            'percent': cpu_percent,
            'count': cpu_count,
            'freq': f'{cpu_freq.current:.0f}MHz' if cpu_freq else '未知',
        },
        'memory': {
            'total': format_size(mem.total),
            'used': format_size(mem.used),
            'available': format_size(mem.available),
            'percent': mem.percent,
        },
        'disk': {
            'total': format_size(disk.total),
            'used': format_size(disk.used),
            'free': format_size(disk.free),
            'percent': disk.percent,
        },
        'gpu': gpu_info,
        'user': {
            'username': username,
            'dir_size': format_size(user_dir_size),
            'file_count': file_count,
            'running_processes': running_count,
            'online_time': format_time(online_seconds),
        },
        'server': {
            'uptime': format_time(server_uptime),
            'memory': format_size(server_mem.rss),
            'python_version': platform.python_version(),
            'platform': platform.platform(),
        },
        'python_environments': environments,
        'leaderboard': {
            'disk': disk_rank,
            'files': file_rank,
            'scripts': py_rank,
        },
    })


@app.route('/api/leaderboard')
@login_required
def api_leaderboard():
    """排行榜数据（轻量接口）"""
    users_dir = WORK_DIR / 'users'
    leaderboard = []
    if users_dir.exists():
        for user_folder in users_dir.iterdir():
            if not user_folder.is_dir():
                continue
            uname = user_folder.name
            u_size = 0
            u_files = 0
            u_py_count = 0
            for f in user_folder.rglob('*'):
                if f.is_file():
                    try:
                        u_size += f.stat().st_size
                        u_files += 1
                        if f.suffix == '.py':
                            u_py_count += 1
                    except Exception:
                        pass
            leaderboard.append({
                'username': uname,
                'disk_bytes': u_size,
                'disk': format_size(u_size),
                'file_count': u_files,
                'py_count': u_py_count,
            })
    disk_rank = sorted(leaderboard, key=lambda x: x['disk_bytes'], reverse=True)
    file_rank = sorted(leaderboard, key=lambda x: x['file_count'], reverse=True)
    py_rank = sorted(leaderboard, key=lambda x: x['py_count'], reverse=True)
    return jsonify({
        'disk': disk_rank,
        'files': file_rank,
        'scripts': py_rank,
    })


@app.route('/api/gpus')
@login_required
def api_gpus():
    """返回可用 GPU 列表"""
    gpus = detect_gpus()
    return jsonify({'gpus': gpus, 'count': len(gpus)})


@app.route('/api/gpu_status')
@login_required
def api_gpu_status():
    """实时 GPU 状态（供监控面板用）"""
    gpus = detect_gpus()
    for g in gpus:
        g['mem_total_fmt'] = f"{g['mem_total']}MB"
        g['mem_used_fmt'] = f"{g['mem_used']}MB"
        g['mem_free_fmt'] = f"{g['mem_free']}MB"
        g['mem_percent'] = round(g['mem_used'] / g['mem_total'] * 100, 1) if g['mem_total'] > 0 else 0
    return jsonify({'gpus': gpus})


@app.route('/api/performance_status')
@login_required
def api_performance_status():
    """实时性能监控：CPU + 内存 + GPU"""
    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.3)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    # CPU 名称（缓存，不会变）
    if not hasattr(api_performance_status, '_cpu_name'):
        api_performance_status._cpu_name = ''
        try:
            r = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip() and l.strip() != 'Name']
                if lines:
                    api_performance_status._cpu_name = lines[0]
        except Exception:
            try:
                import platform
                api_performance_status._cpu_name = platform.processor() or ''
            except Exception:
                pass
    cpu_name = api_performance_status._cpu_name

    # CPU 温度（Windows WMI）
    cpu_temp = None
    try:
        import wmi
        w = wmi.WMI(namespace=r'root\OpenHardwareMonitor')
        sensors = w.Sensor()
        for s in sensors:
            if s.SensorType == 'Temperature' and 'CPU' in s.Name.upper():
                cpu_temp = float(s.Value)
                break
        if cpu_temp is None:
            # 尝试所有温度传感器取最高
            temps = [float(s.Value) for s in sensors if s.SensorType == 'Temperature']
            if temps:
                cpu_temp = max(temps)
    except Exception:
        pass
    # 备用方案：通过 LibreHardwareMonitor 的 WMI namespace
    if cpu_temp is None:
        try:
            r = subprocess.run(
                ['powershell', '-Command',
                 r"Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace 'root/wmi' | Select -First 1 -ExpandProperty CurrentTemperature"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                # 返回值是十分之一开尔文，转换为摄氏度
                raw = int(r.stdout.strip())
                cpu_temp = round(raw / 10.0 - 273.15, 1)
        except Exception:
            pass

    # 内存
    mem = psutil.virtual_memory()

    # GPU
    gpus = detect_gpus()
    for g in gpus:
        g['mem_percent'] = round(g['mem_used'] / g['mem_total'] * 100, 1) if g['mem_total'] > 0 else 0

    return jsonify({
        'cpu': {
            'name': cpu_name,
            'percent': cpu_percent,
            'count': cpu_count,
            'freq': f"{cpu_freq.current:.0f}MHz" if cpu_freq else 'N/A',
            'temp': cpu_temp,
        },
        'memory': {
            'percent': mem.percent,
            'used': format_size(mem.used),
            'available': format_size(mem.available),
            'total': format_size(mem.total),
            'used_bytes': mem.used,
            'total_bytes': mem.total,
        },
        'gpus': gpus,
    })


# ==================== 运行任意文件 (VS Code 界面) ====================

@app.route('/api/run_file')
@login_required
def run_file():
    """运行服务器上的任意 Python 文件（通过文件路径，仅限用户目录）"""
    user = get_current_user()
    path = request.args.get('path', '')
    python_path = request.args.get('python_path', '')  # 完整的 Python 路径
    env_choice = request.args.get('env', 'system')  # 保留兼容性
    cwd = request.args.get('cwd', '')  # 工作目录（个人云盘当前目录）
    gpu_id = request.args.get('gpu', '')  # GPU 设备 ID
    multi_gpu = request.args.get('multi_gpu', '') == '1'  # 多卡并行
    
    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403
    
    p = Path(path)
    if not p.exists() or not p.is_file():
        return jsonify({'error': '文件不存在'}), 404
    if not p.name.endswith('.py') and not p.name.endswith('.m'):
        return jsonify({'error': '只支持 .py 和 .m 文件'}), 400
    
    with _file_lock:
        if p.name in _file_running:
            return jsonify({'error': '该文件正在运行中'}), 409
    
    def generate():
        proc = None
        drain_thread = None
        output_queue = queue.Queue()
        username = user['username']
        filename = p.name
        try:
            # 优先使用 python_path 参数（完整路径）
            is_wsl = False
            if python_path and python_path.startswith('wsl:'):
                is_wsl = True
                wsl_python = python_path[4:]  # 去掉 wsl: 前缀
                python_exe = 'wsl.exe'
            elif python_path:
                python_exe = python_path
            elif env_choice == 'torch':
                if TORCH_PYTHON:
                    python_exe = TORCH_PYTHON
                else:
                    yield 'data: ' + json.dumps({'type': 'error', 'msg': '未找到 PyTorch 环境'}) + '\n\n'
                    return
            elif env_choice and env_choice.startswith('wsl:'):
                is_wsl = True
                wsl_python = env_choice[4:]
                python_exe = 'wsl.exe'
            else:
                python_exe = sys.executable

            # .m 文件（MATLAB/Octave）
            is_matlab = p.name.endswith('.m')
            matlab_exe = None
            if is_matlab:
                # python_path 参数可能是 matlab 或 octave 的路径
                if python_path and os.path.basename(python_path).lower().startswith('matlab'):
                    matlab_exe = python_path
                elif python_path and os.path.basename(python_path).lower().startswith('octave'):
                    matlab_exe = python_path
                elif env_choice == 'matlab':
                    matlab_exe = 'matlab'
                elif env_choice == 'octave':
                    matlab_exe = 'octave'
                else:
                    # 自动检测：优先 MATLAB，其次 Octave
                    for cmd in ['matlab', 'octave']:
                        try:
                            find_cmd = 'where' if platform.system() == 'Windows' else 'which'
                            r = subprocess.run([find_cmd, cmd], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3,
                                               creationflags=0x08000000 if platform.system() == 'Windows' else 0)
                            if r.returncode == 0 and r.stdout.strip():
                                matlab_exe = cmd
                                break
                        except Exception:
                            pass
                if not matlab_exe:
                    yield 'data: ' + json.dumps({'type': 'error', 'msg': '未找到 MATLAB 或 Octave，请先安装'}) + '\n\n'
                    return

            # 工作目录：优先使用前端传来的 cwd，否则用用户目录
            user_dir = get_user_dir(username)
            if cwd and is_path_allowed(cwd, username) and Path(cwd).is_dir():
                run_cwd = cwd
            else:
                run_cwd = str(user_dir)

            # .m 文件执行（MATLAB/Octave）
            if is_matlab:
                import tempfile as _tmp
                is_octave = 'octave' in os.path.basename(matlab_exe).lower()
                if is_octave:
                    # Octave: 运行脚本后保存图片到文件
                    _img_dir = str(user_dir / 'config' / '_plots')
                    os.makedirs(_img_dir, exist_ok=True)
                    for _f in os.listdir(_img_dir):
                        try:
                            os.remove(os.path.join(_img_dir, _f))
                        except Exception:
                            pass
                    _img_dir_safe = _img_dir.replace('\\', '/')
                    _plot_wrapper = (
                        f"cd('{run_cwd.replace(chr(92), '/')}'); "
                        f"run('{str(p).replace(chr(92), '/')}'); "
                        "drawnow; "
                        "figs = get(0, 'Children'); "
                        "if ~isempty(figs) "
                        "  for i = 1:length(figs) "
                        "    drawnow; "
                        f"    print(figs(i), fullfile('{_img_dir_safe}', sprintf('plot_%d.png', i)), '-dpng', '-r600'); "
                        "  end "
                        "end"
                    )
                    # 写入临时 .m 文件
                    _tmp_script = _tmp.NamedTemporaryFile(mode='w', suffix='.m', delete=False, dir=run_cwd)
                    _tmp_script.write(_plot_wrapper)
                    _tmp_script.close()
                    cmd = [matlab_exe, '--no-gui', '--no-window-system', _tmp_script.name]
                else:
                    # MATLAB: 把脚本所在目录加入 path，直接调用脚本名
                    script_name = p.stem  # 不含扩展名的文件名
                    script_dir = str(p.parent)
                    # 图片保存目录（Python 预先创建，避免 MATLAB mkdir 问题）
                    _img_dir = str(user_dir / 'config' / '_plots')
                    os.makedirs(_img_dir, exist_ok=True)
                    # 清空旧图片和临时文件
                    for _f in os.listdir(_img_dir):
                        if _f.endswith('.png') or _f.endswith('.tmp') or _f.endswith('.log'):
                            try:
                                os.remove(os.path.join(_img_dir, _f))
                            except Exception:
                                pass
                    # MATLAB 保存图片：用 print -dpng，加 mkdir + try-catch
                    # diary 写日志文件，文件标记代替 stdout 标记
                    _img_dir_mat = _img_dir.replace('/', '\\')
                    _run_id = str(int(time.time() * 1000))
                    _marker_file = os.path.join(_img_dir, f'_done_{_run_id}.tmp').replace('\\', '/')
                    _log_file = os.path.join(_img_dir, f'_output_{_run_id}.log').replace('\\', '/')
                    _matlab_cmd = (
                        f"addpath('{script_dir.replace(chr(92), '/')}'); "
                        f"diary('{_log_file}'); diary on; "
                        f"try, {script_name}; catch e, disp(e.message); end; "
                        f"drawnow; pause(1); "
                        f"figs = findall(0,'Type','figure'); "
                        f"if ~isempty(figs), "
                        f"  if ~isfolder('{_img_dir_mat}'), mkdir('{_img_dir_mat}'); end; "
                        f"  for i = 1:length(figs), "
                        f"    drawnow; "
                        f"    fname = fullfile('{_img_dir_mat}', sprintf('plot_%d.png', i)); "
                        f"    try, "
                        f"      print(figs(i), fname, '-dpng', '-r600'); "
                        f"    catch e2, "
                        f"      disp(['保存图片失败: ' fname ' -> ' e2.message]); "
                        f"    end; "
                        f"  end; "
                        f"end; "
                        f"diary off; "
                        f"f = fopen('{_marker_file}', 'w'); fclose(f); "
                        f"exit"
                    )
                    cmd = [matlab_exe, '-nosplash', '-nodesktop', '-r', _matlab_cmd]
                env = os.environ.copy()
                if gpu_id:
                    env['CUDA_VISIBLE_DEVICES'] = gpu_id
                proc = subprocess.Popen(
                    cmd,
                    cwd=run_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    text=True,
                    encoding='utf-8', errors='replace',
                    bufsize=1,
                    creationflags=0x08000000 if platform.system() == 'Windows' else 0,
                )
                with _file_lock:
                    _file_running[filename] = proc
                output_buffer = collections.deque(maxlen=BUFFER_MAX_LINES)
                with _user_proc_lock:
                    if username not in _user_processes:
                        _user_processes[username] = {}
                    _user_processes[username][filename] = {
                        'proc': proc, 'buffer': output_buffer,
                        'start_time': time.time(), 'path': str(p),
                        'finished': False, 'return_code': None,
                    }
                def drain_matlab():
                    _marker = _marker_file
                    _log = _log_file
                    _read_pos = 0
                    try:
                        # 轮询日志文件 + 等待标记文件
                        while True:
                            # 读取日志文件的新内容
                            if os.path.isfile(_log):
                                try:
                                    with open(_log, 'r', encoding='utf-8', errors='replace') as _f:
                                        _f.seek(_read_pos)
                                        _new = _f.read()
                                        _read_pos = _f.tell()
                                    if _new:
                                        for _line in _new.splitlines():
                                            _line = _line.rstrip()
                                            if _line:
                                                output_buffer.append(_line)
                                                output_queue.put(('stdout', _line))
                                except Exception:
                                    pass
                            # 检查标记文件
                            if os.path.isfile(_marker):
                                try:
                                    os.remove(_marker)
                                except Exception:
                                    pass
                                break
                            time.sleep(0.3)
                    except Exception:
                        pass
                    finally:
                        # 最后再读一次日志
                        if os.path.isfile(_log):
                            try:
                                with open(_log, 'r', encoding='utf-8', errors='replace') as _f:
                                    _f.seek(_read_pos)
                                    _new = _f.read()
                                if _new:
                                    for _line in _new.splitlines():
                                        _line = _line.rstrip()
                                        if _line:
                                            output_buffer.append(_line)
                                            output_queue.put(('stdout', _line))
                            except Exception:
                                pass
                        # 确保进程完全退出
                        try:
                            proc.wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            parent = psutil.Process(proc.pid)
                            for c in parent.children(recursive=True):
                                try:
                                    c.wait(timeout=10)
                                except Exception:
                                    try:
                                        c.kill()
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        rc = proc.returncode if proc.returncode is not None else 0
                        output_queue.put(('done', rc))
                        with _user_proc_lock:
                            if username in _user_processes and filename in _user_processes[username]:
                                _user_processes[username][filename]['finished'] = True
                                _user_processes[username][filename]['return_code'] = rc
                        with _file_lock:
                            _file_running.pop(filename, None)
                        # 清理 Octave 临时脚本
                        try:
                            if '_tmp_script' in dir() and _tmp_script:
                                os.unlink(_tmp_script.name)
                        except Exception:
                            pass
                        try:
                            notify_run_complete(username, filename, rc == 0, rc)
                        except Exception:
                            pass
                drain_thread = threading.Thread(target=drain_matlab, daemon=True)
                drain_thread.start()
                # SSE 输出
                yield 'data: ' + json.dumps({'type': 'start', 'file': filename}) + '\n\n'
                while True:
                    try:
                        msg_type, data = output_queue.get(timeout=0.5)
                        if msg_type == 'done':
                            # MATLAB 已输出完成标记，图片已经写完
                            import base64 as _b64_mod
                            _images = []
                            time.sleep(0.5)
                            if os.path.isdir(_img_dir):
                                for _f in sorted(os.listdir(_img_dir)):
                                    if _f.endswith('.png'):
                                        _fpath = os.path.join(_img_dir, _f)
                                        try:
                                            with open(_fpath, 'rb') as _imgf:
                                                _b64 = _b64_mod.b64encode(_imgf.read()).decode('ascii')
                                                _images.append(_b64)
                                            os.remove(_fpath)
                                        except Exception:
                                            pass
                            # 发送图片输出
                            for _b64 in _images:
                                yield 'data: ' + json.dumps({'type': 'output', 'msg': '<!--IMG:' + _b64 + '-->'}) + '\n\n'
                            # 清理日志文件和图片目录
                            try:
                                if os.path.isfile(_log_file):
                                    os.remove(_log_file)
                            except Exception:
                                pass
                            try:
                                if os.path.isdir(_img_dir):
                                    import shutil as _shutil
                                    _shutil.rmtree(_img_dir, ignore_errors=True)
                            except Exception:
                                pass
                            yield 'data: ' + json.dumps({'type': 'done', 'return_code': data}) + '\n\n'
                            break
                        elif msg_type == 'stdout':
                            yield 'data: ' + json.dumps({'type': 'output', 'msg': data}) + '\n\n'
                    except queue.Empty:
                        yield ': keepalive\n\n'
                return

            # 使用包装脚本，支持 matplotlib 内联显示
            wrapper_code = FILE_RUNNER_WRAPPER.replace(
                "__SCRIPT_PATH_PLACEHOLDER__",
                str(p).replace("\\", "\\\\")
            )
            # 多卡并行：注入 DataParallel 包装
            if multi_gpu:
                dp_inject = r'''
import torch as _t
if _t.cuda.device_count() > 1:
    _orig_to = _t.nn.Module.to
    _dp_set = set()
    def _dp_to(self, *a, **k):
        _orig_to(self, *a, **k)
        try:
            if len(list(self.parameters())) > 0 and id(self) not in _dp_set:
                dev = str(a[0]) if a else str(k.get('device',''))
                if 'cuda' in dev or (not dev and next(self.parameters()).is_cuda):
                    self = _t.nn.DataParallel(self)
                    _dp_set.add(id(self))
                    print(f"[MultiGPU] DataParallel 包装完成，{_t.cuda.device_count()} GPUs")
        except: pass
        return self
    _t.nn.Module.to = _dp_to
    print(f"[MultiGPU] 检测到 {_t.cuda.device_count()} GPUs，已启用自动并行")
else:
    print("[MultiGPU] 仅 1 个 GPU，跳过")
'''
                # 插入到 wrapper_code 的 runpy 之前
                wrapper_code = wrapper_code.replace(
                    'import runpy',
                    dp_inject + '\nimport runpy'
                )
            if is_wsl:
                # WSL 模式：转换路径并用 wsl.exe 执行
                def win_to_wsl(wp):
                    wp = wp.replace('\\', '/')
                    if len(wp) >= 2 and wp[1] == ':':
                        return '/mnt/' + wp[0].lower() + wp[2:]
                    return wp
                wsl_script_path = win_to_wsl(str(p))
                wsl_cwd = win_to_wsl(run_cwd)
                # 修改 wrapper_code 中的 Windows 路径为 WSL 路径
                wrapper_code = wrapper_code.replace(
                    str(p).replace("\\", "\\\\"), wsl_script_path
                ).replace(
                    str(p).replace("\\", "/"), wsl_script_path
                )
                wsl_env = os.environ.copy()
                if gpu_id:
                    wsl_env['CUDA_VISIBLE_DEVICES'] = gpu_id
                proc = subprocess.Popen(
                    ['wsl.exe', wsl_python, '-u', '-c', wrapper_code],
                    cwd=run_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=wsl_env,
                    text=True,
                    encoding='utf-8', errors='replace',
                    bufsize=1,
                    creationflags=0x08000000,
                )
            else:
                proc = subprocess.Popen(
                    [python_exe, '-u', '-c', wrapper_code],
                    cwd=run_cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=get_env(gpu_id),
                    text=True,
                    encoding='utf-8', errors='replace',
                    bufsize=1,
                )
            with _file_lock:
                _file_running[filename] = proc

            # 注册到用户进程表（带输出缓冲）
            output_buffer = collections.deque(maxlen=BUFFER_MAX_LINES)
            with _user_proc_lock:
                if username not in _user_processes:
                    _user_processes[username] = {}
                _user_processes[username][filename] = {
                    'proc': proc,
                    'buffer': output_buffer,
                    'start_time': time.time(),
                    'path': str(p),
                    'finished': False,
                    'return_code': None,
                }

            # 后台线程持续读取管道，写入缓冲和队列
            def drain_pipe():
                try:
                    for line in proc.stdout:
                        line = line.rstrip()
                        output_buffer.append(line)
                        output_queue.put(('stdout', line))
                except Exception:
                    pass
                finally:
                    proc.wait()
                    rc = proc.returncode
                    output_queue.put(('done', rc))
                    with _user_proc_lock:
                        if username in _user_processes and filename in _user_processes[username]:
                            _user_processes[username][filename]['finished'] = True
                            _user_processes[username][filename]['return_code'] = rc
                    with _file_lock:
                        _file_running.pop(filename, None)
                    # 发送 Webhook 通知
                    try:
                        notify_run_complete(username, filename, rc == 0, rc)
                    except Exception:
                        pass
                    # 30分钟后清理该进程记录
                    def cleanup():
                        time.sleep(1800)
                        with _user_proc_lock:
                            if username in _user_processes and filename in _user_processes[username]:
                                if _user_processes[username][filename]['finished']:
                                    _user_processes[username].pop(filename, None)
                    threading.Thread(target=cleanup, daemon=True).start()

            drain_thread = threading.Thread(target=drain_pipe, daemon=True)
            drain_thread.start()

            yield 'data: ' + json.dumps({'type': 'status', 'msg': 'started'}) + '\n\n'

            # 从队列读取输出
            while True:
                try:
                    msg_type, msg = output_queue.get(timeout=0.5)
                except queue.Empty:
                    yield 'data: ' + json.dumps({'type': 'heartbeat'}) + '\n\n'
                    continue

                if msg_type == 'done':
                    # 检查生成的图像
                    generated = []
                    for f in sorted(user_dir.iterdir()):
                        if f.suffix in ('.png', '.jpg', '.jpeg', '.bmp'):
                            if f.stat().st_mtime > time.time() - 60:
                                generated.append(f.name)
                    done = json.dumps({
                        'type': 'done',
                        'return_code': msg,
                        'images': generated,
                    })
                    yield f'data: {done}\n\n'
                    break
                elif msg_type == 'stdout':
                    data = json.dumps({'type': 'stdout', 'msg': msg})
                    yield f'data: {data}\n\n'

        except GeneratorExit:
            # 浏览器断开，进程继续运行
            pass
        except Exception as e:
            err = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err}\n\n'

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ==================== Notebook Cell 执行（持久化 Kernel） ====================

# 持久化 Notebook 内核：每个 notebook 维护一个长驻 Python 进程，变量在 Cell 间共享
_nb_kernels = {}       # {notebook_path: subprocess.Popen}
_nb_kernels_lock = threading.Lock()

WRAPPER_SCRIPT = r'''
import sys, os, json, traceback
os.environ["MPLBACKEND"] = "Agg"
os.environ["PYTHONUNBUFFERED"] = "1"

__SENTINEL__ = "__SENTINEL_PATH__"
__OUTPUT__ = "__OUTPUT_PATH__"
__ns = {"__builtins__": __builtins__}
__ns["__name__"] = "__main__"

# 将 stdout/stderr 重定向到输出文件（无缓冲，实时写入）
_outf = open(__OUTPUT__, "wb", buffering=0)
_outfd = _outf.fileno()
os.dup2(_outfd, 1)
os.dup2(_outfd, 2)
# 用无缓冲的二进制写入包装 fd 1/2，确保 \r 立即写入文件
class _UnbufferedWriter:
    def __init__(self, fd):
        self._fd = fd
    def write(self, s):
        if isinstance(s, str):
            s = s.encode('utf-8', errors='replace')
        os.write(self._fd, s)
        return len(s)
    def flush(self):
        pass
    def reconfigure(self, **kw):
        pass
sys.stdout = _UnbufferedWriter(1)
sys.stderr = _UnbufferedWriter(2)

# 自动 flush 的 print，确保实时输出
import builtins as _builtins
_real_print = _builtins.print
def _flush_print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    _real_print(*args, **kwargs)
__ns["print"] = _flush_print

# Matplotlib 内联显示钩子
try:
    import matplotlib
    import matplotlib.pyplot as _plt
    import base64 as _b64
    # 配置中文字体
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False
    def _inline_show(*a, **kw):
        for num in _plt.get_fignums():
            fig = _plt.figure(num)
            from io import BytesIO
            buf = BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
            buf.seek(0)
            b64 = _b64.b64encode(buf.read()).decode("ascii")
            print("<!--IMG:" + b64 + "-->", flush=True)
        _plt.close("all")
    _plt.show = _inline_show
    __ns["_plt"] = _plt
except ImportError:
    pass

while True:
    code = ""
    for line in sys.stdin:
        stripped = line.rstrip("\n")
        if stripped == "__CELL_END__":
            break
        code += line
    else:
        break  # EOF

    code = code.rstrip("\n")
    if not code:
        sys.stdout.flush()
        sys.stderr.flush()
        with open(__SENTINEL__, "w", encoding="utf-8") as f:
            json.dump({"rc": 0, "exc": None, "tb": None}, f)
        continue

    # 清空输出文件，防止跨 cell 累积
    _outf.seek(0)
    _outf.truncate()

    try:
        exec(compile(code, "<cell>", "exec"), __ns)
        rc, exc, tb = 0, None, None
    except SystemExit as e:
        rc = int(getattr(e, "code", 1) or 0)
        exc, tb = "SystemExit", ""
    except Exception as e:
        rc = 1
        exc = type(e).__name__
        tb = traceback.format_exc()
        print(tb, end="", flush=True)

    sys.stdout.flush()
    sys.stderr.flush()
    with open(__SENTINEL__, "w", encoding="utf-8") as f:
        json.dump({"rc": rc, "exc": exc, "tb": tb}, f)
'''

import tempfile as _tempfile

# .py 文件运行包装脚本（支持 matplotlib 内联显示）
FILE_RUNNER_WRAPPER = r'''
import sys, os
os.environ["MPLBACKEND"] = "Agg"
os.environ["PYTHONUNBUFFERED"] = "1"

__SCRIPT_PATH__ = "__SCRIPT_PATH_PLACEHOLDER__"

# Matplotlib 内联显示钩子
try:
    import matplotlib
    import matplotlib.pyplot as _plt
    import base64 as _b64
    matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False
    def _inline_show(*a, **kw):
        for num in _plt.get_fignums():
            fig = _plt.figure(num)
            from io import BytesIO
            buf = BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
            buf.seek(0)
            b64 = _b64.b64encode(buf.read()).decode("ascii")
            print("<!--IMG:" + b64 + "-->", flush=True)
        _plt.close("all")
    _plt.show = _inline_show
except ImportError:
    pass

import runpy
runpy.run_path(__SCRIPT_PATH__, run_name="__main__")

# 确保最后的图也被输出
try:
    _plt.show()
except:
    pass
'''

# 每个 notebook 内核的固定文件路径
_nb_kernel_files = {}  # {path: {'sentinel': ..., 'output': ...}}
_nb_kernel_users = {}  # {path: username}
_nb_kernel_busy = {}   # {path: True/False} — 是否正在执行 cell

def _get_or_create_kernel(path, python_exe, run_cwd, gpu_id=None, multi_gpu=False):
    """获取或创建持久化内核进程，返回 (proc, sentinel_path, output_path)"""
    with _nb_kernels_lock:
        proc = _nb_kernels.get(path)
        files = _nb_kernel_files.get(path)
        if proc and proc.poll() is not None:
            proc = None  # 进程已退出
        if proc and files:
            return proc, files['sentinel'], files['output']
        # 创建新的内核（临时文件放在系统临时目录，不污染用户文件夹）
        tmp_dir = _tempfile.mkdtemp(prefix='_nb_kernel_')
        sentinel_path = os.path.join(tmp_dir, 'sentinel.json')
        output_path = os.path.join(tmp_dir, 'output.txt')
        wrapper_code = WRAPPER_SCRIPT.replace("__SENTINEL_PATH__", sentinel_path.replace("\\", "\\\\"))
        wrapper_code = wrapper_code.replace("__OUTPUT_PATH__", output_path.replace("\\", "\\\\"))
        # 多卡并行：注入 DataParallel
        if multi_gpu:
            dp_code = r'''
import torch as _t
if _t.cuda.device_count() > 1:
    _orig_to = _t.nn.Module.to
    _dp_set = set()
    def _dp_to(self, *a, **k):
        _orig_to(self, *a, **k)
        try:
            if len(list(self.parameters())) > 0 and id(self) not in _dp_set:
                dev = str(a[0]) if a else str(k.get('device',''))
                if 'cuda' in dev or (not dev and next(self.parameters()).is_cuda):
                    self = _t.nn.DataParallel(self)
                    _dp_set.add(id(self))
                    print(f"[MultiGPU] DataParallel 包装完成，{_t.cuda.device_count()} GPUs")
        except: pass
        return self
    _t.nn.Module.to = _dp_to
    print(f"[MultiGPU] 检测到 {_t.cuda.device_count()} GPUs，已启用自动并行")
else:
    print("[MultiGPU] 仅 1 个 GPU，跳过")
'''
            wrapper_code = wrapper_code.replace(
                '# Matplotlib 内联显示钩子',
                dp_code + '\n# Matplotlib 内联显示钩子'
            )
        try:
            proc = subprocess.Popen(
                [python_exe, '-u', '-c', wrapper_code],
                cwd=run_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=get_env(gpu_id),
                text=True,
                encoding='utf-8', errors='replace',
                bufsize=0,
            )
            _nb_kernels[path] = proc
            _nb_kernel_files[path] = {'sentinel': sentinel_path, 'output': output_path}
            return proc, sentinel_path, output_path
        except Exception as e:
            return None, str(e), None


def _read_kernel_output(sentinel_path, output_path, timeout=120):
    """等待 sentinel 出现，然后从输出文件读取结果"""
    import time as _time
    deadline = _time.time() + timeout

    # 等待 sentinel
    result = None
    while _time.time() < deadline:
        if os.path.exists(sentinel_path):
            try:
                with open(sentinel_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    result = json.loads(content)
                    break
            except (json.JSONDecodeError, OSError):
                pass
        _time.sleep(0.05)

    if result is None:
        result = {'rc': -1, 'exc': 'Timeout', 'tb': f'执行超时（{timeout}秒）'}

    # 读取输出文件
    output_lines = []
    try:
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    output_lines.append(line.rstrip('\n\r'))
    except Exception:
        pass

    # 清理 sentinel（output 文件保留供下次使用）
    try:
        if os.path.exists(sentinel_path):
            os.remove(sentinel_path)
    except Exception:
        pass

    return output_lines, result

@app.route('/api/run_cell', methods=['POST'])
@login_required
def run_cell():
    """执行 Notebook Cell（持久化内核，变量在 Cell 间共享）"""
    user = get_current_user()
    data = request.get_json() or {}
    code = data.get('code', '')
    path = data.get('path', '')
    python_path = data.get('python_path', '')
    env_choice = data.get('env', 'system')
    gpu_id = data.get('gpu', '')
    multi_gpu = data.get('multi_gpu', '') == '1'

    if not path:
        return jsonify({'error': '未指定 Notebook 路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403
    if not code.strip():
        return jsonify({'error': 'Cell 代码为空'}), 400

    p = Path(path)
    if not p.exists():
        return jsonify({'error': 'Notebook 文件不存在'}), 404

    if python_path:
        python_exe = python_path
    elif env_choice == 'torch':
        if TORCH_PYTHON:
            python_exe = TORCH_PYTHON
        else:
            return jsonify({'error': '未找到 PyTorch 环境'}), 400
    else:
        python_exe = sys.executable

    run_cwd = str(p.parent)

    def generate():
        import time as _time
        try:
            proc, sentinel_path, output_path = _get_or_create_kernel(path, python_exe, run_cwd, gpu_id, multi_gpu)
            with _nb_kernels_lock:
                _nb_kernel_users[path] = user['username']
                _nb_kernel_busy[path] = True
            if proc is None:
                with _nb_kernels_lock:
                    _nb_kernel_busy[path] = False
                err = json.dumps({'type': 'error', 'msg': f'无法启动内核：{sentinel_path}'})
                yield f'data: {err}\n\n'
                return

            # 清理上次的 sentinel（output 文件由 wrapper 内部清空，不要删除）
            try:
                if os.path.exists(sentinel_path):
                    os.remove(sentinel_path)
            except Exception:
                pass

            yield 'data: ' + json.dumps({'type': 'status', 'msg': 'started'}) + '\n\n'

            # 发送代码到内核
            try:
                proc.stdin.write(code.rstrip('\n') + '\n__CELL_END__\n')
                proc.stdin.flush()
            except BrokenPipeError:
                err = json.dumps({'type': 'error', 'msg': '内核进程已断开，请重启内核'})
                yield f'data: {err}\n\n'
                return

            # 实时流式读取输出
            deadline = _time.time() + 120
            offset = 0
            result = None

            while _time.time() < deadline:
                # 检查是否执行完成
                if os.path.exists(sentinel_path):
                    try:
                        with open(sentinel_path, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                        if content:
                            result = json.loads(content)
                    except (json.JSONDecodeError, OSError):
                        pass

                # 读取新输出
                if os.path.exists(output_path):
                    try:
                        with open(output_path, 'rb') as f:
                            f.seek(offset)
                            new_bytes = f.read()
                        if new_bytes:
                            offset += len(new_bytes)
                            new_data = new_bytes.decode('utf-8', errors='replace')
                            for line in new_data.split('\n'):
                                line = line.rstrip('\r')
                                if not line:
                                    continue
                                # 保留 \r 供前端处理 tqdm 等进度条替换
                                yield 'data: ' + json.dumps({'type': 'stdout', 'msg': line}) + '\n\n'
                    except Exception:
                        pass

                # 执行完成且没有更多输出
                if result is not None:
                    # 最后再读一次，确保没遗漏
                    if os.path.exists(output_path):
                        try:
                            with open(output_path, 'rb') as f:
                                f.seek(offset)
                                new_bytes = f.read()
                            if new_bytes:
                                new_data = new_bytes.decode('utf-8', errors='replace')
                                for line in new_data.split('\n'):
                                    line = line.rstrip('\r')
                                    if not line:
                                        continue
                                    yield 'data: ' + json.dumps({'type': 'stdout', 'msg': line}) + '\n\n'
                        except Exception:
                            pass
                    break

                _time.sleep(0.05)

            if result is None:
                result = {'rc': -1, 'exc': 'Timeout', 'tb': '执行超时'}

            # 清理 sentinel
            try:
                if os.path.exists(sentinel_path):
                    os.remove(sentinel_path)
            except Exception:
                pass

            done_data = json.dumps({
                'type': 'done',
                'return_code': result.get('rc', -1),
                'images': [],
            })
            yield f'data: {done_data}\n\n'

        except Exception as e:
            err_data = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err_data}\n\n'
        finally:
            with _nb_kernels_lock:
                _nb_kernel_busy[path] = False

    return Response(
        generate(),
        mimetype='text-event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/stop_cell', methods=['POST'])
@login_required
def stop_cell():
    """停止正在运行的 Cell（杀死内核进程）"""
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')

    if not path:
        return jsonify({'error': '未指定 Notebook 路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问'}), 403

    with _nb_kernels_lock:
        proc = _nb_kernels.pop(path, None)
        files = _nb_kernel_files.pop(path, None)
        _nb_kernel_users.pop(path, None)
        _nb_kernel_busy.pop(path, None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        if files:
            tmp_dir = os.path.dirname(files.get('output', ''))
            if tmp_dir and os.path.isdir(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir)
                except Exception:
                    pass

    return jsonify({'status': 'ok', 'msg': '已停止'})


@app.route('/api/restart_kernel', methods=['POST'])
@login_required
def restart_kernel():
    """重启 Notebook 内核（清除所有变量）"""
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')

    if not path:
        return jsonify({'error': '未指定 Notebook 路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问'}), 403

    with _nb_kernels_lock:
        proc = _nb_kernels.pop(path, None)
        files = _nb_kernel_files.pop(path, None)
        _nb_kernel_users.pop(path, None)
        _nb_kernel_busy.pop(path, None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        if files:
            # 清理临时目录
            tmp_dir = os.path.dirname(files.get('output', ''))
            if tmp_dir and os.path.isdir(tmp_dir):
                try:
                    shutil.rmtree(tmp_dir)
                except Exception:
                    pass

    return jsonify({'status': 'ok', 'msg': '内核已重启'})


@app.route('/api/save_notebook', methods=['POST'])
@login_required
def api_save_notebook():
    """保存 Notebook JSON 到 .ipynb 文件"""
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')
    notebook = data.get('notebook', None)

    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    if notebook is None:
        return jsonify({'error': '未提供 Notebook 数据'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    p = Path(path)
    if p.exists() and p.is_dir():
        return jsonify({'error': '不能覆盖文件夹'}), 400

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(notebook, ensure_ascii=False, indent=1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stop_file', methods=['POST'])
def stop_file():
    """停止正在通过 /api/run_file 运行的文件"""
    with _file_lock:
        if not _file_running:
            return jsonify({'error': '没有正在运行的文件'}), 404
        for name, proc in list(_file_running.items()):
            try:
                if sys.platform == 'win32':
                    proc.terminate()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
            _file_running.pop(name, None)
            break
    return jsonify({'status': 'stopped'})


# ==================== 个人云盘 ====================

# 允许访问的目录（安全限制 - 仅限桌面dxw文件夹）
ALLOWED_PATHS = [
    WORK_DIR,  # C:\Users\94885\Desktop\dxw
]

# 共享文件夹路径（允许所有用户访问）
SHARED_DIR_PATH = SHARED_DIR.resolve()

def is_shared_folder_allowed(path_obj, username):
    """检查用户是否有权访问私有共享文件夹路径"""
    try:
        path_str = str(path_obj.resolve())
        private_dir = str((SHARED_DIR / 'private').resolve())
        if not path_str.lower().startswith(private_dir.lower()):
            return False
        # 从路径中提取文件夹名称
        rel = Path(path_str).relative_to(SHARED_DIR / 'private')
        folder_name = str(rel).split('\\')[0].split('/')[0]
        # 检查用户是否有权访问该文件夹
        folders = get_accessible_shared_folders(username)
        return any(f['name'] == folder_name for f in folders)
    except Exception:
        return False

def is_path_allowed(path, username=None):
    """检查路径是否在允许访问的范围内 - 允许浏览本地磁盘"""
    try:
        path_obj = Path(path).resolve()
        path_lower = str(path_obj).lower()
        work_dir_lower = str(WORK_DIR.resolve()).lower()
        upload_dir = str(UPLOAD_DIR.resolve()).lower()

        # 用户目录内文件完全放行
        if username:
            user_dir = get_user_dir(username).resolve()
            user_dir_lower = str(user_dir).lower()
            if path_lower.startswith(user_dir_lower):
                return True

        # 全局 uploads/ 目录放行
        if path_lower.startswith(upload_dir):
            return True

        # 共享文件夹放行（公共共享允许所有用户访问，私有通过 is_shared_folder_allowed 检查）
        shared_lower = str(SHARED_DIR_PATH).lower()
        if path_lower.startswith(shared_lower):
            pub_lower = str((SHARED_DIR / 'public').resolve()).lower()
            if path_lower.startswith(pub_lower):
                return True
            # 私有共享：需要检查用户是否是该共享文件夹的成员
            if username:
                return is_shared_folder_allowed(path_obj, username)
            return False

        # 用户目录互相访问限制
        users_dir = str((WORK_DIR / 'users').resolve()).lower()
        if path_lower.startswith(users_dir) and username:
            return path_lower.startswith(user_dir_lower)
        elif path_lower.startswith(users_dir):
            return False

        # 禁止访问私信附件和上传临时目录
        blocked_internal = [
            str(_ATTACHMENTS_DIR.resolve()).lower(),
            str(_UPLOAD_CHUNKS_DIR.resolve()).lower(),
        ]
        for b in blocked_internal:
            if path_lower.startswith(b):
                return False

        # 允许访问本地磁盘上的文件和目录
        if path_obj.exists():
            # 禁止访问系统敏感目录
            blocked = [
                'c:\\windows\\system32',
                'c:\\windows\\syswow64',
                'c:\\programdata',
            ]
            for b in blocked:
                if path_lower.startswith(b):
                    return False
            return True

        return False
    except Exception:
        return False


def get_available_drives():
    """获取服务器上所有可用的磁盘分区"""
    drives = []
    if sys.platform == 'win32':
        import string
        for letter in string.ascii_uppercase:
            drive = f'{letter}:\\'
            try:
                path = Path(drive)
                if path.exists():
                    usage = shutil.disk_usage(drive)
                    drives.append({
                        'letter': letter,
                        'path': drive,
                        'total': usage.total,
                        'free': usage.free,
                    })
            except (OSError, PermissionError):
                pass
    else:
        # Linux/Mac: 根目录
        drives.append({
            'letter': '/',
            'path': '/',
            'total': 0,
            'free': 0,
        })
    return drives


@app.route('/api/drives')
@login_required
def api_list_drives():
    """返回服务器上所有可用的磁盘分区"""
    return jsonify({'drives': get_available_drives()})


@app.route('/files')
@login_required
def file_browser():
    """个人云盘页面"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return _whitelist_denied()
    user_dir = str(get_user_dir(user['username']).resolve())
    return render_template('files.html', user_dir=user_dir)


@app.route('/api/my_dir')
@login_required
def api_my_dir():
    """返回当前用户的个人目录路径"""
    user = get_current_user()
    return jsonify({'path': str(get_user_dir(user['username']).resolve())})


@app.route('/api/files')
@login_required
def api_list_files():
    """列出指定目录的文件和子目录（允许访问项目目录，禁止访问其他用户目录）"""
    user = get_current_user()
    path = request.args.get('path', '')
    
    # 如果没有指定路径，默认列出项目根目录
    if not path:
        path = str(WORK_DIR.resolve())
    
    # 检查路径是否允许访问
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该路径'}), 403
    
    try:
        p = Path(path)
        if not p.exists():
            return jsonify({'error': '路径不存在'}), 404
        
        if not p.is_dir():
            return jsonify({'error': '不是目录'}), 400
        
        items = []
        
        for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # 隐藏系统文件夹
            if item.name in ('config', 'moments', 'uploads', 'lcvr_plots') and item.is_dir():
                continue
            try:
                if item.is_dir():
                    items.append({
                        'name': item.name,
                        'type': 'directory',
                        'path': str(item.resolve()),
                        'size': '-',
                        'mtime': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    })
                else:
                    items.append({
                        'name': item.name,
                        'type': 'file',
                        'path': str(item.resolve()),
                        'size': item.stat().st_size,
                        'mtime': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'is_text': item.suffix in ('.txt', '.py', '.md', '.json', '.csv', '.log', '.html', '.css', '.js', '.ipynb', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf', '.sh', '.bat', '.ps1', '.xml', '.svg', '.tex', '.rst', '.env', '.gitignore', '.dockerfile', '.makefile', '.m'),
                        'is_image': item.suffix in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'),
                    })
            except Exception:
                continue
        
        return jsonify({
            'current_path': str(p.resolve()),
            'items': items
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _get_dir_size(path):
    """递归计算目录大小"""
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += _get_dir_size(entry.path)
            except Exception:
                continue
    except Exception:
        pass
    return total


def _fmt_size(size_bytes):
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@app.route('/api/disk_usage')
@login_required
def api_disk_usage():
    """获取磁盘使用情况：用户总占用 + 当前目录占用 + 磁盘剩余"""
    user = get_current_user()
    path = request.args.get('path', '')

    user_dir = get_user_dir(user['username']).resolve()
    # 用户总占用
    user_total = _get_dir_size(str(user_dir))

    # 当前目录占用（如果指定了路径且在允许范围内）
    dir_size = 0
    if path:
        try:
            p = Path(path).resolve()
            if p.exists() and p.is_dir() and is_path_allowed(str(p), user['username']):
                dir_size = _get_dir_size(str(p))
        except Exception:
            pass

    # 磁盘剩余空间
    try:
        usage = shutil.disk_usage(str(user_dir))
        disk_free = usage.free
        disk_total = usage.total
    except Exception:
        disk_free = 0
        disk_total = 0

    return jsonify({
        'user_total': user_total,
        'user_total_fmt': _fmt_size(user_total),
        'dir_size': dir_size,
        'dir_size_fmt': _fmt_size(dir_size),
        'dir_path': path,
        'disk_free': disk_free,
        'disk_free_fmt': _fmt_size(disk_free),
        'disk_total': disk_total,
        'disk_total_fmt': _fmt_size(disk_total),
    })


@app.route('/api/file_content')
@login_required
def api_file_content():
    """读取文本文件内容（仅限用户目录）"""
    user = get_current_user()
    path = request.args.get('path', '')
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 0, type=int)
    if not path:
        return jsonify({'error': '未指定文件路径'}), 400

    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return jsonify({'error': '文件不存在'}), 404

        file_size = p.stat().st_size
        # 读取原始字节，自动检测编码
        raw = p.read_bytes()
        # 尝试 UTF-8，失败则尝试 GBK，最后 latin-1
        content = None
        for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
            try:
                content = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:
            content = raw.decode('utf-8', errors='replace')
        # 大文件分块返回
        if limit > 0 or file_size > 50 * 1024 * 1024:
            chunk_size = limit if limit > 0 else 50 * 1024 * 1024
            content = content[offset:offset + chunk_size]
            return jsonify({
                'name': p.name,
                'path': str(p.resolve()),
                'content': content,
                'size': file_size,
                'offset': offset,
                'has_more': offset + chunk_size < file_size,
            })
        else:
            return jsonify({
                'name': p.name,
                'path': str(p.resolve()),
                'content': content,
                'size': file_size,
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download_file')
def api_download_file():
    """下载文件（仅限用户目录）"""
    user = get_current_user()
    if not user:
        return jsonify({'error': '未登录'}), 401
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': '未指定文件路径'}), 400

    # 如果是相对路径，解析为用户目录下的完整路径
    p = Path(path)
    if not p.is_absolute():
        user_dir = get_user_dir(user['username']).resolve()
        candidate = (user_dir / path).resolve()
        if str(candidate).startswith(str(user_dir)):
            p = candidate

    if not is_path_allowed(str(p), user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    try:
        if not p.exists() or not p.is_file():
            return jsonify({'error': '文件不存在：' + str(p)}), 404

        return send_from_directory(str(p.parent), p.name, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/file_preview')
def api_file_preview():
    """预览图片文件（内联显示）"""
    user = get_current_user()
    if not user:
        return jsonify({'error': '未登录'}), 401
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403
    
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return jsonify({'error': '文件不存在：' + path}), 404
        
        # 判断是否为图片
        ext = p.suffix.lower()
        if ext not in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'):
            return jsonify({'error': '不是图片文件'}), 400
        
        # MIME 类型映射
        mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.bmp': 'image/bmp', '.gif': 'image/gif', '.webp': 'image/webp'}
        mimetype = mime_map.get(ext, 'application/octet-stream')
        
        return send_from_directory(str(p.parent), p.name, mimetype=mimetype)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/video_stream')
def api_video_stream():
    """视频流接口，支持 HTTP Range 请求用于拖拽进度条"""
    user = get_current_user()
    if not user:
        return jsonify({'error': '未登录'}), 401
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': '未指定文件路径'}), 400

    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return jsonify({'error': '文件不存在'}), 404

        ext = p.suffix.lower()
        video_exts = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.ogv': 'video/ogg',
                      '.mov': 'video/mp4', '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo',
                      '.flv': 'video/x-flv', '.wmv': 'video/x-ms-wmv', '.m4v': 'video/mp4'}
        if ext not in video_exts:
            return jsonify({'error': '不是视频文件'}), 400

        file_size = p.stat().st_size
        mimetype = video_exts[ext]
        range_header = request.headers.get('Range')

        if range_header:
            # 解析 Range: bytes=start-end
            range_match = range_header.replace('bytes=', '').split('-')
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if range_match[1] else file_size - 1
            if start >= file_size:
                return Response(status=416, headers={'Content-Range': f'bytes */{file_size}'})
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            def generate():
                with open(p, 'rb') as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining > 0:
                        read_size = min(remaining, 1024 * 1024)  # 1MB chunks
                        data = f.read(read_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            resp = Response(generate(), status=206, mimetype=mimetype)
            resp.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            resp.headers['Content-Length'] = chunk_size
            resp.headers['Accept-Ranges'] = 'bytes'
            return resp
        else:
            return send_from_directory(str(p.parent), p.name, mimetype=mimetype)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 文件操作 API ====================

INVALID_NAME_CHARS = set('\\/:*?"<>|')


@app.route('/api/running_processes')
@login_required
def api_running_processes():
    """获取当前用户正在运行的进程列表"""
    user = get_current_user()
    username = user['username']
    result = []
    with _user_proc_lock:
        procs = _user_processes.get(username, {})
        for fname, info in procs.items():
            result.append({
                'filename': fname,
                'path': info['path'],
                'start_time': info['start_time'],
                'finished': info['finished'],
                'return_code': info['return_code'],
                'output_lines': len(info['buffer']),
            })
    return jsonify(result)


@app.route('/api/active_user_processes')
@login_required
def api_active_user_processes():
    """获取所有用户的活跃进程（仅白名单/管理员可查看）"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '无权限'}), 403
    result = []
    now = time.time()
    with _user_proc_lock:
        for uname, procs in _user_processes.items():
            for fname, info in procs.items():
                if info.get('finished'):
                    continue
                elapsed = int(now - info['start_time'])
                mins, secs = divmod(elapsed, 60)
                hrs, mins = divmod(mins, 60)
                if hrs > 0:
                    elapsed_str = f'{hrs}h{mins}m'
                elif mins > 0:
                    elapsed_str = f'{mins}m{secs}s'
                else:
                    elapsed_str = f'{secs}s'
                result.append({
                    'username': uname,
                    'filename': fname,
                    'path': info['path'],
                    'start_time': info['start_time'],
                    'elapsed': elapsed_str,
                    'pid': info['proc'].pid if hasattr(info['proc'], 'pid') else None,
                })

    # 补充 _running_processes 中的脚本（upload 页面运行的）
    with _running_lock:
        for sname, proc in _running_processes.items():
            pid = proc.pid if hasattr(proc, 'pid') else None
            # 检查是否已在 user_processes 中
            already = any(
                r['pid'] == pid for r in result if r['pid'] and pid
            ) if pid else False
            if not already:
                result.append({
                    'username': 'system',
                    'filename': sname,
                    'path': '',
                    'start_time': time.time(),
                    'elapsed': '',
                    'pid': pid,
                })

    # 补充 notebook 内核进程
    with _nb_kernels_lock:
        for nb_path, proc in _nb_kernels.items():
            if proc.poll() is not None:
                continue
            if not _nb_kernel_busy.get(nb_path):
                continue
            pid = proc.pid if hasattr(proc, 'pid') else None
            already = any(
                r['pid'] == pid for r in result if r['pid'] and pid
            ) if pid else False
            if not already:
                nb_p = Path(nb_path)
                nb_username = _nb_kernel_users.get(nb_path, '')
                result.append({
                    'username': nb_username or '?',
                    'filename': nb_p.name,
                    'path': nb_path,
                    'start_time': time.time(),
                    'elapsed': '',
                    'pid': pid,
                })

    # 为每个进程添加 GPU 信息
    for r in result:
        r['gpu'] = None

    return jsonify(result)


@app.route('/api/reconnect_output')
@login_required
def api_reconnect_output():
    """重连到正在运行的进程，先回放缓存输出，再实时推送"""
    user = get_current_user()
    username = user['username']
    filename = request.args.get('filename', '')

    if not filename:
        return jsonify({'error': '未指定文件名'}), 400

    with _user_proc_lock:
        procs = _user_processes.get(username, {})
        info = procs.get(filename)

    if not info:
        return jsonify({'error': '未找到该进程'}), 404

    def generate():
        try:
            # 1. 回放缓冲区中的历史输出
            for line in info['buffer']:
                data = json.dumps({'type': 'stdout', 'msg': line})
                yield f'data: {data}\n\n'

            # 2. 如果进程已结束，直接发送完成信息
            if info['finished']:
                done = json.dumps({'type': 'done', 'return_code': info['return_code']})
                yield f'data: {done}\n\n'
                return

            # 3. 进程还在运行，实时监听新输出
            last_idx = len(info['buffer'])
            while True:
                time.sleep(0.3)
                current_buf = info['buffer']
                if len(current_buf) > last_idx:
                    for i in range(last_idx, len(current_buf)):
                        data = json.dumps({'type': 'stdout', 'msg': current_buf[i]})
                        yield f'data: {data}\n\n'
                    last_idx = len(current_buf)

                if info['finished']:
                    done = json.dumps({'type': 'done', 'return_code': info['return_code']})
                    yield f'data: {done}\n\n'
                    break

        except GeneratorExit:
            pass

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/save_file', methods=['POST'])
@login_required
def api_save_file():
    """保存文件内容"""
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')
    content = data.get('content', '')

    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该文件'}), 403

    p = Path(path)
    if p.exists() and p.is_dir():
        return jsonify({'error': '不能覆盖文件夹'}), 400

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/create_folder', methods=['POST'])
@login_required
def api_create_folder():
    """创建文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    parent = data.get('path', '')
    name = data.get('name', '')

    if not parent or not name:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(parent, user['username']):
        return jsonify({'error': '无权访问该路径'}), 403
    if any(c in INVALID_NAME_CHARS for c in name):
        return jsonify({'error': '文件夹名包含非法字符'}), 400

    target = Path(parent) / name
    if target.exists():
        return jsonify({'error': '同名文件夹已存在'}), 400

    try:
        target.mkdir(parents=True, exist_ok=True)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/delete_file', methods=['POST'])
@login_required
def api_delete_file():
    """删除文件或文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')

    if not path:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该路径'}), 403

    # 不允许删除用户根目录本身
    user_dir = get_user_dir(user['username']).resolve()
    target = Path(path).resolve()
    if target == user_dir:
        return jsonify({'error': '不能删除用户根目录'}), 400

    try:
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            os.remove(str(target))
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/move_file', methods=['POST'])
@login_required
def api_move_file():
    """移动文件或文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    src = data.get('src', '')
    dst_dir = data.get('dst_dir', '')

    if not src or not dst_dir:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(src, user['username']):
        return jsonify({'error': '无权访问源路径'}), 403
    if not is_path_allowed(dst_dir, user['username']):
        return jsonify({'error': '无权访问目标目录'}), 403

    src_path = Path(src)
    dst_path = Path(dst_dir) / src_path.name
    if dst_path.exists():
        return jsonify({'error': '目标位置已存在同名文件'}), 400

    try:
        shutil.move(str(src_path), str(dst_path))
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/copy_file', methods=['POST'])
@login_required
def api_copy_file():
    """复制文件或文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    src = data.get('src', '')
    dst_dir = data.get('dst_dir', '')

    if not src or not dst_dir:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(src, user['username']):
        return jsonify({'error': '无权访问源路径'}), 403
    if not is_path_allowed(dst_dir, user['username']):
        return jsonify({'error': '无权访问目标目录'}), 403

    src_path = Path(src)
    dst_path = Path(dst_dir) / src_path.name
    if dst_path.exists():
        return jsonify({'error': '目标位置已存在同名文件'}), 400

    try:
        if src_path.is_dir():
            shutil.copytree(str(src_path), str(dst_path))
        else:
            shutil.copy2(str(src_path), str(dst_path))
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rename_file', methods=['POST'])
@login_required
def api_rename_file():
    """重命名文件或文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')
    new_name = data.get('new_name', '')

    if not path or not new_name:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该路径'}), 403
    if any(c in INVALID_NAME_CHARS for c in new_name):
        return jsonify({'error': '名称包含非法字符'}), 400

    src = Path(path)
    dst = src.parent / new_name
    if dst.exists():
        return jsonify({'error': '同名文件已存在'}), 400

    try:
        src.rename(dst)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 用户设置 API ====================

@app.route('/api/user_settings', methods=['GET'])
@login_required
def api_get_user_settings():
    """获取当前用户设置"""
    user = get_current_user()
    settings = get_user_settings(user['username'])
    return jsonify(settings)


@app.route('/api/user_settings', methods=['POST'])
@login_required
def api_save_user_settings():
    """保存当前用户设置（合并到现有设置）"""
    user = get_current_user()
    data = request.get_json() or {}
    settings = get_user_settings(user['username'])
    settings.update(data)
    save_user_settings(user['username'], settings)
    return jsonify({'status': 'ok'})


# ==================== 批量操作 API ====================

@app.route('/api/batch_delete', methods=['POST'])
@login_required
def api_batch_delete():
    """批量删除文件/文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    paths = data.get('paths', [])
    if not paths:
        return jsonify({'error': '缺少参数'}), 400

    user_dir = get_user_dir(user['username']).resolve()
    success, failed = 0, 0
    errors = []
    for path in paths:
        if not is_path_allowed(path, user['username']):
            failed += 1
            errors.append(f'{path}: 无权访问')
            continue
        target = Path(path).resolve()
        if target == user_dir:
            failed += 1
            errors.append(f'{path}: 不能删除用户根目录')
            continue
        try:
            if target.is_dir():
                shutil.rmtree(str(target))
            else:
                os.remove(str(target))
            success += 1
        except Exception as e:
            failed += 1
            errors.append(f'{path}: {e}')

    return jsonify({'status': 'ok', 'success': success, 'failed': failed, 'errors': errors})


@app.route('/api/user_dir')
@login_required
def api_user_dir():
    """获取用户根目录路径"""
    user = get_current_user()
    user_dir = get_user_dir(user['username']).resolve()
    return jsonify({'path': str(user_dir)})


_SYNC_SCAN_CACHE = {}  # {dir_path: {'mtime': float, 'data': {rel: (mtime, size)}}}
_SYNC_SCAN_CACHE_TTL = 60  # 缓存 60 秒


def _scan_server_dir(sync_root):
    """扫描服务端目录，带缓存（避免分批比对时重复扫描）"""
    import time as _time
    dir_key = str(sync_root)
    now = _time.time()
    cached = _SYNC_SCAN_CACHE.get(dir_key)
    if cached and now - cached['mtime'] < _SYNC_SCAN_CACHE_TTL:
        return cached['data']

    files = {}
    try:
        with os.scandir(str(sync_root)) as it:
            for entry in it:
                _scan_entry(entry, sync_root, files)
    except (OSError, PermissionError):
        pass

    _SYNC_SCAN_CACHE[dir_key] = {'mtime': now, 'data': files}
    # 缓存过期清理
    if len(_SYNC_SCAN_CACHE) > 50:
        cutoff = now - _SYNC_SCAN_CACHE_TTL * 2
        for k in list(_SYNC_SCAN_CACHE):
            if _SYNC_SCAN_CACHE[k]['mtime'] < cutoff:
                del _SYNC_SCAN_CACHE[k]
    return files


def _scan_entry(entry, root, files):
    """递归扫描单个目录条目"""
    try:
        if entry.is_dir(follow_symlinks=False):
            name = entry.name
            if name.startswith('.') or name in ('__pycache__', 'node_modules', '.git'):
                return
            with os.scandir(entry.path) as it:
                for child in it:
                    _scan_entry(child, root, files)
        elif entry.is_file(follow_symlinks=False):
            st = entry.stat(follow_symlinks=False)
            rel = os.path.relpath(entry.path, str(root)).replace('\\', '/')
            files[rel] = (st.st_mtime, st.st_size)
    except (OSError, PermissionError):
        pass


@app.route('/api/sync/server_tree')
@login_required
def api_sync_server_tree():
    """返回服务端目录树（客户端拉取后本地比对）"""
    user = get_current_user()
    base_path = request.args.get('base_path', '')

    user_dir = get_user_dir(user['username']).resolve()
    if base_path:
        candidate = (user_dir / base_path).resolve()
        if str(candidate).startswith(str(user_dir)):
            sync_root = candidate
        else:
            sync_root = user_dir
    else:
        sync_root = user_dir

    if not sync_root.exists():
        return jsonify({})

    server_files = _scan_server_dir(sync_root)
    return jsonify(server_files)


@app.route('/api/sync/changes', methods=['POST'])
@login_required
def api_sync_changes():
    """比对客户端与服务端文件差异"""
    user = get_current_user()
    data = request.get_json() or {}
    base_path = data.get('base_path', '')
    client_files = data.get('files', [])  # [{relative_path, mtime, size}]

    user_dir = get_user_dir(user['username']).resolve()
    if base_path:
        candidate = (user_dir / base_path).resolve()
        if str(candidate).startswith(str(user_dir)):
            sync_root = candidate
        else:
            sync_root = user_dir
    else:
        sync_root = user_dir
    if not sync_root.exists():
        missing_server = [f.get('relative_path', '').replace('\\', '/') for f in client_files]
        return jsonify({'newer_on_server': [], 'missing_server': missing_server, 'missing_client': [], 'deleted_on_server': []})

    # 从缓存获取服务端文件列表（只在首次或缓存过期时扫描）
    server_files = _scan_server_dir(sync_root)

    # 比对
    newer_on_server = []
    newer_on_client = []
    missing_server = []
    missing_client = []

    for f in client_files:
        rp = f.get('relative_path', '').replace('\\', '/')
        if rp not in server_files:
            missing_server.append(rp)
        else:
            client_mtime = f.get('mtime', 0)
            server_mtime = server_files[rp][0]
            if server_mtime > client_mtime + 1:
                newer_on_server.append(rp)
            elif client_mtime > server_mtime + 1:
                newer_on_client.append(rp)

    server_paths = {rp for rp in server_files}
    client_paths = {f.get('relative_path', '').replace('\\', '/') for f in client_files}
    for rp in server_paths - client_paths:
        missing_client.append(rp)

    return jsonify({
        'newer_on_server': newer_on_server,
        'newer_on_client': newer_on_client,
        'missing_server': missing_server,
        'missing_client': missing_client,
        'deleted_on_server': [],
    })


@app.route('/api/sync/download_batch', methods=['POST'])
@login_required
def api_sync_download_batch():
    """批量下载文件，保持目录结构"""
    import tempfile
    user = get_current_user()
    data = request.get_json() or {}
    paths = data.get('paths', [])  # 相对路径列表
    base_path = data.get('base_path', '')

    if not paths:
        return jsonify({'error': '缺少文件路径'}), 400

    user_dir = get_user_dir(user['username']).resolve()
    sync_root = (user_dir / base_path).resolve() if base_path else user_dir
    if not str(sync_root).startswith(str(user_dir)):
        return jsonify({'error': '路径不合法'}), 400

    # 用临时文件代替内存，防止大文件吃爆内存
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    try:
        added = 0
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            for rp in paths:
                rp = rp.replace('\\', '/')
                full = (sync_root / rp).resolve()
                if not str(full).startswith(str(sync_root)):
                    continue
                if full.is_file() and full.exists():
                    zf.write(str(full), arcname=rp)
                    added += 1
        tmp.close()

        if added == 0:
            os.unlink(tmp.name)
            return jsonify({'error': '没有找到文件'}), 404

        return send_file(
            tmp.name,
            mimetype='application/zip',
            as_attachment=True,
            download_name='sync_files.zip',
        )
    except Exception as e:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise


@app.route('/api/batch_move', methods=['POST'])
@login_required
def api_batch_move():
    """批量移动文件/文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    paths = data.get('paths', [])
    dst_dir = data.get('dst_dir', '')
    if not paths or not dst_dir:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(dst_dir, user['username']):
        return jsonify({'error': '无权访问目标目录'}), 403

    success, failed = 0, 0
    errors = []
    for path in paths:
        if not is_path_allowed(path, user['username']):
            failed += 1
            errors.append(f'{path}: 无权访问')
            continue
        src_path = Path(path)
        dst_path = Path(dst_dir) / src_path.name
        if dst_path.exists():
            failed += 1
            errors.append(f'{path}: 目标已存在同名文件')
            continue
        try:
            shutil.move(str(src_path), str(dst_path))
            success += 1
        except Exception as e:
            failed += 1
            errors.append(f'{path}: {e}')

    return jsonify({'status': 'ok', 'success': success, 'failed': failed, 'errors': errors})


@app.route('/api/batch_download', methods=['POST'])
@login_required
def api_batch_download():
    """批量下载：单文件直接下载，多文件打包为 zip"""
    user = get_current_user()
    data = request.get_json() or {}
    paths = data.get('paths', [])
    if not paths:
        return jsonify({'error': '缺少参数'}), 400

    # 单个文件直接下载
    if len(paths) == 1:
        path = paths[0]
        if not is_path_allowed(path, user['username']):
            return jsonify({'error': '无权访问'}), 403
        target = Path(path)
        if target.is_file():
            return send_from_directory(str(target.parent), target.name, as_attachment=True)

    # 多文件/目录：打包 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            if not is_path_allowed(path, user['username']):
                continue
            target = Path(path)
            if target.is_file():
                zf.write(str(target), target.name)
            elif target.is_dir():
                for root, dirs, files in os.walk(str(target)):
                    for fn in files:
                        fp = Path(root) / fn
                        arcname = fp.relative_to(target.parent)
                        zf.write(str(fp), str(arcname))

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': 'attachment; filename=download.zip'},
    )


# ==================== 终端 API ====================

import queue as _queue

_terminals = {}  # {term_id: {proc, output_q, reader_thread, cwd}}
_terminals_lock = threading.Lock()


@app.route('/api/terminal/start', methods=['POST'])
@login_required
def terminal_start():
    """初始化终端会话（返回 term_id 和 cwd）"""
    user = get_current_user()
    data = request.get_json() or {}
    cwd = data.get('cwd', '')
    if not cwd or not is_path_allowed(cwd, user['username']):
        cwd = str(get_user_dir(user['username']).resolve())

    import uuid
    term_id = str(uuid.uuid4())[:8]

    with _terminals_lock:
        _terminals[term_id] = {'cwd': cwd}

    # 返回欢迎信息
    return jsonify({'status': 'ok', 'term_id': term_id, 'cwd': cwd})


@app.route('/api/terminal/exec', methods=['POST'])
@login_required
def terminal_exec():
    """执行命令并流式返回输出"""
    data = request.get_json() or {}
    term_id = data.get('term_id', '')
    cmd = data.get('cmd', '')

    with _terminals_lock:
        info = _terminals.get(term_id)
    if not info:
        return jsonify({'error': '终端不存在'}), 404

    cwd = info['cwd']

    # 处理 cd 命令：切换目录不执行子进程
    stripped = cmd.strip()
    if stripped.lower().startswith('cd ') or stripped.lower().startswith('cd\t'):
        new_dir = stripped[3:].strip().strip('"').strip("'")
        if new_dir:
            test_path = Path(cwd) / new_dir if not Path(new_dir).is_absolute() else Path(new_dir)
            if test_path.is_dir():
                info['cwd'] = str(test_path.resolve())
                return Response(
                    'data: ' + json.dumps({'type': 'output', 'text': ''}) + '\n\n'
                    + 'data: ' + json.dumps({'type': 'done', 'rc': 0, 'cwd': info['cwd']}) + '\n\n',
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
                )
            else:
                return Response(
                    'data: ' + json.dumps({'type': 'output', 'text': '系统找不到指定的路径。\r\n'}) + '\n\n'
                    + 'data: ' + json.dumps({'type': 'done', 'rc': 1, 'cwd': cwd}) + '\n\n',
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
                )

    # 清空命令
    if stripped.lower() in ('cls', 'clear'):
        return Response(
            'data: ' + json.dumps({'type': 'clear'}) + '\n\n'
            + 'data: ' + json.dumps({'type': 'done', 'rc': 0, 'cwd': cwd}) + '\n\n',
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'

    def generate():
        try:
            if platform.system() == 'Windows':
                proc = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    bufsize=0,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
            else:
                proc = subprocess.Popen(
                    ['bash', '-c', cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    bufsize=0,
                )

            # 实时读取输出
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            while True:
                chunk = proc.stdout.read(1)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    yield 'data: ' + json.dumps({'type': 'output', 'text': text}) + '\n\n'

            # 刷新解码器
            text = decoder.decode(b'', final=True)
            if text:
                yield 'data: ' + json.dumps({'type': 'output', 'text': text}) + '\n\n'

            rc = proc.wait()
            yield 'data: ' + json.dumps({'type': 'done', 'rc': rc, 'cwd': cwd}) + '\n\n'

        except Exception as e:
            yield 'data: ' + json.dumps({'type': 'output', 'text': str(e) + '\r\n'}) + '\n\n'
            yield 'data: ' + json.dumps({'type': 'done', 'rc': -1, 'cwd': cwd}) + '\n\n'

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/api/terminal/close', methods=['POST'])
@login_required
def terminal_close():
    """关闭终端会话"""
    data = request.get_json() or {}
    term_id = data.get('term_id', '')
    with _terminals_lock:
        _terminals.pop(term_id, None)
    return jsonify({'status': 'ok'})


# ==================== AI 聊天代理 ====================

@app.route('/api/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    """代理各厂商 AI API 请求"""
    data = request.get_json() or {}
    provider = data.get('provider', 'claude')
    api_key = data.get('apiKey', '')
    base_url = data.get('baseUrl', '')
    model = data.get('model', '')
    messages = data.get('messages', [])

    if not api_key:
        return jsonify({'error': '缺少 API Key'}), 400
    if not messages:
        return jsonify({'error': '缺少消息'}), 400

    import urllib.request
    import ssl

    ctx = ssl.create_default_context()

    # Claude 使用 Messages API，其他使用 OpenAI 兼容格式
    if provider == 'claude':
        url = (base_url or 'https://api.anthropic.com') + '/v1/messages'
        payload = {
            'model': model or 'claude-sonnet-4-20250514',
            'max_tokens': 4096,
            'messages': [{'role': m['role'], 'content': m['content']} for m in messages],
        }
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
    else:
        # OpenAI / DeepSeek / 自定义 (OpenAI 兼容)
        url = (base_url or 'https://api.openai.com/v1') + '/chat/completions'
        payload = {
            'model': model or 'gpt-4o',
            'max_tokens': 4096,
            'messages': [{'role': m['role'], 'content': m['content']} for m in messages],
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + api_key,
        }

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            result = json.loads(resp.read().decode())

        if provider == 'claude':
            content = result.get('content', [{}])[0].get('text', '')
        else:
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        return jsonify({'content': content})
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode()
        except Exception:
            pass
        return jsonify({'error': f'API 错误 {e.code}: {body[:300]}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 终端认证 ====================

@app.route('/api/terminal/token')
@login_required
def api_terminal_token():
    """生成终端 WebSocket 连接 token"""
    user = get_current_user()
    from terminal_server import generate_terminal_token
    token = generate_terminal_token(user['username'])
    return jsonify({'token': token})


# ==================== WSL 支持 ====================

@app.route('/api/wsl_distros')
@login_required
def api_wsl_distros():
    """列出 WSL 发行版"""
    try:
        result = subprocess.run(
            ['wsl.exe', '-l', '-q'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            creationflags=0x08000000,
        )
        distros = []
        for line in result.stdout.strip().split('\n'):
            name = line.strip().replace('\x00', '')  # wsl -l 输出有 null 字节
            if name and not name.startswith('-'):
                distros.append(name)
        return jsonify({'distros': distros})
    except Exception as e:
        return jsonify({'distros': [], 'error': str(e)})


# ==================== 训练队列 ====================

import queue as _queue_mod

_train_queue = []  # [{id, username, filename, env, devices, multi_gpu, status, added_at, started_at}]
_train_queue_lock = threading.Lock()


def _get_running_count():
    """当前正在运行的任务数"""
    with _train_queue_lock:
        return sum(1 for t in _train_queue if t['status'] == 'running')


def _get_available_gpus():
    """获取空闲 GPU 列表"""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,memory.used,memory.total', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )
        available = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            idx, used, total = int(parts[0]), int(parts[1]), int(parts[2])
            if used < total * 0.1:  # 显存使用 <10% 视为空闲
                available.append(idx)
        return available
    except Exception:
        return []


def _try_start_next():
    """尝试从队列中启动下一个任务"""
    with _train_queue_lock:
        for task in _train_queue:
            if task['status'] != 'queued':
                continue
            # 检查 GPU 可用性
            if task['devices']:
                requested = [int(d) for d in task['devices'].split(',')]
                available = _get_available_gpus()
                if not all(g in available for g in requested):
                    continue
            task['status'] = 'running'
            task['started_at'] = time.time()
            # 在新线程中启动任务
            t = threading.Thread(target=_run_queued_task, args=(task,), daemon=True)
            t.start()
            break


def _run_queued_task(task):
    """执行队列中的任务"""
    try:
        username = task['username']
        filename = task['filename']
        env = task['env']
        devices = task['devices']
        multi_gpu = task['multi_gpu']

        # 调用已有的运行逻辑
        _execute_script(username, filename, env, devices, multi_gpu)
    except Exception as e:
        print(f'[Queue] 任务执行失败: {e}')
    finally:
        with _train_queue_lock:
            task['status'] = 'completed'
            task['finished_at'] = time.time()
        # 尝试启动下一个
        _try_start_next()


def _execute_script(username, filename, env, devices, multi_gpu):
    """实际执行脚本（复用已有逻辑）"""
    # 这里复用 run_file 中的执行逻辑，简化版
    filepath = Path(filename)
    if not filepath.exists():
        return

    cmd_parts = []
    if env and env != 'system':
        python_exe = Path(env) / 'python.exe' if platform.system() == 'Windows' else Path(env) / 'bin' / 'python'
        if python_exe.exists():
            cmd_parts.append(str(python_exe))
        else:
            cmd_parts.append(env)
    else:
        cmd_parts.append(sys.executable)

    cmd_parts.append(str(filepath))

    env_vars = os.environ.copy()
    env_vars['PYTHONUNBUFFERED'] = '1'
    if devices:
        env_vars['CUDA_VISIBLE_DEVICES'] = devices

    try:
        proc = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(filepath.parent),
            env=env_vars,
            bufsize=0,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
        )
        proc.wait()
    except Exception as e:
        print(f'[Queue] 执行出错: {e}')


@app.route('/api/train_queue', methods=['GET'])
@login_required
def api_get_queue():
    user = get_current_user()
    with _train_queue_lock:
        # 返回当前用户的队列 + 整体状态
        my_queue = [t for t in _train_queue if t['username'] == user['username']]
        running = [t for t in _train_queue if t['status'] == 'running']
    return jsonify({
        'queue': my_queue,
        'running_count': len(running),
        'total_in_queue': len(my_queue),
    })


@app.route('/api/train_queue/add', methods=['POST'])
@login_required
def api_add_to_queue():
    user = get_current_user()
    data = request.get_json() or {}
    filename = data.get('filename', '')
    if not filename:
        return jsonify({'error': '缺少文件名'}), 400

    task_id = str(uuid.uuid4())[:8]
    task = {
        'id': task_id,
        'username': user['username'],
        'filename': filename,
        'env': data.get('env', 'system'),
        'devices': data.get('devices', ''),
        'multi_gpu': data.get('multi_gpu', False),
        'status': 'queued',
        'added_at': time.time(),
        'started_at': None,
        'finished_at': None,
    }

    with _train_queue_lock:
        _train_queue.append(task)

    # 尝试立即启动
    _try_start_next()

    return jsonify({'status': 'ok', 'task_id': task_id, 'queue_position': sum(1 for t in _train_queue if t['status'] == 'queued')})


@app.route('/api/train_queue/remove', methods=['POST'])
@login_required
def api_remove_from_queue():
    data = request.get_json() or {}
    task_id = data.get('task_id', '')
    with _train_queue_lock:
        for i, t in enumerate(_train_queue):
            if t['id'] == task_id and t['status'] == 'queued':
                _train_queue.pop(i)
                return jsonify({'status': 'ok'})
    return jsonify({'error': '任务不存在或已在运行'}), 404


@app.route('/api/train_queue/clear', methods=['POST'])
@login_required
def api_clear_queue():
    user = get_current_user()
    with _train_queue_lock:
        _train_queue[:] = [t for t in _train_queue if t['username'] != user['username'] or t['status'] == 'running']
    return jsonify({'status': 'ok'})


# ==================== 显存清理 ====================

@app.route('/api/gpu/cleanup', methods=['POST'])
@login_required
def api_gpu_cleanup():
    """杀掉所有占用 GPU 显存的进程（除了本服务器）"""
    try:
        # 获取当前服务器 PID
        server_pid = os.getpid()

        # 用 nvidia-smi 获取 GPU 进程
        result = subprocess.run(
            ['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_memory', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5
        )
        if result.returncode != 0:
            return jsonify({'error': 'nvidia-smi 执行失败'}), 500

        killed = []
        skipped = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            pid = int(parts[0])
            mem = parts[2] if len(parts) > 2 else '?'

            # 跳过自身
            if pid == server_pid:
                skipped.append(pid)
                continue

            try:
                os.kill(pid, 9)  # SIGKILL
                killed.append({'pid': pid, 'mem': mem + 'MB'})
            except ProcessLookupError:
                pass  # 进程已不存在
            except PermissionError:
                skipped.append(pid)

        return jsonify({'killed': killed, 'skipped': len(skipped), 'total': len(killed) + len(skipped)})
    except FileNotFoundError:
        return jsonify({'error': 'nvidia-smi 未找到，可能没有 NVIDIA GPU'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Webhook 通知 ====================

def get_user_webhook(username):
    """读取用户 Webhook 配置"""
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    webhook_path = config_dir / 'webhook.json'
    if webhook_path.exists():
        try:
            with open(webhook_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'enabled': False, 'provider': '', 'config': {}, 'events': {'complete': True, 'error': True}}


def save_user_webhook(username, webhook):
    """保存用户 Webhook 配置"""
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    webhook_path = config_dir / 'webhook.json'
    with open(webhook_path, 'w', encoding='utf-8') as f:
        json.dump(webhook, f, ensure_ascii=False, indent=2)


def send_webhook_notification(username, title, content):
    """发送 Webhook 通知"""
    webhook = get_user_webhook(username)
    if not webhook.get('enabled') or not webhook.get('provider'):
        return
    provider = webhook['provider']
    cfg = webhook.get('config', {})
    try:
        if provider == 'serverchan':
            _notify_serverchan(cfg.get('key', ''), title, content)
        elif provider == 'pushplus':
            _notify_pushplus(cfg.get('token', ''), title, content)
        elif provider == 'wecom':
            _notify_wecom(cfg.get('webhook_url', ''), title, content)
        elif provider == 'custom':
            _notify_custom(cfg.get('url', ''), cfg.get('headers', {}), title, content)
    except Exception as e:
        print(f'[Webhook] 发送失败 ({provider}): {e}')


def _notify_serverchan(key, title, content):
    if not key:
        return
    import urllib.request
    import urllib.parse
    url = f'https://sctapi.ftqq.com/{key}.send'
    data = urllib.parse.urlencode({'title': title, 'desp': content}).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    urllib.request.urlopen(req, timeout=10)


def _notify_pushplus(token, title, content):
    if not token:
        return
    import urllib.request
    url = 'https://www.pushplus.plus/send'
    payload = json.dumps({'token': token, 'title': title, 'content': content, 'template': 'txt'}).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    urllib.request.urlopen(req, timeout=10)


def _notify_wecom(webhook_url, title, content):
    if not webhook_url:
        return
    import urllib.request
    payload = json.dumps({
        'msgtype': 'text',
        'text': {'content': f'{title}\n{content}'}
    }).encode()
    req = urllib.request.Request(webhook_url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    urllib.request.urlopen(req, timeout=10)


def _notify_custom(url, headers, title, content):
    if not url:
        return
    import urllib.request
    payload = json.dumps({'title': title, 'content': content}).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    urllib.request.urlopen(req, timeout=10)


@app.route('/api/webhook/config', methods=['GET'])
@login_required
def api_get_webhook():
    user = get_current_user()
    return jsonify(get_user_webhook(user['username']))


@app.route('/api/webhook/config', methods=['POST'])
@login_required
def api_save_webhook():
    user = get_current_user()
    data = request.get_json() or {}
    current = get_user_webhook(user['username'])
    # 合并更新
    if 'enabled' in data:
        current['enabled'] = data['enabled']
    if 'provider' in data:
        current['provider'] = data['provider']
    if 'config' in data:
        current['config'] = data['config']
    if 'events' in data:
        current['events'] = data['events']
    save_user_webhook(user['username'], current)
    return jsonify({'status': 'ok'})


@app.route('/api/webhook/test', methods=['POST'])
@login_required
def api_test_webhook():
    user = get_current_user()
    try:
        send_webhook_notification(user['username'], '测试通知', '这是一条测试消息，Webhook 配置成功！')
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# 在脚本运行完成时发送通知（在 run_file 的 done 回调处调用）
def notify_run_complete(username, filename, success, return_code):
    webhook = get_user_webhook(username)
    if not webhook.get('enabled'):
        return
    events = webhook.get('events', {})
    if success and not events.get('complete', True):
        return
    if not success and not events.get('error', True):
        return
    status = '完成' if success else '失败'
    title = f'脚本运行{status}'
    content = f'文件: {filename}\n返回码: {return_code}'
    send_webhook_notification(username, title, content)


# ==================== 速度测试页面 ====================

@app.route('/speed_test')
def speed_test():
    """下载速度测试页面"""
    return render_template('speed_test.html')

@app.route('/api/speed_test_data')
def speed_test_data():
    """生成随机数据供速度测试下载"""
    size_mb = int(request.args.get('mb', 10))
    size_mb = min(size_mb, 100)  # 最大100MB
    total = size_mb * 1024 * 1024

    def generate():
        sent = 0
        chunk = os.urandom(64 * 1024)  # 64KB chunks
        while sent < total:
            to_send = min(len(chunk), total - sent)
            yield chunk[:to_send]
            sent += to_send

    return Response(
        generate(),
        mimetype='application/octet-stream',
        headers={
            'Content-Length': str(total),
            'Content-Disposition': f'attachment; filename="speedtest_{size_mb}mb.bin"',
            'Cache-Control': 'no-cache',
        },
    )


# ==================== 共享文件夹 API ====================

def _notify_shared_members(folder_id, folder, actor, notif_type, title, message, link=''):
    """通知共享文件夹内除操作者外的所有成员"""
    if folder['type'] == 'public':
        # 公共文件夹：通知所有用户
        all_users = get_all_users()
        members = [u['username'] for u in all_users if u['username'] != actor]
    else:
        # 私有文件夹：通知成员列表中的人
        member_rows = get_shared_folder_members(int(folder_id))
        members = [m['username'] for m in member_rows if m['username'] != actor]
    app.logger.info(f'[共享通知] folder={folder["display_name"]}, actor={actor}, members={members}, type={notif_type}')
    for m in members:
        create_notification(m, notif_type, title, message, link)


@app.route('/api/shared/list')
@login_required
def api_shared_list():
    """获取用户可访问的共享文件夹列表"""
    user = get_current_user()
    folders = get_accessible_shared_folders(user['username'])
    # 公共文件夹显示全部用户数
    all_users = get_all_users()
    all_usernames = [u['username'] for u in all_users]
    result = []
    for f in folders:
        if f['type'] == 'public':
            member_names = all_usernames
        else:
            members = get_shared_folder_members(f['id'])
            member_names = [m['username'] for m in members]
        result.append({
            'id': f['id'],
            'name': f['name'],
            'display_name': f['display_name'],
            'type': f['type'],
            'owner': f['owner'],
            'created_at': f['created_at'],
            'member_count': len(member_names),
            'members': member_names,
        })
    return jsonify(result)


@app.route('/api/shared/create', methods=['POST'])
@login_required
def api_shared_create():
    """创建共享文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    display_name = data.get('display_name', '').strip() or name
    share_type = data.get('type', 'private')
    if not name:
        return jsonify({'error': '文件夹名称不能为空'}), 400
    if share_type not in ('public', 'private'):
        return jsonify({'error': '类型只能是 public 或 private'}), 400
    # 在磁盘上创建目录
    if share_type == 'public':
        folder_path = SHARED_DIR / 'public' / name
    else:
        folder_path = SHARED_DIR / 'private' / name
    if folder_path.exists():
        return jsonify({'error': '同名共享文件夹已存在'}), 400
    folder_path.mkdir(parents=True, exist_ok=True)
    # 记录到数据库
    folder_id, err = create_shared_folder(name, display_name, share_type, user['username'] if share_type == 'private' else '')
    if err:
        import shutil
        shutil.rmtree(str(folder_path), ignore_errors=True)
        return jsonify({'error': err}), 500
    # 如果是私有文件夹，将创建者加入成员表
    if share_type == 'private':
        add_shared_folder_member(folder_id, user['username'], user['username'])
    return jsonify({'ok': True, 'folder_id': folder_id, 'path': str(folder_path)})


@app.route('/api/shared/browse')
@login_required
def api_shared_browse():
    """浏览共享文件夹内容"""
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    rel_path = request.args.get('path', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    # 获取文件夹信息
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问该共享文件夹'}), 403
    # 构建实际路径
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = base / rel_path if rel_path else base
    if not target.exists() or not target.is_dir():
        return jsonify({'error': '路径不存在'}), 404
    items = []
    rel_paths = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            rel = item.relative_to(base).as_posix() if item != base else ''
            stat = item.stat()
            mtime = stat.st_mtime
            modified = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
            if item.is_dir():
                items.append({
                    'name': item.name,
                    'is_dir': True,
                    'path': rel,
                    'size': 0,
                    'modified': modified,
                })
            else:
                items.append({
                    'name': item.name,
                    'is_dir': False,
                    'path': rel,
                    'size': stat.st_size,
                    'modified': modified,
                })
            rel_paths.append(rel)
        except Exception:
            continue
    # 获取文件元数据（创建者）
    meta = get_shared_files_meta(folder_id, rel_paths)
    for item in items:
        m = meta.get(item['path'])
        if m:
            item['created_by'] = m['created_by']
            item['created_at'] = m['created_at']
        else:
            item['created_by'] = ''
            item['created_at'] = ''
    return jsonify({
        'items': items,
        'folder_name': folder['display_name'],
        'type': folder['type'],
    })


@app.route('/api/shared/members')
@login_required
def api_shared_members():
    """获取共享文件夹成员"""
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    members = get_shared_folder_members(int(folder_id))
    # 获取文件夹信息以确认有权访问
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    # 补充超管信息
    return jsonify(members)


@app.route('/api/shared/invite', methods=['POST'])
@login_required
def api_shared_invite():
    """邀请用户加入私有共享文件夹（创建待确认邀请）"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    target_user = data.get('username', '').strip()
    if not folder_id or not target_user:
        return jsonify({'error': '参数不完整'}), 400
    # 确认有权操作
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder or folder['type'] != 'private':
        return jsonify({'error': '无权邀请'}), 403
    # 检查是否已是成员
    members = get_shared_folder_members(int(folder_id))
    if any(m['username'] == target_user for m in members):
        return jsonify({'error': '该用户已是成员'}), 400
    ok = invite_to_shared_folder(int(folder_id), target_user, user['username'])
    if ok:
        # 发送通知（链接到消息中心待处理邀请）
        create_notification(target_user, 'shared_folder', '共享文件夹邀请',
            f'{user["username"]} 邀请您加入共享文件夹 "{folder["display_name"]}"', '/messages')
        return jsonify({'ok': True, 'msg': '邀请已发送，等待对方确认'})
    return jsonify({'error': '邀请发送失败'}), 500


@app.route('/api/shared/accept_invite', methods=['POST'])
@login_required
def api_shared_accept():
    """接受共享文件夹邀请"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    ok, msg = accept_shared_invitation(int(folder_id), user['username'])
    if ok:
        # 通知邀请人
        folders = get_accessible_shared_folders(user['username'])
        folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
        if folder and folder.get('owner'):
            create_notification(folder['owner'], 'shared_folder', '邀请已接受',
                f'{user["username"]} 已接受您的共享文件夹邀请', '/files')
        return jsonify({'ok': True, 'msg': msg})
    return jsonify({'error': msg}), 400


@app.route('/api/shared/reject_invite', methods=['POST'])
@login_required
def api_shared_reject():
    """拒绝共享文件夹邀请"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    ok = reject_shared_invitation(int(folder_id), user['username'])
    return jsonify({'ok': ok})


@app.route('/api/shared/pending_invitations')
@login_required
def api_shared_pending():
    """获取当前用户的待处理邀请列表"""
    user = get_current_user()
    invitations = get_pending_invitations(user['username'])
    return jsonify(invitations)


@app.route('/api/shared/upload', methods=['POST'])
@login_required
def api_shared_upload():
    """上传文件到共享文件夹"""
    user = get_current_user()
    folder_id = request.form.get('folder_id', '')
    rel_path = request.form.get('path', '')
    files = request.files.getlist('files')
    if not folder_id or not files:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = base / rel_path if rel_path else base
    target.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for f in files:
        f.save(str(target / f.filename))
        uploaded.append(f.filename)
        # 记录文件创建者
        rel = (rel_path + '/' + f.filename) if rel_path else f.filename
        record_shared_file_meta(folder_id, rel, user['username'])
    # 通知共享文件夹其他成员
    file_names = ', '.join(uploaded)
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_upload', '共享文件夹文件上传',
                           '{} 向「{}」上传了文件：{}'.format(user['username'], folder['display_name'], file_names),
                           '/files')
    return jsonify({'ok': True, 'uploaded': uploaded})


@app.route('/api/shared/create_dir', methods=['POST'])
@login_required
def api_shared_create_dir():
    """在共享文件夹中创建目录"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    dir_name = data.get('name', '').strip()
    rel_path = data.get('path', '')
    if not folder_id or not dir_name:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    new_dir = (base / rel_path / dir_name) if rel_path else (base / dir_name)
    new_dir.mkdir(parents=True, exist_ok=True)
    # 记录目录创建者
    dir_rel = (rel_path + '/' + dir_name) if rel_path else dir_name
    record_shared_file_meta(folder_id, dir_rel, user['username'])
    # 通知共享文件夹其他成员
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_upload', '共享文件夹新建目录',
                           '{} 在「{}」中新建了文件夹：{}'.format(user['username'], folder['display_name'], dir_name),
                           '/files')
    return jsonify({'ok': True})


@app.route('/api/shared/delete', methods=['POST'])
@login_required
def api_shared_delete():
    """删除共享文件夹中的文件/目录 或 删除整个共享文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    file_path = data.get('path', '')
    delete_whole = data.get('delete_whole', False)
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if delete_whole:
        # 创建者或管理员可删除共享文件夹
        if folder['owner'] != user['username'] and not is_admin(user['username']):
            return jsonify({'error': '仅创建者或管理员可删除共享文件夹'}), 403
        if folder['type'] == 'public':
            base = SHARED_DIR / 'public' / folder['name']
        else:
            base = SHARED_DIR / 'private' / folder['name']
        import shutil
        shutil.rmtree(str(base), ignore_errors=True)
        # 清理该共享文件夹所有文件元数据
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(str(DB_PATH))
        _c = _conn.cursor()
        _c.execute('DELETE FROM shared_file_meta WHERE folder_id = ?', (int(folder_id),))
        _conn.commit()
        _conn.close()
        delete_shared_folder(int(folder_id), user['username'])
        return jsonify({'ok': True})
    # 删除文件夹内文件/目录
    if not file_path:
        return jsonify({'error': '缺少文件路径'}), 400
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = base / file_path
    if not target.exists():
        return jsonify({'error': '文件不存在'}), 404
    if target.is_dir():
        import shutil
        shutil.rmtree(str(target), ignore_errors=True)
        # 清理该目录下所有子项的元数据
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM shared_file_meta WHERE folder_id = ? AND (rel_path = ? OR rel_path LIKE ?)',
                  (int(folder_id), file_path, file_path + '/%'))
        conn.commit()
        conn.close()
    else:
        target.unlink()
        # 清理该文件的元数据
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM shared_file_meta WHERE folder_id = ? AND rel_path = ?',
                  (int(folder_id), file_path))
        conn.commit()
        conn.close()
    # 通知共享文件夹其他成员
    deleted_name = file_path.split('/')[-1]
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_delete', '共享文件夹文件删除',
                           '{} 从「{}」中删除了「{}」'.format(user['username'], folder['display_name'], deleted_name),
                           '/files')
    return jsonify({'ok': True})


@app.route('/api/shared/rename_item', methods=['POST'])
@login_required
def api_shared_rename_item():
    """重命名共享文件夹内的文件/目录"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    file_path = data.get('path', '')
    new_name = data.get('new_name', '').strip()
    if not folder_id or not file_path or not new_name:
        return jsonify({'error': '参数不完整'}), 400
    # 防止路径遍历
    if '/' in new_name or '\\' in new_name or new_name in ('.', '..'):
        return jsonify({'error': '名称包含非法字符'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = base / file_path
    if not target.exists():
        return jsonify({'error': '文件不存在'}), 404
    new_path = target.parent / new_name
    if new_path.exists():
        return jsonify({'error': '同名文件已存在'}), 400
    try:
        target.rename(new_path)
        # 更新元数据路径和修改者
        new_rel = str(new_path.relative_to(base))
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('UPDATE shared_file_meta SET rel_path = ?, created_by = ? WHERE folder_id = ? AND rel_path = ?',
                  (new_rel, user['username'], int(folder_id), file_path))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'重命名失败: {e}'}), 500
    # 通知共享文件夹其他成员
    old_name = file_path.split('/')[-1]
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_rename', '共享文件夹文件重命名',
                           '{} 在「{}」中将「{}」重命名为「{}」'.format(user['username'], folder['display_name'], old_name, new_name),
                           '/files')
    return jsonify({'ok': True})


@app.route('/api/shared/move', methods=['POST'])
@login_required
def api_shared_move():
    """移动共享文件夹内的文件/目录"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    src_path = data.get('src', '')
    dst_path = data.get('dst', '')
    if not folder_id or not src_path:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    src = base / src_path
    if not src.exists():
        return jsonify({'error': '源文件不存在'}), 404
    # 目标目录
    if dst_path:
        dst_dir = base / dst_path
    else:
        dst_dir = base
    if not dst_dir.exists():
        return jsonify({'error': '目标目录不存在'}), 404
    # 防止移动到自身内部
    try:
        src.resolve().relative_to(dst_dir.resolve())
        return jsonify({'error': '不能移动到自身子目录'}), 400
    except ValueError:
        pass
    dst = dst_dir / src.name
    if dst.exists():
        return jsonify({'error': '目标位置已存在同名文件'}), 400
    try:
        import shutil
        shutil.move(str(src), str(dst))
        # 更新元数据路径
        new_rel = str(dst.relative_to(base))
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        # 如果是目录，需要更新所有子项的路径前缀
        if src.is_dir():
            old_prefix = src_path + '/'
            new_prefix = new_rel + '/'
            c.execute('UPDATE shared_file_meta SET rel_path = ? || substr(rel_path, ?) WHERE folder_id = ? AND rel_path LIKE ?',
                      (new_prefix, len(old_prefix), int(folder_id), old_prefix + '%'))
            # 更新目录本身（如果有记录）
            c.execute('UPDATE shared_file_meta SET rel_path = ? WHERE folder_id = ? AND rel_path = ?',
                      (new_rel, int(folder_id), src_path))
        else:
            c.execute('UPDATE shared_file_meta SET rel_path = ? WHERE folder_id = ? AND rel_path = ?',
                      (new_rel, int(folder_id), src_path))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'移动失败: {e}'}), 500
    # 通知共享文件夹其他成员
    moved_name = src_path.split('/')[-1]
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_move', '共享文件夹文件移动',
                           '{} 在「{}」中移动了「{}」'.format(user['username'], folder['display_name'], moved_name),
                           '/files')
    return jsonify({'ok': True})


@app.route('/api/shared/copy', methods=['POST'])
@login_required
def api_shared_copy():
    """复制共享文件夹内的文件/目录"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    src_path = data.get('src', '')
    dst_path = data.get('dst', '')
    if not folder_id or not src_path:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    src = base / src_path
    if not src.exists():
        return jsonify({'error': '源文件不存在'}), 404
    # 目标目录
    if dst_path:
        dst_dir = base / dst_path
    else:
        dst_dir = base
    if not dst_dir.exists():
        return jsonify({'error': '目标目录不存在'}), 404
    # 处理同名文件
    dst = dst_dir / src.name
    if dst.exists():
        # 加序号
        stem = src.stem
        suffix = src.suffix
        i = 1
        while dst.exists():
            dst = dst_dir / f'{stem}_{i}{suffix}'
            i += 1
    try:
        import shutil
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        # 记录新文件的元数据
        new_rel = str(dst.relative_to(base))
        if src.is_dir():
            # 复制目录内所有文件的元数据
            for root, dirs, files in os.walk(str(src)):
                for f in files:
                    old_file = Path(root) / f
                    old_rel = str(old_file.relative_to(base))
                    new_file = Path(str(src).replace(str(src), str(dst))) / f
                    new_file_rel = str(new_file.relative_to(base))
                    record_shared_file_meta(folder_id, new_file_rel, user['username'])
        else:
            record_shared_file_meta(folder_id, new_rel, user['username'])
    except Exception as e:
        return jsonify({'error': f'复制失败: {e}'}), 500
    return jsonify({'ok': True})


@app.route('/api/shared/rename', methods=['POST'])
@login_required
def api_shared_rename():
    """重命名共享文件夹"""
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    new_name = data.get('name', '').strip()
    if not folder_id or not new_name:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['owner'] != user['username'] and not is_admin(user['username']):
        return jsonify({'error': '仅创建者或管理员可重命名'}), 403
    # 重命名磁盘目录
    if folder['type'] == 'public':
        old_path = SHARED_DIR / 'public' / folder['name']
        new_path = SHARED_DIR / 'public' / new_name
    else:
        old_path = SHARED_DIR / 'private' / folder['name']
        new_path = SHARED_DIR / 'private' / new_name
    if new_path.exists():
        return jsonify({'error': '同名文件夹已存在'}), 400
    try:
        old_path.rename(new_path)
    except Exception as e:
        return jsonify({'error': f'重命名失败: {e}'}), 500
    # 更新数据库
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('UPDATE shared_folders SET name = ?, display_name = ? WHERE id = ?',
              (new_name, new_name, int(folder_id)))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/shared/download')
@login_required
def api_shared_download():
    """下载共享文件夹中的文件"""
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    file_path = request.args.get('path', '')
    if not folder_id or not file_path:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = base / file_path
    if not target.exists() or not target.is_file():
        return jsonify({'error': '文件不存在'}), 404
    return send_from_directory(str(target.parent), target.name, as_attachment=True)


@app.route('/api/shared/users')
@login_required
def api_shared_users():
    """获取所有注册用户列表（供邀请时选择，含磁盘用户）"""
    db_users = get_all_users()
    db_names = {u['username'] for u in db_users}
    all_users = list(db_users)
    users_dir = Path(__file__).resolve().parent / 'users'
    if users_dir.exists():
        for d in sorted(users_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.') and d.name not in db_names:
                all_users.append({'username': d.name, 'created_at': '-', 'role': 'user'})
    cur_user = get_current_user()
    result = [{'username': u['username']} for u in all_users if u['username'] != cur_user['username']]
    return jsonify(result)


# ==================== 个人资料 API ====================

@app.route('/api/profile')
@login_required
def api_get_profile():
    """获取当前用户个人资料"""
    user = get_current_user()
    profile = get_user_profile(user['username'])
    avatar_path = get_user_avatar_path(user['username'])
    profile['has_avatar'] = avatar_path is not None
    return jsonify(profile)


@app.route('/api/profile/update', methods=['POST'])
@login_required
def api_update_profile():
    """更新个人资料"""
    user = get_current_user()
    data = request.get_json() or {}
    profile = get_user_profile(user['username'])
    if 'display_name' in data:
        profile['display_name'] = data['display_name'].strip() or user['username']
    if 'bio' in data:
        profile['bio'] = data['bio'].strip()
    if 'signature' in data:
        profile['signature'] = data['signature'].strip()
    if 'theme' in data and data['theme'] in ('dark', 'light'):
        profile['theme'] = data['theme']
    save_user_profile(user['username'], profile)
    return jsonify({'ok': True, 'profile': profile})


@app.route('/api/profile/avatar', methods=['POST'])
@login_required
def api_upload_avatar():
    """上传头像"""
    user = get_current_user()
    if 'avatar' not in request.files:
        return jsonify({'error': '未选择文件'}), 400
    file = request.files['avatar']
    if not file.filename:
        return jsonify({'error': '未选择文件'}), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return jsonify({'error': '仅支持 PNG/JPG/GIF/WebP 格式'}), 400
    profile_dir = get_user_profile_dir(user['username'])
    profile_dir.mkdir(parents=True, exist_ok=True)
    # 删除旧头像
    old = get_user_avatar_path(user['username'])
    if old:
        old.unlink()
    save_path = profile_dir / f'avatar{ext}'
    file.save(str(save_path))
    return jsonify({'ok': True, 'url': f'/api/profile/avatar/{user["username"]}?_={int(time.time())}'})


@app.route('/api/profile/cover', methods=['POST'])
@login_required
def api_upload_cover():
    """上传封面图"""
    user = get_current_user()
    if 'cover' not in request.files:
        return jsonify({'error': '未选择文件'}), 400
    file = request.files['cover']
    if not file.filename:
        return jsonify({'error': '未选择文件'}), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return jsonify({'error': '仅支持 PNG/JPG/GIF/WebP 格式'}), 400
    profile_dir = get_user_profile_dir(user['username'])
    profile_dir.mkdir(parents=True, exist_ok=True)
    # 删除旧封面
    for old_ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
        old = profile_dir / f'cover{old_ext}'
        if old.exists():
            old.unlink()
    save_path = profile_dir / f'cover{ext}'
    file.save(str(save_path))
    return jsonify({'ok': True, 'url': f'/api/profile/cover/{user["username"]}?_={int(time.time())}'})


@app.route('/api/profile/cover/<username>')
def api_serve_cover(username):
    """提供用户封面图"""
    safe_username = os.path.basename(username)
    profile_dir = get_user_profile_dir(safe_username)
    for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
        p = profile_dir / f'cover{ext}'
        if p.exists():
            mime = {
                '.png': 'image/png', '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg', '.gif': 'image/gif',
                '.webp': 'image/webp',
            }.get(ext, 'image/png')
            return send_from_directory(str(profile_dir), f'cover{ext}', mimetype=mime)
    return '', 204


@app.route('/api/profile/avatar/<username>')
def api_serve_avatar(username):
    """提供用户头像"""
    avatar_path = get_user_avatar_path(username)
    if avatar_path and avatar_path.exists():
        ext = avatar_path.suffix.lower()
        mime = {
            '.png': 'image/png', '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg', '.gif': 'image/gif',
            '.webp': 'image/webp',
        }.get(ext, 'image/png')
        return send_from_directory(str(avatar_path.parent), avatar_path.name, mimetype=mime)
    # 默认头像
    initials = username[0].upper() if username else '?'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
      <rect width="100" height="100" rx="50" fill="#334155"/>
      <text x="50" y="62" text-anchor="middle" fill="#e2e8f0" font-size="40" font-weight="bold">{initials}</text>
    </svg>'''
    return Response(svg, mimetype='image/svg+xml')


@app.route('/profile')
@login_required
def profile_page():
    """个人设置页面"""
    user = get_current_user()
    return render_template('profile.html', username=user['username'])




# ==================== 动态 API ====================

# ==================== 动态 API ====================

@app.route('/api/moment_image/<username>/<path:filename>')
@login_required
def serve_moment_image(username, filename):
    """提供动态图片（所有登录用户可访问）"""
    # 安全检查：防止路径遍历
    safe_username = os.path.basename(username)
    safe_filename = os.path.basename(filename)
    user_moments_dir = USERS_DIR / safe_username / 'moments'
    file_path = user_moments_dir / safe_filename
    if not file_path.exists() or not file_path.is_file():
        return jsonify({'error': '文件不存在'}), 404
    # 确保文件在 moments 目录下（防止路径遍历）
    try:
        file_path.resolve().relative_to(user_moments_dir.resolve())
    except ValueError:
        return jsonify({'error': '非法路径'}), 403
    return send_from_directory(str(user_moments_dir), safe_filename)


@app.route('/api/moments', methods=['GET'])
@login_required
def api_get_moments():
    """获取动态列表"""
    user = get_current_user()
    username = request.args.get('username', '')
    offset = int(request.args.get('offset', 0))
    limit = 20
    if username:
        moments = get_user_moments(username, limit, offset)
    else:
        moments = get_moments(limit, offset)
    # 补充点赞信息和头像
    moment_ids = [m['id'] for m in moments]
    like_status = get_moments_like_status(moment_ids, user['username'])
    for m in moments:
        m['liked'] = like_status.get(m['id'], False)
        m['likes'] = get_moment_likes(m['id'])
        m['like_count'] = len(m['likes'])
        # 获取用户显示名
        profile = get_user_profile(m['username'])
        m['display_name'] = profile.get('display_name', m['username'])
    return jsonify({'moments': moments, 'has_more': len(moments) >= limit})


@app.route('/api/moments', methods=['POST'])
@login_required
def api_create_moment():
    """发布动态"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '仅白名单用户可发布'}), 403
    content = request.form.get('content', '').strip()
    if not content and not request.files.getlist('images'):
        return jsonify({'error': '请输入内容或上传图片'}), 400
    # 处理图片上传（最多9张）
    images = []
    files = request.files.getlist('images')
    if files:
        user_dir = get_user_dir(user['username'])
        moments_dir = user_dir / 'moments'
        moments_dir.mkdir(parents=True, exist_ok=True)
        for f in files[:9]:
            if not f.filename:
                continue
            ext = Path(f.filename).suffix.lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif', '.tiff', '.tif'):
                continue
            import time
            ts = int(time.time() * 1000)
            save_name = f'{ts}_{len(images)}{ext}'
            f.save(str(moments_dir / save_name))
            images.append(f'/api/moment_image/{user["username"]}/{save_name}')
    moment_id = create_moment(user['username'], content, ','.join(images))
    if moment_id is None:
        return jsonify({'error': '发布失败'}), 500
    return jsonify({'ok': True, 'id': moment_id})


@app.route('/api/moments/<int:moment_id>', methods=['DELETE'])
@login_required
def api_delete_moment(moment_id):
    """删除动态"""
    user = get_current_user()
    ok, msg = delete_moment(moment_id, user['username'])
    if ok:
        return jsonify({'ok': True})
    else:
        return jsonify({'error': msg}), 403


@app.route('/api/moments/<int:moment_id>/like', methods=['POST'])
@login_required
def api_toggle_like(moment_id):
    """点赞/取消点赞"""
    user = get_current_user()
    liked = toggle_moment_like(moment_id, user['username'])
    likes = get_moment_likes(moment_id)
    return jsonify({'liked': liked, 'like_count': len(likes), 'likes': likes})


@app.route('/user/<username>')
@login_required
def user_profile_page(username):
    """用户主页（查看其他用户的公开资料）"""
    cur_user = get_current_user()
    profile = get_user_profile(username)
    avatar_path = get_user_avatar_path(username)
    profile['has_avatar'] = avatar_path is not None
    profile['username'] = username
    return render_template('user_profile.html', profile=profile, cur_user=cur_user['username'])


# ==================== 在线用户 ====================

@app.route('/online_users')
@login_required
def online_users_page():
    """在线用户页面 - 仅白名单用户可查看"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    return render_template('online_users.html')


@app.route('/api/online_users')
@login_required
def api_online_users():
    """当前在线用户（5分钟内有请求的用户）- 仅白名单用户可查看"""
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '需要白名单权限'}), 403
    cutoff = time.time() - 300  # 5分钟
    user_activity = {}  # username -> {last_time, last_path, ip, requests}
    with VISITORS_LOCK:
        for v in VISITORS:
            try:
                t = datetime.strptime(v['time'], '%Y-%m-%d %H:%M:%S').timestamp()
            except Exception:
                continue
            if t < cutoff:
                continue
            uname = v.get('username', '-')
            if uname == '-':
                continue
            entry = user_activity.setdefault(uname, {
                'username': uname,
                'last_time': v['time'],
                'last_path': v['path'],
                'ip': v['ip'],
                'requests': 0,
            })
            entry['requests'] += 1
            if v['time'] > entry['last_time']:
                entry['last_time'] = v['time']
                entry['last_path'] = v['path']
                entry['ip'] = v['ip']
    result = sorted(user_activity.values(), key=lambda x: x['last_time'], reverse=True)
    return jsonify(result)


# ==================== 全局错误处理 ====================

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

    import sys, os, logging
    if sys.platform == 'win32':
        # 禁用终端 QuickEdit 模式，防止点击/选中终端导致服务器 I/O 阻塞
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value & ~0x0040)
        except Exception:
            pass
        # stdout/stderr 重定向到文件，彻底避免终端阻塞
        _log_f = open('dxw_server.log', 'a', encoding='utf-8')
        sys.stdout = _log_f
        sys.stderr = _log_f

    from waitress import serve
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                        handlers=[logging.FileHandler('dxw_server.log', encoding='utf-8')])
    log.info('启动 DXW 服务器 (waitress) ...')
    log.info('访问地址: http://0.0.0.0:5000')
    log.info('日志文件: dxw_server.log')
    serve(app, host='0.0.0.0', port=5000, threads=8, channel_timeout=300, max_request_body_size=10*1024*1024*1024)
