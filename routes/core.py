# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, send_from_directory, session, jsonify, Response
import subprocess, os, sys, signal, json
from pathlib import Path
from auth import login_required, get_current_user, get_user_role, is_whitelisted, is_admin
from config import WORK_DIR, SCRIPTS, TORCH_PYTHON
from state import _running_processes, _running_lock
from utils import get_env

bp = Blueprint('core', __name__)


@bp.route('/')
@login_required
def lobby():
    user = get_current_user()
    role = get_user_role(user['username'])
    is_wl = is_whitelisted(user['username'])
    return render_template('lobby.html', username=user['username'], user_role=role, user_is_whitelisted=is_wl)


@bp.route('/index')
@login_required
def index():
    user = get_current_user()
    return render_template('index.html', scripts=SCRIPTS, username=user['username'])


@bp.route('/output/<path:filename>')
def serve_output(filename):
    return send_from_directory(str(WORK_DIR), filename)


@bp.route('/controller')
@login_required
def controller():
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
        user_is_whitelisted=is_wl,
    )


def _whitelist_denied():
    """白名单未通过的拒绝页面 — 服务器性能展示"""
    return render_template('showcase.html')


@bp.route('/showcase')
@login_required
def showcase():
    """性能展示页面（非白名单用户可见）"""
    return render_template('showcase.html')


# ==================== 脚本运行 API ====================

@bp.route('/api/scripts')
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


@bp.route('/api/run/<script_name>', methods=['GET'])
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


@bp.route('/api/stop/<script_name>', methods=['POST'])
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
