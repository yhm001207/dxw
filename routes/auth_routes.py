# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, session, render_template
from auth import verify_user, register_user

bp = Blueprint('auth_routes', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'GET':
        return render_template('login.html')
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
    import time
    session['login_time'] = time.time()
    return jsonify({'ok': True, 'username': user['username']})


@bp.route('/register', methods=['GET', 'POST'])
def register_page():
    if request.method == 'GET':
        return render_template('register.html')
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
    user = verify_user(username, password)
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user.get('role', 'user')
    return jsonify({'ok': True, 'username': user['username']})


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@bp.route('/forgot-password', methods=['GET'])
def forgot_password_page():
    return render_template('forgot_password.html')


@bp.route('/api/password-recovery/request', methods=['POST'])
def request_password_recovery():
    """用户提交密码找回申请"""
    if 'username' not in session:
        return jsonify({'error': '请先登录'}), 401
    data = request.get_json()
    new_password = data.get('new_password', '').strip()
    reason = data.get('reason', '').strip()
    if not new_password or len(new_password) < 6:
        return jsonify({'error': '新密码至少需要6个字符'}), 400
    from auth import request_password_recovery as do_request
    req_id, err = do_request(session['username'], new_password, reason)
    if err:
        return jsonify({'error': err}), 400
    return jsonify({'ok': True, 'id': req_id})


@bp.route('/api/password-recovery/my-status', methods=['GET'])
def my_recovery_status():
    """获取当前用户的密码找回申请状态"""
    if 'username' not in session:
        return jsonify({'error': '请先登录'}), 401
    from auth import get_my_recovery_request
    req = get_my_recovery_request(session['username'])
    return jsonify(req or {'status': 'none'})


@bp.route('/api/password-recovery/pending', methods=['GET'])
def pending_recovery_requests():
    """获取所有待审批的密码找回申请（仅管理员）"""
    if 'username' not in session:
        return jsonify({'error': '请先登录'}), 401
    if session.get('role') not in ('admin', 'super_admin'):
        return jsonify({'error': '无权限'}), 403
    from auth import get_pending_recovery_requests
    return jsonify(get_pending_recovery_requests())


@bp.route('/api/password-recovery/approvals/<int:req_id>', methods=['GET'])
def recovery_approvals(req_id):
    """获取某密码找回申请的审批记录"""
    if 'username' not in session:
        return jsonify({'error': '请先登录'}), 401
    if session.get('role') not in ('admin', 'super_admin'):
        return jsonify({'error': '无权限'}), 403
    from auth import get_recovery_approvals
    return jsonify(get_recovery_approvals(req_id))


@bp.route('/api/password-recovery/approve', methods=['POST'])
def approve_recovery():
    """管理员审批密码找回申请"""
    if 'username' not in session:
        return jsonify({'error': '请先登录'}), 401
    if session.get('role') not in ('admin', 'super_admin'):
        return jsonify({'error': '无权限'}), 403
    data = request.get_json()
    req_id = data.get('req_id')
    decision = data.get('decision')
    if not req_id or decision not in ('approve', 'reject'):
        return jsonify({'error': '参数错误'}), 400
    from auth import submit_recovery_approval
    ok, msg = submit_recovery_approval(req_id, session['username'], decision)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'msg': msg})
