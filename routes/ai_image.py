# -*- coding: utf-8 -*-
"""AI 图像生成 — 集成胜算云 ModelMesh API"""

import json
import time
import uuid
from pathlib import Path

import requests
from flask import Blueprint, jsonify, render_template, request, send_file
from auth import get_current_user, login_required, USERS_DIR

bp = Blueprint('ai_image', __name__, url_prefix='/ai-image')

API_BASE = 'https://router.shengsuanyun.com/api'


# ── 工具函数 ──────────────────────────────

def _get_config_dir(username):
    return USERS_DIR / username / 'config'


def _get_key_path(username):
    return _get_config_dir(username) / 'ai_key.json'


def _get_history_path(username):
    return _get_config_dir(username) / 'ai_history.json'


def _get_image_cache_dir(username):
    """图片存到 config/ai_images/（不是 uploads/，不占网盘空间）"""
    d = _get_config_dir(username) / 'ai_images'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_key(username):
    p = _get_key_path(username)
    if p.exists():
        try:
            return json.loads(p.read_text('utf-8'))
        except Exception:
            return {}
    return {}


def _save_key(username, api_key):
    p = _get_key_path(username)
    data = {'api_key': api_key, 'updated_at': int(time.time())}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
    return data


def _load_history(username):
    p = _get_history_path(username)
    if p.exists():
        try:
            return json.loads(p.read_text('utf-8'))
        except Exception:
            return []
    return []


def _save_history(username, history):
    p = _get_history_path(username)
    p.write_text(json.dumps(history, ensure_ascii=False, indent=2), 'utf-8')


# ── 页面路由 ──────────────────────────────

@bp.route('/')
@bp.route('')
@login_required
def ai_image_page():
    user = get_current_user()
    key_data = _load_key(user['username'])
    api_key = key_data.get('api_key', '')
    return render_template('ai_image.html',
                           username=user['username'],
                           api_key=api_key,
                           api_base=API_BASE)


# ── API: 保存/更新 API Key ─────────────────

@bp.route('/api/save-key', methods=['POST'])
@login_required
def api_save_key():
    user = get_current_user()
    data = request.get_json() or {}
    api_key = (data.get('api_key') or '').strip()
    if not api_key:
        return jsonify({'error': 'API Key 不能为空'}), 400
    _save_key(user['username'], api_key)
    return jsonify({'ok': True, 'message': 'API Key 保存成功'})


# ── API: 保存生成结果 ──────────────────────

@bp.route('/api/save-result', methods=['POST'])
@login_required
def api_save_result():
    user = get_current_user()
    data = request.get_json() or {}

    prompt = (data.get('prompt') or '').strip()
    image_url = (data.get('image_url') or '').strip()
    model = data.get('model', '')
    size = data.get('size', '')
    cost = data.get('cost', 0)
    elapsed = data.get('elapsed', '')
    input_image = (data.get('input_image') or '').strip()

    if not prompt or not image_url:
        return jsonify({'error': '缺少必要参数'}), 400

    record_id = str(uuid.uuid4())[:8]
    local_url = ''
    input_local_url = ''

    # 下载输出图到本地缓存
    try:
        key_data = _load_key(user['username'])
        api_key = key_data.get('api_key', '')
        cache_dir = _get_image_cache_dir(user['username'])
        local_name = f'ai_{record_id}_{int(time.time())}.png'
        local_path = cache_dir / local_name
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        r = requests.get(image_url, headers=headers, timeout=30)
        if r.status_code == 200:
            local_path.write_bytes(r.content)
            local_url = f'/ai-image/api/image/{user["username"]}/{local_name}'
    except Exception:
        pass  # 下载失败不影响记录保存，前端会 fallback 到 image_url

    # 保存输入图（i2i 模式，base64 dataURL）
    if input_image and input_image.startswith('data:image/'):
        try:
            import base64
            cache_dir = _get_image_cache_dir(user['username'])
            # 从 data:image/xxx;base64, 中提取格式和数据
            header, _, b64data = input_image.partition(',')
            fmt = 'png'
            if 'png' in header:
                fmt = 'png'
            elif 'jpeg' in header or 'jpg' in header:
                fmt = 'jpg'
            elif 'webp' in header:
                fmt = 'webp'
            input_name = f'input_{record_id}_{int(time.time())}.{fmt}'
            input_path = cache_dir / input_name
            input_path.write_bytes(base64.b64decode(b64data))
            input_local_url = f'/ai-image/api/image/{user["username"]}/{input_name}'
        except Exception:
            pass  # 输入图保存失败不影响记录

    record = {
        'id': record_id,
        'timestamp': int(time.time()),
        'prompt': prompt,
        'model': model,
        'size': size,
        'cost': cost,
        'elapsed': elapsed,
        'image_url': image_url,
        'local_url': local_url,
        'input_local_url': input_local_url,
    }

    # 追加到历史
    history = _load_history(user['username'])
    history.insert(0, record)
    if len(history) > 100:
        history = history[:100]
    _save_history(user['username'], history)

    return jsonify({'ok': True, 'record': record})


# ── API: 提供本地缓存的图片 ────────────────

@bp.route('/api/image/<username>/<path:filename>')
@login_required
def serve_image(username, filename):
    cache_path = _get_image_cache_dir(username) / filename
    if not cache_path.exists():
        return jsonify({'error': '图片不存在'}), 404
    return send_file(str(cache_path), mimetype='image/png')


# ── API: 获取历史 ─────────────────────────

@bp.route('/api/history')
@login_required
def api_history():
    user = get_current_user()
    history = _load_history(user['username'])
    return jsonify(history)


# ── API: 删除单条记录 ──────────────────────

@bp.route('/api/delete-record', methods=['POST'])
@login_required
def api_delete_record():
    user = get_current_user()
    data = request.get_json() or {}
    record_id = (data.get('id') or '').strip()

    if not record_id:
        return jsonify({'error': '缺少记录 ID'}), 400

    history = _load_history(user['username'])
    # 找到要删除的记录，清理本地缓存文件
    removed = [r for r in history if r['id'] == record_id]
    for r in removed:
        local_url = r.get('local_url', '')
        if local_url:
            # 提取文件名，构造缓存路径
            try:
                filename = local_url.rsplit('/', 1)[-1]
                cache_path = _get_image_cache_dir(user['username']) / filename
                if cache_path.exists():
                    cache_path.unlink()
            except Exception:
                pass
    history = [r for r in history if r['id'] != record_id]
    _save_history(user['username'], history)

    return jsonify({'ok': True, 'message': '已删除'})


# ── API: 清空全部历史 ──────────────────────

@bp.route('/api/clear-history', methods=['POST'])
@login_required
def api_clear_history():
    user = get_current_user()
    # 清理所有本地缓存图片
    try:
        cache_dir = _get_image_cache_dir(user['username'])
        if cache_dir.exists():
            for f in cache_dir.iterdir():
                if f.is_file():
                    f.unlink()
    except Exception:
        pass
    _save_history(user['username'], [])
    return jsonify({'ok': True, 'message': '已清空历史'})
