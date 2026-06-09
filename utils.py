# -*- coding: utf-8 -*-
"""
共享工具函数：路径校验、格式化、环境检测、Webhook 等
"""

import os
import sys
import json
import time
import subprocess
import platform
import shutil
import threading
from pathlib import Path

from config import (
    WORK_DIR, UPLOAD_DIR, SHARED_DIR, SHARED_DIR_PATH,
    TORCH_PYTHON, _ENV_CACHE_TTL, _UPLOAD_CHUNKS_DIR, _ATTACHMENTS_DIR,
)
from state import _env_cache, _env_cache_lock
from auth import get_user_dir, get_accessible_shared_folders


def get_env(gpu_id=None):
    env = os.environ.copy()
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    if gpu_id is not None and gpu_id != '' and gpu_id != 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = str(gpu_id)
    elif gpu_id == 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = '-1'
    return env


def detect_gpus():
    gpus = []
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5,
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 7:
                    gpus.append({
                        'id': int(parts[0]),
                        'name': parts[1],
                        'mem_total': int(parts[2]),
                        'mem_used': int(parts[3]),
                        'mem_free': int(parts[4]),
                        'temp': int(parts[5]),
                        'util': int(parts[6]),
                    })
    except Exception:
        pass
    return gpus


def format_size(b):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


def format_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f'{h}小时{m}分钟'
    return f'{m}分钟'


def _get_dir_size(path):
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += _get_dir_size(entry.path)
    except (PermissionError, OSError):
        pass
    return total


