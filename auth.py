# -*- coding: utf-8 -*-
"""
用户认证模块
处理用户注册、登录、Session 管理
角色体系：
  super_admin  - 超级管理员（ssr），可指派/撤销普通管理员，拥有全部后台权限
  admin        - 普通管理员，可访问后台监管（白名单、流量等），但不能管理角色
  user         - 普通用户
白名单审批体系：
  普通用户申请白名单 → 所有管理员（super_admin + admin）均需同意 → 自动加入白名单
"""

import sqlite3
import hashlib
import secrets
import json
from pathlib import Path
from functools import wraps
from datetime import datetime


DB_PATH = Path(__file__).resolve().parent / 'users.db'
USERS_DIR = Path(__file__).resolve().parent / 'users'

SUPER_ADMIN_USER = 'ssr'


def init_db():
    """初始化用户数据库（含角色字段及白名单审批表迁移）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS whitelist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 白名单申请表
    c.execute('''
        CREATE TABLE IF NOT EXISTS whitelist_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            applicant TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    ''')
    # 管理员审批记录表
    c.execute('''
        CREATE TABLE IF NOT EXISTS whitelist_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            admin_username TEXT NOT NULL,
            decision TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(application_id, admin_username)
        )
    ''')
    conn.commit()

    # 迁移：若旧表没有 role 列，动态添加
    try:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        conn.commit()
    except Exception:
        pass

    # 确保 ssr 的角色始终是 super_admin
    c.execute('UPDATE users SET role = ? WHERE username = ?', ('super_admin', SUPER_ADMIN_USER,))

    # 站内消息表
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 系统通知表
    c.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'system',
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            related_link TEXT DEFAULT '',
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 共享文件夹表
    c.execute('''
        CREATE TABLE IF NOT EXISTS shared_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'public',
            owner TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # 共享文件夹成员表（仅私有文件夹使用）
    c.execute('''
        CREATE TABLE IF NOT EXISTS shared_folder_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            invited_by TEXT NOT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(folder_id, username)
        )
    ''')
    # 共享文件夹邀请表
    c.execute('''
        CREATE TABLE IF NOT EXISTS shared_folder_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            invited_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(folder_id, username)
        )
    ''')
    # 共享文件夹文件元数据表（记录上传者/创建者）
    try:
        c.execute('''
            CREATE TABLE IF NOT EXISTS shared_file_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id INTEGER NOT NULL,
                rel_path TEXT NOT NULL,
                created_by TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(folder_id, rel_path)
            )
        ''')
    except Exception:
        pass
    # 朋友圈动态表
    try:
        c.execute('''
            CREATE TABLE IF NOT EXISTS moments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT DEFAULT '',
                images TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    except Exception:
        pass
    # 朋友圈点赞表
    try:
        c.execute('''
            CREATE TABLE IF NOT EXISTS moment_likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                moment_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(moment_id, username)
            )
        ''')
    except Exception:
        pass
    # 兼容旧数据库：给 messages 表加附件字段
    try:
        c.execute("ALTER TABLE messages ADD COLUMN attachment_name TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE messages ADD COLUMN attachment_path TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()

    # 迁移旧记录：将 UTC 时间转换为本地时间（+8小时，仅执行一次）
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY)")
        c.execute("SELECT 1 FROM _migrations WHERE name = 'utc_to_local'")
        if not c.fetchone():
            import re as _re
            from datetime import timedelta
            utc_offset = timedelta(hours=8)
            for table in ('messages', 'notifications', 'moments'):
                try:
                    c.execute(f'SELECT id, created_at FROM {table}')
                    rows = c.fetchall()
                    for row in rows:
                        rid, ts = row
                        if not ts:
                            continue
                        m = _re.match(r'(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})', ts)
                        if not m:
                            continue
                        try:
                            dt = datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')
                            dt_local = dt + utc_offset
                            new_ts = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                            if new_ts != ts[:19]:
                                c.execute(f'UPDATE {table} SET created_at = ? WHERE id = ?', (new_ts, rid))
                        except Exception:
                            pass
                except Exception:
                    pass
            c.execute("INSERT INTO _migrations (name) VALUES ('utc_to_local')")
        conn.commit()
        conn.close()
    except Exception:
        pass


def hash_password(password, salt=None):
    """使用盐值哈希密码"""
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    return pwd_hash, salt


def register_user(username, password):
    """注册新用户"""
    if len(username) < 3 or len(username) > 20:
        return {'error': '用户名长度需要 3-20 个字符'}
    if len(password) < 6:
        return {'error': '密码长度至少 6 个字符'}

    pwd_hash, salt = hash_password(password)
    role = 'super_admin' if username == SUPER_ADMIN_USER else 'user'

    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT INTO users (username, password_hash, salt, role) VALUES (?, ?, ?, ?)',
            (username, pwd_hash, salt, role)
        )
        conn.commit()
        user_id = c.lastrowid
        conn.close()

        user_dir = USERS_DIR / username
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / 'uploads').mkdir(exist_ok=True)

        return {'ok': True, 'user_id': user_id}
    except sqlite3.IntegrityError:
        return {'error': '用户名已存在'}


def verify_user(username, password):
    """验证用户登录"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT id, password_hash, salt, role FROM users WHERE username = ?',
        (username,)
    )
    row = c.fetchone()
    conn.close()

    if row is None:
        return None

    user_id, stored_hash, salt, role = row
    pwd_hash, _ = hash_password(password, salt)

    if pwd_hash == stored_hash:
        return {'id': user_id, 'username': username, 'role': role}
    return None


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import session, redirect, url_for, request, jsonify
        if 'user_id' not in session:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': '请先登录', 'need_login': True}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function


