# -*- coding: utf-8 -*-
"""
集中配置：路径、常量、脚本映射
"""

import os
import sys
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WORK_DIR / 'templates'
UPLOAD_DIR = WORK_DIR / 'uploads'
SHARED_DIR = WORK_DIR / 'shared'
SHARED_DIR_PATH = SHARED_DIR.resolve()

_UPLOAD_CHUNKS_DIR = WORK_DIR / '_upload_chunks'
_ATTACHMENTS_DIR = WORK_DIR / '_message_attachments'

# PyTorch 环境路径
TORCH_PYTHON = None
for _p in [
    Path(r'D:\apps\anaconda\envs\torch\python.exe'),
    Path(r'D:\apps\anaconda3\envs\torch\python.exe'),
]:
    if _p.exists():
        TORCH_PYTHON = str(_p)
        break

# 脚本映射
SCRIPTS = {
    'cascade': {
        'file': 'lcvr_cascade_3lc.py',
        'title': '三级级联调制',
        'desc': '三级级联偏振调制仿真，生成 30 张调制矩阵子图',
        'images': ['lcvr_triple_cascade.png'],
        'needs_torch': False,
    },
    'simple': {
        'file': 'lcvr_simple.py',
        'title': '二级简单级联',
        'desc': '两级级联调制仿真，展示调制矩阵随电压变化',
        'images': ['lcvr_modulation_matrices_final.png'],
        'needs_torch': False,
    },
    'load_pt': {
        'file': 'load_pt_web.py',
        'title': '加载 .pt 相位文件',
        'desc': '加载当前目录下所有 .pt 相位文件，显示相位分布图',
        'images': [],
        'needs_torch': True,
    },
    'sgd_dfft': {
        'file': 'SGD_of_MM_ADAM_DFFT_v5.py',
        'title': 'SGD 全息优化 (DFFT)',
        'desc': '多平面相位全息图优化（PyTorch 自动求导 + Adam），运行较慢',
        'images': ['loss_curve.png', 'phase_hologram.png', 'reconstructions.png'],
        'needs_torch': True,
    },
    'sgd_siren': {
        'file': 'SGD_of_MM_ADAM_SIREN_v6.py',
        'title': 'SGD 全息优化 (SIREN)',
        'desc': 'SIREN 神经网络参数化的相位全息图优化，结果更平滑',
        'images': ['loss_curve_siren.png', 'phase_hologram_siren.png', 'reconstructions_siren.png'],
        'needs_torch': True,
    },
}

BUFFER_MAX_LINES = 500
_ENV_CACHE_TTL = 60
_SYNC_SCAN_CACHE_TTL = 60
_SKIP_LOG_PATHS = ('/output/', '/api/', '/static/', '/uploads/')

ALLOWED_PATHS = [
    WORK_DIR,
]

INVALID_NAME_CHARS = set('\\/:*?"<>|')
