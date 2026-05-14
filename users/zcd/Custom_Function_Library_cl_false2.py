# %%
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import matplotlib.pyplot as plt
from scipy.io import loadmat
import os
from tqdm import tqdm
from datetime import datetime
import json
import pandas as pd
import glob
from torch.utils.data import DataLoader,Dataset, Subset, TensorDataset
import warnings
warnings.filterwarnings('ignore')

# 设置中文字体（Linux兼容中文）
plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

## 缩放至0~1
def normalize_to_0_to_1(matrix):
    """归一化到[0,1]范围 - 与MATLAB版本一致"""
    if isinstance(matrix, torch.Tensor):
        min_val = torch.min(matrix)
        max_val = torch.max(matrix)
        norm_matrix = (matrix - min_val) / (max_val - min_val + 1e-10)
    else:
        min_val = np.min(matrix)
        max_val = np.max(matrix)
        norm_matrix = (matrix - min_val) / (max_val - min_val + 1e-10)
    return norm_matrix

## 生成高斯噪声
def Gnoisegen(x, snr):
    """生成高斯噪声 - 与MATLAB版本一致"""
    if isinstance(x, torch.Tensor):
        noise = torch.randn_like(x)
        Nx = x.shape[0]
        signal_power = torch.sum(x**2) / Nx
        noise_power = torch.sum(noise**2) / Nx
        noise_variance = signal_power / (10**(snr/10))
        noise = torch.sqrt(noise_variance / noise_power) * noise
        y = x + noise
    else:
        noise = np.random.randn(*x.shape)
        Nx = len(x)
        signal_power = np.sum(x**2) / Nx
        noise_power = np.sum(noise**2) / Nx
        noise_variance = signal_power / (10**(snr/10))
        noise = np.sqrt(noise_variance / noise_power) * noise
        y = x + noise
    return y, noise

## 角谱衍射 适配3D输入
def ASM_3D(input_field, spatial_size, lamda, Z):
    """
    角谱衍射计算 - 与MATLAB版本完全对应
    支持批处理
    """
    if input_field.dim() == 2:
        N, M = input_field.shape
        P = 1
        input_field = input_field.unsqueeze(-1)
    else:
        N, M, P = input_field.shape
    
    # 移动到CPU进行FFT（PyTorch的FFT在CPU上更稳定）
    device = input_field.device
    input_field = input_field.cpu()
    
    k = 2 * np.pi / lamda
    
    # 生成频率网格
    fx = torch.tensor(np.linspace(-M/(spatial_size[0]*2), M/(spatial_size[0]*2), M))
    fy = torch.tensor(np.linspace(-N/(spatial_size[1]*2), N/(spatial_size[1]*2), N))
    Fx, Fy = torch.meshgrid(fx, fy, indexing='xy')
    
    # 传递函数
    H_trans = torch.exp(1j * k * Z * torch.sqrt(1 - (lamda*Fx)**2 - (lamda*Fy)**2))
    
    if P == 1:
        # 单个矩阵
        input_fft = torch.fft.fft2(input_field.squeeze(-1))
        input_fft_shifted = torch.fft.fftshift(input_fft)
        filtered = H_trans * input_fft_shifted
        filtered_shifted = torch.fft.ifftshift(filtered)
        output = torch.fft.ifft2(filtered_shifted)
        output = output.unsqueeze(-1)
    else:
        # 批处理
        H_trans_3D = H_trans.unsqueeze(-1).repeat(1, 1, P)
        
        # 批量FFT
        input_fft = torch.fft.fft2(input_field, dim=(0, 1))
        
        # fftshift
        input_fft_shifted = torch.fft.fftshift(input_fft, dim=(0, 1))
        
        # 频域滤波
        filtered_fft = H_trans_3D * input_fft_shifted
        
        # ifftshift
        filtered_ifftshift = torch.fft.ifftshift(filtered_fft, dim=(0, 1))
        
        # 批量逆FFT
        output = torch.fft.ifft2(filtered_ifftshift, dim=(0, 1))
    
    # 移回原始设备
    output = output.to(device)
    if P == 1:
        output = output.squeeze(-1)
    
    return output

## NMSE计算函数 - 优化版（支持大样本）
def NMSE_compute_Wout(reg, X_train, Y_train, M):
    """计算权重矩阵 Wout"""
    
    # 构建正规方程
    X_train_T = X_train.T
    XTX = X_train_T @ X_train
    reg_matrix = reg * torch.eye(M, device=X_train.device)
    XTX_reg = XTX + reg_matrix
    
    # 计算右边项
    rhs = X_train_T @ Y_train.unsqueeze(1) if Y_train.dim() == 1 else X_train_T @ Y_train.T
    
    # 求解 Wout
    try:
        # 方法1: 使用torch.linalg.solve
        Wout_T = torch.linalg.solve(XTX_reg, rhs)
        Wout = Wout_T.T
    except:
        try:
            # 方法2: 使用最小二乘法
            Wout = torch.linalg.lstsq(XTX_reg, rhs).solution.T
        except:
            # 方法3: 回退到伪逆
            inv_XTX_reg = torch.linalg.pinv(XTX_reg)
            Wout = Y_train @ X_train @ inv_XTX_reg
    
    return Wout

def compute_NMSE_from_Wout(Wout, X_test, Y_test):
    """根据Wout计算NMSE"""
    
    # 计算预测输出
    if Y_test.dim() == 1:
        Yout = X_test @ Wout.squeeze()
    else:
        Yout = (X_test @ Wout.T).T
    
    # 计算NMSE
    NMSE = torch.sum((Y_test - Yout)**2) / (len(Yout) * torch.var(Y_test))
    
    return NMSE, Yout

def SER_compute_Wout(reg, X_train, Y_train, M):
    """计算SER任务的权重矩阵Wout"""
    
    # 构建正规方程
    X_train_T = X_train.T
    XTX = X_train_T @ X_train
    reg_matrix = reg * torch.eye(M, device=X_train.device)
    XTX_reg = XTX + reg_matrix
    
    # 计算右边项
    rhs = X_train_T @ Y_train.unsqueeze(1) if Y_train.dim() == 1 else X_train_T @ Y_train.T
    
    # 求解Wout
    try:
        Wout_T = torch.linalg.solve(XTX_reg, rhs)
        Wout = Wout_T.T
    except:
        try:
            Wout = torch.linalg.lstsq(XTX_reg, rhs).solution.T
        except:
            inv_XTX_reg = torch.linalg.pinv(XTX_reg)
            Wout = Y_train @ X_train @ inv_XTX_reg
    
    return Wout

def compute_SER_from_Wout(Wout, X_test, Y_test, x_test):
    """根据Wout计算SER和代理损失"""
    
    # 计算连续输出
    if Y_test.dim() == 1:
        Yout_continuous = X_test @ Wout.squeeze()
    else:
        Yout_continuous = (X_test @ Wout.T).T
    
    # === 可微分离散化 ===
    class DifferentiableDiscretize(torch.autograd.Function):
        @staticmethod
        def forward(ctx, y_continuous):
            y_discrete = y_continuous.clone()
            
            mask1 = y_continuous > 5/6
            y_discrete[mask1] = 1.0
            
            mask2 = y_continuous < 1/6
            y_discrete[mask2] = 0.0
            
            mask3 = (y_continuous > 1/6) & (y_continuous < 1/2)
            y_discrete[mask3] = 1/3
            
            mask4 = (y_continuous > 1/2) & (y_continuous < 5/6)
            y_discrete[mask4] = 2/3
            
            ctx.save_for_backward(y_continuous)
            return y_discrete
        
        @staticmethod
        def backward(ctx, grad_output):
            y_continuous, = ctx.saved_tensors
            temp = 10.0
            
            sig1 = torch.sigmoid((y_continuous - 5/6) * temp)
            grad1 = sig1 * (1 - sig1) * temp
            
            sig2 = torch.sigmoid((1/6 - y_continuous) * temp)
            grad2 = sig2 * (1 - sig2) * temp * (-1)
            
            sig3_lower = torch.sigmoid((y_continuous - 1/6) * temp)
            sig3_upper = torch.sigmoid((1/2 - y_continuous) * temp)
            grad3 = sig3_lower * (1 - sig3_lower) * temp - sig3_upper * (1 - sig3_upper) * temp
            
            sig4_lower = torch.sigmoid((y_continuous - 1/2) * temp)
            sig4_upper = torch.sigmoid((5/6 - y_continuous) * temp)
            grad4 = sig4_lower * (1 - sig4_lower) * temp - sig4_upper * (1 - sig4_upper) * temp
            
            probs = torch.stack([
                sig2, sig3_lower * sig3_upper, sig4_lower * sig4_upper, sig1
            ], dim=0)
            probs = probs / (probs.sum(dim=0, keepdim=True) + 1e-8)
            
            grad_weighted = (probs[0] * grad2 + probs[1] * grad3 + 
                           probs[2] * grad4 + probs[3] * grad1)
            
            return grad_output * grad_weighted
    
    # 应用可微分离散化
    Yout_discrete = DifferentiableDiscretize.apply(Yout_continuous)
    
    # 计算SER（硬分类）
    with torch.no_grad():
        Y_test_np = Y_test.detach().cpu().numpy()
        Yout_continuous_np = Yout_continuous.detach().cpu().numpy()
        Yout_c = Yout_continuous_np.copy()
        
        for m in range(x_test):
            if Yout_continuous_np[m] > 5/6:
                Yout_c[m] = 1
            elif Yout_continuous_np[m] < 1/6:
                Yout_c[m] = 0
            elif Yout_continuous_np[m] > 1/6 and Yout_continuous_np[m] < 1/2:
                Yout_c[m] = 1/3
            elif Yout_continuous_np[m] > 1/2 and Yout_continuous_np[m] < 5/6:
                Yout_c[m] = 2/3
        
        correct = np.sum(np.abs(Yout_c - Y_test_np) < 1e-6)
        SER = (x_test - correct) / x_test
    
    # 转换为张量
    Yout_c = torch.tensor(Yout_c, device=Yout_continuous.device, dtype=Yout_continuous.dtype)
    
    # 计算代理损失
    ser_proxy_loss = torch.mean((Yout_discrete - Y_test) ** 2)
    
    return SER, Yout_continuous, Yout_c, ser_proxy_loss

