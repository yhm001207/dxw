# DXW 远程服务器管理平台 — 完整功能文档

> 项目路径: `C:\Users\94885\Desktop\dxw - 副本`

---

## 一、项目概述

**DXW** 是一个多功能远程服务器管理平台，集 Web 管理面板、桌面同步客户端、Windows 资源管理器扩展于一体。主要功能包括：

- 远程执行 Python/MATLAB/Octave 脚本
- 个人云盘文件管理
- 服务器性能监控（CPU、GPU、内存、磁盘）
- 协作共享：共享文件夹、站内消息、社交动态
- 本地 ↔ 服务器双向文件同步
- 代码编辑器 + 终端 + AI 助手

### 架构概览

```
┌────────────────────────────────────────────────────────┐
│                   DXW 平台架构                          │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Web 服务器 (Flask + Waitress, 端口 5000)              │
│    ├── Web 管理面板 (13 个 HTML 页面)                  │
│    ├── REST API (80+ 个接口)                           │
│    └── SSE 实时流 (脚本输出、终端响应)                  │
│                                                        │
│  WebSocket 终端服务器 (端口 5001)                       │
│    ├── xterm.js 网页终端                                │
│    ├── Token 认证                                       │
│    └── 真实 PTY (winpty / ptyprocess)                   │
│                                                        │
│  SQLite 数据库 (users.db)                               │
│    ├── 用户认证 + 角色管理                              │
│    ├── 消息系统                                         │
│    ├── 通知系统                                         │
│    ├── 共享文件夹元数据                                 │
│    └── 白名单申请记录                                   │
│                                                        │
│  桌面同步客户端 (PySide6)                               │
│    ├── 5 个功能页面                                     │
│    ├── 3 种主题 (Light / Dark / Sci-Fi)                 │
│    ├── 同步引擎 (后台线程 + Qt 信号)                    │
│    └── 系统托盘                                         │
│                                                        │
│  Windows 资源管理器扩展 (C#)                            │
│    └── 同步状态覆盖图标                                 │
│                                                        │
│  同步引擎 (Python)                                      │
│    ├── 双向同步                                         │
│    ├── 分块上传 (30MB)                                  │
│    ├── 文件监控 (watchdog)                              │
│    └── 智能对比 (修改时间 + 大小)                       │
│                                                        │
│  AI/ML 脚本运行器                                       │
│    ├── PyTorch 环境检测                                 │
│    ├── GPU 选择 + 多卡 DataParallel                     │
│    ├── WSL Python 支持                                  │
│    ├── MATLAB/Octave 执行                               │
│    └── 训练任务队列                                     │
└────────────────────────────────────────────────────────┘
```

---

## 二、Web 服务器功能 (Flask)

### 2.1 认证与用户管理

| 功能 | 接口 | 说明 |
|------|------|------|
| 登录 | `/login` | 用户名 + 密码，PBKDF2 加密验证，Session 会话 |
| 注册 | `/register` | 用户名 3-20 字符，密码 6+，自动登录 |
| 登出 | `/logout` | 清除 Session |
| 角色体系 | 内置 | `super_admin`(ssr) > `admin` > `user`(需白名单) |

### 2.2 Web 页面 (13 个模板)

#### 1. 登录页 (`login.html`)
- 用户名/密码表单
- 错误/成功提示
- 注册入口

#### 2. 注册页 (`register.html`)
- 注册表单 + 客户端密码一致性校验
- 注册后自动登录跳转

#### 3. 大厅 (`lobby.html`) — 登录后首页
- 顶部导航：头像、昵称、在线指示器、通知铃铛、登出
- 功能卡片区（6+ 卡片）：
  - 代码编辑器 → `/upload`（需白名单）
  - 个人云盘 → `/files`（需白名单）
  - 系统状态 → 弹窗模态
  - 速度测试 → `/speed_test`
  - 在线用户 → `/online_users`
  - 管理面板 → `/controller`（管理员）
  - 申请白名单 → 非白名单用户可见
- 管理员目录（可折叠）
- 排行榜（磁盘空间、文件数、脚本数）
- 系统状态弹窗（CPU、内存、磁盘、GPU、Python 环境）

