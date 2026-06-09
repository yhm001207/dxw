# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, render_template, Response
from pathlib import Path
import os, json, uuid, time, sqlite3
from datetime import datetime
from auth import (
    login_required, get_current_user, get_all_users,
    send_message, get_inbox, get_sent, mark_message_read,
    get_unread_message_count, delete_message, get_message, get_user_dir,
    DB_PATH,
)
from config import _ATTACHMENTS_DIR

bp = Blueprint('messaging', __name__)


@bp.route('/messages')
@login_required
def messages_page():
    user = get_current_user()
    return render_template('messages.html', username=user['username'])


@bp.route('/api/messages/send', methods=['POST'])
@login_required
def api_send_message():
    user = get_current_user()
    data = request.get_json() or {}
    recipient = data.get('recipient', '').strip()
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()
    attachment_name = data.get('attachment_name', '')
    attachment_path = data.get('attachment_path', '')
    # 兼容旧版：如果只传了 attachment，当作 attachment_name
    if not attachment_name:
        attachment_name = data.get('attachment', '')
    if not recipient or not body:
        return jsonify({'error': '缺少收件人或内容'}), 400
    result = send_message(user['username'], recipient, subject, body, attachment_name, attachment_path)
    msg_id, err = result
    if err:
        return jsonify({'error': f'发送失败: {err}'}), 500
    return jsonify({'ok': True})


@bp.route('/api/messages/upload_attachment', methods=['POST'])
@login_required
def api_upload_attachment():
    user = get_current_user()
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': '未选择文件'}), 400
    user_att_dir = _ATTACHMENTS_DIR / user['username']
    user_att_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix
    safe_name = f'{uuid.uuid4().hex[:8]}_{int(time.time())}{ext}'
    save_path = user_att_dir / safe_name
    file.save(str(save_path))
    return jsonify({
        'ok': True,
        'filename': file.filename,
        'saved_name': safe_name,
        'path': str(save_path.resolve()),
        'size': save_path.stat().st_size,
    })


@bp.route('/api/messages/attachment_download')
@login_required
def api_download_attachment():
    from flask import send_file
    user = get_current_user()
    # 支持 msg_id 查询附件路径
    msg_id = request.args.get('msg_id', '')
    att_index = request.args.get('att_index', '')
    if msg_id:
        msg = get_message(int(msg_id), user['username'])
        if not msg:
            return jsonify({'error': '消息不存在'}), 404
        if user['username'] not in (msg['sender'], msg['recipient']):
            return jsonify({'error': '无权访问'}), 403
        paths = msg.get('attachment_path', [])
        if not paths:
            return jsonify({'error': '附件路径缺失'}), 404
        if att_index and paths:
            try:
                idx = int(att_index)
            except (ValueError, TypeError):
                return jsonify({'error': '附件索引无效'}), 400
            if 0 <= idx < len(paths):
                path = paths[idx]
            else:
                return jsonify({'error': '附件索引无效'}), 400
        elif paths:
            path = paths[0]
        else:
            path = ''
    else:
        path = request.args.get('path', '')
    if not path:
        return jsonify({'error': '缺少路径'}), 400
    p = Path(path)
    if not p.exists() or not p.is_file():
        return jsonify({'error': '文件不存在'}), 404
    att_root = _ATTACHMENTS_DIR.resolve()
    try:
        p.resolve().relative_to(att_root)
    except ValueError:
        return jsonify({'error': '无权访问'}), 403
    return send_file(str(p), as_attachment=True, download_name=p.name)