## 加载和预处理数据 - 修复.mat文件加载问题
def load_and_preprocess_data(config):
    """加载和预处理数据 - 修复.mat文件加载问题"""
    
    # 任务1数据 - Santafe.txt
    print("加载任务1数据...")
    try:
        # 检查文件是否存在
        if not os.path.exists('Santafe.txt'):
            raise FileNotFoundError("Santafe.txt not found")
        
        # 读取文本文件
        dataload_1 = np.loadtxt('Santafe.txt')
        
        # 确保是一维数组
        if dataload_1.ndim > 1:
            dataload_1 = dataload_1.flatten()
        
        # 检查数据长度
        if len(dataload_1) < config.x_L + 10:  # 加一些余量
            print(f"警告: Santafe.txt数据长度({len(dataload_1)})小于所需长度({config.x_L})")
            # 如果数据太短，进行填充
            needed_length = config.x_L + 100
            if len(dataload_1) < needed_length:
                # 重复数据或使用随机数据
                repeats = needed_length // len(dataload_1) + 1
                dataload_1 = np.tile(dataload_1, repeats)[:needed_length]
        
        data1 = (dataload_1 - np.min(dataload_1)) / (np.max(dataload_1) - np.min(dataload_1) + 1e-10)
        dataInput_1 = data1[:config.x_L]
        
        # 确保索引在范围内
        end_idx_1 = min(config.wash_up_nodes+config.x_train+1, len(data1))
        end_idx_2 = min(config.x_L+1, len(data1))
        
        Y_train_1 = data1[config.wash_up_nodes+1:end_idx_1]
        Y_test_1 = data1[config.wash_up_nodes+config.x_train+1:end_idx_2]
        
        print(f"任务1数据加载完成")
        print(f"Y_train_1 shape: {Y_train_1.shape}, Y_test_1 shape: {Y_test_1.shape}")
        
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("使用随机数据作为任务1数据")
        # 生成符合要求的随机数据
        data1 = np.random.randn(config.x_L + 100)
        data1 = (data1 - np.min(data1)) / (np.max(data1) - np.min(data1) + 1e-10)
        dataInput_1 = data1.copy()
        Y_train_1 = data1[config.wash_up_nodes+1:config.wash_up_nodes+config.x_train+1]
        Y_test_1 = data1[config.wash_up_nodes+config.x_train+1:config.x_L+1]
    
    # 任务2数据 - SER_Target_new.mat
    print("\n加载任务2数据...")
    try:
        # 检查文件是否存在
        if not os.path.exists("SER_Target_new.mat"):
            raise FileNotFoundError("SER_Target_new.mat not found")
        
        # 使用scipy加载.mat文件
        from scipy.io import loadmat
        dataload_2 = loadmat("SER_Target_new.mat")
        
        # 尝试不同的变量名
        if 'dc' in dataload_2:
            data2 = dataload_2['dc'].flatten()
        elif 'data' in dataload_2:
            data2 = dataload_2['data'].flatten()
        else:
            # 使用第一个变量
            first_key = list(dataload_2.keys())[0]
            if not first_key.startswith('__'):  # 跳过内部变量
                data2 = dataload_2[first_key].flatten()
            else:
                raise KeyError("未找到有效数据变量")
        
        # 检查'o'变量
        if 'o' in dataload_2:
            o = dataload_2['o'].flatten()
        else:
            print("警告: 未找到'o'变量，使用随机数据")
            o = np.random.randn(len(data2))
        
        print(f"data2长度: {len(data2)}, o长度: {len(o)}")
        
        # 确保数据长度足够
        if len(o) < config.x_L:
            print(f"警告: u数据长度({len(o)})小于所需长度({config.x_L})")
            needed_length = config.x_L + 100
            repeats = needed_length // len(o) + 1
            o = np.tile(o, repeats)[:needed_length]
        
        if len(data2) < config.x_L:
            print(f"警告: data2数据长度({len(data2)})小于所需长度({config.x_L})")
            needed_length = config.x_L + 100
            repeats = needed_length // len(data2) + 1
            data2 = np.tile(data2, repeats)[:needed_length]
        
        # 生成噪声数据
        u = dataload_2['u'][config.snr,:config.x_L] ## 直接调用带有噪声的信号
        # dataInput_2, noise = Gnoisegen(u, config.snr)   
        dataInput_2 = normalize_to_0_to_1(u) 
        
        # 提取训练和测试数据
        Y_train_2 = data2[config.wash_up_nodes:min(config.wash_up_nodes+config.x_train, len(data2))]
        Y_test_2 = data2[config.wash_up_nodes+config.x_train:min(config.wash_up_nodes+config.x_total, len(data2))]
        
        print(f"任务2数据加载完成")
        print(f"Y_train_2 shape: {Y_train_2.shape}, Y_test_2 shape: {Y_test_2.shape}")
        
    except Exception as e:
        print(f"加载任务2数据时出错: {e}")
        print("使用随机数据作为任务2数据")
        # 生成随机数据
        data2 = np.random.rand(config.x_L + 100)
        u = np.random.rand(config.x_L + 100)
        dataInput_2, _ = Gnoisegen(u[:config.x_L], 4*config.snr)
        dataInput_2 = normalize_to_0_to_1(dataInput_2)
        Y_train_2 = data2[config.wash_up_nodes:config.wash_up_nodes+config.x_train]
        Y_test_2 = data2[config.wash_up_nodes+config.x_train:config.wash_up_nodes+config.x_total]
    
    # 转换为PyTorch张量
    data1 = torch.tensor(data1, dtype=torch.float64, device=config.device)
    dataInput_1 = torch.tensor(dataInput_1, dtype=torch.float64, device=config.device)
    Y_train_1 = torch.tensor(Y_train_1, dtype=torch.float64, device=config.device)
    Y_test_1 = torch.tensor(Y_test_1, dtype=torch.float64, device=config.device)
    
    data2 = torch.tensor(data2, dtype=torch.float64, device=config.device)
    dataInput_2 = torch.tensor(dataInput_2, dtype=torch.float64, device=config.device)
    Y_train_2 = torch.tensor(Y_train_2, dtype=torch.float64, device=config.device)
    Y_test_2 = torch.tensor(Y_test_2, dtype=torch.float64, device=config.device)
    
    return (data1, dataInput_1, Y_train_1, Y_test_1, 
            data2, dataInput_2, Y_train_2, Y_test_2)