#### 4. 脚本运行器 (`index.html`) — 功能选择页
- 服务器运行/暂停徽章
- 快捷操作栏
- 5 个预置脚本卡片（cascade / simple / load_pt / sgd_dfft / sgd_siren）
- 每个卡片：运行/停止按钮 + 实时日志
- 工具卡片（云盘、上传、管理、测速）

#### 5. 代码编辑器 (`upload.html`) — 最复杂页面 (~2000 行)
- **三栏布局**：文件浏览器 | 编辑器 | 输出面板
- **文件浏览器**（左）：
  - 文件树 + 颜色分类图标
  - 复选框多选
  - 路径栏（手动输入、导航、收藏夹、位置下拉）
  - 右键菜单（下载、重命名、删除）
  - 拖拽上传
- **代码编辑器**（中）：
  - 多标签编辑
  - VS Code Dark+ 风格语法高亮
  - 行号 + 实时编辑
  - 图片预览 + 视频播放
- **输出面板**（右）：
  - 5 个标签：运行日志 / 生成文件 / 终端 / AI 助手 / 性能监控
  - **终端**：xterm.js WebSocket 终端（cmd/PowerShell/Git Bash）
  - **AI 助手**：支持 Claude / OpenAI / DeepSeek
  - **性能监控**：CPU/内存实时曲线 + GPU 状态
- **工具栏**：运行/停止/保存、Python 环境选择、GPU 选择、多卡开关、训练队列
- **笔记本模式**：交互式 Cell（代码/Markdown），逐 Cell 运行
- **移动端适配**：`.is-mobile` 响应式布局

#### 6. 云端文件管理 (`files.html`) — 个人云盘
- 面包屑导航
- 工具栏：返回、刷新、磁盘驱动器、个人目录、共享文件夹切换
- 上传（文件 + 文件夹，逐文件进度条）
- 新建文件夹、批量删除、批量下载（智能：大文件直传，小文件 zip）
- 文件搜索/过滤
- 文件列表（复选框、图标、排序）
- 拖拽上传
- 右键菜单（打开、预览、下载、复制、剪切、粘贴、重命名、移动、删除）
- 预览面板（文本/图片/视频）
- 磁盘用量条
- **共享文件夹面板**：
  - 公共/私有两种类型
  - 创建、浏览、上传、邀请成员
  - 邀请系统（接受/拒绝）

#### 7. 管理面板 (`controller.html`) — Admin 控制台
- 服务器状态卡片（运行/暂停）+ 控制按钮
- **申请白名单**（非管理员）→ 申请状态追踪
- **用户流量统计**（管理员）→ 上传/下载/总计排行
- **白名单管理**（管理员）→ 用户列表 + 批量编辑（super_admin）
- **管理员管理**（super_admin）→ 授权/撤销管理员、删除用户（10s 倒计时）
- **访客日志** → 完整访问记录（自动刷新 + 静态资源过滤）
- **待审批申请** → 审批卡（批准/拒绝）

#### 8. 消息中心 (`messages.html`)
- 4 个标签：收件箱 / 已发送 / 通知 / 写消息
- **收件箱**：未读标记、发件人、主题、预览、附件处理（下载、转存云盘）
- **已发送**：收件人、已读状态、附件
- **通知**：类型图标、关联链接、标记已读、删除、清空
- **写消息**：收件人下拉（自动填充）、主题、正文、附件上传
- **共享文件夹邀请**：接受/拒绝按钮

#### 9. 个人设置 (`profile.html`)
- **头像**：点击上传，裁剪模态（可拖拽/缩放/圆形裁剪）
- **封面**：上传裁剪（2.7:1 比例，1080×400 输出）
- **昵称**（30 字）、**简介**（200 字）、**签名**（100 字）
- **动态 (Moments)**：图文社交动态，点赞/取消，删除

#### 10. 用户主页 (`user_profile.html`) — 查看他人主页
- 封面、头像、昵称、简介、签名
- "发消息" 或 "编辑资料" 按钮
- 动态流（点赞/删除/图片灯箱）

#### 11. 性能展示 (`showcase.html`) — 非白名单用户
- 动画 Hero 区
- CPU 仪表盘（圆形）、内存仪表盘、GPU 卡片（利用率/显存/温度）
- 每 2 秒自动刷新
- "联系管理员" CTA

#### 12. 在线用户 (`online_users.html`)
- 摘要卡片：在线数、5 分钟请求、活跃页面
- 用户卡片：头像、在线点、IP、最后活动、运行进程、请求数
- 每 5 秒自动刷新

