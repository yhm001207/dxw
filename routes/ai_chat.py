# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
import json, ssl
import urllib.request
from auth import login_required

bp = Blueprint('ai_chat', __name__)


@bp.route('/api/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    """代理各厂商 AI API 请求"""
    data = request.get_json() or {}
    provider = data.get('provider', 'claude')
    api_key = data.get('apiKey', '')
    base_url = data.get('baseUrl', '')
    model = data.get('model', '')
    messages = data.get('messages', [])

    if not api_key:
        return jsonify({'error': '缺少 API Key'}), 400
    if not messages:
        return jsonify({'error': '缺少消息'}), 400

    ctx = ssl.create_default_context()

    if provider == 'claude':
        url = (base_url or 'https://api.anthropic.com') + '/v1/messages'
        payload = {
            'model': model or 'claude-sonnet-4-20250514',
            'max_tokens': 4096,
            'messages': [{'role': m['role'], 'content': m['content']} for m in messages],
        }
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
    else:
        url = (base_url or 'https://api.openai.com/v1') + '/chat/completions'
        payload = {
            'model': model or 'gpt-4o',
            'max_tokens': 4096,
            'messages': [{'role': m['role'], 'content': m['content']} for m in messages],
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + api_key,
        }

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            result = json.loads(resp.read().decode())

        if provider == 'claude':
            content = result.get('content', [{}])[0].get('text', '')
        else:
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        return jsonify({'content': content})
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode()
        except Exception:
            pass
        return jsonify({'error': f'API 错误 {e.code}: {body[:300]}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
