"""
SGD of MM (Adam + DFFT) v5 — Multi-plane Fresnel Holography Optimization
修复梯度爆炸版本。基于 Adam 优化器的多平面菲涅尔全息图生成。
PyTorch GPU 加速版。
"""

import numpy as np
import torch
import matplotlib.pyplot as plt
from PIL import Image
from scipy.io import savemat
import matplotlib
import time

matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
DTYPE = torch.complex128
FDTYPE = torch.float64


# ============================================================
# 1. Fresnel 衍射 (GPU)
# ============================================================

def fresnel_MM(U_in, lam, z, dx_in, dy_in, dx_out, dy_out):
    """Fresnel 衍射矩阵乘法 — PyTorch GPU 版本"""
    N, M = U_in.shape
    lam = torch.tensor(lam, dtype=FDTYPE, device=DEVICE)
    z_t = torch.tensor(z, dtype=FDTYPE, device=DEVICE)
    k = 2 * torch.pi / lam

    x_in = (torch.arange(M, dtype=FDTYPE, device=DEVICE) - (M - 1) / 2) * dx_in
    y_in = (torch.arange(N, dtype=FDTYPE, device=DEVICE) - (N - 1) / 2) * dy_in
    x_out = (torch.arange(M, dtype=FDTYPE, device=DEVICE) - (M - 1) / 2) * dx_out
    y_out = (torch.arange(N, dtype=FDTYPE, device=DEVICE) - (N - 1) / 2) * dy_out

    X_in, Y_in = torch.meshgrid(x_in, y_in, indexing='xy')
    X_out, Y_out = torch.meshgrid(x_out, y_out, indexing='xy')

    quad_phase_in = torch.exp(1j * k / (2 * z_t) * (X_in**2 + Y_in**2))
    U1 = U_in * quad_phase_in

    E_y = torch.exp(-1j * k / z_t * torch.outer(y_out, y_in))
    E_x = torch.exp(-1j * k / z_t * torch.outer(x_in, x_out))

    U2 = E_y @ U1 @ E_x

    A = torch.exp(1j * k * z_t) / (1j * lam * z_t) * \
        torch.exp(1j * k / (2 * z_t) * (X_out**2 + Y_out**2)) * dx_in * dy_in

    U_out = A * U2
    return U_out


# ============================================================
# 2. TV 梯度 (GPU)
# ============================================================

def compute_TV_gradient(phi, N, M):
    """Total Variation 正则化梯度 — PyTorch GPU 版本"""
    dpx = phi[:, 1:] - phi[:, :-1]
    dpy = phi[1:, :] - phi[:-1, :]
    sign_dx = torch.sign(dpx)
    sign_dy = torch.sign(dpy)

    grad_TV = torch.zeros((N, M), dtype=FDTYPE, device=DEVICE)
    grad_TV[:, :-1] -= sign_dx
    grad_TV[:, 1:] += sign_dx
    grad_TV[:-1, :] -= sign_dy
    grad_TV[1:, :] += sign_dy
    grad_TV /= (N * M)

    return grad_TV


# ============================================================
# 3. 权重调度 (CPU，标量运算)
# ============================================================

def get_weights(iter_num, max_iter):
    """串扰和TV权重的调度函数"""
    if iter_num <= 100:
        lam_ct = 0.0
        lam_tv = 0.0
    elif iter_num <= 250:
        t = (iter_num - 100) / 150
        lam_ct = 0.3 * t
        lam_tv = 0.001 * t
    else:
        lam_ct = 0.3
        lam_tv = 0.001
    return lam_ct, lam_tv


# ============================================================
# 4. 多平面优化主函数 (v5, PyTorch GPU)
# ============================================================