def get_current_user():
    """获取当前登录用户（含角色）"""
    from flask import session
    if 'user_id' not in session:
        return None
    return {
        'id': session['user_id'],
        'username': session['username'],
        'role': session.get('role', 'user'),
    }


def get_user_role(username):
    """从数据库获取用户角色"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT role FROM users WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return 'user'
    return row[0]


def set_user_role(username, role):
    """
    设置用户角色（仅允许 'admin' 或 'user'）
    super_admin (ssr) 的角色不能被修改。
    """
    if username == SUPER_ADMIN_USER:
        return False
    if role not in ('admin', 'user'):
        return False
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('UPDATE users SET role = ? WHERE username = ?', (role, username))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_user_dir(username):
    """获取用户目录路径"""
    return USERS_DIR / username


def get_user_uploads_dir(username):
    """获取用户上传目录路径"""
    return USERS_DIR / username / 'uploads'


def get_user_profile_dir(username):
    """获取用户个人资料目录路径"""
    return USERS_DIR / username / 'config'


def get_user_profile(username):
    """读取用户个人资料"""
    profile_dir = get_user_profile_dir(username)
    profile_file = profile_dir / 'profile.json'
    default = {
        'display_name': username,
        'bio': '',
        'theme': 'dark',
        'signature': '',
    }
    if profile_file.exists():
        try:
            with open(profile_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k in default:
                data.setdefault(k, default[k])
            return data
        except Exception:
            return default
    return default


def save_user_profile(username, data):
    """保存用户个人资料"""
    profile_dir = get_user_profile_dir(username)
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_file = profile_dir / 'profile.json'
    with open(profile_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True


def get_user_avatar_path(username):
    """获取用户头像路径"""
    profile_dir = get_user_profile_dir(username)
    for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
        p = profile_dir / f'avatar{ext}'
        if p.exists():
            return p
    return None


# ==================== 角色判断 ====================

def is_super_admin(username):
    """是否为超级管理员"""
    return username == SUPER_ADMIN_USER


def is_admin(username):
    """是否为管理员（super_admin 或 admin 均返回 True）"""
    if username == SUPER_ADMIN_USER:
        return True
    role = get_user_role(username)
    return role in ('super_admin', 'admin')


# 向后兼容别名
ADMIN_USER = SUPER_ADMIN_USER


# ==================== 白名单管理 ====================

def get_whitelist():
    """获取白名单用户列表"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT username, added_at FROM whitelist ORDER BY added_at')
    rows = c.fetchall()
    conn.close()
    return [{'username': r[0], 'added_at': r[1]} for r in rows]


def is_whitelisted(username):
    """检查用户是否在白名单中（管理员不受限）"""
    if is_admin(username):
        return True
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT 1 FROM whitelist WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()
    return row is not None


