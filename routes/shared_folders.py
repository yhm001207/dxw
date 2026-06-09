# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, send_from_directory, send_file, Response, after_this_request
import os, shutil, sqlite3, zipfile, io, tempfile, time
from pathlib import Path
from datetime import datetime
from auth import (
    login_required, get_current_user, is_admin, is_whitelisted,
    get_all_users, get_user_dir,
    get_accessible_shared_folders, get_shared_folder_members,
    create_shared_folder, add_shared_folder_member, remove_shared_folder_member,
    leave_shared_folder,
    invite_to_shared_folder, accept_shared_invitation,
    reject_shared_invitation, get_pending_invitations,
    get_shared_files_meta, record_shared_file_meta,
    delete_shared_folder, update_shared_folder_description,
    create_notification,
)
from config import SHARED_DIR
from auth import DB_PATH

bp = Blueprint('shared_folders', __name__)


def _safe_shared_path(base, rel_path):
    """校验路径是否在 base 目录内，返回 resolved Path 或 None"""
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base.resolve())):
        return None
    return target


def _notify_shared_members(folder_id, folder, actor, notif_type, title, message, link=''):
    if folder['type'] == 'public':
        all_users = get_all_users()
        members = [u['username'] for u in all_users if u['username'] != actor]
    else:
        member_rows = get_shared_folder_members(int(folder_id))
        members = [m['username'] for m in member_rows if m['username'] != actor]
    for m in members:
        create_notification(m, notif_type, title, message, link)


@bp.route('/api/shared/list')
@login_required
def api_shared_list():
    user = get_current_user()
    folders = get_accessible_shared_folders(user['username'])
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
            'description': f.get('description', ''),
            'created_at': f['created_at'],
            'member_count': len(member_names),
            'members': member_names,
        })
    return jsonify(result)


@bp.route('/api/shared/create', methods=['POST'])
@login_required
def api_shared_create():
    user = get_current_user()
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    display_name = data.get('display_name', '').strip() or name
    share_type = data.get('type', 'private')
    description = data.get('description', '').strip()
    if not name:
        return jsonify({'error': '文件夹名称不能为空'}), 400
    if share_type not in ('public', 'private'):
        return jsonify({'error': '类型只能是 public 或 private'}), 400
    if share_type == 'public':
        folder_path = SHARED_DIR / 'public' / name
    else:
        folder_path = SHARED_DIR / 'private' / name
    if folder_path.exists():
        return jsonify({'error': '同名共享文件夹已存在'}), 400
    folder_path.mkdir(parents=True, exist_ok=True)
    folder_id, err = create_shared_folder(name, display_name, share_type, user['username'] if share_type == 'private' else '', description)
    if err:
        shutil.rmtree(str(folder_path), ignore_errors=True)
        return jsonify({'error': err}), 500
    if share_type == 'private':
        add_shared_folder_member(folder_id, user['username'], user['username'])
    return jsonify({'ok': True, 'folder_id': folder_id, 'path': str(folder_path)})


@bp.route('/api/shared/list_files')
@login_required
def api_shared_list_files():
    """递归列出共享文件夹目录下所有文件路径"""
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    rel_path = request.args.get('path', '')
    if not folder_id:
        return jsonify({'files': []})
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = _safe_shared_path(base, rel_path or '')
    if not target:
        return jsonify({'error': '路径不合法'}), 403
    if not target.exists():
        return jsonify({'files': []})
    if target.is_file():
        return jsonify({'files': [{'path': rel_path, 'name': target.name}]})
    files = []
    for root, dirs, fnames in os.walk(str(target)):
        for fn in fnames:
            fp = os.path.join(root, fn)
            try:
                rel = os.path.relpath(fp, str(base)).replace('\\', '/')
            except ValueError:
                rel = fn
            files.append({'path': rel, 'name': fn})
    return jsonify({'files': files})


@bp.route('/api/shared/browse')
@login_required
def api_shared_browse():
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    rel_path = request.args.get('path', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问该共享文件夹'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = _safe_shared_path(base, rel_path or '')
    if not target:
        return jsonify({'error': '路径不合法'}), 403
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
        'folder_description': folder.get('description', ''),
        'type': folder['type'],
    })


@bp.route('/api/shared/members')
@login_required
def api_shared_members():
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    members = get_shared_folder_members(int(folder_id))
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    return jsonify(members)