def get_user_settings(username):
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / 'settings.json'
    if settings_path.exists():
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_settings(username, settings):
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / 'settings.json'
    with open(settings_path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def is_shared_folder_allowed(path_obj, username):
    try:
        path_str = str(path_obj.resolve())
        private_dir = str((SHARED_DIR / 'private').resolve())
        if not path_str.lower().startswith(private_dir.lower()):
            return False
        rel = Path(path_str).relative_to(SHARED_DIR / 'private')
        folder_name = str(rel).split('\\')[0].split('/')[0]
        folders = get_accessible_shared_folders(username)
        return any(f['name'] == folder_name for f in folders)
    except Exception:
        return False


def is_path_allowed(path, username=None):
    try:
        path_obj = Path(path).resolve()
        path_lower = str(path_obj).lower()
        upload_dir = str(UPLOAD_DIR.resolve()).lower()

        if username:
            user_dir = get_user_dir(username).resolve()
            user_dir_lower = str(user_dir).lower()
            if path_lower.startswith(user_dir_lower):
                return True

        if path_lower.startswith(upload_dir):
            return True

        shared_lower = str(SHARED_DIR_PATH).lower()
        if path_lower.startswith(shared_lower):
            pub_lower = str((SHARED_DIR / 'public').resolve()).lower()
            if path_lower.startswith(pub_lower):
                return True
            if username:
                return is_shared_folder_allowed(path_obj, username)
            return False

        users_dir = str((WORK_DIR / 'users').resolve()).lower()
        if path_lower.startswith(users_dir) and username:
            return path_lower.startswith(user_dir_lower)
        elif path_lower.startswith(users_dir):
            return False

        blocked_internal = [
            str(_ATTACHMENTS_DIR.resolve()).lower(),
            str(_UPLOAD_CHUNKS_DIR.resolve()).lower(),
        ]
        for b in blocked_internal:
            if path_lower.startswith(b):
                return False

        if path_obj.exists():
            blocked = [
                'c:\\windows\\system32',
                'c:\\windows\\syswow64',
                'c:\\programdata',
            ]
            for b in blocked:
                if path_lower.startswith(b):
                    return False
            return True

        return False
    except Exception:
        return False


def get_available_drives():
    drives = []
    if sys.platform == 'win32':
        import string
        for letter in string.ascii_uppercase:
            drive = f'{letter}:\\'
            try:
                path = Path(drive)
                if path.exists():
                    usage = shutil.disk_usage(drive)
                    drives.append({
                        'letter': letter,
                        'path': drive,
                        'total': usage.total,
                        'free': usage.free,
                    })
            except (OSError, PermissionError):
                pass
    else:
        drives.append({'letter': '/', 'path': '/', 'total': 0, 'free': 0})
    return drives


# ==================== Python 环境检测 ====================

def _refresh_env_cache():
    try:
        result = _detect_python_environments_full()
        with _env_cache_lock:
            _env_cache['data'] = result
            _env_cache['time'] = time.time()
    except Exception:
        pass


def detect_python_environments():
    now = time.time()
    with _env_cache_lock:
        cached = _env_cache['data']
        age = now - _env_cache['time']
    if cached is not None and age < _ENV_CACHE_TTL:
        return cached
    if cached is not None:
        threading.Thread(target=_refresh_env_cache, daemon=True).start()
        return cached
    result = _detect_python_environments_full()
    with _env_cache_lock:
        _env_cache['data'] = result
        _env_cache['time'] = time.time()
    return result


def _detect_python_environments_full():
    environments = []
    environments.append({
        'name': '系统 Python',
        'path': sys.executable,
        'type': 'system',
        'description': f'当前系统 Python ({sys.version_info.major}.{sys.version_info.minor})'
    })

    common_paths = [
        Path(r'C:\Python39\python.exe'),
        Path(r'C:\Python310\python.exe'),
        Path(r'C:\Python311\python.exe'),
        Path(r'C:\Python312\python.exe'),
        Path(r'C:\Python313\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python39\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python310\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python311\python.exe'),
        Path(r'C:\Users\94885\AppData\Local\Programs\Python\Python312\python.exe'),
    ]

    for path in common_paths:
        if path.exists() and str(path) != sys.executable:
            try:
                result = subprocess.run(
                    [str(path), '--version'],
                    capture_output=True, text=True, timeout=5,
                    creationflags=0x08000000 if os.name == 'nt' else 0,
                )
                version = result.stdout.strip() or result.stderr.strip()
                environments.append({
                    'name': f'Python ({path.parent.parent.name})',
                    'path': str(path),
                    'type': 'system',
                    'description': version
                })
            except Exception:
                pass

    if TORCH_PYTHON:
        try:
            result = subprocess.run(
                [TORCH_PYTHON, '--version'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000 if os.name == 'nt' else 0,
            )
            version = result.stdout.strip() or result.stderr.strip()
            environments.append({
                'name': 'PyTorch 环境',
                'path': TORCH_PYTHON,
                'type': 'torch',
                'description': f'PyTorch 环境 - {version}'
            })
        except Exception:
            environments.append({
                'name': 'PyTorch 环境',
                'path': TORCH_PYTHON,
                'type': 'torch',
                'description': 'PyTorch 环境 (torch)'
            })

    try:
        result = subprocess.run(
            ['conda', 'env', 'list', '--json'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
        if result.returncode == 0:
            conda_envs = json.loads(result.stdout)
            for env_path in conda_envs.get('envs', []):
                python_path = Path(env_path) / 'python.exe'
                if python_path.exists():
                    env_name = Path(env_path).name
                    try:
                        ver_result = subprocess.run(
                            [str(python_path), '--version'],
                            capture_output=True, text=True, timeout=5,
                            creationflags=0x08000000 if os.name == 'nt' else 0,
                        )
                        version = ver_result.stdout.strip() or ver_result.stderr.strip()
                    except Exception:
                        version = 'unknown'
                    environments.append({
                        'name': f'Conda: {env_name}',
                        'path': str(python_path),
                        'type': 'conda',
                        'description': f'Conda 环境 {env_name} - {version}'
                    })
    except Exception:
        pass

    try:
        result = subprocess.run(
            ['wsl.exe', 'bash', '-c', 'which python3 python 2>/dev/null | head -1'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            wsl_python = result.stdout.strip()
            ver_result = subprocess.run(
                ['wsl.exe', 'bash', '-c', f'{wsl_python} --version 2>&1'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
                creationflags=0x08000000 if platform.system() == 'Windows' else 0,
            )
            version = ver_result.stdout.strip() or 'WSL Python'
            environments.append({
                'name': 'WSL Python',
                'path': 'wsl:' + wsl_python,
                'type': 'wsl',
                'description': f'WSL - {version}'
            })
        conda_result = subprocess.run(
            ['wsl.exe', 'bash', '-lic', 'conda env list --json 2>/dev/null'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
        )
        if conda_result.returncode == 0 and conda_result.stdout.strip():
            try:
                conda_data = json.loads(conda_result.stdout)
                for env_path in conda_data.get('envs', []):
                    env_name = env_path.rstrip('/').split('/')[-1]
                    py_path = env_path + '/bin/python'
                    environments.append({
                        'name': f'WSL Conda: {env_name}',
                        'path': 'wsl:' + py_path,
                        'type': 'wsl',
                        'description': f'WSL Conda 环境 {env_name}'
                    })
            except Exception:
                pass
    except Exception:
        pass

    for cmd, name in [('matlab', 'MATLAB'), ('octave', 'GNU Octave')]:
        try:
            find_cmd = 'where' if platform.system() == 'Windows' else 'which'
            result = subprocess.run(
                [find_cmd, cmd],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace',
                creationflags=0x08000000 if platform.system() == 'Windows' else 0,
            )
            if result.returncode == 0 and result.stdout.strip():
                exe_path = result.stdout.strip().split('\n')[0].strip().strip('"')
                version = 'installed'
                if cmd == 'matlab':
                    import re as _re
                    ver_match = _re.search(r'R\d{4}[ab]', exe_path)
                    if ver_match:
                        version = ver_match.group(0)
                elif cmd == 'octave':
                    try:
                        ver_result = subprocess.run(
                            [exe_path, '--version'],
                            capture_output=True, text=True, timeout=5,
                            encoding='utf-8', errors='replace',
                            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
                        )
                        if ver_result.returncode == 0 and ver_result.stdout.strip():
                            version = ver_result.stdout.strip().split('\n')[0]
                    except Exception:
                        pass
                environments.append({
                    'name': name,
                    'path': exe_path,
                    'type': 'matlab' if cmd == 'matlab' else 'octave',
                    'description': f'{name} - {version}'
                })
        except Exception:
            pass

    return environments


# ==================== Webhook 通知 ====================

def get_user_webhook(username):
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    webhook_path = config_dir / 'webhook.json'
    if webhook_path.exists():
        try:
            with open(webhook_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'enabled': False, 'provider': '', 'config': {}, 'events': {'complete': True, 'error': True}}


def save_user_webhook(username, webhook):
    config_dir = get_user_dir(username) / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    webhook_path = config_dir / 'webhook.json'
    with open(webhook_path, 'w', encoding='utf-8') as f:
        json.dump(webhook, f, ensure_ascii=False, indent=2)


def send_webhook_notification(username, title, content):
    webhook = get_user_webhook(username)
    if not webhook.get('enabled') or not webhook.get('provider'):
        return
    provider = webhook['provider']
    cfg = webhook.get('config', {})
    try:
        if provider == 'serverchan':
            _notify_serverchan(cfg.get('key', ''), title, content)
        elif provider == 'pushplus':
            _notify_pushplus(cfg.get('token', ''), title, content)
        elif provider == 'wecom':
            _notify_wecom(cfg.get('webhook_url', ''), title, content)
        elif provider == 'custom':
            _notify_custom(cfg.get('url', ''), cfg.get('headers', {}), title, content)
    except Exception as e:
        print(f'[Webhook] 发送失败 ({provider}): {e}')


def _notify_serverchan(key, title, content):
    if not key:
        return
    import urllib.request
    import urllib.parse
    url = f'https://sctapi.ftqq.com/{key}.send'
    data = urllib.parse.urlencode({'title': title, 'desp': content}).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    urllib.request.urlopen(req, timeout=10)


def _notify_pushplus(token, title, content):
    if not token:
        return
    import urllib.request
    url = 'https://www.pushplus.plus/send'
    payload = json.dumps({'token': token, 'title': title, 'content': content, 'template': 'txt'}).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    urllib.request.urlopen(req, timeout=10)


def _notify_wecom(webhook_url, title, content):
    if not webhook_url:
        return
    import urllib.request
    payload = json.dumps({
        'msgtype': 'text',
        'text': {'content': f'{title}\n{content}'}
    }).encode()
    req = urllib.request.Request(webhook_url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    urllib.request.urlopen(req, timeout=10)


def _notify_custom(url, headers, title, content):
    if not url:
        return
    import urllib.request
    payload = json.dumps({'title': title, 'content': content}).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    urllib.request.urlopen(req, timeout=10)


def notify_run_complete(username, filename, success, return_code):
    webhook = get_user_webhook(username)
    if not webhook.get('enabled'):
        return
    events = webhook.get('events', {})
    if success and not events.get('complete', True):
        return
    if not success and not events.get('error', True):
        return
    status = '完成' if success else '失败'
    title = f'脚本运行{status}'
    content = f'文件: {filename}\n返回码: {return_code}'
    send_webhook_notification(username, title, content)