def add_to_whitelist(username):
    """直接添加用户到白名单（绕过审批，供超管使用）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO whitelist (username) VALUES (?)', (username,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def remove_from_whitelist(username):
    """从白名单移除用户"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM whitelist WHERE username = ?', (username,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_all_users():
    """获取所有注册用户（含角色信息）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT username, created_at, role FROM users ORDER BY created_at')
    rows = c.fetchall()
    conn.close()
    return [{'username': r[0], 'created_at': r[1], 'role': r[2]} for r in rows]


# ==================== 白名单审批流程 ====================

def get_all_admins():
    """获取所有管理员列表（super_admin + admin）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE role IN ('super_admin', 'admin') ORDER BY role DESC, username")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def create_application(applicant, reason=''):
    """
    创建白名单申请。
    若已有 pending 申请则拒绝重复提交。
    返回 application id 或 None。
    """
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    # 检查是否已有 pending 申请
    c.execute(
        "SELECT 1 FROM whitelist_applications WHERE applicant = ? AND status = 'pending'",
        (applicant,)
    )
    if c.fetchone():
        conn.close()
        return None, '您已有待审批的申请，请勿重复提交'
    # 检查是否已被加入白名单
    if is_whitelisted(applicant):
        conn.close()
        return None, '您已在白名单中，无需申请'
    c.execute(
        'INSERT INTO whitelist_applications (applicant, reason, status) VALUES (?, ?, ?)',
        (applicant, reason, 'pending')
    )
    app_id = c.lastrowid
    conn.commit()
    conn.close()
    return app_id, None


def get_my_application(username):
    """获取当前用户自己的申请记录（最新一条）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT id, applicant, reason, status, created_at, resolved_at '
        'FROM whitelist_applications WHERE applicant = ? ORDER BY created_at DESC LIMIT 1',
        (username,)
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        'id': row[0],
        'applicant': row[1],
        'reason': row[2],
        'status': row[3],
        'created_at': row[4],
        'resolved_at': row[5],
    }


def get_pending_applications():
    """获取所有待审批的申请（供管理员查看）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT id, applicant, reason, status, created_at '
        'FROM whitelist_applications WHERE status = ? ORDER BY created_at',
        ('pending',)
    )
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            'id': r[0],
            'applicant': r[1],
            'reason': r[2],
            'status': r[3],
            'created_at': r[4],
        })
    return result


def get_application_approvals(app_id):
    """获取某申请的管理员审批记录"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT admin_username, decision, created_at FROM whitelist_approvals '
        'WHERE application_id = ? ORDER BY created_at',
        (app_id,)
    )
    rows = c.fetchall()
    conn.close()
    return [{'admin': r[0], 'decision': r[1], 'at': r[2]} for r in rows]


def submit_approval(app_id, admin_username, decision):
    """
    管理员提交审批意见。
    decision: 'approve' 或 'reject'
    返回：(ok: bool, msg: str)
    """
    if decision not in ('approve', 'reject'):
        return False, '无效的审批决定'

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # 检查申请是否存在且 pending
    c.execute(
        'SELECT id, applicant, status FROM whitelist_applications WHERE id = ?',
        (app_id,)
    )
    row = c.fetchone()
    if row is None:
        conn.close()
        return False, '申请不存在'
    if row[2] != 'pending':
        conn.close()
        return False, f'该申请已处理（状态：{row[2]}）'

    applicant = row[1]

    # UPSERT 审批记录（允许管理员修改决定）
    c.execute(
        'SELECT 1 FROM whitelist_approvals WHERE application_id = ? AND admin_username = ?',
        (app_id, admin_username)
    )
    if c.fetchone():
        c.execute(
            'UPDATE whitelist_approvals SET decision = ?, created_at = CURRENT_TIMESTAMP '
            'WHERE application_id = ? AND admin_username = ?',
            (decision, app_id, admin_username)
        )
    else:
        c.execute(
            'INSERT INTO whitelist_approvals (application_id, admin_username, decision) '
            'VALUES (?, ?, ?)',
            (app_id, admin_username, decision)
        )
    conn.commit()

    # 检查是否需要处理申请
    admins = get_all_admins()
    c.execute(
        'SELECT admin_username, decision FROM whitelist_approvals WHERE application_id = ?',
        (app_id,)
    )
    approvals = {row[0]: row[1] for row in c.fetchall()}
    conn.close()

    has_reject = any(d == 'reject' for d in approvals.values())
    all_approved = all(approvals.get(a) == 'approve' for a in admins)

    if has_reject:
        # 任一管理员拒绝 → 拒绝
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            "UPDATE whitelist_applications SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (app_id,)
        )
        conn.commit()
        conn.close()
        # 通知申请人
        create_notification(applicant, 'whitelist_result', '白名单申请未通过',
            f'您的白名单申请已被 {admin_username} 拒绝', '/controller')
        return True, '已拒绝该申请'

    if all_approved and len(approvals) >= len(admins):
        # 所有管理员都同意 → 通过
        if not add_to_whitelist(applicant):
            # 添加白名单失败，不标记为通过
            return False, '系统错误：无法将用户添加到白名单，请重试或联系开发者'
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            "UPDATE whitelist_applications SET status = 'approved', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (app_id,)
        )
        conn.commit()
        conn.close()
        # 通知申请人
        create_notification(applicant, 'whitelist_result', '白名单申请已通过',
            '恭喜！您的白名单申请已通过，现在可以访问代码编辑器和云盘了', '/upload')
        return True, '申请已通过，用户已加入白名单！'

    return True, '审批意见已提交，等待其他管理员审批'


def count_pending_for_admin(admin_username):
    """统计该管理员还未审批的申请数量（用于通知）"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT COUNT(*) FROM whitelist_applications WHERE status = ? '
        'AND id NOT IN (SELECT application_id FROM whitelist_approvals WHERE admin_username = ?)',
        ('pending', admin_username)
    )
    cnt = c.fetchone()[0]
    conn.close()
    return cnt