def GD_multiplane_v5(targets, z_list, lam, dx_in, dy_in, dx_out, dy_out,
                     max_iter, lr_max, lr_min, tol):
    """多平面全息优化（v5: 修复梯度爆炸，PyTorch GPU 加速）"""
    num_plane = len(z_list)
    N, M = targets[0].shape

    # 识别有内容的平面
    content_idx = []
    for p in range(num_plane):
        if np.sum(targets[p]**2) > 1e-6:
            content_idx.append(p)
    num_content = len(content_idx)
    print(f'内容平面: {content_idx}（共 {num_content} 个）')

    # 目标强度与能量 — 上传到 GPU
    T_intensity = [torch.as_tensor(targets[p]**2, dtype=FDTYPE, device=DEVICE)
                   for p in range(num_plane)]
    target_energy = torch.tensor(
        [float(T_intensity[p].sum()) for p in range(num_plane)],
        dtype=FDTYPE, device=DEVICE)

    # 所有平面配对
    pair_ij = []
    for i in range(num_content):
        for j in range(i + 1, num_content):
            pair_ij.append((content_idx[i], content_idx[j]))
    num_pairs = len(pair_ij)

    # 初始化 GPU 张量
    phase = 2 * torch.pi * torch.rand(N, M, dtype=FDTYPE, device=DEVICE)
    m = torch.zeros(N, M, dtype=FDTYPE, device=DEVICE)
    v = torch.zeros(N, M, dtype=FDTYPE, device=DEVICE)
    t_adam = 0
    beta1, beta2, eps_adam = 0.9, 0.999, 1e-8

    best_phase = phase.clone()
    best_loss = float('inf')
    no_improve = 0
    patience = 2000

    loss_hist = np.zeros(max_iter)
    prev_Lp = np.ones(num_content) / num_content

    # 预热 GPU
    print('GPU 预热中...')
    with torch.no_grad():
        _ = fresnel_MM(
            torch.ones(N, M, dtype=DTYPE, device=DEVICE),
            lam, z_list[0], dx_in, dy_in, dx_out, dy_out)
    torch.cuda.synchronize()
    print('GPU 预热完成')

    t_start = time.time()

    for it in range(max_iter):
        lr = lr_min + 0.5 * (lr_max - lr_min) * (1 + np.cos(np.pi * it / max_iter))
        lam_ct, lam_tv = get_weights(it, max_iter)
        w = prev_Lp / (np.sum(prev_Lp) + 1e-8)

        grad_total = torch.zeros(N, M, dtype=FDTYPE, device=DEVICE)
        total_loss = 0.0
        total_mse = 0.0
        curr_Lp = np.zeros(num_content)

        U_slm = torch.exp(1j * phase)

        # 缓存所有平面结果
        I_all = [None] * num_plane
        I_norm_all = [None] * num_plane
        U_f_all = [None] * num_plane
        S_all = torch.zeros(num_plane, dtype=FDTYPE, device=DEVICE)

        for p in range(num_plane):
            U_f = fresnel_MM(U_slm, lam, z_list[p], dx_in, dy_in, dx_out, dy_out)
            I_all[p] = torch.abs(U_f)**2
            S_all[p] = I_all[p].sum()
            U_f_all[p] = U_f
            E_p = target_energy[p]
            if S_all[p] > 0:
                I_norm_all[p] = I_all[p] * (E_p / S_all[p])
            else:
                I_norm_all[p] = I_all[p].clone()

        # MSE 梯度
        dU_mse_all = [None] * num_plane
        for idx in range(num_content):
            p = content_idx[idx]
            diff = I_norm_all[p] - T_intensity[p]
            Lp_mse = float(torch.mean(diff**2))
            curr_Lp[idx] = Lp_mse
            total_mse += w[idx] * Lp_mse
            total_loss += w[idx] * Lp_mse

            dI = (2 / (N * M)) * diff
            if S_all[p] > 0:
                dI = dI * (target_energy[p] / S_all[p])
            dU_mse_all[p] = w[idx] * dI * U_f_all[p]

        # 串扰惩罚梯度（v5 修复版）
        dU_ct_all = [torch.zeros(N, M, dtype=DTYPE, device=DEVICE)
                     for _ in range(num_plane)]
        if lam_ct > 0 and num_pairs > 0:
            for k_idx in range(num_pairs):
                i, j = pair_ij[k_idx]
                I_ni = I_norm_all[i]
                T_i = T_intensity[i]
                T_j = T_intensity[j]
                E_i = target_energy[i]
                S_i = S_all[i]

                L_wrong = float(torch.mean((I_ni - T_j)**2))
                L_right = float(torch.mean((I_ni - T_i)**2))
                total_loss += lam_ct * (L_wrong - L_right)

                dI = (2 / (N * M)) * (T_i - T_j)
                if S_i > 0:
                    dI = dI * (E_i / S_i)
                dU_ct_all[i] += lam_ct * dI * U_f_all[i]

        # 反向传播
        for idx in range(num_content):
            p = content_idx[idx]
            dU_combined = dU_mse_all[p] + dU_ct_all[p]
            if torch.any(dU_combined != 0):
                grad_U = fresnel_MM(dU_combined, lam, -z_list[p],
                                    dx_out, dy_out, dx_in, dy_in)
                grad_total += 2 * torch.imag(torch.conj(U_slm) * grad_U)

        if lam_tv > 0:
            grad_total += lam_tv * compute_TV_gradient(phase, N, M)

        prev_Lp = curr_Lp
        loss_hist[it] = total_mse

        # Adam 更新
        t_adam += 1
        m = beta1 * m + (1 - beta1) * grad_total
        v = beta2 * v + (1 - beta2) * (grad_total**2)
        m_hat = m / (1 - beta1**t_adam)
        v_hat = v / (1 - beta2**t_adam)
        phase -= lr * m_hat / (torch.sqrt(v_hat) + eps_adam)
        phase = torch.fmod(phase, 2 * torch.pi)

        if total_mse < best_loss:
            best_loss = total_mse
            best_phase = phase.clone()
            no_improve = 0
        else:
            no_improve += 1

        if (it + 1) % 10 == 0:
            elapsed = time.time() - t_start
            speed = (it + 1) / elapsed
            print(f'Iter {it+1:4d}/{max_iter} | MSE={total_mse:.4e} | Total={total_loss:.4e} '
                  f'| lr={lr:.4f} | ct={lam_ct:.2f} | tv={lam_tv:.4f} '
                  f'| {speed:.1f} it/s')

        if no_improve >= patience:
            print(f'收敛于迭代 {it+1}，最佳损失 = {best_loss:.6e}')
            break

    phase = best_phase
    elapsed = time.time() - t_start
    print(f'\n优化完成，总用时: {elapsed:.1f}s ({elapsed/(it+1):.2f}s/iter)')

    # 绘图需转回 CPU numpy
    phase_cpu = phase.cpu().numpy()
    loss_cpu = loss_hist[:it+1]

    plt.figure()
    plt.plot(loss_cpu, linewidth=1.5)
    plt.xlabel('Iteration')
    plt.ylabel('MSE')
    plt.title('MSE 收敛曲线（v5: 修复梯度爆炸, GPU）')
    plt.grid(True)
    plt.tight_layout()
    plt.show()

    return phase_cpu