#### 13. 速度测试 (`speed_test.html`)
- 开始测试按钮（下载 100MB 随机数据）
- 进度条
- 结果：文件大小、下载时间、平均速度（MB/s，颜色编码）

---

### 2.3 REST API 列表 (80+ 接口)

#### 脚本执行
| 接口 | 说明 |
|------|------|
| `/api/scripts` | 列出可用脚本 |
| `/api/run/<name>` | SSE 流式执行预置脚本 |
| `/api/stop/<name>` | 停止脚本 |
| `/api/run_upload/<file>` | SSE 执行上传文件 |
| `/api/run_file` | SSE 执行任意文件（GPU/多卡/WSL/MATLAB） |
| `/api/run_cell` | SSE 执行笔记本 Cell |
| `/api/save_notebook` | 保存 .ipynb |
| `/api/reconnect_output` | SSE 重连运行中的输出 |

#### 文件管理
| 接口 | 说明 |
|------|------|
| `/api/files` | 列出目录内容 |
| `/api/my_dir` | 获取个人目录路径 |
| `/api/file_content` | 读取文本文件（自动检测编码） |
| `/api/download_file` | 下载文件 |
| `/api/file_preview` | 预览图片 |
| `/api/video_stream` | 视频流（HTTP Range） |
| `/api/create_folder` | 创建文件夹 |
| `/api/delete_file` | 删除文件/文件夹 |
| `/api/move_file` | 移动 |
| `/api/copy_file` | 复制 |
| `/api/rename_file` | 重命名 |
| `/api/batch_delete` | 批量删除 |
| `/api/batch_move` | 批量移动 |
| `/api/batch_download` | 批量下载 |
| `/api/save_file` | 保存文件内容 |
| `/api/drives` | 列出磁盘驱动器 |
| `/api/disk_usage` | 磁盘用量 |
| `/api/user_dir` | 用户根目录 |

#### 上传系统
| 接口 | 说明 |
|------|------|
| `/api/upload` | 上传文件（支持目录结构） |
| `/api/upload_chunk` | 上传分块 |
| `/api/upload_complete` | 合并分块 |

#### 白名单 & 审批
| 接口 | 说明 |
|------|------|
| `/api/whitelist` | 获取白名单 |
| `/api/whitelist/add/remove` | 添加/移除 |
| `/api/whitelist/apply` | 提交申请 |
| `/api/whitelist/approve/reject` | 审批/拒绝 |
| `/api/whitelist/pending` | 待审批列表 |

#### 消息 & 通知
| 接口 | 说明 |
|------|------|
| `/api/messages/send/inbox/sent` | 消息 CRUD |
| `/api/notifications` | 通知列表 |
| `/api/notifications/read/delete` | 通知操作 |

#### 共享文件夹
| 接口 | 说明 |
|------|------|
| `/api/shared/create/browse/delete` | 共享文件夹 CRUD |
| `/api/shared/invite/accept/reject` | 邀请系统 |

#### 动态 (Moments)
| 接口 | 说明 |
|------|------|
| `/api/moments` | GET 列表 / POST 创建 |
| `/api/moments/<id>` | DELETE 删除 |
| `/api/moments/<id>/like` | 点赞/取消 |

#### 性能监控
| 接口 | 说明 |
|------|------|
| `/api/system_status` | 完整系统状态 |
| `/api/performance_status` | 实时 CPU/内存/GPU |
| `/api/showcase_status` | 公开性能展示 |
| `/api/gpus` | GPU 列表 |
| `/api/leaderboard` | 用户排行榜 |
| `/api/traffic` | 流量统计 |
| `/api/visitors` | 访客日志 |

#### 服务器控制
| 接口 | 说明 |
|------|------|
| `/api/status` | 服务器状态 |
| `/api/shutdown` | 暂停 |
| `/api/start` | 恢复 |

#### 终端 & AI
| 接口 | 说明 |
|------|------|
| `/api/terminal/token` | 获取 WebSocket Token |
| `/api/terminal/exec` | SSE 执行命令 |
| `/api/ai/chat` | AI 对话代理 |

#### 训练队列
| 接口 | 说明 |
|------|------|
| `/api/train_queue` | 队列状态 |
| `/api/gpu/cleanup` | 清理 GPU 进程 |

