# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, render_template, send_from_directory, Response
import os, time
from pathlib import Path
from auth import (
    login_required, get_current_user, is_whitelisted,
    get_user_profile, save_user_profile,
    get_user_profile_dir, get_user_avatar_path,
    get_user_moments, get_moments, create_moment,
    delete_moment, toggle_moment_like, get_moment_likes,
    get_moments_like_status, USERS_DIR,
)

bp = Blueprint('profiles', __name__)


@bp.route('/api/profile')
@login_required
def api_get_profile():
    user = get_current_user()
    profile = get_user_profile(user['username'])
    avatar_path = get_user_avatar_path(user['username'])
    profile['has_avatar'] = avatar_path is not None
    return jsonify(profile)


@bp.route('/api/profile/update', methods=['POST'])
@login_required
def api_update_profile():
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


@bp.route('/api/profile/avatar', methods=['POST'])
@login_required
def api_upload_avatar():
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
    old = get_user_avatar_path(user['username'])
    if old:
        old.unlink()
    save_path = profile_dir / f'avatar{ext}'
    file.save(str(save_path))
    return jsonify({'ok': True, 'url': f'/api/profile/avatar/{user["username"]}?_={int(time.time())}'})


@bp.route('/api/profile/cover', methods=['POST'])
@login_required
def api_upload_cover():
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
    for old_ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
        old = profile_dir / f'cover{old_ext}'
        if old.exists():
            old.unlink()
    save_path = profile_dir / f'cover{ext}'
    file.save(str(save_path))
    return jsonify({'ok': True, 'url': f'/api/profile/cover/{user["username"]}?_={int(time.time())}'})


@bp.route('/api/profile/cover/<username>')
def api_serve_cover(username):
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


@bp.route('/api/profile/avatar/<username>')
def api_serve_avatar(username):
    avatar_path = get_user_avatar_path(username)
    if avatar_path and avatar_path.exists():
        ext = avatar_path.suffix.lower()
        mime = {
            '.png': 'image/png', '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg', '.gif': 'image/gif',
            '.webp': 'image/webp',
        }.get(ext, 'image/png')
        return send_from_directory(str(avatar_path.parent), avatar_path.name, mimetype=mime)
    initials = username[0].upper() if username else '?'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
      <rect width="100" height="100" rx="50" fill="#334155"/>
      <text x="50" y="62" text-anchor="middle" fill="#e2e8f0" font-size="40" font-weight="bold">{initials}</text>
    </svg>'''
    return Response(svg, mimetype='image/svg+xml')


@bp.route('/profile')
@login_required
def profile_page():
    user = get_current_user()
    return render_template('profile.html', username=user['username'])


@bp.route('/user/<username>')
@login_required
def user_profile_page(username):
    cur_user = get_current_user()
    profile = get_user_profile(username)
    avatar_path = get_user_avatar_path(username)
    profile['has_avatar'] = avatar_path is not None
    profile['username'] = username
    return render_template('user_profile.html', profile=profile, cur_user=cur_user['username'])


# ==================== 动态 API ====================

@bp.route('/api/moment_image/<username>/<path:filename>')
@login_required
def serve_moment_image(username, filename):
    safe_username = os.path.basename(username)
    safe_filename = os.path.basename(filename)
    user_moments_dir = USERS_DIR / safe_username / 'moments'
    file_path = user_moments_dir / safe_filename
    if not file_path.exists() or not file_path.is_file():
        return jsonify({'error': '文件不存在'}), 404
    try:
        file_path.resolve().relative_to(user_moments_dir.resolve())
    except ValueError:
        return jsonify({'error': '非法路径'}), 403
    return send_from_directory(str(user_moments_dir), safe_filename)


@bp.route('/api/moments', methods=['GET'])
@login_required
def api_get_moments():
    user = get_current_user()
    username = request.args.get('username', '')
    offset = int(request.args.get('offset', 0))
    limit = 20
    if username:
        moments = get_user_moments(username, limit, offset)
    else:
        moments = get_moments(limit, offset)
    moment_ids = [m['id'] for m in moments]
    like_status = get_moments_like_status(moment_ids, user['username'])
    for m in moments:
        m['liked'] = like_status.get(m['id'], False)
        m['likes'] = get_moment_likes(m['id'])
        m['like_count'] = len(m['likes'])
        profile = get_user_profile(m['username'])
        m['display_name'] = profile.get('display_name', m['username'])
    return jsonify({'moments': moments, 'has_more': len(moments) >= limit})


@bp.route('/api/moments', methods=['POST'])
@login_required
def api_create_moment():
    user = get_current_user()
    if not is_whitelisted(user['username']):
        return jsonify({'error': '仅白名单用户可发布'}), 403
    content = request.form.get('content', '').strip()
    if not content and not request.files.getlist('images'):
        return jsonify({'error': '请输入内容或上传图片'}), 400
    images = []
    files = request.files.getlist('images')
    if files:
        from auth import get_user_dir
        user_dir = get_user_dir(user['username'])
        moments_dir = user_dir / 'moments'
        moments_dir.mkdir(parents=True, exist_ok=True)
        for f in files[:9]:
            if not f.filename:
                continue
            ext = Path(f.filename).suffix.lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif', '.tiff', '.tif'):
                continue
            ts = int(time.time() * 1000)
            save_name = f'{ts}_{len(images)}{ext}'
            f.save(str(moments_dir / save_name))
            images.append(f'/api/moment_image/{user["username"]}/{save_name}')
    moment_id = create_moment(user['username'], content, ','.join(images))
    if moment_id is None:
        return jsonify({'error': '发布失败'}), 500
    return jsonify({'ok': True, 'id': moment_id})


@bp.route('/api/moments/<int:moment_id>', methods=['DELETE'])
@login_required
def api_delete_moment(moment_id):
    user = get_current_user()
    ok, msg = delete_moment(moment_id, user['username'])
    if ok:
        return jsonify({'ok': True})
    else:
        return jsonify({'error': msg}), 403


@bp.route('/api/moments/<int:moment_id>/like', methods=['POST'])
@login_required
def api_toggle_like(moment_id):
    user = get_current_user()
    liked = toggle_moment_like(moment_id, user['username'])
    likes = get_moment_likes(moment_id)
    return jsonify({'liked': liked, 'like_count': len(likes), 'likes': likes})
