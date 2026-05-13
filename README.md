
# DXW 服务器远程链接平台

一套完整的 **服务器远程管理 + 文件同步** 解决方案，包含 Web 控制面板（服务端）和桌面同步客户端两部分。

---

## 目录

- [功能概览](#功能概览)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [Web 控制面板](#web-控制面板)
  - [用户与权限体系](#用户与权限体系)
  - [白名单审批流程](#白名单审批流程)
  - [脚本运行与训练队列](#脚本运行与训练队列)
  - [性能监控](#性能监控)
  - [Web 终端](#web-终端)
  - [个人云盘](#个人云盘)
  - [共享文件夹](#共享文件夹)
  - [站内消息与通知](#站内消息与通知)
  - [朋友圈](#朋友圈)
  - [AI 助手](#ai-助手)
- [桌面同步客户端](#桌面同步客户端)
  - [功能特性](#功能特性)
  - [配置说明](#配置说明)
  - [跨平台支持](#跨平台支持)
- [文件结构](#文件结构)
- [配置文件说明](#配置文件说明)
- [常见问题](#常见问题)
- [开发与部署](#开发与部署)

---

## 功能概览

```
┌─────────────────────────────────────────────────────────────┐
│                    DXW 服务器远程链接平台                      │
├──────────────────────┬──────────────────────────────────────┤
│   Web 控制面板 (服务端)  │       桌面同步客户端 (sync_client)      │
├──────────────────────┼──────────────────────────────────────┤
│ · 用户注册/登录/角色    │ · tkinter 深色主题 GUI                │
│ · 白名单审批体系       │ · 系统托盘 (pystray)                  │
│ · 脚本编辑/运行/调试    │ · 双向文件同步                        │
│ · GPU 训练队列        │ · 大文件分片上传 (30MB/片)             │
│ · 实时性能监控        │ · 文件变更自动检测 (watchdog)           │
│ · Web 终端 (WebSocket)│ · 云盘浏览/上传/下载                   │
│ · 个人云盘            │ · 多文件夹同步                        │
│ · 共享文件夹          │ · 同步日志                            │
│ · 站内消息            │                                      │
│ · 系统通知            │                                      │
│ · 朋友圈动态          │                                      │
│ · AI 助手            │                                      │
└──────────────────────┴──────────────────────────────────────┘
```

| 模块 | 说明 |
|------|------|
| **Web 控制面板** | Flask + Waitress 生产服务器，通过浏览器远程管理服务器 |
| **用户系统** | 注册/登录、三级角色（超级管理员/管理员/普通用户）、白名单审批 |
| **脚本运行** | 上传/编辑/运行 Python 脚本，支持多 GPU 环境选择 |
| **训练队列** | GPU 任务自动排队，显存不足时等待，完成后通知 |
| **性能监控** | 实时 CPU/内存/GPU 监控图表，一键清理僵尸进程 |
| **Web 终端** | 浏览器内运行真实 cmd/PowerShell/Git Bash/WSL 终端 |
| **个人云盘** | 每用户独立存储空间，文件/文件夹上传、下载、预览 |
| **共享文件夹** | 公共/私有文件夹，邀请制加入，文件创建者追踪 |
| **站内消息** | 用户间私信，支持附件 |
| **系统通知** | 训练完成/出错自动推送（企业微信/Server酱/PushPlus） |
| **朋友圈** | 用户动态发布、点赞、图片分享 |
| **AI 助手** | 内置多模型 AI 对话（Claude/OpenAI/DeepSeek/自定义） |
| **同步客户端** | 桌面端 tkinter 应用，双向文件同步，系统托盘常驻 |

---

## 系统架构

```
                    ┌──────────────────────────┐
                    │      浏览器 / 客户端       │
                    └────────┬─────────────────┘
                             │ HTTP / WebSocket
                    ┌────────▼─────────────────┐
                    │    Waitress 生产服务器      │
                    │    (app.py :5000)         │
                    ├──────────────────────────┤
                    │  Flask Web 控制面板        │
                    │  · 用户认证 (auth.py)      │
                    │  · 文件管理               │
                    │  · 脚本运行               │
                    │  · 性能监控               │
                    │  · AI 助手               │
                    └────────┬─────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──────┐ ┌────▼────┐ ┌───────▼──────┐
     │ WebSocket 终端 │ │ SQLite  │ │  用户文件存储   │
     │ (terminal_     │ │ (users  │ │  (users/xxx/  │
     │  server.py     │ │  .db)   │ │   uploads/)  │
     │  :5001)        │ │         │ │              │
     └────────────────┘ └─────────┘ └──────────────┘

     ┌─────────────────────────────────────────┐
     │         桌面同步客户端 (sync_client)       │
     │  · tkinter GUI + 系统托盘                 │
     │  · 定时同步 / 文件变更触发                  │
     │  · 分片上传 / 批量下载                     │
     │  · SQLite 本地状态跟踪                     │
     └─────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

- Python 3.8+
- Windows (服务端终端功能依赖 `pywinpty`) / macOS (客户端可用)
- 推荐 GPU 环境用于训练任务

### 1. 安装依赖

```bash
# 服务端依赖
pip install flask waitress psutil requests

# WebSocket 终端依赖 (仅 Windows)
pip install websockets pywinpty

# 同步客户端依赖
pip install requests pystray Pillow psutil

# 可选：文件监控 (自动同步触发)
pip install watchdog
```

### 2. 启动服务器

```bash
# 推荐：使用 Waitress 生产服务器
python run_server.py

# 或直接启动 Flask 开发服务器
python app.py
```

启动后访问：
- 控制面板：`http://localhost:5000`
- 上传页面：`http://localhost:5000/upload`
- 控制器：`http://localhost:5000/controller`

### 3. 首次使用

1. 访问注册页面创建账号
2. 超级管理员用户名固定为 `ssr`（拥有全部权限）
3. 普通用户需申请白名单，管理员审批后才能访问代码编辑器和云盘

### 4. 启动同步客户端

```bash
python sync_client.py
```

首次启动会弹出设置窗口，填写服务器地址、账号密码、选择本地同步文件夹即可。

---

## Web 控制面板

### 用户与权限体系

系统采用三级角色体系：

| 角色 | 标识 | 权限范围 |
|------|------|---------|
| **超级管理员** | `ssr` | 全部权限，可指派/撤销管理员，管理白名单，删除用户 |
| **管理员** | `admin` | 访问后台监管（白名单审批、用户管理），不能管理角色 |
| **普通用户** | `user` | 基础功能，需白名单审批后才能访问编辑器和云盘 |

- 密码使用 PBKDF2-SHA256 + 随机盐值哈希存储（100,000 次迭代）
- Session 认证，支持 Flask Session + API Token 双模式
- 超级管理员角色不可被修改或降级

### 白名单审批流程

```
用户提交申请 ──→ 所有管理员收到通知
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    管理员A审批  管理员B审批  管理员C审批
        │           │           │
        └───────────┼───────────┘
                    ▼
            全部同意 → 自动加入白名单
            任一拒绝 → 申请被驳回
```

- 用户提交白名单申请（需填写理由）
- 所有管理员（超级管理员 + 普通管理员）均需审批
- **全部同意**才通过，任一拒绝即驳回
- 审批结果通过系统通知推送给申请人
- 防重复提交：已有 pending 申请时拒绝新申请

### 脚本运行与训练队列

- 支持上传 Python 脚本并在服务器上执行
- 可选择 Python 环境（支持 Anaconda 虚拟环境）
- 实时 WebSocket 输出日志
- **GPU 训练队列**：
  - GPU 显存不足时自动排队
  - 任务完成/出错时自动触发 Webhook 通知
  - 支持多 GPU 选择

### 性能监控

- **CPU**：使用率、温度、型号、核心数
- **内存**：已用/总量/可用/使用率
- **GPU**：每张卡的显存使用、温度、GPU 占用率
- **趋势图**：CPU/GPU 历史数据可视化
- **清理显存**：一键杀掉占用 GPU 的僵尸进程

### Web 终端

- 基于 WebSocket + xterm.js 的真实终端
- 支持 **cmd.exe / PowerShell / Git Bash / WSL** 四种 Shell
- 真实 PTY 终端，行为与本地终端完全一致
- 右侧面板支持竖排 3 个终端窗口，可拖拽调整高度
- 右键菜单：复制、粘贴、清屏
- Token 认证，只有登录用户才能连接

### 个人云盘

每个用户拥有独立的文件存储空间：

```
users/
├── <username>/
│   ├── uploads/        # 用户上传的文件
│   ├── config/         # 用户配置
│   │   ├── profile.json    # 个人资料
│   │   └── avatar.*        # 头像
│   └── ...
```

功能：
- 文件/文件夹上传（支持拖拽）
- 文件预览（图片直接在页面内显示）
- 下载、重命名、删除
- `.pt` / `.mat` 等二进制文件管理

### 共享文件夹

支持公共和私有两种共享模式：

| 类型 | 说明 |
|------|------|
| **公共文件夹** | 所有白名单用户可见，自动共享 |
| **私有文件夹** | 创建者邀请制，需接受邀请才能加入 |

功能：
- 邀请/接受/拒绝加入
- 文件创建者追踪（`shared_file_meta` 表记录上传者）
- 管理员可删除任意共享文件夹
- 磁盘文件夹自动同步到数据库

### 站内消息与通知

**站内消息**：
- 用户间私信，支持主题、正文、附件
- 收件箱/已发送/未读计数
- 仅发送者和接收者可查看/删除

**系统通知**：
- 白名单审批结果通知
- 训练任务完成/出错通知
- 邀请通知
- 支持一键全部已读

### 朋友圈

- 发布文字动态（支持图片）
- 点赞/取消点赞
- 按时间线浏览所有用户动态
- 查看某用户的动态
- 作者或管理员可删除动态

### AI 助手

- 内置多模型 AI 对话
- 支持 Claude / OpenAI / DeepSeek / 自定义模型
- 在 Web 控制面板内直接使用

---

## 桌面同步客户端

### 功能特性

`sync_client.py` 是基于 tkinter 的桌面文件同步工具，主要功能：

| 功能 | 说明 |
|------|------|
| **双向同步** | 本地与服务器文件双向同步，基于 mtime 比对 |
| **多文件夹** | 支持同时同步多个本地文件夹到不同云端路径 |
| **分片上传** | 大文件 (≥10MB) 自动分片上传 (30MB/片)，内存友好 |
| **自动下载** | 可开启/关闭，开启时服务器变更自动下载到本地 |
| **文件监控** | 安装 watchdog 后支持文件变更自动触发同步 |
| **系统托盘** | 最小化到系统托盘，后台静默运行 |
| **云盘浏览** | 浏览服务器云盘文件，支持上传/下载/新建文件夹 |
| **同步日志** | 查看同步历史记录 |
| **深色主题** | Catppuccin 风格深色 GUI |

### 界面预览

```
┌─────────────────────────────────────────────────────┐
│  CP Group Cloud   [立即同步] [刷新] [上传] [下载] ... │
├──────────────┬──────────────────────────────────────┤
│  [ 云盘 ]     │  文件名          大小     修改时间  类型  │
│              │                                      │
│  [D] 我的云盘  │  [D] documents    -     05-14 ...  文件夹│
│   ├ [D] ...  │  report.pdf    2.3 MB   05-13 ...  PDF  │
│   └ [D] ...  │  data.csv     15.6 KB   05-12 ...  文件 │
│              │  model.pt     245.0 MB   05-10 ...  文件 │
├──────────────┴──────────────────────────────────────┤
│  ● 同步完成          自动下载: 开启  |  用户: xxx       │
└─────────────────────────────────────────────────────┘
```

### 同步流程

```
1. 登录服务器
2. 扫描本地文件夹 (os.scandir 高性能扫描)
3. 拉取服务端目录树
4. 基于 mtime 比对差异
   - 本地新/修改 → 上传
   - 服务端新 → 下载 (auto_download=true)
5. 大文件分片上传，小文件直传
6. 更新本地状态数据库
7. 等待下一轮同步 (默认 15 秒间隔)
```

### 配置说明

配置文件保存在 `~/.dxw_sync_config.json`：

```json
{
  "server_url": "http://your-server:5000",
  "username": "your_username",
  "password": "your_password",
  "sync_folders": [
    {
      "local": "C:/Users/xxx/Documents",
      "remote": "documents"
    },
    {
      "local": "D:/Projects",
      "remote": "projects"
    }
  ],
  "sync_interval": 15,
  "auto_download": true
}
```

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `server_url` | 服务器地址 | `http://localhost:5000` |
| `username` | 用户名 | - |
| `password` | 密码 | - |
| `sync_folders` | 同步文件夹列表 `[{local, remote}]` | `[]` |
| `sync_interval` | 同步间隔（秒），最小 10 | `15` |
| `auto_download` | 开启自动下载 | `true` |

### 跨平台支持

同步客户端使用跨平台技术栈，可运行在 Windows 和 macOS 上：

| 组件 | 技术 | Windows | macOS |
|------|------|---------|-------|
| GUI | tkinter | ✅ | ✅ |
| 系统托盘 | pystray | ✅ | ✅ |
| HTTP 请求 | requests | ✅ | ✅ |
| 文件监控 | watchdog | ✅ | ✅ |
| 进程监控 | psutil | ✅ | ✅ |

**Windows 特有优化**：
- `SetProcessWorkingSetSize` 内存回收
- `pywinpty` 终端支持

**macOS 打包**：
- 支持 GitHub Actions 自动打包成 `.app`
- 推送 `v*` tag 或手动触发即可构建
- 详见 `.github/workflows/build_mac.yml`

---

## 文件结构

```
dxw/
├── app.py                      # Flask 主应用 (Web 控制面板)
├── auth.py                     # 用户认证模块 (角色/白名单/消息/通知)
├── run_server.py               # Waitress 生产服务器启动脚本
├── terminal_server.py          # WebSocket 终端服务器
├── sync_client.py              # 桌面同步客户端 (tkinter GUI)
│
├── templates/                  # HTML 模板
│   ├── index.html              # 主面板
│   ├── editor.html             # 代码编辑器
│   └── ...
│
├── users/                      # 用户数据目录
│   └── <username>/
│       ├── uploads/            # 用户上传文件
│       ├── config/
│       │   ├── profile.json    # 个人资料
│       │   └── avatar.*        # 头像
│       └── ...
│
├── shared/                     # 共享文件夹
│   ├── public/                 # 公共共享
│   └── private/                # 私有共享
│
├── uploads/                    # 全局上传目录 (兼容性)
├── users.db                    # SQLite 用户数据库
│
├── build_mac.py                # macOS 本地打包脚本
├── requirements.txt            # Python 依赖清单
├── .github/
│   └── workflows/
│       └── build_mac.yml       # GitHub Actions macOS 自动打包
│
├── *.bat                       # Windows 启动/停止脚本
├── logs/                       # 运行日志
└── README.md                   # 本文档
```

---

## 配置文件说明

### 用户通知配置

路径：`users/<username>/webhook.json`

```json
{
  "type": "qywx",
  "qywx_webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
  "serverchan_key": "",
  "pushplus_token": ""
}
```

支持渠道：
- **企业微信群机器人** (`type: "qywx"`)
- **Server酱** - 推送至微信 (`type: "serverchan"`)
- **PushPlus** - 推送至微信 (`type: "pushplus"`)
- **自定义 Webhook** (`type: "custom"`)

触发场景：训练任务完成、训练出错、队列任务启动。

### 用户设置

路径：`users/<username>/settings.json`

```json
{
  "favorite_path": "C:/path/to/workspace",
  "python_env": "C:/Python39/python.exe"
}
```

### 同步客户端配置

路径：`~/.dxw_sync_config.json`

```json
{
  "server_url": "http://localhost:5000",
  "username": "user",
  "password": "pass",
  "sync_folders": [
    {"local": "/path/to/local", "remote": "remote_name"}
  ],
  "sync_interval": 15,
  "auto_download": true
}
```

---

## 常见问题

### 服务端

**Q: 终端连接失败？**
A: 确保已登录，未登录用户无法获取终端 Token。Token 有效期 1 小时，过期需重新登录。

**Q: GPU 任务排队不执行？**
A: 检查 GPU 显存是否被占用，点击"清理显存"按钮释放僵尸进程。

**Q: 训练完成没收到通知？**
A: 检查 `users/<username>/webhook.json` 配置是否正确，使用测试通知功能验证。

**Q: 上传文件失败？**
A: 最大上传限制 10GB。大文件建议使用同步客户端的分片上传功能。

**Q: 白名单申请一直 pending？**
A: 需要所有管理员都审批。联系管理员尽快处理，任一拒绝即会收到驳回通知。

### 同步客户端

**Q: 同步客户端支持 macOS 吗？**
A: 支持。使用 tkinter + pystray 跨平台技术栈，macOS 上可直接运行或通过 GitHub Actions 打包成 `.app`。

**Q: 大文件上传很慢？**
A: 大文件 (≥10MB) 自动分片上传，每片 30MB。网络不稳定时会自动重试（最多 3 次）。

**Q: 如何在 macOS 上打包？**
A: 两种方式：
1. 本地：在 Mac 上运行 `python build_mac.py`
2. 远程：推送 `v*` tag 到 GitHub，Actions 自动打包，从 Releases 下载 `.app`

**Q: 同步冲突怎么处理？**
A: 当前基于 mtime 比对，本地和服务端同时修改同一文件时，后同步的会覆盖先同步的。建议单向编辑或及时同步。

---

## 开发与部署

### 启动方式

| 命令 | 说明 |
|------|------|
| `python run_server.py` | Waitress 生产服务器 (推荐) |
| `python app.py` | Flask 开发服务器 |
| `python sync_client.py` | 启动同步客户端 |

### Windows 后台运行

| 脚本 | 说明 |
|------|------|
| `start_hidden.bat` | 无窗口后台运行 |
| `start_admin.bat` | 管理员模式启动 |
| `start_debug.bat` | 调试模式启动 |
| `restart_server.bat` | 重启服务器 |
| `stop_server.bat` | 停止服务器 |

### 服务器配置

`run_server.py` 默认配置：
- 监听地址：`0.0.0.0:5000`
- 线程数：16
- 最大并发连接：100
- 连接超时：300s
- 最大请求体：10GB

### 数据库

使用 SQLite，主要表：

| 表名 | 说明 |
|------|------|
| `users` | 用户账号（含角色） |
| `whitelist` | 白名单 |
| `whitelist_applications` | 白名单申请 |
| `whitelist_approvals` | 管理员审批记录 |
| `messages` | 站内消息 |
| `notifications` | 系统通知 |
| `shared_folders` | 共享文件夹 |
| `shared_folder_members` | 共享文件夹成员 |
| `shared_folder_invitations` | 共享文件夹邀请 |
| `shared_file_meta` | 共享文件元数据 |
| `moments` | 朋友圈动态 |
| `moment_likes` | 朋友圈点赞 |

### 技术栈

**服务端**：
- Python 3.8+
- Flask + Waitress (生产 WSGI)
- websockets (WebSocket 终端)
- pywinpty (Windows PTY)
- SQLite (数据库)
- psutil (系统监控)

**同步客户端**：
- Python 3.8+
- tkinter (GUI)
- pystray + Pillow (系统托盘)
- requests (HTTP)
- watchdog (文件监控)
- psutil (进程监控)
