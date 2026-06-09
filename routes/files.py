# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, render_template, Response
import os, sys, json, time, platform, shutil, zipfile, tempfile as _tempfile, signal, queue
import collections
import psutil
import subprocess
from pathlib import Path
from datetime import datetime
from auth import (
    login_required, get_current_user, get_user_dir, get_user_uploads_dir,
    is_whitelisted, is_admin, get_all_users, get_user_role, get_user_profile,
    get_user_avatar_path, USERS_DIR,
)
from config import WORK_DIR, UPLOAD_DIR, SHARED_DIR, TORCH_PYTHON, BUFFER_MAX_LINES, _ATTACHMENTS_DIR, _UPLOAD_CHUNKS_DIR, INVALID_NAME_CHARS
from state import (
    _running_processes, _running_lock, _upload_running, _upload_lock,
    _file_running, _file_lock, _user_processes, _user_proc_lock,
)
from utils import is_path_allowed, get_env, format_size, format_time, detect_gpus

bp = Blueprint('files', __name__)

# 打包进度跟踪
import threading as _threading
_PACK_PROGRESS = {}  # req_id -> {total, done, ready, error}
_PACK_LOCK = _threading.Lock()


def _count_files(paths, username):
    count = 0
    for path in paths:
        if not is_path_allowed(path, username):
            continue
        target = Path(path).resolve()
        if target.is_file():
            count += 1
        elif target.is_dir():
            for _, _, files in os.walk(str(target)):
                count += len(files)
    return count


# ==================== 文件上传页面 ====================

@bp.route('/upload')
@login_required
def upload_page():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return render_template('showcase.html')
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


@bp.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    from flask import current_app
    user = get_current_user()
    user_uploads_dir = get_user_uploads_dir(user['username'])
    user_uploads_dir.mkdir(parents=True, exist_ok=True)

    if 'file' not in request.files:
        return jsonify({'error': '请求中没有文件字段，请检查表单提交方式'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择任何文件'}), 400

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

    relative_path = request.form.get('relative_path', '')
    if relative_path:
        rel_dir = os.path.dirname(relative_path)
        if rel_dir:
            save_dir = save_dir / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)

    safe_name = os.path.basename(file.filename)
    save_path = save_dir / safe_name
    content_length = request.content_length or 0
    max_size = current_app.config.get('MAX_CONTENT_LENGTH', 0)

    print(f'[UPLOAD] 用户={user["username"]} 文件={safe_name} 大小={content_length} 限制={max_size} 目标={save_path}')

    if max_size and content_length > max_size:
        return jsonify({'error': f'文件太大：{content_length / 1024 / 1024:.1f}MB，超过限制 {max_size / 1024 / 1024:.0f}MB'}), 413

    version_retention_count = request.form.get('version_retention_count', type=int, default=0)
    version_retention_days = request.form.get('version_retention_days', type=int, default=0)
    version_retention_mode = request.form.get('version_retention_mode', 'count')
    if (version_retention_count > 0 or version_retention_days > 0) and save_path.exists():
        versions_dir = save_path.parent / '__versions__' / save_path.name
        versions_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        version_path = versions_dir / f'{timestamp}_{safe_name}'
        try:
            shutil.copy2(str(save_path), str(version_path))
            now = time.time()
            all_versions = sorted(list(versions_dir.iterdir()), key=lambda p: p.stat().st_mtime, reverse=True)
            for old_ver in all_versions:
                keep = True
                if version_retention_mode == 'count' and version_retention_count > 0:
                    idx = all_versions.index(old_ver)
                    if idx >= version_retention_count:
                        keep = False
                if version_retention_mode == 'days' and version_retention_days > 0:
                    age_days = (now - old_ver.stat().st_mtime) / 86400
                    if age_days > version_retention_days:
                        keep = False
                if not keep:
                    try:
                        old_ver.unlink()
                    except Exception:
                        pass
        except Exception as e:
            print(f'[VERSION] 备份旧版本失败: {e}')

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
        'status': 'ok', 'filename': safe_name,
        'file_path': str(save_path.resolve()),
        'size': written, 'size_fmt': f'{written / 1024 / 1024:.1f}MB',
    })