# ==================== 站内消息系统 ====================

def send_message(sender, recipient, subject, body, attachment_name='', attachment_path=''):
    """发送站内私信"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT INTO messages (sender, recipient, subject, body, attachment_name, attachment_path, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (sender, recipient, subject, body, attachment_name, attachment_path, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        msg_id = c.lastrowid
        conn.commit()
        conn.close()
        return msg_id, None
    except Exception as e:
        return None, str(e)


def get_inbox(username, limit=50):
    """获取收件箱"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        'SELECT id, sender, recipient, subject, body, is_read, created_at, '
        'attachment_name, attachment_path '
        'FROM messages WHERE recipient = ? ORDER BY created_at DESC LIMIT ?',
        (username, limit)
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_sent(username, limit=50):
    """获取已发送"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        'SELECT id, sender, recipient, subject, body, is_read, created_at, '
        'attachment_name, attachment_path '
        'FROM messages WHERE sender = ? ORDER BY created_at DESC LIMIT ?',
        (username, limit)
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_message(msg_id, username):
    """获取单条消息（仅发送者或接收者可查看）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        'SELECT id, sender, recipient, subject, body, is_read, created_at, '
        'attachment_name, attachment_path '
        'FROM messages WHERE id = ? AND (sender = ? OR recipient = ?)',
        (msg_id, username, username)
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def mark_message_read(msg_id, username):
    """标记消息为已读"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'UPDATE messages SET is_read = 1 WHERE id = ? AND recipient = ?',
            (msg_id, username)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_unread_message_count(username):
    """获取未读消息数"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT COUNT(*) FROM messages WHERE recipient = ? AND is_read = 0',
        (username,)
    )
    cnt = c.fetchone()[0]
    conn.close()
    return cnt


def delete_message(msg_id, username):
    """删除消息（仅收件人或发件人可删除）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'DELETE FROM messages WHERE id = ? AND (recipient = ? OR sender = ?)',
            (msg_id, username, username)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


# ==================== 系统通知 ====================

def create_notification(username, type_, title, message, link=''):
    """创建系统通知"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT INTO notifications (username, type, title, message, related_link, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (username, type_, title, message, link, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_notifications(username, limit=20):
    """获取通知列表"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        'SELECT id, username, type, title, message, related_link, is_read, created_at '
        'FROM notifications WHERE username = ? ORDER BY created_at DESC LIMIT ?',
        (username, limit)
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_unread_notification_count(username):
    """获取未读通知数"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT COUNT(*) FROM notifications WHERE username = ? AND is_read = 0',
        (username,)
    )
    cnt = c.fetchone()[0]
    conn.close()
    return cnt