---

## 三、桌面同步客户端 (PySide6)

### 3.1 架构

```
sync_client_ui/
├── main.py                  # 入口，QApplication
├── core/
│   ├── config.py            # 配置读写 + 密码加密 (base64)
│   ├── cloud_api.py         # REST API 封装
│   ├── sync_engine.py       # 同步引擎 (QObject + Qt 信号)
│   └── db.py                # 本地 SQLite 状态数据库
├── ui/
│   ├── main_window.py       # 主窗口 (自定义标题栏 + 侧栏 + 内容区)
│   ├── theme.py             # 3 主题: Light / Dark / Sci-Fi
│   ├── pages/
│   │   ├── dashboard_page.py  # 首页
│   │   ├── tasks_page.py      # 备份任务
│   │   ├── files_page.py      # 云端文件
│   │   ├── logs_page.py       # 同步日志
│   │   └── settings_page.py   # 设置
│   ├── widgets/
│   │   ├── sidebar.py         # 导航侧栏
│   │   ├── top_bar.py         # 顶部状态栏
│   │   ├── bottom_bar.py      # 底部栏 (托盘/暂停)
│   │   └── status_bar.py      # 状态栏 (上次同步/文件数)
│   ├── animations/
│   │   ├── page_transition.py    # 页面切换滑动动画
│   │   ├── fade_in_mixin.py      # 组件滑入动画
│   │   ├── numeric_animator.py   # 数字滚动动效
│   │   ├── progress_animator.py  # 进度条平滑填充
│   │   ├── breathing_effect.py   # 状态呼吸脉冲
│   │   ├── toast_manager.py      # Toast 通知
│   │   ├── button_effects.py     # 按钮 hover 动效
│   │   ├── skeleton_screen.py    # 骨架屏加载态
│   │   └── extra_effects.py      # 更多动效
│   └── resources/styles/     # QSS 样式表 (light/dark/sci_fi)
├── dialogs/
│   ├── add_task_wizard.py    # 3 步添加任务向导
│   └── confirm_dialog.py     # 通用确认弹窗
└── resources/                # 图标、图片
```

### 3.2 页面功能

#### 首页 (Dashboard)
- 4 个统计卡片：今日成功备份数、今日失败数、总文件数、已备份数据量
- 数字滚动动画
- 最近活动动态流（上传/下载状态）
- 快速操作：立即同步、新建任务
- 卡片错开入场动效

#### 备份任务 (Tasks)
- 任务卡片列表：文件夹名、本地路径→远程路径、进度条、状态、详情
- 每张卡片操作按钮：立即同步、暂停、编辑、删除
- 添加任务按钮 → 3 步向导：选择文件夹 → 策略配置 → 确认
- 新卡片滑入动效

#### 云端文件 (Files)
- 树形视图：文件名、大小、修改时间、类型
- 面包屑导航
- 上传/下载/删除/刷新按钮
- 拖拽文件上传 + 高亮脉冲反馈
- 右键菜单（下载）
- 多选支持
- 上传进度条（平滑填充）

#### 同步日志 (Logs)
- 表格：时间、类型（图标）、操作、文件、大小、详情
- 文件名搜索
- 级别筛选（INFO/ERROR/WARN）
- 导出 CSV
- 清空（确认弹窗）

#### 设置 (Settings)
- **服务器**：URL、端口、用户名、密码、测试连接
- **备份**：频率（手动/实时/每小时/每天/每周）、增量/全量、冲突策略、重试次数
- **网络**：上传限速、下载限速、代理
- **通用**：开机自启、最小化到托盘、自动下载、通知开关、UI 主题

### 3.3 同步引擎
- QObject 后台线程
- 信号：`status_changed`、`progress_updated`、`task_completed`、`sync_error`、`activity_added`
- 扫描 → 对比 → 上传/下载 流水线
- Session 过期自动重连
- 分块上传（30MB）

### 3.4 系统托盘
- 颜色编码图标：绿色=已连接、蓝色=同步中、红色=错误、橙色=暂停
- 右键菜单：打开主窗口、立即同步、退出
- 双击还原窗口

### 3.5 三种主题
- **Light**：浅色商务风格
- **Dark**：深色护眼
- **Sci-Fi**：赛博朋克霓虹风格（青+紫渐变）

