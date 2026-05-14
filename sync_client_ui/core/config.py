import os
import json
import base64
from pathlib import Path

CONFIG_PATH = Path.home() / '.dxw_sync_config.json'

DEFAULT_CONFIG = {
    'server_url': 'http://localhost:5000',
    'port': 5000,
    'username': '',
    'password_enc': '',
    'sync_folders': [],
    'sync_interval': 60,
    'backup_type': 'incremental',
    'backup_frequency': 'manual',
    'backup_cron': '',
    'version_retention_days': 36500,
    'conflict_strategy': 'keep_newer',
    'retry_count': 3,
    'retry_interval': 30,
    'upload_limit': 0,
    'download_limit': 0,
    'proxy': '',
    'auto_start': False,
    'auto_download': False,
    'minimize_to_tray': True,
    'notify_success': False,
    'notify_failure': True,
    'ui_mode': 'light',
}


def _encrypt(s):
    if not s:
        return ''
    return base64.b64encode(s.encode()).decode()


def _decrypt(s):
    if not s:
        return ''
    try:
        return base64.b64decode(s.encode()).decode()
    except Exception:
        return ''


def load():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            if 'dark_mode' in cfg and 'ui_mode' not in cfg:
                cfg['ui_mode'] = 'dark' if cfg.pop('dark_mode') else 'light'
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save(cfg):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise e


def get_password(cfg):
    return _decrypt(cfg.get('password_enc', ''))


def set_password(cfg, pwd):
    cfg['password_enc'] = _encrypt(pwd)
