# -*- coding: utf-8 -*-
"""
全局共享状态：进程字典、锁、队列、缓存
所有需要跨 Blueprint 共享的可变状态集中在此。
"""

import threading
import collections

# ==================== 脚本执行进程管理 ====================
_running_processes = {}
_running_lock = threading.Lock()

_upload_running = {}
_upload_lock = threading.Lock()

_file_running = {}
_file_lock = threading.Lock()

# 用户进程运行状态和输出缓冲（用于断线重连）
_user_processes = {}  # {username: {filename: {proc, buffer, start_time, path}}}
_user_proc_lock = threading.Lock()

# ==================== 监控 ====================
VISITORS = []
VISITORS_LOCK = threading.Lock()

_USER_TRAFFIC = {}
_USER_TRAFFIC_LOCK = threading.Lock()

# ==================== 服务器控制 ====================
SERVER_STOPPED = False
SERVER_LOCK = threading.Lock()

# ==================== 白名单变更日志 ====================
_WHITELIST_CHANGE_LOG = []
_WHITELIST_CHANGE_LOCK = threading.Lock()

# ==================== 环境缓存 ====================
_env_cache = {'data': None, 'time': 0}
_env_cache_lock = threading.Lock()

# ==================== 同步扫描缓存 ====================
_SYNC_SCAN_CACHE = {}

# ==================== 训练队列 ====================
_train_queue = []
_train_queue_lock = threading.Lock()

# ==================== 终端 ====================
_terminals = {}
_terminals_lock = threading.Lock()

# ==================== Notebook 内核 ====================
_nb_kernels = {}
_nb_kernels_lock = threading.Lock()
_nb_kernel_files = {}
_nb_kernel_users = {}
_nb_kernel_busy = {}