## 准备输入数据 - 修复维度问题
def prepare_input_data(config, dataInput_1, dataInput_2, Y_train_1, Y_train_2, Y_test_1, Y_test_2):
    """准备输入数据 - 修复维度问题"""
    
    print(f"dataInput_1 shape: {dataInput_1.shape}")
    print(f"dataInput_2 shape: {dataInput_2.shape}")
    
    # 任务1数据重塑
    data1_add = torch.cat([
        torch.zeros(config.All_step_1 - 1, device=config.device),
        dataInput_1[:config.x_L]
    ])
    
    # 创建滑动窗口视图
    data1_add_reshaped = data1_add.unfold(0, config.x_L, 1)
    data1_add_reshaped = data1_add_reshaped.unsqueeze(0).unsqueeze(0)  # [1, 1, All_step_1, x_L]
    
    # 任务2数据重塑
    data2_add = torch.cat([
        torch.zeros(config.All_step_2 - 1, device=config.device),
        dataInput_2[:config.x_L]
    ])
    data2_add_reshaped = data2_add.unfold(0, config.x_L, 1)
    data2_add_reshaped = data2_add_reshaped.unsqueeze(0).unsqueeze(0)  # [1, 1, All_step_2, x_L]
    
    # 计算重复参数
    repeat_h1 = config.Input_size // config.SL // config.All_step_1
    repeat_w1 = config.Number_task * config.SL
    
    repeat_h2 = config.Input_size // config.SL // config.All_step_2
    repeat_w2 = config.Number_task * config.SL
    
    # 构建Input_data列表
    Input_data_list = []
    
    # 任务1部分
    for ss in range(config.All_step_1):
        # 获取当前步的数据
        input_data = data1_add_reshaped[:, :, ss, :]  # [1, 1, x_L]
        
        # 重塑为 [1, 1, 1, x_L] 然后扩展
        input_data = input_data.unsqueeze(2)  # [1, 1, 1, x_L]
        
        # 在高度和宽度维度上重复
        input_data_expanded = input_data.repeat(repeat_h1, repeat_w1, 1, 1)
        
        # 重塑为 [H, W, x_L]
        input_data_final = input_data_expanded.squeeze(2)  # [repeat_h1, repeat_w1, x_L]
        
        Input_data_list.append(input_data_final)
    
    # 计算需要添加的零行数
    zero_rows_needed = config.Number_task*config.SL - config.All_step_1 - config.All_step_2
    print(f"需要在任务1和任务2之间添加 {zero_rows_needed} 行零数据")
    
    # 在任务1和任务2之间添加零数据
    if zero_rows_needed > 0:
        # 创建零数据的模板（取任务1的最后一个数据作为参考维度）
        if Input_data_list:  # 确保列表不为空
            template = Input_data_list[0]  # 获取一个模板来确定维度
            zero_data = torch.zeros(zero_rows_needed, template.shape[1], template.shape[2], 
                                   device=config.device)
            
            # 将零数据添加到列表中
            Input_data_list.append(zero_data)
    
    # 任务2部分
    for ss in range(config.All_step_2):
        # 获取当前步的数据
        input_data = data2_add_reshaped[:, :, ss, :]  # [1, 1, x_L]
        
        # 重塑为 [1, 1, 1, x_L] 然后扩展
        input_data = input_data.unsqueeze(2)  # [1, 1, 1, x_L]
        
        # 在高度和宽度维度上重复
        input_data_expanded = input_data.repeat(repeat_h2, repeat_w2, 1, 1)
        
        # 重塑为 [H, W, x_L]
        input_data_final = input_data_expanded.squeeze(2)  # [repeat_h2, repeat_w2, x_L]
        
        Input_data_list.append(input_data_final)
    
    # 合并所有数据
    Input_data = torch.cat(Input_data_list, dim = 0)  # 在高度维度上拼接
    Input_data = Input_data[:, :, config.wash_up_nodes:config.x_L]

    # 量化到0-255再转换到相位
    # 注意：这里除以2是因为MATLAB代码中有 /2
    Input_data_quantized = torch.round(255 * Input_data / 2)
    Input_data_quantized = Input_data_quantized * 2 * torch.pi / 255
    
    # 计算电场分量
    E_x = torch.sin(Input_data_quantized / 2)
    E_y = torch.zeros_like(E_x)

    # 提取训练数据部分
    E_x_train =   E_x[:, :, :config.x_train]
    E_y_train =   E_y[:, :, :config.x_train]
    Y_train_1 =   Y_train_1
    Y_train_2 =   Y_train_2

    # 提取测试数据部分
    E_x_test =    E_x[:, :, config.x_train:config.x_total]
    E_y_test =    E_y[:, :, config.x_train:config.x_total]
    Y_test_1 =   Y_test_1
    Y_test_2 =   Y_test_2  

    # 重塑并创建DataLoader
    train_data = TensorDataset(
        E_x_train.permute(2, 0, 1),      # [3000, 32, 32]
        E_y_train.permute(2, 0, 1),      # [3000, 32, 32]
        Y_train_1,      # [3000]
        Y_train_2,       # [3000]
    )
    
    test_data = TensorDataset(
        E_x_test.permute(2, 0, 1),      # [1000, 32, 32]
        E_y_test.permute(2, 0, 1),      # [1000, 32, 32]
        Y_test_1,      # [1000]
        Y_test_2,       # [1000]
    )

    train_loader_batch = DataLoader(train_data, config.x_train//config.x_test*config.batch_size, shuffle = False)
    test_loader_batch = DataLoader(test_data, config.batch_size, shuffle = False)

    return Input_data, E_x, E_y, train_loader_batch, test_loader_batch

## 光学系统模型
class OpticalSystem(nn.Module):
    """光学系统模型，包含可训练的Mask"""
    def __init__(self, config):
        super().__init__()
        self.config = config

        if config.mask_index == 0:
            # 加载norMask.mat
            mask_data = loadmat('norMask.mat')
            mask_matrix = mask_data['Mask']
            print(f"加载 norMask.mat，mask_index = {config.mask_index}")
        elif config.mask_index == 1:
            # 加载randMask.mat
            mask_data = loadmat('randMask.mat')
            mask_matrix = mask_data['Mask']
            print(f"加载 randMask.mat，mask_index = {config.mask_index}")
        else:
            raise ValueError(f"不支持的mask_index值: {config.mask_index}，应为0或1")
        
        # 转换为PyTorch张量
        mask_tensor = torch.tensor(mask_matrix, dtype=torch.float64)

        # 初始化可训练Mask（相位值在0到pi之间）
        self.mask = nn.Parameter(mask_tensor)
        
    def qwp_transform(self, E_x, E_y, Gamma):
        """QWP变换 - 支持自动微分"""
        cos_2alpha = torch.cos(2 * self.mask)
        sin_2alpha = torch.sin(2 * self.mask)
        cos_Gamma2 = torch.cos(Gamma/2)
        sin_Gamma2 = torch.sin(Gamma/2)
        
        W_total_11 = cos_Gamma2 - 1j * sin_Gamma2 * cos_2alpha
        W_total_12 = -1j * sin_Gamma2 * sin_2alpha
        W_total_21 = W_total_12
        W_total_22 = cos_Gamma2 + 1j * sin_Gamma2 * cos_2alpha
        
        # 3D扩展
        batch_size = E_x.shape[2]
        W_total_11_3D = W_total_11.unsqueeze(2).repeat(1, 1, batch_size)
        W_total_12_3D = W_total_12.unsqueeze(2).repeat(1, 1, batch_size)
        W_total_21_3D = W_total_21.unsqueeze(2).repeat(1, 1, batch_size)
        W_total_22_3D = W_total_22.unsqueeze(2).repeat(1, 1, batch_size)
        
        # 通过QWP
        E_x_after_qwp = W_total_11_3D * E_x + W_total_12_3D * E_y
        E_y_after_qwp = W_total_21_3D * E_x + W_total_22_3D * E_y
        
        return E_x_after_qwp, E_y_after_qwp
    
    def forward_single_V(self, E_x, E_y, Gamma, Z_length):
        """单个电压V下的前向传播"""
        # 1. QWP变换
        E_x_qwp, E_y_qwp = self.qwp_transform(E_x, E_y, Gamma)
        
        # 2. 放大
        E_x_enlarge = E_x_qwp.repeat_interleave(self.config.Compress_index, dim=0)
        E_x_enlarge = E_x_enlarge.repeat_interleave(self.config.Compress_index, dim=1)
        
        E_y_enlarge = E_y_qwp.repeat_interleave(self.config.Compress_index, dim=0)
        E_y_enlarge = E_y_enlarge.repeat_interleave(self.config.Compress_index, dim=1)
        
        # 3. 补零
        pad_each_side = (self.config.N_size - self.config.Input_size) // 2
        
        # 对每个时间片单独处理
        batch_size = E_x_enlarge.shape[2]
        E_x_final_list = []
        E_y_final_list = []
        
        for t in range(batch_size):
            E_x_slice = E_x_enlarge[:, :, t]
            E_y_slice = E_y_enlarge[:, :, t]
            
            # 补零
            E_x_padded = torch.nn.functional.pad(
                E_x_slice.unsqueeze(0).unsqueeze(-1),
                (0, 0, pad_each_side, pad_each_side, pad_each_side, pad_each_side)
            ).squeeze()
            
            E_y_padded = torch.nn.functional.pad(
                E_y_slice.unsqueeze(0).unsqueeze(-1),
                (0, 0, pad_each_side, pad_each_side, pad_each_side, pad_each_side)
            ).squeeze()
            
            E_x_final_list.append(E_x_padded)
            E_y_final_list.append(E_y_padded)
        
        E_x_final = torch.stack(E_x_final_list, dim=2)
        E_y_final = torch.stack(E_y_final_list, dim=2)
        
        # 4. 角谱衍射
        Spatial_Size = [self.config.L_scale, self.config.L_scale]
        Lamda = 633e-9  ## 波长633nm
        
        Output_x = ASM_3D(E_x_final, Spatial_Size, Lamda, Z_length)
        Output_y = ASM_3D(E_y_final, Spatial_Size, Lamda, Z_length)
        
        # 5. 去padding
        start_pixel = pad_each_side + 1
        end_pixel = pad_each_side + self.config.Input_size
        
        Output_x_final = Output_x[start_pixel-1:end_pixel, start_pixel-1:end_pixel, :]
        Output_y_final = Output_y[start_pixel-1:end_pixel, start_pixel-1:end_pixel, :]
        
        # 6. 计算强度
        Output_final = torch.abs(Output_x_final)**2 + torch.abs(Output_y_final)**2
        
        return Output_final

def compute_performance_for_mask_train(optical_system, config, E_x, E_y, 
                                Y_train_1, Y_train_2, Y_test_1, Y_test_2,
                                use_all_V=True, specific_V=None):
    """
    计算给定Mask的性能
    如果use_all_V=True，计算所有V下的性能
    如果specific_V不为None，只计算特定V的性能
    """
    # 临时设置Mask

    if use_all_V:
        V_values = config.V_range
    elif specific_V is not None:
        V_values = torch.tensor([specific_V], device=config.device)
    else:
        V_values = config.V_range[[config.V_select_index]]
    
    nmse_results = []
    ser_results = []
    
    for V in V_values:
        Gamma = torch.pi * V
        Z_length = config.Z_range[config.Z_select_index]
        
        # 前向传播
        with torch.no_grad():
            Output_final = optical_system.forward_single_V(
                E_x, E_y, Gamma, Z_length
            )
        
        # 降采样
        X_dim = Output_final.shape[0]
        N = Output_final.shape[2]  
        
        # 使用平均池化进行降采样
        block_size = X_dim // config.SL
        Output_reshaped = Output_final.view(
            config.SL, block_size, config.SL, block_size, N
        )
        Output_ds = Output_reshaped.mean(dim=(1, 3))  # [new_dim, new_dim, batch]
        
        # 分割任务
        Output_ds_1 = Output_ds[:config.SL//config.Number_task, :, :]
        Output_ds_2 = Output_ds[config.SL//config.Number_task:2*config.SL//config.Number_task, :, :]
        
        # 归一化
        Output_cacu_1 = normalize_to_0_to_1(Output_ds_1.permute(2, 0, 1).flatten())
        Output_cacu_2 = normalize_to_0_to_1(Output_ds_2.permute(2, 0, 1).flatten())
        
        # 重塑
        Output_reshape_1 = Output_cacu_1.view(
            (config.x_train//config.x_test+1)*config.batch_size ,config.M1
        )
        Output_reshape_2 = Output_cacu_2.view(
            (config.x_train//config.x_test+1)*config.batch_size ,config.M1
        )
        
        # 提取训练和测试数据
        X_train_1 = Output_reshape_1[:(config.x_train//config.x_test)*config.batch_size, :config.M1]
        X_train_2 = Output_reshape_2[:(config.x_train//config.x_test)*config.batch_size, :config.M1]

        X_test_1 = Output_reshape_1[(config.x_train//config.x_test)*config.batch_size:, :config.M1]
        X_test_2 = Output_reshape_2[(config.x_train//config.x_test)*config.batch_size:, :config.M1]
        
        # NMSE
        Wout_1 = NMSE_compute_Wout(config.reg, X_train_1, Y_train_1, config.M1)
        nmse_val, _ = compute_NMSE_from_Wout(Wout_1, X_test_1, Y_test_1)  ##！！！！！！！！！！

        # SER
        Wout_2 = SER_compute_Wout(config.reg, X_train_2, Y_train_2, config.M1)
        ser_val, _, _, _ = compute_SER_from_Wout(Wout_2, X_test_2, Y_test_2, config.batch_size)  ##！！！！！！！！！
        
        nmse_results.append(nmse_val.item())
        ser_results.append(ser_val)
        
        if use_all_V or specific_V is not None:
            print(f"  V={V.item():.2f} (Γ={V.item()*torch.pi:.2f}): NMSE={nmse_val.item():.4f}, SER={ser_val:.4f}")
    
    return np.array(nmse_results), np.array(ser_results)

def compute_performance_for_mask_with_gradient_train(optical_system, config, E_x, E_y, 
                                          Y_train_1, Y_train_2, Y_test_1, Y_test_2,
                                          use_all_V=True, specific_V=None):
    """
    计算给定Mask的性能（支持梯度计算）
    只计算特定V的性能
    """
    # 启用梯度计算
    optical_system.train()
    
    if use_all_V:
        V_values = config.V_range
    elif specific_V is not None:
        V_values = torch.tensor([specific_V], device=config.device)
    else:
        V_values = config.V_range[[config.V_select_index]]
    
    nmse_results = []
    ser_results = []
    
    for V in V_values:
        Gamma = torch.pi * V
        Z_length = config.Z_range[config.Z_select_index]

        Output_final = optical_system.forward_single_V(
            E_x, E_y, Gamma, Z_length)
        
        # 降采样
        X_dim = Output_final.shape[0]
        N = Output_final.shape[2]  
        
        # 使用平均池化进行降采样
        block_size = X_dim // config.SL
        Output_reshaped = Output_final.view(
            config.SL, block_size, config.SL, block_size, N
        )
        Output_ds = Output_reshaped.mean(dim=(1, 3))  # [new_dim, new_dim, batch]
        
        print("1. 前向传播完成，开始降采样和分割任务")
        
        # 分割任务
        Output_ds_1 = Output_ds[:config.SL//config.Number_task, :, :]
        Output_ds_2 = Output_ds[config.SL//config.Number_task:2*config.SL//config.Number_task, :, :]
        
        # 归一化
        batch_size = Output_ds_1.shape[2]//(config.x_train//config.x_test+1)
        Output_cacu_1 = normalize_to_0_to_1(Output_ds_1.permute(2, 0, 1).flatten())
        Output_cacu_2 = normalize_to_0_to_1(Output_ds_2.permute(2, 0, 1).flatten())

        print("2. 前向传播完成，开始重塑和提取训练测试数据")
        
        # 重塑
        Output_reshape_1 = Output_cacu_1.view(
            (config.x_train//config.x_test+1)*batch_size ,config.M1
        )
        Output_reshape_2 = Output_cacu_2.view(
            (config.x_train//config.x_test+1)*batch_size ,config.M1
        )
        
        # 提取训练和测试数据
        X_train_1 = Output_reshape_1[:(config.x_train//config.x_test)*batch_size, :config.M1]
        X_train_2 = Output_reshape_2[:(config.x_train//config.x_test)*batch_size, :config.M1]

        X_test_1 = Output_reshape_1[(config.x_train//config.x_test)*batch_size:, :config.M1]
        X_test_2 = Output_reshape_2[(config.x_train//config.x_test)*batch_size:, :config.M1]
        
        # NMSE
        Wout_1 = NMSE_compute_Wout(config.reg, X_train_1, Y_train_1, config.M1)
        nmse_val, _ = compute_NMSE_from_Wout(Wout_1, X_test_1, Y_test_1)  ##！！！！！！！！！！

        # SER
        Wout_2 = SER_compute_Wout(config.reg, X_train_2, Y_train_2, config.M1)
        ser_val, _, _, ser_proxy_loss = compute_SER_from_Wout(Wout_2, X_test_2, Y_test_2, config.batch_size)  ##！！！！！！！！！
        
        
        # 注意：保留梯度，存储张量而不是标量
        nmse_results.append(nmse_val)  # 不要加 .item()
        ser_results.append(ser_val)    # 
            
        if use_all_V or specific_V is not None:
            print(f"  V={V.item():.2f} (Γ={V.item()*torch.pi:.2f}): NMSE={nmse_val.item():.4f}, SER={ser_val:.4f} (采样点)")
    
    
    # 返回张量列表，保持梯度
    return nmse_results, ser_results, ser_proxy_loss

def compute_performance_for_mask_test(optical_system, mask, config, E_x, E_y, 
                                Y_train_1, Y_test_1, Y_train_2, Y_test_2,
                                use_all_V=True, specific_V=None):
    """
    计算给定Mask的性能
    如果use_all_V=True，计算所有V下的性能
    如果specific_V不为None，只计算特定V的性能
    """

    # 临时设置Mask
    original_mask = optical_system.mask.data.clone()
    optical_system.mask.data = mask.clone()
    
    if use_all_V:
        V_values = config.V_range
    elif specific_V is not None:
        V_values = torch.tensor([specific_V], device=config.device)
    else:
        V_values = config.V_range[[config.V_select_index]]
    
    nmse_results = []
    ser_results = []
    
    for V in V_values:
        Gamma = torch.pi * V
        Z_length = config.Z_range[config.Z_select_index]
        
        # 前向传播
        with torch.no_grad():
            Output_final = optical_system.forward_single_V(
                E_x, E_y, Gamma, Z_length
            )
        
        # 降采样
        X_dim = Output_final.shape[0]
        N = Output_final.shape[2]  
        
        # 使用平均池化进行降采样
        block_size = X_dim // config.SL
        Output_reshaped = Output_final.view(
            config.SL, block_size, config.SL, block_size, N
        )
        Output_ds = Output_reshaped.mean(dim=(1, 3))  # [new_dim, new_dim, batch]
        
        # 分割任务
        Output_ds_1 = Output_ds[:config.SL//config.Number_task, :, :]
        Output_ds_2 = Output_ds[config.SL//config.Number_task:2*config.SL//config.Number_task, :, :]
        
        # 归一化
        Output_cacu_1 = normalize_to_0_to_1(Output_ds_1.permute(2, 0, 1).flatten())
        Output_cacu_2 = normalize_to_0_to_1(Output_ds_2.permute(2, 0, 1).flatten())
        
        # 重塑
        Output_reshape_1 = Output_cacu_1.view(
            config.x_total ,config.M1
        )
        Output_reshape_2 = Output_cacu_2.view(
            config.x_total ,config.M1
        )
        
        # 提取训练和测试数据
        X_train_1 = Output_reshape_1[:config.x_train, :config.M1]
        X_test_1 = Output_reshape_1[config.x_train:config.x_total, :config.M1]
        
        X_train_2 = Output_reshape_2[:config.x_train, :config.M1]
        X_test_2 = Output_reshape_2[config.x_train:config.x_total, :config.M1]
        
        # NMSE
        Wout_1 = NMSE_compute_Wout(config.reg, X_train_1, Y_train_1, config.M1)
        nmse_val, _ = compute_NMSE_from_Wout(Wout_1, X_test_1, Y_test_1) 

        # SER
        Wout_2 = SER_compute_Wout(config.reg, X_train_2, Y_train_2, config.M1)
        ser_val, _, _, _ = compute_SER_from_Wout(Wout_2, X_test_2, Y_test_2, config.x_test)  
        
        nmse_results.append(nmse_val.item())
        ser_results.append(ser_val)
        
        if use_all_V or specific_V is not None:
            print(f"  V={V.item():.2f} (Γ={V.item()*torch.pi:.2f}): NMSE={nmse_val.item():.4f}, SER={ser_val:.4f}")
    
    # 恢复原始Mask
    optical_system.mask.data = original_mask.clone()
    
    return np.array(nmse_results), np.array(ser_results)

## 加载检查点函数
def load_checkpoint(checkpoint_path, optical_system):
    """
    加载检查点，恢复训练状态
    :param checkpoint_path: 检查点文件路径（如 'checkpoints/epoch_0010.pt'）
    :param optical_system: 光学系统对象（需要恢复Mask）
    :return: 恢复后的训练状态（epoch、loss_history、优化器参数等）
    """
    # 加载检查点
    checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=False)

    # ======================
    # 🔥 核心修复：兼容 DataParallel 多GPU
    # ======================
    if isinstance(optical_system, torch.nn.DataParallel):
        # 多GPU模式：取出内部真实模型
        model = optical_system.module
    else:
        # 单GPU/CPU模式：直接使用
        model = optical_system

    # 1. 恢复Mask（自动适配GPU/CPU）
    if torch.cuda.is_available():
        model.mask.data = checkpoint['Mask'].cuda()  # 有GPU则移到GPU
    else:
        model.mask.data = checkpoint['Mask'].cpu()   # 无GPU则用CPU    

    # 2. 恢复历史记录
    loss_history = checkpoint['loss_history']
    nmse_history = checkpoint['nmse_history']
    ser_history = checkpoint['ser_history']
    mask_history = checkpoint['mask_history']

    # 3. 恢复学习率
    learning_rate = checkpoint['learning_rate']

    # 4. 恢复最后训练的epoch
    last_epoch = checkpoint['epoch']

    print(f"✅ 成功加载检查点：{checkpoint_path}")
    print(f"   - 恢复到 Epoch {last_epoch}")

    return last_epoch, loss_history, nmse_history, ser_history, mask_history, learning_rate

# ========================= DDP 多卡训练函数 =========================
def train_mask_ddp(config, optical_system, train_loader_batch, test_loader_batch,
                   resume_from_checkpoint=None, rank=0, world_size=1, device=None):
    import torch.distributed as dist
    from torch.cuda.amp import GradScaler, autocast
    import matplotlib
    matplotlib.use('Agg')  # 强制Linux无GUI绘图，彻底解决字体/绘图崩溃

    # ========== 显存优化设置 ==========
    torch.cuda.empty_cache()
    torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = True

    if device is None:
        device = torch.device(f'cuda:{rank}' if torch.cuda.is_available() else 'cpu')

    if torch.cuda.is_available():
        torch.cuda.set_device(rank)

    # 混合精度训练器
    scaler = GradScaler()
    # 梯度累积步数（显存不够可改4，显存充足可改1）
    accumulation_steps = 2
    gpu_count = world_size

    if rank == 0:
        print(f"✅ 使用设备: {device}")
        print(f"✅ 实际使用的进程数 (GPU数量): {gpu_count}")
        print(f"✅ 梯度累积步数: {accumulation_steps}")

    # 加载检查点
    if resume_from_checkpoint is not None:
        last_epoch, loss_history, nmse_history, ser_history, mask_history, lr = load_checkpoint(
            resume_from_checkpoint, optical_system.module if hasattr(optical_system, 'module') else optical_system
        )
        config.learning_rate = lr
        start_epoch = last_epoch
    else:
        loss_history = []
        nmse_history = []
        ser_history = []
        mask_history = []
        start_epoch = 0

    # 保存目录设置（仅主进程rank=0执行）
    if rank == 0:
        save_dir = config.save_dir
        os.makedirs(save_dir, exist_ok=True)
        config_dict = {k: v for k, v in vars(config).items() if not k.startswith('_')}
        config_dict['start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(os.path.join(save_dir, 'config.json'), 'w') as f:
            json.dump(config_dict, f, indent=4, default=str)
        checkpoint_dir = os.path.join(save_dir, 'checkpoints')
        os.makedirs(checkpoint_dir, exist_ok=True)
        data_dir = os.path.join(save_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        print(f"训练结果将保存到: {save_dir}")

    # 优化器
    optimizer = torch.optim.Adam(optical_system.parameters(), lr=config.learning_rate)

    def get_original_model(model):
        return model.module if hasattr(model, 'module') else model

    # ========== 训练循环 ==========
    for epoch in range(start_epoch, config.num_epochs):
        # 🔥 多GPU核心：每个epoch重置采样器，保证数据打乱
        if hasattr(train_loader_batch.sampler, 'set_epoch'):
            train_loader_batch.sampler.set_epoch(epoch)

        if rank == 0:
            print(f"\n=== Epoch {epoch+1}/{config.num_epochs} ===")

        # 保存初始mask（仅主进程）
        if epoch == 0 and rank == 0:
            original_model = get_original_model(optical_system)
            plt.imsave(os.path.join(data_dir, f'mask_epoch_0000.png'),
                       original_model.mask.data.cpu().numpy(),
                       cmap='hsv', vmin=0, vmax=torch.pi)
            mask_history.append(original_model.mask.data.clone().cpu())

        if rank == 0:
            print("  计算性能、损失和梯度...")

        V_samples = config.V_range[::config.V_interval]
        nmse_all = np.zeros(len(config.V_range))
        ser_all = np.zeros(len(config.V_range))
        total_nmse_loss_all = 0.0
        total_ser_loss_all = 0.0

        target_nmse = config.target_nmse.to(device)
        target_ser = config.target_ser.to(device)

        # 每个epoch开始时同步所有进程
        dist.barrier()

        optimizer.zero_grad()

        for batch_index, (train_batch, test_batch) in enumerate(zip(train_loader_batch, test_loader_batch)):
            if rank == 0:
                print(f"\n=== 处理 Batch {batch_index+1}/{len(train_loader_batch)} ===")

            e_x_train, e_y_train, y1_train, y2_train = train_batch
            e_x_test, e_y_test, y1_test, y2_test = test_batch

            # 数据移到对应GPU（non_blocking加速）
            e_x_train = e_x_train.squeeze(1).permute(1, 2, 0).to(device, non_blocking=True)
            e_y_train = e_y_train.squeeze(1).permute(1, 2, 0).to(device, non_blocking=True)
            e_x_test = e_x_test.squeeze(1).permute(1, 2, 0).to(device, non_blocking=True)
            e_y_test = e_y_test.squeeze(1).permute(1, 2, 0).to(device, non_blocking=True)

            E_x = torch.cat((e_x_train, e_x_test), dim=2)
            E_y = torch.cat((e_y_train, e_y_test), dim=2)

            Y_train_1 = y1_train.to(device, non_blocking=True)
            Y_train_2 = y2_train.to(device, non_blocking=True)
            Y_test_1 = y1_test.to(device, non_blocking=True)
            Y_test_2 = y2_test.to(device, non_blocking=True)

            # ========== 对每个V值计算 ==========
            for i, V in enumerate(config.V_range):
                V = V.to(device)
                is_sample = V in V_samples.to(device)
                original_model = get_original_model(optical_system)

                # 🔥 修复：去掉CFL前缀，直接调用本库内的函数
                if is_sample:
                    with autocast():
                        nmse_vals, ser_vals, ser_proxy_loss = compute_performance_for_mask_with_gradient_train(
                            original_model, config,
                            E_x, E_y,
                            Y_train_1, Y_train_2, Y_test_1, Y_test_2,
                            use_all_V=False, specific_V=V
                        )

                        nmse_val = nmse_vals[0]
                        ser_val = ser_vals[0]
                        target_nmse_val = target_nmse[i]
                        target_ser_val = target_ser[i]

                        loss_single = 0.25 * (nmse_val - target_nmse_val)**2 + 0.75 * ser_proxy_loss
                        # 梯度累积：除以累积步数
                        loss_single = loss_single / accumulation_steps
                        
                        # 反向传播（混合精度）
                        scaler.scale(loss_single).backward()

                        total_nmse_loss_all += (nmse_val - target_nmse_val).item()**2
                        total_ser_loss_all += (ser_val - target_ser_val).item()**2
                        nmse_all[i] += nmse_val.item()
                        ser_all[i] += ser_val.item()

                else:
                    with torch.no_grad():
                        # 🔥 修复：去掉CFL前缀
                        nmse_vals, ser_vals = compute_performance_for_mask_train(
                            original_model, config,
                            E_x, E_y, Y_train_1, Y_train_2, Y_test_1, Y_test_2,
                            use_all_V=False, specific_V=V
                        )

                        nmse_val = nmse_vals[0]
                        ser_val = ser_vals[0]
                        target_nmse_val = target_nmse[i]
                        target_ser_val = target_ser[i]

                        total_nmse_loss_all += (nmse_val - target_nmse_val).item()**2
                        total_ser_loss_all += (ser_val - target_ser_val).item()**2
                        nmse_all[i] += nmse_val.item()
                        ser_all[i] += ser_val.item()

                # 清理中间变量
                del nmse_vals, ser_vals
                if is_sample:
                    del ser_proxy_loss

            # ========== 梯度累积更新（修复顺序） ==========
            if (batch_index + 1) % accumulation_steps == 0:
                scaler.unscale_(optimizer)
                # 梯度裁剪，防止爆炸
                torch.nn.utils.clip_grad_norm_(optical_system.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad()

            # 限制mask范围在[0, π]
            with torch.no_grad():
                original_model = get_original_model(optical_system)
                original_model.mask.data = torch.clamp(original_model.mask.data, 0, torch.pi)

            # 清理显存
            del E_x, E_y, Y_train_1, Y_train_2, Y_test_1, Y_test_2, e_x_train, e_y_train, e_x_test, e_y_test
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        # ========== 跨GPU同步结果 ==========
        nmse_all_tensor = torch.tensor(nmse_all, device=device)
        ser_all_tensor = torch.tensor(ser_all, device=device)
        total_nmse_tensor = torch.tensor(total_nmse_loss_all, device=device)
        total_ser_tensor = torch.tensor(total_ser_loss_all, device=device)

        dist.all_reduce(nmse_all_tensor, op=dist.ReduceOp.SUM)
        dist.all_reduce(ser_all_tensor, op=dist.ReduceOp.SUM)
        dist.all_reduce(total_nmse_tensor, op=dist.ReduceOp.SUM)
        dist.all_reduce(total_ser_tensor, op=dist.ReduceOp.SUM)

        num_batches = len(train_loader_batch)
        num_v = len(config.V_range)

        # 多GPU平均：除以GPU数量和batch数
        nmse_all = nmse_all_tensor.cpu().numpy() / gpu_count / num_batches
        ser_all = ser_all_tensor.cpu().numpy() / gpu_count / num_batches
        total_nmse_loss = total_nmse_tensor.item() / num_v / num_batches / gpu_count
        total_ser_loss = total_ser_tensor.item() / num_v / num_batches / gpu_count
        total_loss = total_nmse_loss + total_ser_loss

        # ========== 保存结果（仅主进程rank=0） ==========
        if rank == 0:
            print(f"Loss: NMSE={total_nmse_loss:.6f}, SER={total_ser_loss:.6f}, Total={total_loss:.6f}")
            original_model = get_original_model(optical_system)
            nmse_history.append(nmse_all)
            ser_history.append(ser_all)
            mask_history.append(original_model.mask.data.clone().cpu())
            loss_history.append(total_loss)

            # 动态学习率调整
            if epoch > 1:
                loss_improvement = loss_history[-2] - loss_history[-1]
                if loss_improvement > 0:
                    config.learning_rate = min(config.learning_rate * 1.5, 0.1)
                else:
                    config.learning_rate = max(config.learning_rate * 0.5, 0.01)
                # 更新优化器学习率
                for param_group in optimizer.param_groups:
                    param_group['lr'] = config.learning_rate

            # 保存检查点
            checkpoint = {
                'epoch': epoch + 1,
                'Mask': original_model.mask.data.cpu(),
                'loss_history': loss_history,
                'nmse_history': nmse_history,
                'ser_history': ser_history,
                'mask_history': mask_history,
                'learning_rate': config.learning_rate,
                'optimizer_state_dict': optimizer.state_dict()
            }
            checkpoint_path = os.path.join(checkpoint_dir, f'epoch_{epoch+1:04d}.pt')
            torch.save(checkpoint, checkpoint_path)

            # 保存loss CSV
            loss_df = pd.DataFrame({'epoch': list(range(1, len(loss_history)+1)), 'loss': loss_history})
            loss_df.to_csv(os.path.join(data_dir, 'loss_history.csv'), index=False)

            target_nmse_np = target_nmse.cpu().numpy()
            target_ser_np = target_ser.cpu().numpy()
            performance_df = pd.DataFrame({
                'V_range': config.V_range.cpu().numpy(),
                f'nmse_epoch_{epoch+1}': nmse_all,
                f'ser_epoch_{epoch+1}': ser_all,
                'target_nmse': target_nmse_np,
                'target_ser': target_ser_np
            })
            performance_df.to_csv(os.path.join(data_dir, f'performance_epoch_{epoch+1:04d}.csv'), index=False)

            # 保存mask图像
            mask_img = original_model.mask.data.cpu().numpy()
            plt.imsave(os.path.join(data_dir, f'mask_epoch_{epoch+1:04d}.png'),
                       mask_img, cmap='hsv', vmin=0, vmax=torch.pi)

        # 所有进程同步，等待主进程保存完成
        dist.barrier()

    if rank == 0:
        original_model = get_original_model(optical_system)
        return original_model.mask.data, loss_history, nmse_history, ser_history, mask_history
    else:
        return None, None, None, None, None
                
## 可视化训练进度函数
def visualize_training_progress(epoch, mask, nmse_curve, ser_curve, V_range, target_nmse, target_ser, loss_history, mask_history):
    """可视化训练进度"""
    fig = plt.figure(figsize=(14, 6))

    # 子图1: 当前使用的Mask
    ax1 = plt.subplot(2, 4, 1)
    im1 = ax1.imshow(mask_history[-2].detach().cpu().numpy(), cmap='hsv', vmin=0, vmax=torch.pi)
    plt.colorbar(im1, ax=ax1)
    ax1.set_title(f'训练前 Mask')
    ax1.set_xlabel('X位置')
    ax1.set_ylabel('Y位置')
       
    # 子图2: 当前训练出的Mask
    ax1 = plt.subplot(2, 4, 2)
    im1 = ax1.imshow(mask, cmap='hsv', vmin=0, vmax=torch.pi)
    plt.colorbar(im1, ax=ax1)
    ax1.set_title(f'训练后 Mask')
    ax1.set_xlabel('X位置')
    ax1.set_ylabel('Y位置')

    # 子图3: Mask变化
    ax1 = plt.subplot(2, 4, 3)
    im1 = ax1.imshow((mask-mask_history[-2].detach().cpu().numpy()), cmap='hsv', vmin=-0.01*torch.pi, vmax=0.01*torch.pi)
    plt.colorbar(im1, ax=ax1)
    ax1.set_title(f'Mask 训练前后差异')
    ax1.set_xlabel('X位置')
    ax1.set_ylabel('Y位置')
    
    # 子图4: 性能曲线
    ax2 = plt.subplot(2, 4, 4)
    ax2.plot(V_range * np.pi, nmse_curve, 'r-', linewidth=2, label='NMSE')
    ax2.plot(V_range * np.pi, ser_curve, 'b-', linewidth=2, label='SER')
    ax2.plot(V_range * np.pi, target_nmse, 'r--', linewidth=1.5, label='目标NMSE')
    ax2.plot(V_range * np.pi, target_ser, 'b--', linewidth=1.5, label='目标SER')
    ax2.set_xlabel('相位延迟 (rad)')
    ax2.set_ylabel('性能指标')
    ax2.legend(loc='best')
    ax2.set_title(f'性能曲线 (Epoch {epoch})')
    ax2.grid(True)
    
    # 子图5: 损失下降曲线
    ax3 = plt.subplot(2, 4, 5)
    ax3.plot(range(1, len(loss_history) + 1), loss_history, 'k-', linewidth=2)
    ax3.set_xlabel('训练轮数')
    ax3.set_ylabel('损失值')
    ax3.set_title('训练损失下降曲线')
    ax3.grid(True)
    
    # 子图6: 性能差值
    ax4 = plt.subplot(2, 4, 6)
    performance_diff = nmse_curve - ser_curve
    ax4.plot(V_range * np.pi, performance_diff, 'g-', linewidth=2)
    ax4.axhline(y=0, color='r', linestyle='--', linewidth=1.5, label='平衡点')
    ax4.set_xlabel('相位延迟 (rad)')
    ax4.set_ylabel('NMSE - SER')
    ax4.set_title('性能差值')
    ax4.legend()
    ax4.grid(True)
    
    # 子图7: Mask直方图
    ax5 = plt.subplot(2, 4, 7)
    ax5.hist(mask.flatten(), bins=20, color='b', alpha=0.7)
    ax5.set_xlabel('相位值 (rad)')
    ax5.set_ylabel('频数')
    ax5.set_title('Mask相位分布')
    ax5.set_xlim([0, np.pi])
    
    # 子图8: 任务切换可视化
    ax6 = plt.subplot(2, 4, 8)
    task1_better = nmse_curve < ser_curve
    ax6.fill_between(V_range * np.pi, 0, task1_better, color='r', alpha=0.3, label='任务1主导')
    ax6.fill_between(V_range * np.pi, task1_better, 1, color='b', alpha=0.3, label='任务2主导')
    ax6.plot(V_range * np.pi, nmse_curve, 'r-', linewidth=1.5, label='NMSE')
    ax6.plot(V_range * np.pi, ser_curve, 'b-', linewidth=1.5, label='SER')
    ax6.set_xlabel('相位延迟 (rad)')
    ax6.set_ylabel('任务主导')
    ax6.set_title('任务切换区域')
    ax6.legend(loc='best')
    ax6.grid(True)
    
    plt.suptitle(f'训练进度 - Epoch {epoch}', fontsize=16, fontweight='bold')
    plt.tight_layout()

## 保存训练结果函数
def save_training_results(final_mask, loss_history, nmse_history, ser_history, mask_history, config):
    """保存训练结果，处理早停导致的数据长度不一致"""
    
    # ========== 第一步：统一转换为numpy数组 ==========
    # 1. 转换loss_history（最简单）
    loss_array = np.array(loss_history)
    
    # 2. 转换nmse_history和ser_history（最可能出问题）
    if nmse_history and len(nmse_history) > 0:
        try:
            # 尝试直接转换为2D数组
            nmse_array = np.array(nmse_history)    # 形状: (num_epochs, num_V)
            ser_array = np.array(ser_history)      # 形状: (num_epochs, num_V)
        except ValueError as e:
            print(f"警告: 性能数据形状不一致，使用对象数组保存。错误信息: {e}")
            # 使用对象数组（可以存储不同长度的数组）
            nmse_array = np.array(nmse_history, dtype=object)
            ser_array = np.array(ser_history, dtype=object)
    else:
        nmse_array = np.array([])
        ser_array = np.array([])
    
    # 3. 转换mask_history（Tensor列表 -> numpy数组）
    if mask_history and len(mask_history) > 0:
        # 检查是否所有mask形状相同
        try:
            mask_array = np.array([m.cpu().numpy() for m in mask_history])
        except ValueError:
            # 如果形状不同，使用对象数组
            mask_array = np.array([m.cpu().numpy() for m in mask_history], dtype=object)
    else:
        mask_array = np.array([])
    
    # ========== 第二步：构建结果字典 ==========
    results = {
        'final_mask': final_mask.cpu().numpy(),
        'loss_history': loss_array,          # 已经是numpy数组
        'nmse_history': nmse_array,          # 已经是numpy数组
        'ser_history': ser_array,            # 已经是numpy数组
        'mask_history': mask_array,          # 已经是numpy数组
        'config': {
            'reg': config.reg,
            'SL': config.SL,
            'Number_task': config.Number_task,
            'M1': config.M1,
            'wash_up_nodes': config.wash_up_nodes,
            'x_train': config.x_train,
            'x_test': config.x_test,
            'V_range': config.V_range.cpu().numpy(),
            'Z_range': config.Z_range.cpu().numpy(),
            'max_target': config.max_target,
            'min_target': config.min_target,
            'N_size': config.N_size,
            'Input_size': config.Input_size,
            'All_step_1': config.All_step_1,
            'All_step_2': config.All_step_2,
            'snr': config.snr,
            'learning_rate': config.learning_rate,
            'num_epochs': config.num_epochs,
            'actual_epochs': len(loss_history)  # 新增：记录实际训练epoch数
        }
    }
    
    # ========== 第三步：保存 ==========
    try:
        # 保存为.npz文件
        np.savez(os.path.join(config.save_dir, 'trained_mask_results.npz'), **results)

        # 同时保存为.mat文件（便于MATLAB读取）
        # 注意：scipy.io.savemat不能直接处理对象数组，需要转换
        mat_results = results.copy()
        # 将对象数组转换为cell数组（MATLAB格式）
        if 'nmse_history' in mat_results and hasattr(mat_results['nmse_history'], 'dtype') and mat_results['nmse_history'].dtype == object:
            mat_results['nmse_history'] = mat_results['nmse_history'].tolist()  # 转换为列表
        if 'ser_history' in mat_results and hasattr(mat_results['ser_history'], 'dtype') and mat_results['ser_history'].dtype == object:
            mat_results['ser_history'] = mat_results['ser_history'].tolist()
        if 'mask_history' in mat_results and hasattr(mat_results['mask_history'], 'dtype') and mat_results['mask_history'].dtype == object:
            mat_results['mask_history'] = mat_results['mask_history'].tolist()

        from scipy.io import savemat
        savemat(os.path.join(config.save_dir, 'trained_mask_results.mat'), mat_results)
        
        print("训练结果已保存到 'trained_mask_results.npz' 和 'trained_mask_results.mat'")
        print(f"  设定总epoch数: {config.num_epochs}")
        print(f"  实际训练epoch数: {len(loss_history)}")
        
    except Exception as e:
        print(f"保存失败: {e}")
        print("尝试使用pickle保存原始数据...")
        import pickle
        with open('trained_mask_results.pkl', 'wb') as f:
            pickle.dump({
                'final_mask': final_mask.cpu().numpy(),
                'loss_history': loss_history,
                'nmse_history': nmse_history,
                'ser_history': ser_history,
                'mask_history': mask_history,
                'config': config.__dict__ if hasattr(config, '__dict__') else {}
            }, f)
        print("原始数据已保存到 'trained_mask_results.pkl'")

## 可视化最终结果函数
def visualize_final_results(optical_system, E_x, E_y, Y_train_1, Y_test_1, Y_train_2, Y_test_2,
    final_mask, loss_history, nmse_history, ser_history, mask_history, config):
    """可视化最终结果"""
    fig = plt.figure(figsize=(16, 8))
    
    # 子图1: 训练后的Mask
    ax1 = plt.subplot(2, 4, 1)
    im1 = ax1.imshow(final_mask.cpu().numpy(), cmap='hsv', vmin=0, vmax=torch.pi)
    plt.colorbar(im1, ax=ax1)
    ax1.set_title('训练后的Mask')
    ax1.set_xlabel('X位置')
    ax1.set_ylabel('Y位置')
    
    # 子图2: 最终性能曲线
    nmse_all, ser_all = compute_performance_for_mask_test(
    optical_system, config, E_x, E_y,
    Y_train_1, Y_test_1, Y_train_2, Y_test_2,
    use_all_V=True
    )
    ax2 = plt.subplot(2, 4, 2)
    final_nmse = nmse_all
    final_ser = ser_all
    
    # 计算目标曲线
    V_range_np = config.V_range.cpu().numpy()
    target_nmse = config.target_nmse
    target_ser = config.target_ser
    
    ax2.plot(V_range_np * np.pi, final_nmse, 'r-', linewidth=2, label='NMSE (任务1)')
    ax2.plot(V_range_np * np.pi, final_ser, 'b-', linewidth=2, label='SER (任务2)')
    ax2.plot(V_range_np * np.pi, target_nmse, 'r--', linewidth=1.5, label='目标NMSE')
    ax2.plot(V_range_np * np.pi, target_ser, 'b--', linewidth=1.5, label='目标SER')
    ax2.set_xlabel('相位延迟 (rad)')
    ax2.set_ylabel('性能指标')
    ax2.legend(loc='best')
    ax2.set_title('任务性能 vs 相位延迟')
    ax2.grid(True)
    
    # 子图3: 性能差值
    ax3 = plt.subplot(2, 4, 3)
    performance_diff = final_nmse - final_ser
    ax3.plot(V_range_np * np.pi, performance_diff, 'k-', linewidth=2)
    ax3.axhline(y=0, color='r', linestyle='--', linewidth=1.5, label='平衡点')
    ax3.set_xlabel('相位延迟 (rad)')
    ax3.set_ylabel('NMSE - SER')
    ax3.set_title('性能差值')
    ax3.legend()
    ax3.grid(True)
    
    # 子图4: 损失函数下降曲线
    ax4 = plt.subplot(2, 4, 4)
    ax4.plot(range(1, len(loss_history) + 1), loss_history, 'k-', linewidth=2)
    ax4.set_xlabel('训练轮数')
    ax4.set_ylabel('损失值')
    ax4.set_title('训练损失下降曲线')
    ax4.grid(True)
    
    # 子图5: 训练前后对比
    ax5 = plt.subplot(2, 4, 5)
    initial_nmse = nmse_history[0]
    initial_ser = ser_history[0]
    
    ax5.plot(V_range_np * np.pi, initial_nmse, 'r:', linewidth=1.5, label='初始NMSE')
    ax5.plot(V_range_np * np.pi, initial_ser, 'b:', linewidth=1.5, label='初始SER')
    ax5.plot(V_range_np * np.pi, final_nmse, 'r-', linewidth=2, label='最终NMSE')
    ax5.plot(V_range_np * np.pi, final_ser, 'b-', linewidth=2, label='最终SER')
    ax5.set_xlabel('相位延迟 (rad)')
    ax5.set_ylabel('性能指标')
    ax5.legend(loc='best')
    ax5.set_title('训练前后对比')
    ax5.grid(True)
    
    # 子图6: Mask演化过程（选择几个关键epoch）
    ax6 = plt.subplot(2, 4, 6)
    selected_epochs = [0, len(mask_history)//4, len(mask_history)//2, len(mask_history)-1]
    
    for i, epoch_idx in enumerate(selected_epochs):
        mask_slice = mask_history[epoch_idx][0, :]  # 取第一行作为示例
        ax6.plot(mask_slice, label=f'Epoch {epoch_idx+1}')
    
    ax6.set_xlabel('X位置')
    ax6.set_ylabel('相位值 (rad)')
    ax6.set_title('Mask演化过程（第一行）')
    ax6.legend()
    ax6.grid(True)
    
    # 子图7: 性能提升百分比
    ax7 = plt.subplot(2, 4, 7)
    nmse_improvement = 100 * (initial_nmse - final_nmse) / initial_nmse
    ser_improvement = 100 * (initial_ser - final_ser) / initial_ser
    
    x_pos = np.arange(len(V_range_np))
    width = 0.35
    
    ax7.bar(x_pos - width/2, nmse_improvement, width, label='NMSE改进', color='r', alpha=0.7)
    ax7.bar(x_pos + width/2, ser_improvement, width, label='SER改进', color='b', alpha=0.7)
    
    ax7.set_xlabel('电压索引')
    ax7.set_ylabel('改进百分比 (%)')
    ax7.set_title('性能改进百分比')
    ax7.set_xticks(x_pos)
    ax7.set_xticklabels([f'V{i+1}' for i in range(len(V_range_np))])
    ax7.legend()
    ax7.grid(True, axis='y')
    
    # 子图8: 最优电压分析
    ax8 = plt.subplot(2, 4, 8)
    
    # 计算综合性能得分（越低越好）
    combined_score = final_nmse + final_ser
    
    # 找到最优电压
    optimal_idx = np.argmin(combined_score)
    optimal_V = V_range_np[optimal_idx]
    
    ax8.plot(V_range_np, combined_score, 'g-', linewidth=2, label='综合得分')
    ax8.axvline(x=optimal_V, color='r', linestyle='--', linewidth=1.5, label=f'最优电压: {optimal_V:.2f}V')
    ax8.set_xlabel('电压 (V)')
    ax8.set_ylabel('NMSE + SER')
    ax8.set_title('最优电压分析')
    ax8.legend()
    ax8.grid(True)
    
    plt.suptitle(f'可训练Mask优化结果 - {len(loss_history)}轮训练', fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.show()
    
    # 打印关键指标
    print("\n" + "="*50)
    print("关键性能指标总结:")
    print(f"  最优电压: {optimal_V:.2f} V")
    print(f"  最优电压下的性能:")
    print(f"    NMSE: {final_nmse[optimal_idx]:.4f}")
    print(f"    SER: {final_ser[optimal_idx]:.4f}")
    print(f"  平均改进:")
    print(f"    NMSE平均改进: {np.mean(nmse_improvement):.1f}%")
    print(f"    SER平均改进: {np.mean(ser_improvement):.1f}%")
    print(f"  Mask相位范围:")
    print(f"    最小值: {np.min(final_mask.cpu().numpy()):.3f} rad")
    print(f"    最大值: {np.max(final_mask.cpu().numpy()):.3f} rad")
    print(f"    平均值: {np.mean(final_mask.cpu().numpy()):.3f} rad")
    print("="*50)