@bp.route('/api/shared/remove_member', methods=['POST'])
@login_required
def api_shared_remove_member():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    target_user = data.get('username', '').strip()
    if not folder_id or not target_user:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    ok, msg = remove_shared_folder_member(int(folder_id), target_user, user['username'])
    if ok:
        create_notification(target_user, 'shared_folder', '已被移除',
            f'您已被 {user["username"]} 从共享文件夹 "{folder["display_name"]}" 中移除', '/files')
        return jsonify({'ok': True, 'msg': msg})
    return jsonify({'error': msg}), 400


@bp.route('/api/shared/leave', methods=['POST'])
@login_required
def api_shared_leave():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    ok, msg = leave_shared_folder(int(folder_id), user['username'])
    if ok:
        member_rows = get_shared_folder_members(int(folder_id))
        for m in member_rows:
            if m['username'] != user['username']:
                create_notification(m['username'], 'shared_folder', '成员退出',
                    f'{user["username"]} 已退出共享文件夹 "{folder["display_name"]}"', '/files')
        return jsonify({'ok': True, 'msg': msg})
    return jsonify({'error': msg}), 400


@bp.route('/api/shared/invite', methods=['POST'])
@login_required
def api_shared_invite():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    target_user = data.get('username', '').strip()
    if not folder_id or not target_user:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder or folder['type'] != 'private':
        return jsonify({'error': '无权邀请'}), 403
    members = get_shared_folder_members(int(folder_id))
    if any(m['username'] == target_user for m in members):
        return jsonify({'error': '该用户已是成员'}), 400
    ok = invite_to_shared_folder(int(folder_id), target_user, user['username'])
    if ok:
        create_notification(target_user, 'shared_folder', '共享文件夹邀请',
            f'{user["username"]} 邀请您加入共享文件夹 "{folder["display_name"]}"', '/messages')
        return jsonify({'ok': True, 'msg': '邀请已发送，等待对方确认'})
    return jsonify({'error': '邀请发送失败'}), 500


@bp.route('/api/shared/accept_invite', methods=['POST'])
@login_required
def api_shared_accept():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    ok, msg = accept_shared_invitation(int(folder_id), user['username'])
    if ok:
        folders = get_accessible_shared_folders(user['username'])
        folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
        if folder and folder.get('owner'):
            create_notification(folder['owner'], 'shared_folder', '邀请已接受',
                f'{user["username"]} 已接受您的共享文件夹邀请', '/files')
        return jsonify({'ok': True, 'msg': msg})
    return jsonify({'error': msg}), 400


@bp.route('/api/shared/reject_invite', methods=['POST'])
@login_required
def api_shared_reject():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    ok = reject_shared_invitation(int(folder_id), user['username'])
    return jsonify({'ok': ok})


@bp.route('/api/shared/pending_invitations')
@login_required
def api_shared_pending():
    user = get_current_user()
    invitations = get_pending_invitations(user['username'])
    return jsonify(invitations)


@bp.route('/api/shared/upload', methods=['POST'])
@login_required
def api_shared_upload():
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
    target = _safe_shared_path(base, rel_path or '')
    if not target:
        return jsonify({'error': '路径不合法'}), 403
    target.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for f in files:
        f.save(str(target / f.filename))
        uploaded.append(f.filename)
        rel = (rel_path + '/' + f.filename) if rel_path else f.filename
        record_shared_file_meta(folder_id, rel, user['username'])
    file_names = ', '.join(uploaded)
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_upload', '共享文件夹文件上传',
                           '{} 向「{}」上传了文件：{}'.format(user['username'], folder['display_name'], file_names),
                           '/files')
    return jsonify({'ok': True, 'uploaded': uploaded})


@bp.route('/api/shared/create_dir', methods=['POST'])
@login_required
def api_shared_create_dir():
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
    parent = _safe_shared_path(base, rel_path or '')
    if not parent:
        return jsonify({'error': '路径不合法'}), 403
    new_dir = parent / dir_name
    new_dir.mkdir(parents=True, exist_ok=True)
    dir_rel = (rel_path + '/' + dir_name) if rel_path else dir_name
    record_shared_file_meta(folder_id, dir_rel, user['username'])
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_upload', '共享文件夹新建目录',
                           '{} 在「{}」中新建了文件夹：{}'.format(user['username'], folder['display_name'], dir_name),
                           '/files')
    return jsonify({'ok': True})