---

## 四、终端服务器 (WebSocket)

### 4.1 功能
- 端口 5001，与 Flask 分离
- Token 认证（UUID，1 小时有效期）
- 真实 PTY 终端（Windows: winpty，Linux: ptyprocess）
- 支持：输入、输出、resize、kill、exit
- 会话自动清理

### 4.2 Web 终端特性
- xterm.js 渲染
- 右键菜单（复制/粘贴）
- 清除/重连
- 选择 Shell（cmd/PowerShell/Git Bash）

---

## 五、Windows 资源管理器扩展 (C#)

### 5.1 功能
- 文件同步状态覆盖图标：
  - ✅ 绿色 ✓ = 已同步
  - 🔄 蓝色旋转 = 同步中
  - ❌ 红色 ✗ = 错误
  - ⚠️ 黄色 ! = 冲突
- 读取 `~/.dxw_sync_state.db` SQLite 数据库获取状态
- COM 注册为 `IShellIconOverlayIdentifier`

### 5.2 安装/卸载
- `build_and_install.bat`：编译 → 注册 COM → 重启 Explorer
- `uninstall.bat`：删除注册表项

---

## 六、性能监控系统

### 6.1 监控指标
| 指标 | 来源 | 更新频率 |
|------|------|----------|
| CPU 利用率 | psutil | 实时 |
| CPU 温度 | WMI / LibreHardwareMonitor | 实时 |
| 内存用量 | psutil | 实时 |
| GPU 状态 | nvidia-smi | 2 秒 |
| GPU 温度 | nvidia-smi | 2 秒 |
| 磁盘用量 | psutil | 按需 |
| 网络流量 | 自追踪 | 按需 |

### 6.2 GPU 管理
- 自动检测 NVIDIA GPU 列表
- 每个 GPU：名称、显存（总量/已用/可用）、温度、利用率
- GPU 显存清理：杀死所有 GPU 进程（除服务器外）
- 训练任务队列：GPU 空闲自动启动

---

## 七、用户管理系统

### 7.1 角色权限

| 角色 | 可做操作 |
|------|----------|
| **super_admin** (ssr) | 所有操作：管理管理员、删除用户、批量白名单、启停服务器 |
| **admin** | 白名单编辑、流量查看、访客日志、审批申请 |
| **user** | 基础访问，需白名单才能使用编辑器和云盘 |

### 7.2 白名单制度
- 控制编辑器 + 云盘访问权限
- 多管理员审批流程（全部同意才通过）
- 任一管理员拒绝即驳回
- super_admin 可直接批量设置

---

## 八、共享文件夹系统

### 8.1 类型
- **公共**：所有用户可读写
- **私有**：仅受邀成员可访问

### 8.2 功能
- 完整文件操作（上传、下载、删除、重命名、移动、复制）
- 文件元数据追踪（创建者、时间）
- 变更通知给所有成员
- 邀请系统（发送 → 接受/拒绝）

---

## 九、消息与通知系统

### 9.1 站内消息
- 收件箱/已发送
- 附件上传 + 转存云盘
- 已读/未读状态

### 9.2 通知
- 类型：共享文件夹邀请、系统通知
- 关联操作链接
- 标记已读 / 全部已读 / 删除 / 清空

### 9.3 Webhook 通知
- 支持：ServerChan、PushPlus、企业微信、自定义
- 事件：脚本执行完成、出错时通知

---

## 十、AI / 机器学习功能

### 10.1 脚本运行器
- 5 个预置全息光学仿真脚本（LCVR 级联、SGD 优化）
- PyTorch 环境检测（conda env）
- GPU 选择 + 多卡 DataParallel 自动包装
- WSL Python 支持

### 10.2 笔记本 Kernel
- 持久 Python 进程，Cell 间共享状态
- Matplotlib 内联显示
- Kernel 重启清空状态

### 10.3 MATLAB / Octave
- `.m` 文件执行
- 自动图形截取输出

### 10.4 AI 聊天助手
- 代理 Claude / OpenAI / DeepSeek API
- 代码编辑器右侧面板集成

### 10.5 训练队列
- GPU 资源感知排队
- GPU 空闲时自动启动
- 每任务指定 GPU 设备

---

## 十一、部署与构建

### 11.1 启动方式

