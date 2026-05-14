# -*- coding: utf-8 -*-
"""Load, inspect, and display .pt phase files in current directory."""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import torch
import matplotlib.pyplot as plt
from pathlib import Path

pt_files = sorted(Path('.').glob('*.pt'))
if not pt_files:
    print('No .pt files found.')
else:
    for f in pt_files:
        data = torch.load(f, map_location='cpu', weights_only=True)
        psi = data['psi']
        arr = psi.numpy()

        print(f'{f.name}  shape={list(arr.shape)}  '
              f'range=[{arr.min():.4f}, {arr.max():.4f}]')

        plt.figure(figsize=(6, 5))
        plt.imshow(arr, cmap='jet')
        plt.colorbar()
        plt.title(f'{f.name}')
        plt.tight_layout()
        plt.show()
