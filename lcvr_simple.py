# -*- coding: utf-8 -*-
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 120

# ==================== 参数配置 ====================
LC1_PARAMS = {
    'theta': np.deg2rad(45),     # 快轴角度 (rad)
    'd': 5,                    # 液晶层厚度 (μm)
    'delta_n': 0.23,             # 双折射率
}

LC2_PARAMS = {
    'theta': np.deg2rad(45),     # 快轴角度 (rad)
    'd': 10,                    # 液晶层厚度 (μm)
    'delta_n': 0.23,             # 双折射率
}

POL_ANGLE = 0           # 起偏器/检偏器角度（平行检偏）
POL_ANGLE_MID = 0       # 中间检偏器角度（可独立调整！）

V0 = 2.5       # 特征电压 (V)
V_MAX = 7     # 最大电压

# 波长范围
LAMBDA_MIN = 400  # nm
LAMBDA_MAX = 700  # nm
LAMBDA_N = 150

# 电压采样
V_N = 40

# 固定电压采样点 - V2 取 19 个固定值
FIXED_V2_SAMPLES = np.linspace(0, V_MAX, 19)

# ==================== 核心函数 ====================
def lcvr_retardance(V, d, delta_n, wavelength_nm):
    delta_max = (2 * np.pi / wavelength_nm) * delta_n * (d * 1000)
    modulation = np.exp(-V / V0)
    return delta_max * modulation

def rotation_matrix(theta):
    ct, st = np.cos(theta), np.sin(theta)
    return np.array([[ct, st], [-st, ct]])

def polarizer_jones(theta=0):
    ct, st = np.cos(theta), np.sin(theta)
    return np.array([[ct**2, ct*st], [ct*st, st**2]])

def lcvr_jones(delta, theta):
    R = rotation_matrix(theta)
    J = np.array([[np.exp(-1j * delta / 2), 0],
                  [0, np.exp(1j * delta / 2)]])
    return R.T @ J @ R

def intensity(E):
    return np.abs(E[0])**2 + np.abs(E[1])**2

P = polarizer_jones(np.deg2rad(POL_ANGLE))      # 起偏器
A = polarizer_jones(np.deg2rad(POL_ANGLE))      # 输出检偏器
A_mid = polarizer_jones(np.deg2rad(POL_ANGLE_MID))  # 中间检偏器（可独立调整）
Ein = np.array([1.0, 0.0])

# ==================== 计算调制矩阵 ====================
def compute_single_lc_matrix(lc_params):
    """单个 LCVR 的调制矩阵 (电压 × 波长)"""
    wavelengths = np.linspace(LAMBDA_MIN, LAMBDA_MAX, LAMBDA_N)
    V_values = np.linspace(0, V_MAX, V_N)
    I_mat = np.zeros((V_N, LAMBDA_N))
    
    for i, V in enumerate(V_values):
        for j, wl in enumerate(wavelengths):
            delta = lcvr_retardance(V, lc_params['d'], lc_params['delta_n'], wl)
            M = lcvr_jones(delta, lc_params['theta'])
            E = A @ M @ P @ Ein
            I_mat[i, j] = intensity(E)
    
    return V_values, wavelengths, I_mat

def compute_cascaded_matrix_fixed_LC2(V2_fixed):
    """固定 LC2 电压，扫描 LC1 电压和波长"""
    wavelengths = np.linspace(LAMBDA_MIN, LAMBDA_MAX, LAMBDA_N)
    V1_values = np.linspace(0, V_MAX, V_N)
    I_mat = np.zeros((V_N, LAMBDA_N))
    
    d2_fixed = np.array([lcvr_retardance(V2_fixed, LC2_PARAMS['d'], LC2_PARAMS['delta_n'], wl) 
                         for wl in wavelengths])
    
    for i, V1 in enumerate(V1_values):
        for j, wl in enumerate(wavelengths):
            d1 = lcvr_retardance(V1, LC1_PARAMS['d'], LC1_PARAMS['delta_n'], wl)
            M1 = lcvr_jones(d1, LC1_PARAMS['theta'])
            M2 = lcvr_jones(d2_fixed[j], LC2_PARAMS['theta'])
            E = A @ M2 @ A_mid @ M1 @ P @ Ein
            I_mat[i, j] = intensity(E)
    
    return V1_values, wavelengths, I_mat