@bp.route('/api/shared/delete', methods=['POST'])
@login_required
def api_shared_delete():
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
        if folder['owner'] != user['username'] and not is_admin(user['username']):
            return jsonify({'error': '仅创建者或管理员可删除共享文件夹'}), 403
        if folder['type'] == 'public':
            base = SHARED_DIR / 'public' / folder['name']
        else:
            base = SHARED_DIR / 'private' / folder['name']
        shutil.rmtree(str(base), ignore_errors=True)
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM shared_file_meta WHERE folder_id = ?', (int(folder_id),))
        conn.commit()
        conn.close()
        delete_shared_folder(int(folder_id), user['username'])
        return jsonify({'ok': True})
    if not file_path:
        return jsonify({'error': '缺少文件路径'}), 400
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = _safe_shared_path(base, file_path)
    if not target:
        return jsonify({'error': '路径不合法'}), 403
    if not target.exists():
        return jsonify({'error': '文件不存在'}), 404
    if target.is_dir():
        shutil.rmtree(str(target), ignore_errors=True)
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM shared_file_meta WHERE folder_id = ? AND (rel_path = ? OR rel_path LIKE ?)',
                  (int(folder_id), file_path, file_path + '/%'))
        conn.commit()
        conn.close()
    else:
        target.unlink()
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM shared_file_meta WHERE folder_id = ? AND rel_path = ?',
                  (int(folder_id), file_path))
        conn.commit()
        conn.close()
    deleted_name = file_path.split('/')[-1]
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_delete', '共享文件夹文件删除',
                           '{} 从「{}」中删除了「{}」'.format(user['username'], folder['display_name'], deleted_name),
                           '/files')
    return jsonify({'ok': True})


@bp.route('/api/shared/rename_item', methods=['POST'])
@login_required
def api_shared_rename_item():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    file_path = data.get('path', '')
    new_name = data.get('new_name', '').strip()
    if not folder_id or not file_path or not new_name:
        return jsonify({'error': '参数不完整'}), 400
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
    target = _safe_shared_path(base, file_path)
    if not target:
        return jsonify({'error': '路径不合法'}), 403
    if not target.exists():
        return jsonify({'error': '文件不存在'}), 404
    new_path = target.parent / new_name
    if new_path.exists():
        return jsonify({'error': '同名文件已存在'}), 400
    try:
        target.rename(new_path)
        new_rel = str(new_path.relative_to(base))
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('UPDATE shared_file_meta SET rel_path = ?, created_by = ? WHERE folder_id = ? AND rel_path = ?',
                  (new_rel, user['username'], int(folder_id), file_path))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'重命名失败: {e}'}), 500
    old_name = file_path.split('/')[-1]
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_rename', '共享文件夹文件重命名',
                           '{} 在「{}」中将「{}」重命名为「{}」'.format(user['username'], folder['display_name'], old_name, new_name),
                           '/files')
    return jsonify({'ok': True})


@bp.route('/api/shared/move', methods=['POST'])
@login_required
def api_shared_move():
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
    src = _safe_shared_path(base, src_path)
    if not src or not src.exists():
        return jsonify({'error': '源文件不存在'}), 404
    if dst_path:
        dst_dir = _safe_shared_path(base, dst_path)
        if not dst_dir:
            return jsonify({'error': '路径不合法'}), 403
    else:
        dst_dir = base
    if not dst_dir.exists():
        return jsonify({'error': '目标目录不存在'}), 404
    try:
        src.resolve().relative_to(dst_dir.resolve())
        return jsonify({'error': '不能移动到自身子目录'}), 400
    except ValueError:
        pass
    dst = dst_dir / src.name
    if dst.exists():
        return jsonify({'error': '目标位置已存在同名文件'}), 400
    try:
        shutil.move(str(src), str(dst))
        new_rel = str(dst.relative_to(base))
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        if src.is_dir():
            old_prefix = src_path + '/'
            new_prefix = new_rel + '/'
            c.execute('UPDATE shared_file_meta SET rel_path = ? || substr(rel_path, ?) WHERE folder_id = ? AND rel_path LIKE ?',
                      (new_prefix, len(old_prefix), int(folder_id), old_prefix + '%'))
            c.execute('UPDATE shared_file_meta SET rel_path = ? WHERE folder_id = ? AND rel_path = ?',
                      (new_rel, int(folder_id), src_path))
        else:
            c.execute('UPDATE shared_file_meta SET rel_path = ? WHERE folder_id = ? AND rel_path = ?',
                      (new_rel, int(folder_id), src_path))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'移动失败: {e}'}), 500
    moved_name = src_path.split('/')[-1]
    _notify_shared_members(folder_id, folder, user['username'],
                           'shared_move', '共享文件夹文件移动',
                           '{} 在「{}」中移动了「{}」'.format(user['username'], folder['display_name'], moved_name),
                           '/files')
    return jsonify({'ok': True})


