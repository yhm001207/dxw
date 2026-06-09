# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, send_file
import os, zipfile
from pathlib import Path
from auth import login_required, get_current_user, get_user_dir
from state import _SYNC_SCAN_CACHE

bp = Blueprint('sync', __name__)

_SYNC_SCAN_CACHE_TTL = 60


def _scan_server_dir(sync_root):
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
    if len(_SYNC_SCAN_CACHE) > 50:
        cutoff = now - _SYNC_SCAN_CACHE_TTL * 2
        for k in list(_SYNC_SCAN_CACHE):
            if _SYNC_SCAN_CACHE[k]['mtime'] < cutoff:
                del _SYNC_SCAN_CACHE[k]
    return files


def _scan_entry(entry, root, files):
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


@bp.route('/api/sync/server_tree')
@login_required
def api_sync_server_tree():
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


@bp.route('/api/sync/changes', methods=['POST'])
@login_required
def api_sync_changes():
    user = get_current_user()
    data = request.get_json() or {}
    base_path = data.get('base_path', '')
    client_files = data.get('files', [])

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

    server_files = _scan_server_dir(sync_root)

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


@bp.route('/api/sync/download_batch', methods=['POST'])
@login_required
def api_sync_download_batch():
    import tempfile
    user = get_current_user()
    data = request.get_json() or {}
    paths = data.get('paths', [])
    base_path = data.get('base_path', '')

    if not paths:
        return jsonify({'error': '缺少文件路径'}), 400

    user_dir = get_user_dir(user['username']).resolve()
    sync_root = (user_dir / base_path).resolve() if base_path else user_dir
    if not str(sync_root).startswith(str(user_dir)):
        return jsonify({'error': '路径不合法'}), 400

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