@bp.route('/api/upload_chunk', methods=['POST'])
@login_required
def upload_chunk():
    user = get_current_user()
    chunk = request.files.get('chunk')
    if not chunk:
        return jsonify({'error': '没有切片数据'}), 400
    upload_id = request.form.get('upload_id', '')
    chunk_index = request.form.get('chunk_index', '')
    filename = request.form.get('filename', 'unknown')
    if not upload_id or chunk_index == '':
        return jsonify({'error': '缺少 upload_id 或 chunk_index'}), 400
    chunk_dir = _UPLOAD_CHUNKS_DIR / user['username'] / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f'{chunk_index}.part'
    try:
        chunk.save(str(chunk_path))
    except Exception as e:
        return jsonify({'error': f'切片 {chunk_index} 写入失败：{e}'}), 500
    size = chunk_path.stat().st_size
    return jsonify({'status': 'ok', 'chunk_index': int(chunk_index), 'size': size})


@bp.route('/api/upload_complete', methods=['POST'])
@login_required
def upload_complete():
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
    relative_path = data.get('relative_path', '')
    if relative_path:
        rel_dir = os.path.dirname(relative_path)
        if rel_dir:
            save_dir = save_dir / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / safe_name
    version_retention_count = data.get('version_retention_count', 0)
    version_retention_days = data.get('version_retention_days', 0)
    version_retention_mode = data.get('version_retention_mode', 'count')
    if (version_retention_count > 0 or version_retention_days > 0) and save_path.exists():
        versions_dir = save_path.parent / '__versions__' / save_path.name
        versions_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        version_path = versions_dir / f'{timestamp}_{safe_name}'
        try:
            shutil.copy2(str(save_path), str(version_path))
            now = time.time()
            all_versions = sorted(list(versions_dir.iterdir()), key=lambda p: p.stat().st_mtime, reverse=True)
            for old_ver in list(all_versions):
                keep = True
                if version_retention_mode == 'count' and version_retention_count > 0:
                    idx = all_versions.index(old_ver)
                    if idx >= version_retention_count:
                        keep = False
                if version_retention_mode == 'days' and version_retention_days > 0:
                    age_days = (now - old_ver.stat().st_mtime) / 86400
                    if age_days > version_retention_days:
                        keep = False
                if not keep:
                    try:
                        old_ver.unlink()
                    except Exception:
                        pass
        except Exception as e:
            print(f'[VERSION] 分片上传备份旧版本失败: {e}')
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
    try:
        shutil.rmtree(str(chunk_dir))
    except Exception:
        pass
    return jsonify({
        'status': 'ok', 'filename': safe_name,
        'file_path': str(save_path.resolve()),
        'size': written, 'size_fmt': f'{written / 1024 / 1024:.1f}MB',
    })


# ==================== 版本管理 ====================

@bp.route('/api/versions/list', methods=['POST'])
@login_required
def api_versions_list():
    user = get_current_user()
    data = request.get_json() or {}
    file_path = data.get('path', '')
    if not file_path:
        return jsonify({'error': '缺少文件路径'}), 400
    user_dir = get_user_dir(user['username']).resolve()
    candidate = Path(file_path).resolve()
    if not str(candidate).startswith(str(user_dir)):
        return jsonify({'error': '路径不合法'}), 400
    versions_dir = candidate.parent / '__versions__' / candidate.name
    if not versions_dir.exists():
        return jsonify({'versions': []})
    versions = []
    for vf in sorted(list(versions_dir.iterdir()), key=lambda p: p.stat().st_mtime, reverse=True):
        if vf.is_file():
            ts = vf.stat().st_mtime
            versions.append({
                'name': vf.name, 'path': str(vf.resolve()),
                'size': vf.stat().st_size, 'mtime': ts,
                'mtime_str': datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
            })
    return jsonify({'versions': versions, 'file_path': file_path})