def mark_notification_read(notif_id, username):
    """标记通知已读"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'UPDATE notifications SET is_read = 1 WHERE id = ? AND username = ?',
            (notif_id, username)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def mark_all_notifications_read(username):
    """标记所有通知已读"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'UPDATE notifications SET is_read = 1 WHERE username = ? AND is_read = 0',
            (username,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_notification(notif_id, username):
    """删除单条通知"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM notifications WHERE id = ? AND username = ?', (notif_id, username))
        conn.commit()
        deleted = c.rowcount
        conn.close()
        return deleted > 0
    except Exception:
        return False


def delete_all_notifications(username):
    """删除该用户所有通知"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('DELETE FROM notifications WHERE username = ?', (username,))
        deleted = c.rowcount
        conn.commit()
        conn.close()
        return deleted
    except Exception:
        return 0


# ==================== 共享文件夹 ====================

def sync_shared_folders_from_disk(shared_dir):
    """将磁盘上存在但数据库中没有的共享文件夹补录到数据库，无主文件夹归超级管理员"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('SELECT name FROM shared_folders')
        db_names = {r[0] for r in c.fetchall()}
        for share_type in ('public', 'private'):
            type_dir = Path(shared_dir) / share_type
            if not type_dir.exists():
                continue
            for d in type_dir.iterdir():
                if d.is_dir() and d.name not in db_names:
                    c.execute(
                        'INSERT INTO shared_folders (name, display_name, type, owner) VALUES (?, ?, ?, ?)',
                        (d.name, d.name, share_type, SUPER_ADMIN_USER)
                    )
                    # 同时将超级管理员加入成员表
                    folder_id = c.lastrowid
                    c.execute(
                        'INSERT OR IGNORE INTO shared_folder_members (folder_id, username, invited_by) VALUES (?, ?, ?)',
                        (folder_id, SUPER_ADMIN_USER, '')
                    )
        # 已有的无主文件夹也归超级管理员
        c.execute("UPDATE shared_folders SET owner = ? WHERE owner = '' OR owner IS NULL", (SUPER_ADMIN_USER,))
        # 补录已有文件的元数据（没有创建者记录的文件归超级管理员）
        for share_type in ('public', 'private'):
            type_dir = Path(shared_dir) / share_type
            if not type_dir.exists():
                continue
            for d in type_dir.iterdir():
                if not d.is_dir():
                    continue
                # 查找对应的 folder_id
                c.execute('SELECT id FROM shared_folders WHERE name = ? AND type = ?', (d.name, share_type))
                frow = c.fetchone()
                if not frow:
                    continue
                fid = frow[0]
                # 递归扫描所有文件，补录缺失的元数据
                for fp in d.rglob('*'):
                    if fp.is_dir():
                        continue
                    rel = str(fp.relative_to(d)).replace('\\', '/')
                    c.execute('SELECT id FROM shared_file_meta WHERE folder_id = ? AND rel_path = ?', (fid, rel))
                    if not c.fetchone():
                        c.execute(
                            'INSERT OR IGNORE INTO shared_file_meta (folder_id, rel_path, created_by, created_at) VALUES (?, ?, ?, datetime("now","localtime"))',
                            (fid, rel, SUPER_ADMIN_USER)
                        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def create_shared_folder(name, display_name, type_, owner=''):
    """创建共享文件夹"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT INTO shared_folders (name, display_name, type, owner) VALUES (?, ?, ?, ?)',
            (name, display_name, type_, owner)
        )
        folder_id = c.lastrowid
        conn.commit()
        conn.close()
        return folder_id, None
    except Exception as e:
        return None, str(e)