print('=== 计算调制矩阵 ===')
print('固定 V2 采样点:', list(np.round(FIXED_V2_SAMPLES, 2)))

# 单独调制
V_LC1, wl_LC1, I_LC1 = compute_single_lc_matrix(LC1_PARAMS)
V_LC2, wl_LC2, I_LC2 = compute_single_lc_matrix(LC2_PARAMS)

# 级联调制 - 固定 LC2，扫描 LC1
cascaded_fixed_LC2 = []
for v2 in FIXED_V2_SAMPLES:
    V1, wl, I = compute_cascaded_matrix_fixed_LC2(v2)
    cascaded_fixed_LC2.append((v2, V1, wl, I))
    print('  计算级联 (V2=%.2fV)...' % v2)

print('完成!')

# ==================== 绘制子图 (3行×7列=21个子图) ====================
fig = plt.figure(figsize=(25, 12))

# 1. LC1 单独调制
ax1 = fig.add_subplot(3, 7, 1)
im1 = ax1.pcolormesh(wl_LC1, V_LC1, I_LC1, shading='auto', cmap='viridis', vmin=0, vmax=1)
ax1.set_xlabel('波长 (nm)', fontsize=9)
ax1.set_ylabel('V1 (V)', fontsize=9)
ax1.set_title('LC1 单独调制', fontsize=10)
ax1.set_xlim(LAMBDA_MIN, LAMBDA_MAX)
ax1.set_ylim(0, V_MAX)
ax1.tick_params(axis='both', labelsize=6)

# 2. LC2 单独调制
ax2 = fig.add_subplot(3, 7, 2)
im2 = ax2.pcolormesh(wl_LC2, V_LC2, I_LC2, shading='auto', cmap='viridis', vmin=0, vmax=1)
ax2.set_xlabel('波长 (nm)', fontsize=9)
ax2.set_ylabel('V2 (V)', fontsize=9)
ax2.set_title('LC2 单独调制', fontsize=10)
ax2.set_xlim(LAMBDA_MIN, LAMBDA_MAX)
ax2.set_ylim(0, V_MAX)
ax2.tick_params(axis='both', labelsize=6)

# 3-21. 级联调制 - 固定 LC2，扫描 LC1 (V1作为纵坐标)
subplot_idx = 3
for v2, V1, wl, I in cascaded_fixed_LC2:
    ax = fig.add_subplot(3, 7, subplot_idx)
    im = ax.pcolormesh(wl, V1, I, shading='auto', cmap='viridis', vmin=0, vmax=1)
    ax.set_xlabel('波长 (nm)', fontsize=8)
    ax.set_ylabel('V1 (V)', fontsize=8)
    ax.set_title('V2=%.1fV' % v2, fontsize=9)
    ax.set_xlim(LAMBDA_MIN, LAMBDA_MAX)
    ax.set_ylim(0, V_MAX)
    ax.tick_params(axis='both', labelsize=5)
    subplot_idx += 1

# 统一颜色条
fig.subplots_adjust(right=0.92, wspace=0.35, hspace=0.4)
cbar_ax = fig.add_axes([0.93, 0.15, 0.015, 0.7])
fig.colorbar(im1, cax=cbar_ax, label='透过率')

fig.suptitle('LCVR 调制矩阵分析 (平行检偏)', fontsize=14, y=0.98)
plt.savefig('lcvr_modulation_matrices_final.png', dpi=150, bbox_inches='tight')
plt.show()

print('\n已生成 %d 个子图' % (subplot_idx-1))
print('图像已保存为 lcvr_modulation_matrices_final.png')