@bp.route('/api/versions/restore', methods=['POST'])
@login_required
def api_version_restore():
    user = get_current_user()
    data = request.get_json() or {}
    file_path = data.get('path', '')
    version_name = data.get('version', '')
    if not file_path or not version_name:
        return jsonify({'error': '缺少参数'}), 400
    user_dir = get_user_dir(user['username']).resolve()
    candidate = Path(file_path).resolve()
    if not str(candidate).startswith(str(user_dir)):
        return jsonify({'error': '路径不合法'}), 400
    version_path = candidate.parent / '__versions__' / candidate.name / version_name
    if not version_path.exists():
        return jsonify({'error': '版本文件不存在'}), 404
    try:
        if candidate.exists():
            versions_dir = candidate.parent / '__versions__' / candidate.name
            versions_dir.mkdir(parents=True, exist_ok=True)
            timestamp = int(time.time() * 1000)
            shutil.copy2(str(candidate), str(versions_dir / f'{timestamp}_{candidate.name}'))
        shutil.copy2(str(version_path), str(candidate))
        return jsonify({'status': 'ok', 'message': f'已恢复版本: {version_name}'})
    except Exception as e:
        return jsonify({'error': f'恢复失败: {e}'}), 500


@bp.route('/api/versions/delete', methods=['POST'])
@login_required
def api_version_delete():
    data = request.get_json() or {}
    file_path = data.get('path', '')
    version_name = data.get('version', '')
    if not file_path or not version_name:
        return jsonify({'error': 'missing params'}), 400
    user = get_current_user()
    user_dir = get_user_dir(user['username']).resolve()
    candidate = Path(file_path).resolve()
    if not str(candidate).startswith(str(user_dir)):
        return jsonify({'error': 'invalid path'}), 400
    version_path = candidate.parent / '__versions__' / candidate.name / version_name
    if not version_path.exists():
        return jsonify({'error': 'version not found'}), 404
    try:
        version_path.unlink()
        versions_dir = version_path.parent
        if versions_dir.exists() and not any(versions_dir.iterdir()):
            versions_dir.rmdir()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 上传文件运行 ====================

@bp.route('/api/run_upload/<filename>')
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
    work_dir_choice = request.args.get('work_dir', 'project')
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
                cmd, cwd=run_cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=get_env(), text=True, encoding='utf-8', errors='replace', bufsize=1,
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
                if f.suffix in ('.png', '.jpg', '.jpeg', '.bmp', '.mat', '.pt', '.csv', '.txt', '.pdf'):
                    generated.append(f.name)
            done = json.dumps({'type': 'done', 'return_code': rc, 'files': generated})
            yield f'data: {done}\n\n'
        except Exception as e:
            err = json.dumps({'type': 'error', 'msg': str(e)})
            yield f'data: {err}\n\n'
        finally:
            with _upload_lock:
                _upload_running.pop(safe_name, None)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/upload_files')
@login_required
def api_upload_files():
    user = get_current_user()
    user_uploads_dir = get_user_uploads_dir(user['username'])
    user_dir = get_user_dir(user['username'])
    since = request.args.get('since', type=float, default=0)
    scan_path = request.args.get('path', '')
    files = []
    seen_names = set()
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
                            'name': f.name, 'size': f.stat().st_size,
                            'mtime': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                            'location': 'scan', 'path': str(f),
                        })
    if user_uploads_dir.exists():
        for f in sorted(user_uploads_dir.iterdir()):
            if f.name.endswith('.py'):
                continue
            if since and f.stat().st_mtime < since:
                continue
            if f.name not in seen_names:
                seen_names.add(f.name)
                files.append({
                    'name': f.name, 'size': f.stat().st_size,
                    'mtime': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'location': 'upload'
                })
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
                        'name': f.name, 'size': f.stat().st_size,
                        'mtime': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'location': 'user'
                    })
    return jsonify(files)


@bp.route('/uploads/<path:filename>')
def serve_upload_file(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)


@bp.route('/api/user_file/<path:filename>')
@login_required
def serve_user_file(filename):
    from flask import send_from_directory
    user = get_current_user()
    user_dir = get_user_dir(user['username'])
    file_path = user_dir / filename
    if not file_path.exists() or not file_path.is_file():
        return jsonify({'error': '文件不存在'}), 404
    return send_from_directory(str(user_dir), filename)