@bp.route('/api/shared/copy', methods=['POST'])
@login_required
def api_shared_copy():
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
    src = _safe_shared_path(base, src_path)
    if not src or not src.exists():
        return jsonify({'error': '源文件不存在'}), 404
    if dst_path:
        dst_dir = _safe_shared_path(base, dst_path)
        if not dst_dir:
            return jsonify({'error': '路径不合法'}), 403
    else:
        dst_dir = base
    if not dst_dir.exists():
        return jsonify({'error': '目标目录不存在'}), 404
    dst = dst_dir / src.name
    if dst.exists():
        stem = src.stem
        suffix = src.suffix
        i = 1
        while dst.exists():
            dst = dst_dir / f'{stem}_{i}{suffix}'
            i += 1
    try:
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        new_rel = str(dst.relative_to(base))
        if src.is_dir():
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


@bp.route('/api/shared/rename', methods=['POST'])
@login_required
def api_shared_rename():
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
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('UPDATE shared_folders SET name = ?, display_name = ? WHERE id = ?',
              (new_name, new_name, int(folder_id)))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/shared/cross_move', methods=['POST'])
@login_required
def api_shared_cross_move():
    user = get_current_user()
    data = request.get_json() or {}
    src_folder_id = data.get('src_folder_id', '')
    src_path = data.get('src_path', '')
    dst_folder_id = data.get('dst_folder_id', '')
    dst_path = data.get('dst_path', '')
    if not src_folder_id or not src_path or not dst_folder_id:
        return jsonify({'error': '参数不完整'}), 400
    folders = get_accessible_shared_folders(user['username'])
    src_folder = next((f for f in folders if str(f['id']) == str(src_folder_id)), None)
    dst_folder = next((f for f in folders if str(f['id']) == str(dst_folder_id)), None)
    if not src_folder or not dst_folder:
        return jsonify({'error': '无权访问'}), 403
    src_base = SHARED_DIR / ('public' if src_folder['type'] == 'public' else 'private') / src_folder['name']
    dst_base = SHARED_DIR / ('public' if dst_folder['type'] == 'public' else 'private') / dst_folder['name']
    src = _safe_shared_path(src_base, src_path)
    if not src or not src.exists():
        return jsonify({'error': '源文件不存在'}), 404
    dst_dir = _safe_shared_path(dst_base, dst_path) if dst_path else dst_base
    if dst_path and not dst_dir:
        return jsonify({'error': '路径不合法'}), 403
    if not dst_dir.exists():
        dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        return jsonify({'error': '目标位置已存在同名文件'}), 400
    try:
        shutil.move(str(src), str(dst))
        # 更新源文件夹的 meta 记录
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        if src.is_dir():
            old_prefix = src_path + '/'
            c.execute('DELETE FROM shared_file_meta WHERE folder_id = ? AND (rel_path = ? OR rel_path LIKE ?)',
                      (int(src_folder_id), src_path, old_prefix + '%'))
        else:
            c.execute('DELETE FROM shared_file_meta WHERE folder_id = ? AND rel_path = ?',
                      (int(src_folder_id), src_path))
        # 为新位置创建 meta 记录
        new_rel = str(dst.relative_to(dst_base))
        c.execute('INSERT OR REPLACE INTO shared_file_meta (folder_id, rel_path, created_by) VALUES (?, ?, ?)',
                  (int(dst_folder_id), new_rel, user['username']))
        conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'移动失败: {e}'}), 500
    # 通知两个文件夹的成员
    _notify_shared_members(src_folder_id, src_folder, user['username'],
                           'shared_move', '共享文件夹文件移动',
                           '{} 从「{}」中移动了「{}」到「{}」'.format(
                               user['username'], src_folder['display_name'], src_path.split('/')[-1], dst_folder['display_name']),
                           '/files')
    if str(src_folder_id) != str(dst_folder_id):
        _notify_shared_members(dst_folder_id, dst_folder, user['username'],
                               'shared_upload', '共享文件夹文件移入',
                               '{} 向「{}」移入了文件：{}'.format(
                                   user['username'], dst_folder['display_name'], src.name),
                               '/files')
    return jsonify({'ok': True})


_folder_size_cache = {}  # key -> (timestamp, result)
_FOLDER_SIZE_CACHE_TTL = 30  # seconds

