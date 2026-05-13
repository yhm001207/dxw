
# 远程服务器控制面板

基于 Flask + WebSocket 的远程服务器管理系统，提供浏览器端的代码编辑、脚本运行、性能监控和终端接入功能。

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **Web 终端** | 浏览器内运行真实 cmd/PowerShell/Git Bash/WSL 终端 |
| **代码编辑器** | 支持上传、编辑、运行 Python/Shell 脚本 |
| **性能监控** | 实时 CPU、内存、GPU 监控图表 |
| **文件管理** | 个人云盘，支持文件夹上传、下载 |
| **训练队列** | GPU 任务排队、自动调度 |
| **通知系统** | 训练完成/出错时通过企业微信/Server酱推送通知 |
| **AI 助手** | 内置多模型 AI 对话（Claude/OpenAI/DeepSeek/自定义） |

---

## 快速开始

### 1. 安装依赖

```bash
pip install flask flask-socketio websocket-client websockets psutil pywinpty
```

### 2. 启动服务器

```bash
python app.py
```

服务器启动后访问 `http://localhost:5000`

### 3. 用户注册

首次使用需要注册账号：
- 访问注册页面创建用户名和密码
- 每个用户拥有独立的文件存储空间 (`users/<username>/`)

---

## 核心功能说明

### Web 终端

- 支持 **cmd.exe / PowerShell / Git Bash / WSL** 四种 Shell
- 真实 PTY 终端，行为与本地终端完全一致
- 右侧面板支持竖排 3 个终端窗口，可拖拽调整高度
- 右键菜单：复制、粘贴、清屏
- 需要先登录才能连接终端

### 代码编辑器与脚本运行

- **上传文件**：直接上传或拖拽文件夹
- **编辑脚本**：点击文件打开编辑器
- **运行脚本**：选择 Python 环境，点击运行按钮
- **实时输出**：WebSocket 实时显示脚本输出
- **训练队列**：GPU 不足时自动排队，依次执行

### 性能监控

- **CPU**：使用率、温度、型号
- **内存**：已用/总量
- **GPU**：每张卡的显存、温度、占用率趋势图
- **清理显存**：一键杀掉占用 GPU 的僵尸进程

### 文件管理

- 每个用户独立存储空间 (`users/<username>/`)
- 支持：
  - 文件/文件夹上传（拖拽支持）
  - 文件预览（图片直接在页面内显示）
  - 下载、重命名、删除
  - `.pt`/`.mat` 等二进制文件管理

### 通知系统

配置路径：`users/<username>/webhook.json`

支持渠道：
- Server酱（推送至微信）
- PushPlus（推送至微信）
- 企业微信群机器人
- 自定义 Webhook URL

触发场景：
- 训练任务完成
- 训练任务出错
- 队列任务启动

---

## 文件结构

```
dxw/
├── app.py                      # Flask 主应用
├── auth.py                     # 用户认证模块
├── terminal_server.py          # WebSocket 终端服务器
├── load_pt.py                  # .pt 文件查看工具
├── lcvr_cascade_3lc.py         # LCVR 三级级联仿真
├── SGD_of_MM_ADAM_DFFT_v5.py   # 多平面相位全息优化
├── users/                      # 用户数据目录
│   ├── <username>/
│   │   ├── uploads/            # 用户上传文件
│   │   ├── SGD_of_MM_*.py      # 训练脚本
│   │   ├── *.pt / *.mat        # 模型文件
│   │   └── webhook.json        # 通知配置
│   └── ...
├── templates/                  # HTML 模板
│   ├── index.html              # 主面板
│   ├── editor.html             # 代码编辑器
│   └── ...
├── uploads/                    # 全局上传目录
└── users.db                    # 用户数据库
```

---

## LCVR 仿真模块

`lcvr_cascade_3lc.py` 是独立的光学仿真脚本，用于三级级联 LCVR（液晶可变延迟器）系统仿真。

### 光学结构

```
光源 → P(0°) → LC1 → A_mid1(0°) → LC2 → A_mid2(0°) → LC3 → A(0°) → 探测器
```

### 主要参数

| 参数 | LC1 | LC2 | LC3 |
|------|-----|-----|-----|
| 快轴角度 | 45° | 45° | 45° |
| 厚度 | 4μm | 10μm | 15μm |
| 双折射率 | 0.23 | 0.23 | 0.23 |

- 电压范围：0 - 7.5V（特征电压 V0=2.5V）
- 波长范围：400 - 700nm

### 运行方式

```bash
python lcvr_cascade_3lc.py
```

输出 30 个子图（5×6），包含：
- LC1/LC2/LC3 单独调制矩阵
- 25 种 V2/V3 固定组合下的三级级联调制矩阵

---

## 相位全息优化模块

`SGD_of_MM_ADAM_DFFT_v5.py` 实现基于 PyTorch 的多平面相位全息优化。

### 核心功能

- Fresnel 衍射传播（GPU 加速）
- ADAM 优化器 + 自动梯度
- 损失函数：MSE + 串扰 + Total Variation + 相关系数
- 多 GPU 支持（DataParallel）

### 加载 .pt 文件

```bash
python load_pt.py
```

在当前目录查找 `.pt` 文件并显示相位分布。

---

## 配置文件说明

### webhook.json 示例

```json
{
  "type": "qywx",
  "qywx_webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
  "serverchan_key": "",
  "pushplus_token": ""
}
```

### settings.json（用户设置）

```json
{
  "favorite_path": "C:/path/to/workspace",
  "python_env": "C:/Python39/python.exe"
}
```

---

## 常见问题

**Q: 终端连接失败？**
A: 确保已登录，未登录用户无法获取终端 Token。

**Q: GPU 任务排队不执行？**
A: 检查 GPU 显存是否被占用，点击"清理显存"按钮释放。

**Q: 训练完成没收到通知？**
A: 检查 `users/<username>/webhook.json` 配置是否正确，测试通知功能。

---

## 启动方式

| 命令 | 说明 |
|------|------|
| `python app.py` | 普通启动 |
| `pythonw start_hidden.bat` | 无窗口后台运行 |
| `pythonw start_admin.bat` | 管理员模式启动 |

停止服务器：
```bash
python stop_server.bat
```

重启：
```bash
python restart_server.bat
```
