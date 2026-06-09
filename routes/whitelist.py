from flask import Blueprint, request, jsonify, session
from pathlib import Path
from datetime import datetime
import threading
import sqlite3

from auth import (
    login_required, get_current_user, is_admin, is_super_admin,
    get_whitelist, add_to_whitelist, remove_from_whitelist,
    get_all_users, get_all_admins, get_user_role, DB_PATH,
    create_notification, get_user_dir,
    is_whitelisted, create_application, get_my_application,
    get_application_approvals, get_pending_applications, submit_approval,
)
from config import WORK_DIR
from state import _WHITELIST_CHANGE_LOG, _WHITELIST_CHANGE_LOCK

bp = Blueprint('whitelist', __name__)


# ==================== 白名单 API ====================

@bp.route('/api/whitelist')
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
    users_dir = Path(__file__).resolve().parent.parent / 'users'
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


@bp.route('/api/whitelist/add', methods=['POST'])
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


@bp.route('/api/whitelist/remove', methods=['POST'])
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


@bp.route('/api/whitelist/batch', methods=['POST'])
@login_required
def api_whitelist_batch():
    """批量设置白名单（仅超级管理员可操作）"""
    user = get_current_user()
    if not is_super_admin(user['username']):
        return jsonify({'error': '仅超级管理员有权修改白名单'}), 403
    data = request.get_json() or {}
    usernames = data.get('usernames', [])
    new_set = set(usernames)
    conn = sqlite3.connect(str(DB_PATH))
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


@bp.route('/api/whitelist/changes', methods=['GET'])
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


@bp.route('/api/scan_users')
@login_required
def api_scan_users():
    """扫描 users 目录，返回所有存在的用户（含数据库中有的和磁盘上有的）"""
    user = get_current_user()
    if not is_admin(user['username']):
        return jsonify({'error': '无权限'}), 403

    users_dir = Path(__file__).resolve().parent.parent / 'users'
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


# ==================== 白名单审批 API ====================

@bp.route('/api/whitelist/apply', methods=['POST'])
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


@bp.route('/api/whitelist/my_application')
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


@bp.route('/api/whitelist/pending')
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


@bp.route('/api/whitelist/approve', methods=['POST'])
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


@bp.route('/api/whitelist/reject', methods=['POST'])
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
