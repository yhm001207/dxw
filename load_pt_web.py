# -*- coding: utf-8 -*-
"""
加载 .pt 相位文件（Web 版本）
================================
将每个 .pt 文件的相位分布保存为 png 图片，输出统计信息到控制台。
"""

import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import torch
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent

pt_files = sorted(WORK_DIR.glob('*.pt'))
if not pt_files:
    print('未找到 .pt 文件')
else:
    for f in pt_files:
        data = torch.load(f, map_location='cpu', weights_only=True)
        psi = data['psi']
        arr = psi.numpy()

        print(f'{f.name}  shape={list(arr.shape)}  '
              f'range=[{arr.min():.4f}, {arr.max():.4f}]')

        # 保存图片而非显示
        fig, ax = plt.subplots(figsize=(6, 5))
        im = ax.imshow(arr, cmap='jet')
        plt.colorbar(im, ax=ax)
        ax.set_title(f'{f.name}')
        plt.tight_layout()

        out_name = f'pt_phase_{f.stem}.png'
        fig.savefig(str(WORK_DIR / out_name), dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f'  -> 已保存 {out_name}')

    print(f'\n共处理 {len(pt_files)} 个 .pt 文件')
    print('PT_PHASE_IMAGES:' + ','.join([f'pt_phase_{f.stem}.png' for f in pt_files]))