def add_shared_folder_member(folder_id, username, invited_by):
    """添加共享文件夹成员"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT OR IGNORE INTO shared_folder_members (folder_id, username, invited_by) VALUES (?, ?, ?)',
            (folder_id, username, invited_by)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_accessible_shared_folders(username):
    """获取用户可访问的共享文件夹列表（公共 + 自己是成员的私有）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT DISTINCT f.id, f.name, f.display_name, f.type, f.owner, f.created_at
        FROM shared_folders f
        LEFT JOIN shared_folder_members m ON f.id = m.folder_id
        WHERE f.type = 'public' OR m.username = ? OR f.owner = ? OR (f.type = 'private' AND f.owner = '')
        ORDER BY f.type, f.created_at DESC
    ''', (username, username))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_shared_folder_members(folder_id):
    """获取共享文件夹成员列表"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'SELECT username, invited_by, joined_at FROM shared_folder_members WHERE folder_id = ? ORDER BY joined_at',
        (folder_id,)
    )
    rows = c.fetchall()
    conn.close()
    return [{'username': r[0], 'invited_by': r[1], 'joined_at': r[2]} for r in rows]


def delete_shared_folder(folder_id, username):
    """删除共享文件夹记录（创建者或管理员可删除）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        # 创建者或管理员均可删除
        c.execute('SELECT owner FROM shared_folders WHERE id = ?', (folder_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False
        owner = row[0]
        if owner != username:
            # 检查是否为管理员
            if not is_admin(username):
                conn.close()
                return False
        c.execute('DELETE FROM shared_folders WHERE id = ?', (folder_id,))
        if c.rowcount:
            c.execute('DELETE FROM shared_folder_members WHERE folder_id = ?', (folder_id,))
            c.execute('DELETE FROM shared_folder_invitations WHERE folder_id = ?', (folder_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def invite_to_shared_folder(folder_id, username, invited_by):
    """邀请用户加入私有共享文件夹（创建待确认邀请）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT OR IGNORE INTO shared_folder_invitations (folder_id, username, invited_by) VALUES (?, ?, ?)',
            (folder_id, username, invited_by)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def accept_shared_invitation(folder_id, username):
    """接受共享文件夹邀请"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            "UPDATE shared_folder_invitations SET status = 'accepted' WHERE folder_id = ? AND username = ? AND status = 'pending'",
            (folder_id, username)
        )
        if c.rowcount == 0:
            conn.close()
            return False, '邀请不存在或已处理'
        # 加入成员表
        inv = c.execute(
            'SELECT invited_by FROM shared_folder_invitations WHERE folder_id = ? AND username = ?',
            (folder_id, username)
        ).fetchone()
        invited_by = inv[0] if inv else ''
        c.execute(
            'INSERT OR IGNORE INTO shared_folder_members (folder_id, username, invited_by) VALUES (?, ?, ?)',
            (folder_id, username, invited_by)
        )
        conn.commit()
        conn.close()
        return True, '已加入共享文件夹'
    except Exception as e:
        return False, str(e)


def reject_shared_invitation(folder_id, username):
    """拒绝共享文件夹邀请"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            "UPDATE shared_folder_invitations SET status = 'rejected' WHERE folder_id = ? AND username = ? AND status = 'pending'",
            (folder_id, username)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_pending_invitations(username):
    """获取用户待处理的邀请列表"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT i.id, i.folder_id, i.invited_by, i.created_at,
               f.display_name as folder_name, f.type as folder_type
        FROM shared_folder_invitations i
        JOIN shared_folders f ON i.folder_id = f.id
        WHERE i.username = ? AND i.status = 'pending'
        ORDER BY i.created_at DESC
    ''', (username,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def delete_user(username, keep_files=False):
    """
    彻底删除用户：
    - 删除用户记录
    - 删除白名单数据
    - 删除消息和通知
    - 删除共享文件夹（如为该用户创建）
    - 可选保留或删除个人云盘文件
    返回 (ok: bool, msg: str)
    """
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        # 1. 删除用户记录
        c.execute('DELETE FROM users WHERE username = ?', (username,))
        if c.rowcount == 0:
            conn.close()
            return False, '用户不存在'
        # 2. 删除白名单
        c.execute('DELETE FROM whitelist WHERE username = ?', (username,))
        # 3. 删除白名单申请
        c.execute('DELETE FROM whitelist_applications WHERE applicant = ?', (username,))
        # 4. 删除白名单审批记录
        c.execute('DELETE FROM whitelist_approvals WHERE admin_username = ?', (username,))
        # 5. 删除消息（发送和接收）
        c.execute('DELETE FROM messages WHERE sender = ? OR recipient = ?', (username, username))
        # 6. 删除通知
        c.execute('DELETE FROM notifications WHERE username = ?', (username,))
        # 7. 删除共享文件夹成员记录
        c.execute('DELETE FROM shared_folder_members WHERE username = ?', (username,))
        # 7b. 删除共享文件夹邀请记录
        c.execute('DELETE FROM shared_folder_invitations WHERE username = ? OR invited_by = ?', (username, username))
        # 8. 删除该用户创建的共享文件夹记录
        c.execute('SELECT id, name, type FROM shared_folders WHERE owner = ?', (username,))
        owned_folders = c.fetchall()
        owned_ids = [str(f[0]) for f in owned_folders]
        if owned_ids:
            for fid in owned_ids:
                c.execute('DELETE FROM shared_folder_members WHERE folder_id = ?', (fid,))
        c.execute('DELETE FROM shared_folders WHERE owner = ?', (username,))
        conn.commit()
        conn.close()
    except Exception as e:
        conn.close()
        return False, str(e)

    # 删除用户个人云盘文件（可选）
    user_dir = USERS_DIR / username
    if user_dir.exists():
        if not keep_files:
            import shutil
            shutil.rmtree(str(user_dir), ignore_errors=True)

    return True, '用户已删除'


def record_shared_file_meta(folder_id, rel_path, created_by):
    """记录共享文件夹中文件/目录的创建者信息"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT OR REPLACE INTO shared_file_meta (folder_id, rel_path, created_by, created_at) VALUES (?, ?, ?, datetime("now","localtime"))',
            (int(folder_id), rel_path, created_by)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_shared_files_meta(folder_id, rel_paths):
    """批量获取共享文件夹中文件的元数据（创建者、创建时间）"""
    if not rel_paths:
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        placeholders = ','.join(['?'] * len(rel_paths))
        c.execute(
            'SELECT rel_path, created_by, created_at FROM shared_file_meta WHERE folder_id = ? AND rel_path IN (' + placeholders + ')',
            [int(folder_id)] + list(rel_paths)
        )
        rows = c.fetchall()
        conn.close()
        result = {}
        for row in rows:
            result[row[0]] = {'created_by': row[1], 'created_at': row[2]}
        return result
    except Exception:
        return {}


# ==================== 朋友圈 ====================

def create_moment(username, content, images=''):
    """发布朋友圈动态"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            'INSERT INTO moments (username, content, images, created_at) VALUES (?, ?, ?, ?)',
            (username, content, images, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        moment_id = c.lastrowid
        conn.commit()
        conn.close()
        return moment_id
    except Exception:
        return None


def get_moments(limit=20, offset=0):
    """获取朋友圈动态列表（所有白名单用户的）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            'SELECT m.id, m.username, m.content, m.images, m.created_at, '
            'u.role FROM moments m LEFT JOIN users u ON m.username = u.username '
            'ORDER BY m.created_at DESC LIMIT ? OFFSET ?',
            (limit, offset)
        )
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def get_user_moments(username, limit=20, offset=0):
    """获取某用户的朋友圈动态"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            'SELECT id, username, content, images, created_at FROM moments '
            'WHERE username = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (username, limit, offset)
        )
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def delete_moment(moment_id, username):
    """删除朋友圈动态（仅作者或管理员）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('SELECT username FROM moments WHERE id = ?', (moment_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return False, '动态不存在'
        if row[0] != username and not is_admin(username):
            conn.close()
            return False, '无权删除'
        c.execute('DELETE FROM moment_likes WHERE moment_id = ?', (moment_id,))
        c.execute('DELETE FROM moments WHERE id = ?', (moment_id,))
        conn.commit()
        conn.close()
        return True, '已删除'
    except Exception as e:
        return False, str(e)


def toggle_moment_like(moment_id, username):
    """点赞/取消点赞，返回当前是否已赞"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('SELECT id FROM moment_likes WHERE moment_id = ? AND username = ?',
                  (moment_id, username))
        if c.fetchone():
            c.execute('DELETE FROM moment_likes WHERE moment_id = ? AND username = ?',
                      (moment_id, username))
            conn.commit()
            conn.close()
            return False  # 取消赞
        else:
            c.execute('INSERT INTO moment_likes (moment_id, username) VALUES (?, ?)',
                      (moment_id, username))
            conn.commit()
            conn.close()
            return True  # 已赞
    except Exception:
        return False


def get_moment_likes(moment_id):
    """获取动态的点赞列表"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('SELECT username FROM moment_likes WHERE moment_id = ? ORDER BY created_at',
                  (moment_id,))
        rows = c.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def get_moments_like_status(moment_ids, username):
    """批量查询用户对多条动态的点赞状态"""
    if not moment_ids:
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        placeholders = ','.join(['?'] * len(moment_ids))
        c.execute(
            'SELECT moment_id FROM moment_likes WHERE username = ? AND moment_id IN (' + placeholders + ')',
            [username] + list(moment_ids)
        )
        rows = c.fetchall()
        conn.close()
        return {r[0]: True for r in rows}
    except Exception:
        return {}