@bp.route('/api/shared/folder_size')
@login_required
def api_shared_folder_size():
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    rel_path = request.args.get('path', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400

    cache_key = f'{folder_id}:{rel_path}'
    now = time.time()
    cached = _folder_size_cache.get(cache_key)
    if cached and (now - cached[0]) < _FOLDER_SIZE_CACHE_TTL:
        return jsonify(cached[1])

    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    target = _safe_shared_path(base, rel_path or '')
    if not target:
        return jsonify({'error': '路径不合法'}), 403
    if not target.exists():
        return jsonify({'error': '路径不存在'}), 404
    total_size = 0
    file_count = 0
    dir_count = 0
    try:
        for root, dirs, files in os.walk(str(target)):
            dir_count += len(dirs)
            for f in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                    file_count += 1
                except OSError:
                    pass
                if file_count > 50000:
                    break
            if file_count > 50000:
                break
    except Exception:
        pass
    result = {'size': total_size, 'file_count': file_count, 'dir_count': dir_count}
    _folder_size_cache[cache_key] = (now, result)
    return jsonify(result)


@bp.route('/api/shared/update_description', methods=['POST'])
@login_required
def api_shared_update_description():
    user = get_current_user()
    data = request.get_json() or {}
    folder_id = data.get('folder_id', '')
    description = data.get('description', '').strip()
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    ok = update_shared_folder_description(int(folder_id), description, user['username'])
    if ok:
        return jsonify({'ok': True})
    return jsonify({'error': '更新失败，无权限或文件夹不存在'}), 403


@bp.route('/api/shared/download')
@login_required
def api_shared_download():
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
    target = _safe_shared_path(base, file_path)
    if not target:
        return jsonify({'error': '路径不合法'}), 403
    if not target.exists() or not target.is_file():
        return jsonify({'error': '文件不存在'}), 404
    inline = request.args.get('inline', '') == '1'
    return send_from_directory(str(target.parent), target.name, as_attachment=not inline)


@bp.route('/api/shared/download_zip')
@login_required
def api_shared_download_zip():
    user = get_current_user()
    folder_id = request.args.get('folder_id', '')
    paths_str = request.args.get('paths', '')
    if not folder_id:
        return jsonify({'error': '缺少 folder_id'}), 400
    folders = get_accessible_shared_folders(user['username'])
    folder = next((f for f in folders if str(f['id']) == str(folder_id)), None)
    if not folder:
        return jsonify({'error': '无权访问'}), 403
    if folder['type'] == 'public':
        base = SHARED_DIR / 'public' / folder['name']
    else:
        base = SHARED_DIR / 'private' / folder['name']
    if not base.exists():
        return jsonify({'error': '文件夹不存在'}), 404
    paths = [p.strip() for p in paths_str.split(',') if p.strip()] if paths_str else []
    zip_filename = folder['display_name'] + '.zip'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_STORED) as zf:
            if not paths:
                for root, dirs, files in os.walk(str(base)):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            arcname = os.path.relpath(fp, str(base))
                            zf.write(fp, arcname)
                        except Exception:
                            continue
            else:
                for p in paths:
                    target = _safe_shared_path(base, p)
                    if not target or not target.exists():
                        continue
                    if target.is_file():
                        try:
                            zf.write(str(target), p)
                        except Exception:
                            continue
                    elif target.is_dir():
                        for root, dirs, files in os.walk(str(target)):
                            for f in files:
                                fp = os.path.join(root, f)
                                try:
                                    arcname = os.path.relpath(fp, str(base))
                                    zf.write(fp, arcname)
                                except Exception:
                                    continue
    except Exception as e:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return jsonify({'error': f'打包失败: {e}'}), 500

    @after_this_request
    def cleanup(response):
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        return response

    return send_file(tmp.name, mimetype='application/zip', as_attachment=True, download_name=zip_filename)


@bp.route('/api/shared/users')
@login_required
def api_shared_users():
    db_users = get_all_users()
    db_names = {u['username'] for u in db_users}
    all_users = list(db_users)
    users_dir = Path(__file__).resolve().parent.parent / 'users'
    if users_dir.exists():
        for d in sorted(users_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.') and d.name not in db_names:
                all_users.append({'username': d.name, 'created_at': '-', 'role': 'user'})
    cur_user = get_current_user()
    result = [{'username': u['username']} for u in all_users if u['username'] != cur_user['username']]
    return jsonify(result)