@bp.route('/api/messages/attachment_move', methods=['POST'])
@login_required
def api_move_attachment():
    import shutil
    user = get_current_user()
    data = request.get_json() or {}
    msg_id = data.get('msg_id')
    att_index = data.get('att_index', 0)
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    msg = get_message(int(msg_id), user['username'])
    if not msg:
        return jsonify({'error': '消息不存在'}), 404
    if user['username'] != msg['recipient']:
        return jsonify({'error': '仅接收者可领取附件'}), 403
    paths = msg.get('attachment_path', [])
    if not paths:
        return jsonify({'error': '无附件'}), 404
    try:
        idx = int(att_index) if att_index != '' else 0
    except (ValueError, TypeError):
        idx = 0
    if idx < 0 or idx >= len(paths):
        return jsonify({'error': '附件索引无效'}), 400
    src_path = Path(paths[idx])
    if not src_path.exists():
        return jsonify({'error': '文件不存在'}), 404
    from auth import get_user_dir
    dst_dir = get_user_dir(user['username'])
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_path = dst_dir / src_path.name
    try:
        shutil.move(str(src_path), str(dst_path))
        # 清空该附件路径
        names = msg.get('attachment_name', [])
        paths[idx] = ''
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute('UPDATE messages SET attachment_path = ? WHERE id = ?',
                  (json.dumps(paths, ensure_ascii=False), int(msg_id)))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'path': str(dst_path.resolve())})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/messages/all_attachments_download')
@login_required
def api_all_attachments_download():
    import zipfile, threading
    user = get_current_user()
    msg_id = request.args.get('msg_id', '')
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    msg = get_message(int(msg_id), user['username'])
    if not msg:
        return jsonify({'error': '消息不存在'}), 404
    if user['username'] not in (msg['sender'], msg['recipient']):
        return jsonify({'error': '无权访问'}), 403
    paths = msg.get('attachment_path', [])
    names = msg.get('attachment_name', [])
    # 过滤有效路径
    valid = []
    for i, p in enumerate(paths):
        if p and Path(p).is_file():
            name = names[i] if i < len(names) else Path(p).name
            valid.append((p, name))
    if not valid:
        return jsonify({'error': '无有效附件'}), 404
    if len(valid) == 1:
        from flask import send_file
        p, name = valid[0]
        return send_file(p, as_attachment=True, download_name=name)
    import tempfile, threading as _threading
    _zip_ready = _threading.Event()
    _zip_path = [None]
    _zip_error = [None]
    def _build_zip():
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            _zip_path[0] = tmp.name
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as zf:
                for p, name in valid:
                    try:
                        zf.write(p, name)
                    except Exception:
                        continue
            tmp.close()
        except Exception as e:
            _zip_error[0] = str(e)
        finally:
            _zip_ready.set()
    _threading.Thread(target=_build_zip, daemon=True).start()
    def generate():
        _zip_ready.wait()
        if _zip_error[0]:
            return
        try:
            with open(_zip_path[0], 'rb') as f:
                while True:
                    chunk = f.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            if _zip_path[0]:
                try:
                    os.unlink(_zip_path[0])
                except Exception:
                    pass
    return Response(generate(), mimetype='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="attachments.zip"', 'Cache-Control': 'no-cache'})


@bp.route('/api/messages/inbox')
@login_required
def api_inbox():
    user = get_current_user()
    inbox = get_inbox(user['username'])
    return jsonify(inbox)


@bp.route('/api/messages/sent')
@login_required
def api_sent():
    user = get_current_user()
    sent = get_sent(user['username'])
    return jsonify(sent)


@bp.route('/api/messages/unread_count')
@login_required
def api_unread_count():
    user = get_current_user()
    count = get_unread_message_count(user['username'])
    return jsonify({'count': count})


@bp.route('/api/messages/read', methods=['POST'])
@login_required
def api_mark_read():
    user = get_current_user()
    data = request.get_json() or {}
    msg_id = data.get('msg_id')
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    mark_message_read(int(msg_id), user['username'])
    return jsonify({'ok': True})


@bp.route('/api/messages/read_all', methods=['POST'])
@login_required
def api_mark_all_read():
    user = get_current_user()
    data = request.get_json() or {}
    msg_ids = data.get('msg_ids', [])
    if not msg_ids:
        return jsonify({'error': '缺少 msg_ids'}), 400
    for mid in msg_ids:
        mark_message_read(int(mid), user['username'])
    return jsonify({'ok': True})


@bp.route('/api/messages/delete', methods=['POST'])
@login_required
def api_delete_message():
    user = get_current_user()
    data = request.get_json() or {}
    msg_id = data.get('msg_id')
    if not msg_id:
        return jsonify({'error': '缺少 msg_id'}), 400
    delete_message(int(msg_id), user['username'])
    return jsonify({'ok': True})
