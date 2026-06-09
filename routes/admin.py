# -*- coding: utf-8 -*-
from pathlib import Path

from flask import Blueprint, request, jsonify
from auth import (
    login_required, get_current_user, is_super_admin,
    get_all_users, set_user_role, get_user_role,
    get_all_admins, create_notification, delete_user,
    SUPER_ADMIN_USER,
)
from config import WORK_DIR

bp = Blueprint('admin', __name__)


# ==================== 管理员角色管理 API（仅 super_admin） ====================

@bp.route('/api/admin/list_roles')
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


@bp.route('/api/admin/set_role', methods=['POST'])
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


@bp.route('/api/admin/list')
@login_required
def api_admin_list():
    """获取管理员名录（所有登录用户可查看）"""
    admin_names = get_all_admins()
    result = []
    for name in admin_names:
        role = get_user_role(name)
        result.append({'username': name, 'role': role})
    return jsonify(result)


@bp.route('/api/admin/delete_user', methods=['POST'])
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