@bp.route('/api/stop_upload/<filename>', methods=['POST'])
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


# ==================== 文件浏览器 ====================

@bp.route('/api/drives')
@login_required
def api_list_drives():
    from utils import get_available_drives
    return jsonify({'drives': get_available_drives()})


@bp.route('/files')
@login_required
def file_browser():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return render_template('showcase.html')
    user_dir = str(get_user_dir(user['username']).resolve())
    return render_template('files.html', user_dir=user_dir, username=user['username'], is_admin=is_admin(user['username']))


@bp.route('/api/my_dir')
@login_required
def api_my_dir():
    user = get_current_user()
    return jsonify({'path': str(get_user_dir(user['username']).resolve())})


@bp.route('/api/files')
@login_required
def api_list_files():
    user = get_current_user()
    path = request.args.get('path', '')
    if not path:
        path = str(WORK_DIR.resolve())
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
            if item.name in ('config', 'moments', 'uploads', 'lcvr_plots', '__versions__') and item.is_dir():
                continue
            try:
                if item.is_dir():
                    items.append({
                        'name': item.name, 'type': 'directory',
                        'path': str(item.resolve()), 'size': '-',
                        'mtime': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    })
                else:
                    items.append({
                        'name': item.name, 'type': 'file',
                        'path': str(item.resolve()), 'size': item.stat().st_size,
                        'mtime': datetime.fromtimestamp(item.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'is_text': item.suffix in ('.txt', '.py', '.md', '.json', '.csv', '.log', '.html', '.css', '.js', '.ipynb', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf', '.sh', '.bat', '.ps1', '.xml', '.svg', '.tex', '.rst', '.env', '.gitignore', '.dockerfile', '.makefile', '.m'),
                        'is_image': item.suffix in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'),
                    })
            except Exception:
                continue
        return jsonify({'current_path': str(p.resolve()), 'items': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _get_dir_size(path):
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
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@bp.route('/api/disk_usage')
@login_required
def api_disk_usage():
    user = get_current_user()
    path = request.args.get('path', '')
    user_dir = get_user_dir(user['username']).resolve()
    user_total = _get_dir_size(str(user_dir))
    dir_size = 0
    if path:
        try:
            p = Path(path).resolve()
            if p.exists() and p.is_dir() and is_path_allowed(str(p), user['username']):
                dir_size = _get_dir_size(str(p))
        except Exception:
            pass
    try:
        usage = shutil.disk_usage(str(user_dir))
        disk_free = usage.free
        disk_total = usage.total
    except Exception:
        disk_free = 0
        disk_total = 0
    return jsonify({
        'user_total': user_total, 'user_total_fmt': _fmt_size(user_total),
        'dir_size': dir_size, 'dir_size_fmt': _fmt_size(dir_size), 'dir_path': path,
        'disk_free': disk_free, 'disk_free_fmt': _fmt_size(disk_free),
        'disk_total': disk_total, 'disk_total_fmt': _fmt_size(disk_total),
    })


@bp.route('/api/file_content')
@login_required
def api_file_content():
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
        raw = p.read_bytes()
        content = None
        for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
            try:
                content = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:
            content = raw.decode('utf-8', errors='replace')
        if limit > 0 or file_size > 50 * 1024 * 1024:
            chunk_size = limit if limit > 0 else 50 * 1024 * 1024
            content = content[offset:offset + chunk_size]
            return jsonify({
                'name': p.name, 'path': str(p.resolve()), 'content': content,
                'size': file_size, 'offset': offset, 'has_more': offset + chunk_size < file_size,
            })
        else:
            return jsonify({'name': p.name, 'path': str(p.resolve()), 'content': content, 'size': file_size})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/download_file')
def api_download_file():
    from flask import send_from_directory
    user = get_current_user()
    if not user:
        return jsonify({'error': '未登录'}), 401
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': '未指定文件路径'}), 400
    p = Path(path)
    if not p.is_absolute():
        user_dir = get_user_dir(user['username']).resolve()
        candidate = (user_dir / path).resolve()
        if str(candidate).startswith(str(user_dir)):
            p = candidate
    if not is_path_allowed(str(p), user['username']):
        return jsonify({'error': '无权访问该文件'}), 403
    try:
        if not p.exists():
            return jsonify({'error': '文件不存在：' + str(p)}), 404
        if p.is_file():
            return send_from_directory(str(p.parent), p.name, as_attachment=True)
        if p.is_dir():
            folder_name = p.name + '.zip'
            def generate_zip():
                tmp = None
                try:
                    tmp = _tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as zf:
                        for root, dirs, files in os.walk(str(p)):
                            for fn in files:
                                fp = os.path.join(root, fn)
                                try:
                                    arcname = os.path.relpath(fp, p.parent)
                                except ValueError:
                                    arcname = os.path.basename(fp)
                                arcname = arcname.replace('\\', '/')
                                try:
                                    zf.write(fp, arcname)
                                except Exception:
                                    continue
                    tmp.close()
                    with open(tmp.name, 'rb') as f:
                        while True:
                            chunk = f.read(4 * 1024 * 1024)
                            if not chunk:
                                break
                            yield chunk
                except Exception:
                    pass
                finally:
                    if tmp:
                        try:
                            os.unlink(tmp.name)
                        except Exception:
                            pass
            return Response(generate_zip(), mimetype='application/zip',
                           headers={'Content-Disposition': f'attachment; filename="{folder_name}"', 'Cache-Control': 'no-cache'})
        return jsonify({'error': '不支持的文件类型'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/file_preview')
def api_file_preview():
    from flask import send_from_directory
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
        ext = p.suffix.lower()
        if ext not in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.pdf'):
            return jsonify({'error': '不支持预览该文件类型'}), 400
        mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.bmp': 'image/bmp', '.gif': 'image/gif', '.webp': 'image/webp',
                    '.pdf': 'application/pdf'}
        mimetype = mime_map.get(ext, 'application/octet-stream')
        return send_from_directory(str(p.parent), p.name, mimetype=mimetype)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/video_stream')
def api_video_stream():
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
                        read_size = min(remaining, 1024 * 1024)
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
            from flask import send_from_directory
            return send_from_directory(str(p.parent), p.name, mimetype=mimetype)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 文件操作 ====================

@bp.route('/api/save_file', methods=['POST'])
@login_required
def api_save_file():
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


@bp.route('/api/create_folder', methods=['POST'])
@login_required
def api_create_folder():
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


@bp.route('/api/delete_file', methods=['POST'])
@login_required
def api_delete_file():
    user = get_current_user()
    data = request.get_json() or {}
    path = data.get('path', '')
    if not path:
        return jsonify({'error': '缺少参数'}), 400
    if not is_path_allowed(path, user['username']):
        return jsonify({'error': '无权访问该路径'}), 403
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


@bp.route('/api/move_file', methods=['POST'])
@login_required
def api_move_file():
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


@bp.route('/api/copy_file', methods=['POST'])
@login_required
def api_copy_file():
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


@bp.route('/api/rename_file', methods=['POST'])
@login_required
def api_rename_file():
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


# ==================== 用户设置 ====================

@bp.route('/api/user_settings', methods=['GET'])
@login_required
def api_get_user_settings():
    from utils import get_user_settings as _get_settings
    user = get_current_user()
    settings = _get_settings(user['username'])
    return jsonify(settings)


@bp.route('/api/user_settings', methods=['POST'])
@login_required
def api_save_user_settings():
    from utils import get_user_settings as _get_settings, save_user_settings as _save_settings
    user = get_current_user()
    data = request.get_json() or {}
    settings = _get_settings(user['username'])
    settings.update(data)
    _save_settings(user['username'], settings)
    return jsonify({'status': 'ok'})


# ==================== 批量操作 ====================

@bp.route('/api/batch_delete', methods=['POST'])
@login_required
def api_batch_delete():
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


@bp.route('/api/user_dir')
@login_required
def api_user_dir():
    user = get_current_user()
    user_dir = get_user_dir(user['username']).resolve()
    return jsonify({'path': str(user_dir)})


@bp.route('/api/batch_move', methods=['POST'])
@login_required
def api_batch_move():
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


@bp.route('/api/list_files')
@login_required
def api_list_files_recursive():
    """递归列出目录下所有文件路径"""
    user = get_current_user()
    path = request.args.get('path', '')
    if not path:
        return jsonify({'files': []})
    p = Path(path)
    if not p.is_absolute():
        user_dir = get_user_dir(user['username']).resolve()
        candidate = (user_dir / path).resolve()
        if str(candidate).startswith(str(user_dir)):
            p = candidate
    if not is_path_allowed(str(p), user['username']):
        return jsonify({'error': '无权访问'}), 403
    if not p.exists() or not p.is_dir():
        return jsonify({'files': []})
    files = []
    for root, dirs, fnames in os.walk(str(p)):
        for fn in fnames:
            fp = os.path.join(root, fn)
            files.append({'path': fp, 'name': fn})
    return jsonify({'files': files})


@bp.route('/api/batch_pack_init', methods=['POST'])
@login_required
def api_batch_pack_init():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    if not paths:
        return jsonify({'error': '缺少参数'}), 400
    total = _count_files(paths, user['username'])
    if total == 0:
        return jsonify({'error': '没有有效文件'}), 400
    import uuid
    req_id = uuid.uuid4().hex[:12]
    ready_event = _threading.Event()
    info = {'total': total, 'done': 0, 'ready': False, 'error': None,
            'zip_path': None, 'ready_event': ready_event}
    with _PACK_LOCK:
        _PACK_PROGRESS[req_id] = info

    def _build_zip():
        try:
            tmp = _tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            info['zip_path'] = tmp.name
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as zf:
                for path in paths:
                    if not is_path_allowed(path, user['username']):
                        continue
                    target = Path(path).resolve()
                    if target.is_file():
                        try:
                            zf.write(str(target), target.name)
                        except Exception:
                            continue
                        with _PACK_LOCK:
                            info['done'] += 1
                    elif target.is_dir():
                        for root, dirs, files in os.walk(str(target)):
                            for fn in files:
                                fp = os.path.join(root, fn)
                                try:
                                    arcname = os.path.relpath(fp, target.parent)
                                except ValueError:
                                    arcname = os.path.basename(fp)
                                arcname = arcname.replace('\\', '/')
                                try:
                                    zf.write(fp, arcname)
                                except Exception:
                                    continue
                                with _PACK_LOCK:
                                    info['done'] += 1
            tmp.close()
        except Exception as e:
            with _PACK_LOCK:
                info['error'] = str(e)
        finally:
            with _PACK_LOCK:
                info['ready'] = True
            ready_event.set()
    _threading.Thread(target=_build_zip, daemon=True).start()
    return jsonify({'req_id': req_id, 'total': total})


@bp.route('/api/batch_pack_progress')
@login_required
def api_batch_pack_progress():
    req_id = request.args.get('req_id', '')
    with _PACK_LOCK:
        info = _PACK_PROGRESS.get(req_id)
    if not info:
        return jsonify({'error': '无效的请求'}), 404
    return jsonify({
        'total': info['total'],
        'done': info['done'],
        'ready': info['ready'],
        'error': info['error'],
    })


@bp.route('/api/batch_pack_cancel', methods=['POST'])
@login_required
def api_batch_pack_cancel():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    req_id = data.get('req_id', '') or request.args.get('req_id', '')
    if not req_id:
        return jsonify({'error': '缺少 req_id'}), 400
    with _PACK_LOCK:
        info = _PACK_PROGRESS.pop(req_id, None)
    if info and info.get('zip_path'):
        try:
            os.unlink(info['zip_path'])
        except Exception:
            pass
    return jsonify({'ok': True})


@bp.route('/api/batch_download', methods=['POST'])
@login_required
def api_batch_download():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    req_id = data.get('req_id', '')
    if not paths and not req_id:
        return jsonify({'error': '缺少参数'}), 400
    # 单文件直接下载
    if len(paths) == 1:
        path = paths[0]
        if not is_path_allowed(path, user['username']):
            return jsonify({'error': '无权访问'}), 403
        from flask import send_from_directory
        target = Path(path).resolve()
        if target.is_file():
            return send_from_directory(str(target.parent), target.name, as_attachment=True)
    # 等待打包完成
    if req_id:
        with _PACK_LOCK:
            info = _PACK_PROGRESS.get(req_id)
        if not info:
            return jsonify({'error': '无效的请求'}), 404
        info['ready_event'].wait()
        if info['error']:
            with _PACK_LOCK:
                _PACK_PROGRESS.pop(req_id, None)
            return jsonify({'error': info['error']}), 500
        zip_path = info['zip_path']
        def generate_and_cleanup():
            try:
                with open(zip_path, 'rb') as f:
                    while True:
                        chunk = f.read(50 * 1024 * 1024)
                        if not chunk:
                            break
                        yield chunk
            finally:
                try:
                    os.unlink(zip_path)
                except Exception:
                    pass
                with _PACK_LOCK:
                    _PACK_PROGRESS.pop(req_id, None)
        return Response(generate_and_cleanup(), mimetype='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="download.zip"', 'Cache-Control': 'no-cache'})
    # 无 req_id：直接打包（兼容旧调用方式）
    _zip_ready = _threading.Event()
    _zip_path = [None]
    _zip_error = [None]
    def _build_zip_direct():
        try:
            tmp = _tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            _zip_path[0] = tmp.name
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as zf:
                for path in paths:
                    if not is_path_allowed(path, user['username']):
                        continue
                    target = Path(path).resolve()
                    if target.is_file():
                        try:
                            zf.write(str(target), target.name)
                        except Exception:
                            continue
                    elif target.is_dir():
                        for root, dirs, files in os.walk(str(target)):
                            for fn in files:
                                fp = os.path.join(root, fn)
                                try:
                                    arcname = os.path.relpath(fp, target.parent)
                                except ValueError:
                                    arcname = os.path.basename(fp)
                                arcname = arcname.replace('\\', '/')
                                try:
                                    zf.write(fp, arcname)
                                except Exception:
                                    continue
            tmp.close()
        except Exception as e:
            _zip_error[0] = str(e)
        finally:
            _zip_ready.set()
    _threading.Thread(target=_build_zip_direct, daemon=True).start()
    def generate_batch_zip():
        _zip_ready.wait()
        if _zip_error[0]:
            return
        try:
            with open(_zip_path[0], 'rb') as f:
                while True:
                    chunk = f.read(50 * 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            if _zip_path[0]:
                try:
                    os.unlink(_zip_path[0])
                except Exception:
                    pass
    return Response(generate_batch_zip(), mimetype='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="download.zip"', 'Cache-Control': 'no-cache'})


@bp.route('/api/batch_download_file')
@login_required
def api_batch_download_file():
    """GET 下载：通过 req_id 获取已打包的 ZIP，触发浏览器原生下载"""
    user = get_current_user()
    req_id = request.args.get('req_id', '')
    if not req_id:
        return jsonify({'error': '缺少 req_id'}), 400
    with _PACK_LOCK:
        info = _PACK_PROGRESS.get(req_id)
    if not info:
        return jsonify({'error': '无效的请求'}), 404
    info['ready_event'].wait()
    if info['error']:
        with _PACK_LOCK:
            _PACK_PROGRESS.pop(req_id, None)
        return jsonify({'error': info['error']}), 500
    zip_path = info['zip_path']
    def generate_and_cleanup():
        try:
            with open(zip_path, 'rb') as f:
                while True:
                    chunk = f.read(50 * 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(zip_path)
            except Exception:
                pass
            with _PACK_LOCK:
                _PACK_PROGRESS.pop(req_id, None)
    return Response(generate_and_cleanup(), mimetype='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="download.zip"', 'Cache-Control': 'no-cache'})