| 脚本 | 模式 | 说明 |
|------|------|------|
| `启动服务器.bat` | 开发 | `python app.py` |
| `start_admin.bat` | 生产 | Waitress 16 线程，新窗口 |
| `start_debug.bat` | 调试 | 前台终端运行 |
| `start_hidden.bat` | 无窗 | `pythonw` 静默运行 |
| `stop_server.bat` | 停止 | 杀 5000 端口进程 |
| `restart_server.bat` | 重启 | 停止+启动 |

### 11.2 构建桌面客户端

| 脚本 | 平台 | 输出 |
|------|------|------|
| `build_windows.py` | Windows | `DXW同步客户端.exe` (PyInstaller) |
| `build_mac.py` | macOS | `DXW同步客户端.app` |

### 11.3 Shell 扩展
- `shell_extension/build_and_install.bat`：编译 + 注册 + 重启 Explorer
- `shell_extension/uninstall.bat`：卸载

---

## 十二、动画系统 (Desktop Client)

### 12.1 已实现的动效

| 动效 | 实现方式 | 效果 |
|------|----------|------|
| 页面切换 | QPropertyAnimation on `pos` | 新页左滑入 + 旧页左滑出，250ms |
| 卡片滑入 | QPropertyAnimation on `pos` | 从下方滑入，200ms OutCubic |
| 进度条平滑 | QPropertyAnimation on `value` | 数值平滑过渡，200ms |
| 数字滚动 | QTimer 驱动 (120fps) | 数字逐个递增，300ms |
| 状态呼吸 | QTimer + QSS 颜色切换 | 状态圆点颜色脉冲闪烁 |
| 同步点号 | QTimer + 文本更新 | "同步中" → "同步中." → "同步中.." |
| 按钮呼吸 | QTimer + QSS 边框脉冲 | 同步时主按钮边框亮暗交替 |
| Toast 通知 | QPropertyAnimation on `pos` | 右侧滑入 (200ms) + 上滑消失 (150ms) |
| 拖拽高亮 | QTimer + QSS 边框脉冲 | 拖文件入区域时边框+背景闪烁 |
| 骨架屏 | 静态 ShimmerBlock + QSS | 加载时灰色占位块 |

### 12.2 主题
- **Light** — 清爽商务蓝
- **Dark** — 深色护眼
- **Sci-Fi** — 赛博朋克青紫渐变

---

## 十三、技术栈

### 13.1 依赖
| 组件 | 技术 |
|------|------|
| Web 框架 | Flask + Waitress (WSGI) |
| 桌面 UI | PySide6 (Qt for Python) |
| 旧版桌面 | tkinter |
| Shell 扩展 | C# .NET Framework 4.7.2 |
| 数据库 | SQLite |
| 实时通信 | SSE (Server-Sent Events) + WebSocket |
| 终端 | xterm.js + winpty |
| 语法高亮 | Prism.js |
| 数学渲染 | KaTeX (CDN) |
| 系统监控 | psutil + WMI + nvidia-smi |
| 打包 | PyInstaller |

### 13.2 开发工具
| 工具 | 用途 |
|------|------|
| Python 3.7+ | 主语言 |
| VS Code | 推荐编辑器 |
| PyInstaller | 打包 exe |
| dotnet build | 编译 C# Shell 扩展 |

---

## 十四、配置说明

### 14.1 服务器配置
- 端口：5000 (HTTP) + 5001 (WebSocket)
- 最大上传：10GB
- 线程数：8-16
- 连接限制：100

### 14.2 桌面客户端配置 (`~/.dxw_sync_config.json`)
- 服务器连接：URL、端口、用户名、密码（base64 加密）
- 同步文件夹列表（本地路径、远程路径、频率、策略）
- 网络限制：上传/下载限速、代理
- 通用设置：开机自启、托盘、自动下载、通知、主题

### 14.3 用户个性化设置
- 每个用户 `users/<username>/config/settings.json`
- 收藏路径、编辑器偏好

---

## 十五、安全机制

- 密码：PBKDF2-HMAC-SHA256，100,000 迭代，加盐
- Session：Flask 加密 Cookie
- 路径安全：禁止越权访问其他用户目录
- 接口鉴权：`login_required` 装饰器
- 终端：Token 认证，1 小时有效期
- 管理员分离：普通用户无法访问管理 API