# ============================================================
# 5. 图像加载 (CPU)
# ============================================================

def load_target(path, size):
    """读取目标图像并转为灰度 [0,1]"""
    img = Image.open(path).convert('L')
    img = img.resize((size, size), Image.BILINEAR)
    return np.array(img, dtype=np.float64) / 255.0


# ============================================================
# 主程序
# ============================================================

def main():
    # ---- GPU 信息 ----
    print(f'设备: {torch.cuda.get_device_name(0)}')
    print(f'PyTorch: {torch.__version__}, CUDA: {torch.version.cuda}')
    mem = torch.cuda.mem_get_info(0)
    print(f'显存: 可用 {mem[0]//1024**2}MB / 总计 {mem[1]//1024**2}MB')

    # 物理参数
    lam = 632.8e-9
    dx_in = 2.76e-6
    dy_in = dx_in
    dx_out = dx_in
    dy_out = dx_in
    z_list = [0.06, 0.065, 0.07, 0.075, 0.08]
    num_slice = len(z_list)
    img_size = 768

    # 优化参数
    max_iter = 2000
    lr_max = 0.4
    lr_min = 0.01
    tol = 1e-8

    # ---- 读取目标图像 (CPU) ----
    targets_intensity = [
        load_target('演示文稿1_16.bmp', img_size),
        load_target('black.bmp', img_size),
        load_target('演示文稿1_15.bmp', img_size),
        load_target('black.bmp', img_size),
        load_target('演示文稿1_14.bmp', img_size),
    ]

    targets_amp = []
    for I in targets_intensity:
        max_val = np.max(I)
        if max_val > 0:
            I = I / max_val
        else:
            I = np.zeros_like(I)
        targets_amp.append(np.sqrt(I))

    # 预览目标图像
    fig, axes = plt.subplots(1, num_slice, figsize=(20, 4))
    for i in range(num_slice):
        axes[i].imshow(targets_intensity[i], cmap='gray')
        axes[i].set_title(f'P{i+1} z={z_list[i]}m')
        axes[i].axis('off')
    plt.tight_layout()
    plt.show()

    # ---- 运行 GPU 优化 ----
    phase = GD_multiplane_v5(targets_amp, z_list, lam,
                             dx_in, dy_in, dx_out, dy_out,
                             max_iter, lr_max, lr_min, tol)

    # ---- 保存相位 (CPU) ----
    psi = np.mod(phase + np.pi, 2 * np.pi)
    psi = psi / -2
    savemat('phase_optimized.mat', {'psi': psi})
    print('相位已保存至 phase_optimized.mat')

    # ---- 显示相位全息图 ----
    plt.figure(figsize=(8, 6))
    plt.imshow(phase, cmap='jet')
    plt.title('Phase Hologram (v5: 修复梯度爆炸, GPU)')
    plt.colorbar(label='Phase (rad)')
    plt.tight_layout()
    plt.show()

    # ---- 重建验证 (GPU) ----
    content_idx = []
    for i in range(num_slice):
        if np.sum(targets_intensity[i]**2) > 1e-6:
            content_idx.append(i)

    phase_gpu = torch.as_tensor(phase, dtype=FDTYPE, device=DEVICE)
    fig, axes = plt.subplots(1, len(content_idx), figsize=(5 * len(content_idx), 5))
    for k, i in enumerate(content_idx):
        recon = fresnel_MM(torch.exp(1j * phase_gpu), lam, z_list[i],
                           dx_in, dy_in, dx_out, dy_out)
        I_recon = torch.abs(recon)**2
        I_recon = I_recon.cpu().numpy()
        I_recon = I_recon / np.max(I_recon)
        ax = axes[k] if len(content_idx) > 1 else axes
        im = ax.imshow(I_recon, cmap='gray', origin='upper')
        ax.set_title(f'P{i+1} z={z_list[i]:.3f}m')
        ax.set_aspect('equal')
        plt.colorbar(im, ax=ax, fraction=0.046)
    plt.tight_layout()
    plt.show()

    # ---- 量化评估 (GPU) ----
    print('\n=== Content Plane Evaluation ===')
    for i in content_idx:
        recon = fresnel_MM(torch.exp(1j * phase_gpu), lam, z_list[i],
                           dx_in, dy_in, dx_out, dy_out)
        I_recon = (torch.abs(recon)**2).cpu().numpy()
        I_recon = I_recon / (np.max(I_recon) + 1e-10)
        T = targets_intensity[i] / (np.max(targets_intensity[i]) + 1e-10)

        mse_val = np.mean((I_recon - T)**2)
        psnr_val = -10 * np.log10(mse_val + 1e-10)
        print(f'Plane {i+1} (z={z_list[i]:.3f}m): PSNR = {psnr_val:.2f} dB')

    # ---- 串扰检查 (GPU) ----
    print('\n=== Crosstalk Check ===')
    for i in content_idx:
        recon = fresnel_MM(torch.exp(1j * phase_gpu), lam, z_list[i],
                           dx_in, dy_in, dx_out, dy_out)
        I_recon = (torch.abs(recon)**2).cpu().numpy()
        I_recon = I_recon / (np.max(I_recon) + 1e-10)

        for j in content_idx:
            if i == j:
                continue
            T_other = targets_intensity[j] / (np.max(targets_intensity[j]) + 1e-10)
            crosstalk = np.mean(I_recon * T_other)
            print(f'P{i+1} x P{j+1} cross-correlation = {crosstalk:.6f}')


if __name__ == '__main__':
    main()
