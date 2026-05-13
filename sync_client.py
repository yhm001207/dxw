# -*- coding: utf-8 -*-
"""
CP Group 同步客户端
桌面端文件同步工具，与 CP Group 服务器双向同步
"""
import os
import sys
import json
import time
import sqlite3
import threading
import zipfile
import io
import logging
import gc
import threading as _threading
from pathlib import Path
from datetime import datetime

import requests

# ========== 内存监控 ==========
_proc = None
def _mem_mb():
    global _proc
    try:
        import psutil
        if _proc is None:
            _proc = psutil.Process()
        return _proc.memory_info().rss / 1024 / 1024
    except Exception:
        return 0

def _release_mem():
    """强制把 Python 释放的内存还给操作系统（gc.collect 后调用）"""
    gc.collect()
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetProcessWorkingSetSize(
                ctypes.windll.kernel32.GetCurrentProcess(), -1, -1)
        except Exception:
            pass

# ========== 常量 ==========
CONFIG_PATH = Path.home() / '.dxw_sync_config.json'
DB_PATH = Path.home() / '.dxw_sync_state.db'
LOG_PATH = Path.home() / '.dxw_sync.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_PATH), encoding='utf-8'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger('dxw_sync')

# ========== 配置管理 ==========
DEFAULT_CONFIG = {
    'server_url': 'http://localhost:5000',
    'username': '',
    'password': '',
    'sync_folders': [],  # [{local: 'C:/xxx', remote: 'backup'}, ...]
    'sync_interval': 15,
    'auto_download': True,
}


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            # 迁移旧配置：local_path/remote_path -> sync_folders
            if not cfg.get('sync_folders'):
                lp = cfg.pop('local_path', '') or ''
                rp = cfg.pop('remote_path', '') or ''
                if lp:
                    cfg['sync_folders'] = [{'local': lp, 'remote': rp}]
                else:
                    cfg['sync_folders'] = []
            else:
                cfg.pop('local_path', None)
                cfg.pop('remote_path', None)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ========== 本地状态数据库 ==========
class SyncDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS file_state (
            relative_path TEXT PRIMARY KEY,
            local_mtime REAL DEFAULT 0,
            server_mtime REAL DEFAULT 0,
            local_hash TEXT DEFAULT '',
            last_sync REAL DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            action TEXT,
            path TEXT,
            detail TEXT
        )''')
        self.conn.commit()

    def get_file(self, rel_path):
        c = self.conn.cursor()
        c.execute('SELECT * FROM file_state WHERE relative_path = ?', (rel_path,))
        return c.fetchone()

    def upsert_file(self, rel_path, local_mtime=0, server_mtime=0, local_hash='', status='synced'):
        c = self.conn.cursor()
        c.execute('''INSERT OR REPLACE INTO file_state
            (relative_path, local_mtime, server_mtime, local_hash, last_sync, status)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (rel_path, local_mtime, server_mtime, local_hash, time.time(), status))
        self.conn.commit()

    def remove_file(self, rel_path):
        c = self.conn.cursor()
        c.execute('DELETE FROM file_state WHERE relative_path = ?', (rel_path,))
        self.conn.commit()

    def get_all_files(self):
        c = self.conn.cursor()
        c.execute('SELECT * FROM file_state')
        return {row['relative_path']: dict(row) for row in c.fetchall()}

    def log(self, action, path='', detail=''):
        c = self.conn.cursor()
        c.execute('INSERT INTO sync_log (timestamp, action, path, detail) VALUES (?, ?, ?, ?)',
            (time.time(), action, path, detail))
        self.conn.commit()

    def get_logs(self, limit=50):
        c = self.conn.cursor()
        c.execute('SELECT * FROM sync_log ORDER BY id DESC LIMIT ?', (limit,))
        return [dict(row) for row in c.fetchall()]


# ========== 子进程同步 worker ==========
# 在独立进程中运行，退出时 OS 回收全部内存，彻底解决 pymalloc 内存不释放问题
_SKIP_DIRS = {'.git', '.svn', '__pycache__', 'node_modules', '.idea', '.vscode', '$Recycle.Bin', 'System Volume Information'}
_SKIP_SUFFIXES = {'.tmp', '.swp', '.ds_store', '.lnk'}

def _scan_folder(local_path):
    """扫描文件夹，使用 os.scandir 直接从目录条目获取元数据（避免逐文件 stat 系统调用）"""
    lp = Path(local_path)
    if not lp.exists():
        return {}
    files = {}
    lp_str = str(lp)
    lp_len = len(lp_str) + 1  # 用于截取相对路径

    def _scan_dir(dir_path):
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            name = entry.name
                            if name.startswith('.') or name in _SKIP_DIRS:
                                continue
                            _scan_dir(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            name = entry.name
                            if name.startswith('~$'):
                                continue
                            ext = os.path.splitext(name)[1].lower()
                            if ext in _SKIP_SUFFIXES:
                                continue
                            # entry.stat() 从目录条目直接读取，无需额外系统调用
                            st = entry.stat(follow_symlinks=False)
                            rel = entry.path[lp_len:].replace('\\', '/')
                            files[rel] = (st.st_mtime, st.st_size)
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

    _scan_dir(lp_str)
    return files


def _fetch_server_tree(session, server_url, base_path):
    """拉取服务端目录树，返回 {relative_path: (mtime, size)}"""
    r = None
    try:
        params = {'base_path': base_path} if base_path else {}
        r = session.get(server_url + '/api/sync/server_tree', params=params, timeout=120)
        if r.status_code == 200:
            data = r.json()
            # 服务端返回 {rel_path: [mtime, size]}，转为 tuple
            return {k: (v[0], v[1]) for k, v in data.items()}
        return None
    except Exception:
        return None
    finally:
        if r is not None:
            r.close()


# 分片大小 5MB，文件超过 10MB 走分片上传
_CHUNK_SIZE = 30 * 1024 * 1024
_CHUNK_THRESHOLD = 10 * 1024 * 1024


def _upload_chunked(session, server_url, local_path, rel_path, target_dir, config_dict=None, progress_cb=None):
    """分片上传大文件，内存只占一个 chunk 大小"""
    import uuid
    file_size = local_path.stat().st_size
    total_chunks = (file_size + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    upload_id = uuid.uuid4().hex[:12]
    filename = local_path.name
    log.info('[分片] 开始 %s: %.1f MB, %d 片', filename, file_size / 1024 / 1024, total_chunks)

    try:
        with open(local_path, 'rb') as f:
            for idx in range(total_chunks):
                chunk_data = f.read(_CHUNK_SIZE)
                chunk_buf = io.BytesIO(chunk_data)
                ru = session.post(
                    server_url + '/api/upload_chunk',
                    files={'chunk': (f'{filename}.part', chunk_buf)},
                    data={
                        'upload_id': upload_id,
                        'chunk_index': str(idx),
                        'filename': filename,
                    },
                    timeout=(10, 120),
                )
                chunk_buf.close()
                del chunk_data
                if ru.status_code != 200:
                    log.error('[分片] 切片 %d 失败: HTTP %d %s', idx, ru.status_code, ru.text[:200])
                    ru.close()
                    return False
                ru.close()
                if (idx + 1) % 10 == 0 or idx == total_chunks - 1:
                    pct = (idx + 1) * 100 // total_chunks
                    size_mb = file_size / 1024 / 1024
                    log.info('[分片] 进度 %s: %d/%d (%d%%)', filename, idx + 1, total_chunks, pct)
                    if progress_cb:
                        progress_cb(f'分片上传 {filename}: {idx+1}/{total_chunks} ({pct}%) {size_mb:.0f}MB')

        # 所有切片上传完，通知服务端合并
        log.info('[分片] 合并 %s: %d 片', filename, total_chunks)
        merge_data = {
            'upload_id': upload_id,
            'filename': filename,
            'total_chunks': total_chunks,
            'target_dir': target_dir,
            'relative_path': rel_path,
        }
        for attempt in range(3):
            rc = session.post(
                server_url + '/api/upload_complete',
                json=merge_data,
                timeout=(10, 300),
            )
            if rc.status_code == 401:
                rc.close()
                if not config_dict:
                    log.error('[分片] 合并被拒且无法重新登录')
                    return False
                log.info('[分片] 合并时 session 过期，重新登录...')
                lr = session.post(server_url + '/login',
                    json={'username': config_dict['username'], 'password': config_dict['password']},
                    timeout=10)
                lr.close()
                if lr.status_code == 200:
                    continue
                return False
            if rc.status_code == 200:
                result = rc.json()
                if result.get('error'):
                    log.error('[分片] 合并失败 %s: %s', filename, result['error'])
                    rc.close()
                    return False
                log.info('[分片] 完成 %s: %s', filename, result.get('size_fmt', ''))
                rc.close()
                return True
            log.error('[分片] 合并请求失败 %s: HTTP %d', filename, rc.status_code)
            rc.close()
            return False
        return False
    except Exception as e:
        log.error('[分片] 异常 %s: %s', filename, e)
        return False


def _sync_worker(config_dict, queue, session=None):
    """执行同步：客户端发送本地文件列表到服务端比对，上传/下载差异"""
    own_session = session is None
    try:
        server_url = config_dict['server_url'].rstrip('/')
        username = config_dict['username']
        password = config_dict['password']
        folders = config_dict.get('sync_folders', [])
        auto_download = config_dict.get('auto_download', True)

        if session is None:
            session = requests.Session()

        # Flask session 认证
        queue.put(('status', 'syncing', '正在登录...'))
        r = session.post(server_url + '/login',
            json={'username': username, 'password': password}, timeout=10)
        if r.status_code != 200 or r.json().get('error'):
            r.close()
            queue.put(('status', 'error', '登录失败'))
            return
        r.close()

        global_up, global_down = 0, 0
        for idx, folder in enumerate(folders):
            local_path = folder.get('local', '')
            remote_path = folder.get('remote', '').strip().strip('/').strip('\\')
            if not local_path or not Path(local_path).exists():
                continue
            folder_name = Path(local_path).name

            # 1. 扫描本地文件
            m0 = _mem_mb()
            queue.put(('status', 'syncing', f'[{idx+1}/{len(folders)}] {folder_name}: 扫描本地...'))
            local_files = _scan_folder(local_path)
            file_count = len(local_files)
            m1 = _mem_mb()
            log.info('[内存] 扫描 %s: %d 文件, %.1f MB (delta: %+.1f)', folder_name, file_count, m1, m1 - m0)

            # 2. 拉取服务端目录树
            queue.put(('status', 'syncing', f'[{idx+1}/{len(folders)}] {folder_name}: 拉取服务端目录...'))
            server_files = _fetch_server_tree(session, server_url, remote_path)
            m2 = _mem_mb()
            log.info('[内存] 服务端 %s: %d 文件, %.1f MB (delta: %+.1f)',
                     folder_name, len(server_files) if server_files else 0, m2, m2 - m1)

            if server_files is None:
                queue.put(('status', 'syncing', f'[{idx+1}/{len(folders)}] {folder_name}: 拉取失败，跳过'))
                del local_files
                _release_mem()
                continue

            # 3. 本地比对
            upload_list = []
            download_list = []
            for rp, (local_mtime, local_size) in local_files.items():
                if rp not in server_files:
                    upload_list.append(rp)
                else:
                    server_mtime, server_size = server_files[rp]
                    if local_mtime > server_mtime + 1:
                        upload_list.append(rp)
            if auto_download:
                for rp in server_files:
                    if rp not in local_files:
                        download_list.append(rp)
            log.info('[比对] %s: 本地 %d, 服务端 %d, 待上传 %d, 待下载 %d',
                     folder_name, len(local_files), len(server_files), len(upload_list), len(download_list))
            del server_files, local_files
            _release_mem()
            m3 = _mem_mb()
            log.info('[内存] 比对 %s: %.1f MB (回收: %+.1f)', folder_name, m3, m3 - m2)

            uploaded, downloaded = 0, 0

            # 4. 上传差异文件
            if upload_list:
                for j, rp in enumerate(upload_list):
                    local_p = Path(local_path) / rp
                    if local_p.exists():
                        try:
                            file_size = local_p.stat().st_size
                            if file_size >= _CHUNK_THRESHOLD:
                                # 大文件：分片上传
                                log.info('[上传] 分片上传 %s (%.1f MB)', rp, file_size / 1024 / 1024)
                                def _chunk_progress(msg, _fn=folder_name, _i=idx, _t=len(folders)):
                                    queue.put(('status', 'syncing', f'[{_i+1}/{_t}] {_fn}: {msg}'))
                                ok = _upload_chunked(session, server_url, local_p, rp, remote_path, config_dict, _chunk_progress)
                            else:
                                # 小文件：普通上传
                                read_timeout = max(60, min(file_size // (50 * 1024), 1800))
                                with open(local_p, 'rb') as f:
                                    ru = session.post(
                                        server_url + '/api/upload',
                                        files={'file': (local_p.name, f)},
                                        data={'relative_path': rp, 'target_dir': remote_path},
                                        timeout=(10, read_timeout))
                                ok = ru.status_code == 200 and not ru.json().get('error')
                                ru.close()
                            if ok:
                                uploaded += 1
                                log.info('[上传] 成功: %s', rp)
                            else:
                                log.warning('[上传] 失败: %s', rp)
                        except Exception as e:
                            log.error('[上传] 异常 %s: %s', rp, e)
                    queue.put(('status', 'syncing', f'[{idx+1}/{len(folders)}] {folder_name}: 上传 {j+1}/{len(upload_list)} ({uploaded}成功)'))

            # 5. 下载差异文件
            if download_list:
                for j, rp in enumerate(download_list):
                    try:
                        rd = session.post(server_url + '/api/sync/download_batch',
                            json={'paths': [rp], 'base_path': remote_path},
                            timeout=(10, 300), stream=True)
                        if rd.status_code == 200:
                            import tempfile
                            with tempfile.SpooledTemporaryFile(max_size=10*1024*1024) as tmp:
                                for chunk in rd.iter_content(65536):
                                    tmp.write(chunk)
                                tmp.seek(0)
                                with zipfile.ZipFile(tmp) as zf:
                                    for info in zf.infolist():
                                        if info.is_dir():
                                            continue
                                        local_target = Path(local_path) / info.filename
                                        local_target.parent.mkdir(parents=True, exist_ok=True)
                                        with zf.open(info) as src, open(local_target, 'wb') as dst:
                                            while True:
                                                chunk = src.read(65536)
                                                if not chunk:
                                                    break
                                                dst.write(chunk)
                                        downloaded += 1
                        rd.close()
                    except Exception:
                        pass
                    queue.put(('status', 'syncing', f'[{idx+1}/{len(folders)}] {folder_name}: 下载 {j+1}/{len(download_list)} ({downloaded}成功)'))

            if not upload_list and not download_list:
                queue.put(('status', 'syncing', f'[{idx+1}/{len(folders)}] {folder_name}: 已是最新'))
            else:
                log.info('[同步] %s: 需上传 %d, 需下载 %d', folder_name, len(upload_list), len(download_list))

            global_up += uploaded
            global_down += downloaded
            del upload_list, download_list
            _release_mem()
            m4 = _mem_mb()
            log.info('[内存] 完成 %s: %.1f MB (本轮总delta: %+.1f)', folder_name, m4, m4 - m0)

        summary = []
        if global_up:
            summary.append(f'上传{global_up}')
        if global_down:
            summary.append(f'下载{global_down}')
        msg = '同步完成' if summary else '已是最新'
        if summary:
            msg += ' (' + ', '.join(summary) + ')'
        queue.put(('status', 'synced', msg))
        queue.put(('done', global_up, global_down))

    except Exception as e:
        import traceback
        queue.put(('status', 'error', f'同步出错: {e}'))
        queue.put(('error', str(e), traceback.format_exc()))
    finally:
        if own_session:
            try:
                session.close()
            except Exception:
                pass
        _release_mem()


# ========== 同步引擎 ==========
class SyncEngine:
    def __init__(self, config, db, on_status=None):
        self.config = config
        self.db = db
        self.on_status = on_status or (lambda s, msg: None)
        self.session = requests.Session()
        self.running = False
        self.paused = False
        self._need_sync = False
        self._lock = threading.Lock()

    def login(self):
        r = None
        try:
            url = self.config['server_url'].rstrip('/') + '/login'
            r = self.session.post(url, json={
                'username': self.config['username'],
                'password': self.config['password'],
            }, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get('ok'):
                    log.info('登录成功: %s', self.config['username'])
                    return True
            log.error('登录失败: HTTP %d', r.status_code)
            return False
        except Exception as e:
            log.error('登录异常: %s', e)
            return False
        finally:
            if r is not None:
                r.close()

    def _api_url(self, path):
        return self.config['server_url'].rstrip('/') + path

    # 跳过的目录和文件后缀
    _SKIP_DIRS = {'.git', '.svn', '__pycache__', 'node_modules', '.idea', '.vscode', '$Recycle.Bin', 'System Volume Information'}
    _SKIP_SUFFIXES = {'.tmp', '.swp', '.ds_store', '.lnk'}

    def scan_local_folder(self, local_path):
        lp = Path(local_path)
        if not lp.exists():
            return {}
        files = {}
        lp_str = str(lp)
        for dirpath, dirnames, filenames in os.walk(lp_str):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith('.') and d not in self._SKIP_DIRS]
            for fname in filenames:
                if fname.startswith('~$'):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in self._SKIP_SUFFIXES:
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    st = os.stat(full)
                    rel = os.path.relpath(full, lp_str).replace('\\', '/')
                    files[rel] = (st.st_mtime, st.st_size)  # 不重复存 rel，key 已经有了
                except (OSError, PermissionError):
                    pass
        return files

    def scan_local(self):
        all_files = {}
        for folder in self.config.get('sync_folders', []):
            local_path = folder.get('local', '')
            remote_path = folder.get('remote', '').strip().strip('/').strip('\\')
            files = self.scan_local_folder(local_path)
            # 用 remote_path 作为前缀区分不同同步文件夹
            for rel, info in files.items():
                key = f"{remote_path}/{rel}" if remote_path else rel
                all_files[key] = info
            all_files.setdefault('_folders', {})[remote_path] = folder
        return all_files

    def get_changes_for_folder(self, folder, local_files_dict, _retried=False):
        r = None
        try:
            remote_path = folder.get('remote', '').strip().strip('/').strip('\\')
            import json as _json
            # 流式构建 JSON，避免创建中间 list
            buf = io.StringIO()
            buf.write('{"base_path":')
            _json.dump(remote_path, buf)
            buf.write(',"files":[')
            first = True
            for rp, v in local_files_dict.items():
                if first:
                    first = False
                else:
                    buf.write(',')
                buf.write('{"relative_path":')
                _json.dump(rp, buf)
                buf.write(',"mtime":')
                buf.write(str(v[0]))
                buf.write(',"size":')
                buf.write(str(v[1]))
                buf.write('}')
            buf.write(']}')
            payload = buf.getvalue()
            buf.close()
            r = self.session.post(
                self._api_url('/api/sync/changes'),
                data=payload.encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                timeout=120,
            )
            del payload
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401 and not _retried:
                log.info('Session 过期，重新登录...')
                if self.login():
                    return self.get_changes_for_folder(folder, local_files_dict, _retried=True)
            log.error('获取差异失败 [%s]: HTTP %d', remote_path, r.status_code)
            return None
        except Exception as e:
            log.error('获取差异异常 [%s]: %s', remote_path, e)
            return None
        finally:
            if r is not None:
                r.close()

    def upload_file_to_folder(self, folder, rel_path, _retries=0):
        local_path = Path(folder['local']) / rel_path
        if not local_path.exists():
            return False
        max_retries = 3
        r = None
        try:
            remote_path = folder.get('remote', '').strip().strip('/').strip('\\')
            file_size = local_path.stat().st_size
            read_timeout = max(60, file_size // (50 * 1024))
            read_timeout = min(read_timeout, 1800)
            with open(local_path, 'rb') as f:
                files = {'file': (local_path.name, f)}
                data = {
                    'relative_path': rel_path,
                    'target_dir': remote_path,
                }
                r = self.session.post(
                    self._api_url('/api/upload'),
                    files=files,
                    data=data,
                    timeout=(10, read_timeout),
                )
            if r.status_code == 200:
                result = r.json()
                if result.get('error'):
                    log.error('上传失败 %s: %s', rel_path, result['error'])
                    return False
                log.info('上传成功: %s', rel_path)
                return True
            if r.status_code == 401 and _retries < max_retries:
                log.info('上传时 Session 过期，重新登录...')
                if self.login():
                    return self.upload_file_to_folder(folder, rel_path, _retries + 1)
            log.error('上传失败 %s: HTTP %d', rel_path, r.status_code)
            return False
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if _retries < max_retries:
                log.warning('上传超时/断连 %s，重试 (%d/%d): %s', rel_path, _retries + 1, max_retries, e)
                time.sleep(2)
                return self.upload_file_to_folder(folder, rel_path, _retries + 1)
            log.error('上传失败（重试耗尽）%s: %s', rel_path, e)
            return False
        except Exception as e:
            log.error('上传异常 %s: %s', rel_path, e)
            return False
        finally:
            if r is not None:
                r.close()

    def download_files_to_folder(self, folder, paths, _retries=0):
        if not paths:
            return 0
        local_root = Path(folder['local'])
        remote_path = folder.get('remote', '').strip().strip('/').strip('\\')
        max_retries = 3
        r = None
        try:
            r = self.session.post(self._api_url('/api/sync/download_batch'), json={
                'paths': paths,
                'base_path': remote_path,
            }, timeout=(10, 300), stream=True)
            if r.status_code == 401 and _retries < max_retries:
                log.info('下载时 Session 过期，重新登录...')
                if self.login():
                    return self.download_files_to_folder(folder, paths, _retries + 1)
            if r.status_code != 200:
                log.error('下载失败 [%s]: HTTP %d', remote_path, r.status_code)
                return 0
            import tempfile
            count = 0
            with tempfile.SpooledTemporaryFile(max_size=10*1024*1024) as tmp:
                for chunk in r.iter_content(65536):
                    tmp.write(chunk)
                tmp.seek(0)
                with zipfile.ZipFile(tmp) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        target = (local_root / info.filename).resolve()
                        if not str(target).startswith(str(local_root.resolve())):
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(target, 'wb') as dst:
                            while True:
                                chunk = src.read(65536)
                                if not chunk:
                                    break
                                dst.write(chunk)
                        log.info('下载成功: %s', info.filename)
                        count += 1
            return count
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if _retries < max_retries:
                log.warning('下载超时/断连 [%s]，重试 (%d/%d): %s', remote_path, _retries + 1, max_retries, e)
                time.sleep(2)
                return self.download_files_to_folder(folder, paths, _retries + 1)
            log.error('下载失败（重试耗尽）[%s]: %s', remote_path, e)
            return 0
        except Exception as e:
            log.error('下载异常 [%s]: %s', remote_path, e)
            return 0
        finally:
            if r is not None:
                r.close()

    def sync_cycle(self):
        with self._lock:
            if self.paused:
                return
            if self.running:
                self._need_sync = True
                return
            self.running = True
            self._need_sync = False

        mem_before = _mem_mb()
        try:
            log.info('[内存] 同步开始: %.1f MB', mem_before)
            folders = self.config.get('sync_folders', [])
            if not folders:
                self.on_status('synced', '未配置同步文件夹')
                return

            # 在线程中直接执行同步（避免 Windows multiprocessing.spawn 的巨大开销）
            import queue as _q
            msg_queue = _q.Queue()

            def _worker_wrapper():
                try:
                    _sync_worker(self.config, msg_queue, session=self.session)
                except Exception as e:
                    import traceback
                    msg_queue.put(('status', 'error', f'同步出错: {e}'))
                    msg_queue.put(('error', str(e), traceback.format_exc()))

            worker_thread = _threading.Thread(target=_worker_wrapper, daemon=True)
            worker_thread.start()

            # 从队列读取状态更新，转发给 UI
            while True:
                try:
                    msg = msg_queue.get(timeout=600)
                except _q.Empty:
                    break
                if msg[0] == 'status':
                    self.on_status(msg[1], msg[2])
                elif msg[0] == 'done':
                    break
                elif msg[0] == 'error':
                    log.error('同步错误: %s\n%s', msg[1], msg[2])
                    break

            worker_thread.join(timeout=30)

        except Exception as e:
            import traceback
            log.error('同步异常: %s\n%s', e, traceback.format_exc())
            self.on_status('error', f'同步出错: {e}')
        finally:
            self.running = False
            # 强制垃圾回收 + 把内存还给 OS
            _release_mem()
            mem_after = _mem_mb()
            delta = mem_after - mem_before
            log.info('[内存] 同步结束: %.1f MB (变化: %+.1f MB)', mem_after, delta)
            if self._need_sync:
                self._need_sync = False
                t = _threading.Timer(2, self.sync_cycle)
                t.daemon = True
                t.start()

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def start_sync_loop(self, interval):
        def loop():
            while True:
                time.sleep(interval)
                self.sync_cycle()
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    # ========== 云盘浏览 API ==========
    def get_my_dir(self):
        r = None
        try:
            r = self.session.get(self._api_url('/api/my_dir'), timeout=10)
            if r.status_code == 200:
                return r.json().get('path', '')
            return ''
        except Exception:
            return ''
        finally:
            if r is not None:
                r.close()

    def list_cloud_dir(self, path=''):
        r = None
        try:
            r = self.session.get(
                self._api_url('/api/files'),
                params={'path': path} if path else {},
                timeout=15)
            if r.status_code == 200:
                data = r.json()
                items = []
                for e in data.get('items', []) if isinstance(data, dict) else data:
                    items.append({
                        'name': e.get('name', ''),
                        'type': e.get('type', 'file'),
                        'size': e.get('size', 0),
                        'mtime': e.get('mtime', ''),
                        'path': e.get('path', ''),
                    })
                return items
            return []
        except Exception:
            return []
        finally:
            if r is not None:
                r.close()

    def cloud_download_file(self, remote_path, save_path):
        r = None
        try:
            r = self.session.get(
                self._api_url('/api/download_file'),
                params={'path': remote_path},
                stream=True, timeout=300)
            if r.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                return True
            return False
        except Exception:
            return False
        finally:
            if r is not None:
                r.close()

    def cloud_upload_file(self, local_path, remote_dir=''):
        r = None
        try:
            p = Path(local_path)
            with open(local_path, 'rb') as f:
                files = {'file': (p.name, f)}
                data = {'target_dir': remote_dir} if remote_dir else {}
                r = self.session.post(
                    self._api_url('/api/upload'),
                    files=files, data=data, timeout=300)
            return r.status_code == 200
        except Exception:
            return False
        finally:
            if r is not None:
                r.close()


# ========== 文件监控 ==========
class FileWatcher:
    def __init__(self, local_paths, on_change):
        self.local_paths = local_paths  # list of paths
        self.on_change = on_change
        self._observers = []
        self._debounce_timer = None

    def start(self):
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            watcher_ref = self

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    if event.is_directory:
                        return
                    src = event.src_path
                    if any(x in src for x in ('~$', '.tmp', '.swp', '.ds_store', 'Thumbs.db')):
                        return
                    watcher_ref._debounce()

            handler = Handler()
            for path in self.local_paths:
                if Path(path).exists():
                    obs = Observer()
                    obs.schedule(handler, path, recursive=True)
                    obs.start()
                    self._observers.append(obs)
                    log.info('文件监控已启动: %s', path)
        except ImportError:
            log.warning('未安装 watchdog，文件监控不可用')
        except Exception as e:
            log.error('文件监控启动失败: %s', e)

    def stop(self):
        for obs in self._observers:
            obs.stop()
        for obs in self._observers:
            obs.join(timeout=3)
        self._observers.clear()

    def _debounce(self):
        if self._debounce_timer:
            self._debounce_timer.cancel()
        self._debounce_timer = threading.Timer(1.0, self.on_change)
        self._debounce_timer.start()


# ========== 主窗口 UI ==========
class MainWindow:
    def __init__(self, engine, watcher):
        self.engine = engine
        self.watcher = watcher
        self.root = None
        self.cloud_tree = None
        self.file_list = None
        self.status_var = None
        self.current_cloud_path = ''
        self._icon = None

    def run(self):
        import tkinter as tk
        from tkinter import ttk, messagebox, filedialog

        self.root = tk.Tk()
        self.root.title('CP Group 同步客户端')
        self.root.geometry('900x600')
        self.root.minsize(700, 450)

        # 配色
        BG = '#1e1e2e'
        FG = '#cdd6f4'
        ACCENT = '#89b4fa'
        SURFACE = '#313244'
        GREEN = '#a6e3a1'
        YELLOW = '#f9e2af'
        RED = '#f38ba8'

        self.root.configure(bg=BG)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background=BG, foreground=FG, font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=FG)
        style.configure('TButton', background=SURFACE, foreground=FG, borderwidth=0, padding=(12, 6))
        style.map('TButton', background=[('active', ACCENT)])
        style.configure('Treeview', background=SURFACE, foreground=FG, fieldbackground=SURFACE,
                        borderwidth=0, rowheight=26, font=('Microsoft YaHei UI', 10))
        style.configure('Treeview.Heading', background=BG, foreground=FG, font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Treeview', background=[('selected', ACCENT)], foreground=[('selected', BG)])
        style.configure('Status.TLabel', background=SURFACE, foreground=GREEN, font=('Microsoft YaHei UI', 9))

        # ===== 顶部工具栏 =====
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill='x', padx=10, pady=(10, 5))

        ttk.Label(toolbar, text='CP Group Cloud', font=('Microsoft YaHei UI', 14, 'bold'),
                  foreground=ACCENT).pack(side='left', padx=(0, 15))

        ttk.Button(toolbar, text='立即同步', command=self._on_sync_now).pack(side='left', padx=3)
        ttk.Button(toolbar, text='刷新云盘', command=self._on_refresh_cloud).pack(side='left', padx=3)
        ttk.Button(toolbar, text='上传', command=self._on_upload_file).pack(side='left', padx=3)
        ttk.Button(toolbar, text='下载', command=self._on_download_selected).pack(side='left', padx=3)
        ttk.Button(toolbar, text='新建文件夹', command=self._on_new_folder).pack(side='left', padx=3)

        ttk.Button(toolbar, text='设置', command=self._on_settings).pack(side='right', padx=3)
        ttk.Button(toolbar, text='日志', command=self._on_view_log).pack(side='right', padx=3)

        # ===== 主体区域：左右分栏 =====
        body = ttk.Frame(self.root)
        body.pack(fill='both', expand=True, padx=10, pady=5)

        # 左侧：云盘目录树
        left = ttk.Frame(body, width=250)
        left.pack(side='left', fill='y', padx=(0, 5))
        left.pack_propagate(False)

        ttk.Label(left, text='[ 云盘 ]', font=('Microsoft YaHei UI', 10, 'bold'),
                  foreground=ACCENT).pack(anchor='w', pady=(0, 5))

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill='both', expand=True)

        self.cloud_tree = ttk.Treeview(tree_frame, show='tree', selectmode='browse')
        tree_scroll = ttk.Scrollbar(tree_frame, orient='vertical', command=self.cloud_tree.yview)
        self.cloud_tree.configure(yscrollcommand=tree_scroll.set)
        self.cloud_tree.pack(side='left', fill='both', expand=True)
        tree_scroll.pack(side='right', fill='y')

        self.cloud_tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self.cloud_tree.bind('<<TreeviewOpen>>', self._on_tree_expand)

        # 右侧：文件列表
        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True)

        # 文件列表表头
        cols = ('name', 'size', 'mtime', 'type')
        self.file_list = ttk.Treeview(right, columns=cols, show='headings', selectmode='extended')
        self.file_list.heading('name', text='文件名')
        self.file_list.heading('size', text='大小')
        self.file_list.heading('mtime', text='修改时间')
        self.file_list.heading('type', text='类型')
        self.file_list.column('name', width=300, minwidth=200)
        self.file_list.column('size', width=100, minwidth=80, anchor='e')
        self.file_list.column('mtime', width=150, minwidth=120)
        self.file_list.column('type', width=80, minwidth=60)

        file_scroll = ttk.Scrollbar(right, orient='vertical', command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=file_scroll.set)
        self.file_list.pack(side='left', fill='both', expand=True)
        file_scroll.pack(side='right', fill='y')

        self.file_list.bind('<Double-1>', self._on_file_double_click)
        self.file_list.bind('<Button-3>', self._on_file_right_click)

        # ===== 底部状态栏 =====
        self.status_bar = tk.Frame(self.root, bg=SURFACE, height=36)
        self.status_bar.pack(fill='x', padx=10, pady=(5, 10))
        self.status_bar.pack_propagate(False)

        self.status_dot = tk.Label(self.status_bar, text='  ', bg='#a6e3a1', fg='#a6e3a1')
        self.status_dot.pack(side='left', padx=(8, 5), pady=6)

        self.status_var = tk.StringVar(value='就绪')
        tk.Label(self.status_bar, textvariable=self.status_var, bg=SURFACE, fg=GREEN,
                 font=('Microsoft YaHei UI', 10)).pack(side='left', padx=5)

        dl_state = '开启' if self.engine.config.get('auto_download', True) else '关闭'
        tk.Label(self.status_bar, text=f'自动下载: {dl_state}  |  用户: {self.engine.config["username"]}',
                 bg=SURFACE, fg='#6c7086', font=('Microsoft YaHei UI', 9)).pack(side='right', padx=10)

        # 窗口关闭 → 最小化到托盘
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        # 延迟加载云盘目录和托盘，避免阻塞窗口显示
        self.root.after(100, self._load_cloud_tree)
        self.root.after(200, self._start_tray)

        self.root.mainloop()

    def _format_size(self, size):
        if size < 1024:
            return f'{size} B'
        elif size < 1024 * 1024:
            return f'{size / 1024:.1f} KB'
        elif size < 1024 * 1024 * 1024:
            return f'{size / (1024 * 1024):.1f} MB'
        else:
            return f'{size / (1024 * 1024 * 1024):.2f} GB'

    def _load_cloud_tree(self):
        self.cloud_tree.delete(*self.cloud_tree.get_children())
        my_dir = self.engine.get_my_dir()
        self._my_dir = my_dir
        root_id = self.cloud_tree.insert('', 'end', text='我的云盘', values=(my_dir,))
        items = self.engine.list_cloud_dir(my_dir)
        dirs = [item for item in items if item.get('type') == 'directory' and not item['name'].startswith('.')]
        for item in dirs:
            nid = self.cloud_tree.insert(root_id, 'end', text=f"[D] {item['name']}", values=(item.get('path', item['name']),))
            self.cloud_tree.insert(nid, 'end', text='加载中...')
        if dirs:
            self.cloud_tree.item(root_id, open=True)

    def _on_tree_expand(self, event):
        """点击展开时才加载子目录"""
        node = self.cloud_tree.focus()
        if not node:
            return
        children = self.cloud_tree.get_children(node)
        if not children:
            return
        if self.cloud_tree.item(children[0], 'text') == '加载中...':
            self.cloud_tree.delete(children[0])
            path = self.cloud_tree.item(node, 'values')[0]
            items = self.engine.list_cloud_dir(path)
            dirs = [item for item in items if item.get('type') == 'directory' and not item['name'].startswith('.')]
            for item in dirs:
                full_path = item.get('path', f"{path}/{item['name']}" if path else item['name'])
                nid = self.cloud_tree.insert(node, 'end', text=f"[D] {item['name']}", values=(full_path,))
                self.cloud_tree.insert(nid, 'end', text='加载中...')

    def _on_tree_select(self, event):
        sel = self.cloud_tree.selection()
        if not sel:
            return
        path = self.cloud_tree.item(sel[0], 'values')[0]
        self.current_cloud_path = path
        self._load_file_list(path)

    def _load_file_list(self, path):
        self.file_list.delete(*self.file_list.get_children())

        my_dir = getattr(self, '_my_dir', '')
        if path and path != my_dir:
            self.file_list.insert('', 'end', values=('[..] 返回上级', '', '', ''))

        items = self.engine.list_cloud_dir(path)

        folders = [i for i in items if i.get('type') == 'directory' and not i['name'].startswith('.')]
        files = [i for i in items if i.get('type') != 'directory']

        for item in sorted(folders, key=lambda x: x['name']):
            self.file_list.insert('', 'end', values=(
                f"[D] {item['name']}", '-', item.get('mtime', ''), '文件夹'))

        for item in sorted(files, key=lambda x: x['name']):
            size_str = self._format_size(item.get('size', 0))
            mtime_str = item.get('mtime', '')
            if isinstance(mtime_str, (int, float)) and mtime_str:
                try:
                    mtime_str = datetime.fromtimestamp(float(mtime_str)).strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    pass
            ext = Path(item['name']).suffix.lower()
            type_map = {'.py': 'Python', '.js': 'JS', '.html': 'HTML', '.css': 'CSS',
                        '.json': 'JSON', '.txt': '文本', '.md': 'Markdown',
                        '.png': '图片', '.jpg': '图片', '.jpeg': '图片', '.gif': '图片', '.bmp': '图片',
                        '.mp4': '视频', '.avi': '视频', '.mov': '视频',
                        '.pdf': 'PDF', '.doc': 'Word', '.docx': 'Word',
                        '.xls': 'Excel', '.xlsx': 'Excel', '.pptx': 'PPT',
                        '.zip': '压缩包', '.rar': '压缩包', '.7z': '压缩包',
                        '.mat': 'MATLAB', '.ipynb': 'Notebook'}
            type_name = type_map.get(ext, ext.upper().strip('.') if ext else '文件')

            self.file_list.insert('', 'end', values=(
                item['name'], size_str, mtime_str, type_name))

    def _on_file_double_click(self, event):
        sel = self.file_list.selection()
        if not sel:
            return
        name = self.file_list.item(sel[0], 'values')[0]
        if name.startswith('[..]'):
            my_dir = getattr(self, '_my_dir', '')
            parent = str(Path(self.current_cloud_path).parent)
            if parent == self.current_cloud_path or (my_dir and not parent.startswith(my_dir)):
                parent = my_dir
            self.current_cloud_path = parent
            for node in self.cloud_tree.get_children():
                self._select_tree_node(node, parent)
            self._load_file_list(parent)
        elif name.startswith('[D]'):
            folder_name = name[4:].strip()
            new_path = str(Path(self.current_cloud_path) / folder_name) if self.current_cloud_path else folder_name
            self.current_cloud_path = new_path
            for node in self.cloud_tree.get_children():
                self._select_tree_node(node, new_path)
            self._load_file_list(new_path)

    def _select_tree_node(self, node, path):
        if self.cloud_tree.item(node, 'values')[0] == path:
            self.cloud_tree.selection_set(node)
            self.cloud_tree.see(node)
            return True
        for child in self.cloud_tree.get_children(node):
            if self._select_tree_node(child, path):
                return True
        return False

    def _on_file_right_click(self, event):
        import tkinter as tk
        from tkinter import messagebox

        sel = self.file_list.selection()
        if not sel:
            return

        name = self.file_list.item(sel[0], 'values')[0]
        if name.startswith('[D]') or name.startswith('[..]'):
            return

        menu = tk.Menu(self.root, tearoff=0, bg='#313244', fg='#cdd6f4',
                       activebackground='#89b4fa', activeforeground='#1e1e2e')
        menu.add_command(label='⬇ 下载到本地', command=lambda: self._download_single(name))
        menu.tk_popup(event.x_root, event.y_root)

    def _download_single(self, filename):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        save_path = filedialog.asksaveasfilename(
            title='保存文件',
            initialfile=filename,
            initialdir=str(Path.home()),
        )
        if not save_path:
            return

        full_remote = str(Path(self.current_cloud_path) / filename) if self.current_cloud_path else filename
        threading.Thread(target=self._do_download, args=(full_remote, save_path, filename), daemon=True).start()

    def _do_download(self, remote_path, save_path, display_name):
        from tkinter import messagebox
        self.root.after(0, lambda: self.status_var.set(f'下载中: {display_name}'))
        ok = self.engine.cloud_download_file(remote_path, save_path)
        if ok:
            self.root.after(0, lambda: messagebox.showinfo('下载完成', f'已保存到:\n{save_path}'))
            self.root.after(0, lambda: self.status_var.set('下载完成'))
        else:
            self.root.after(0, lambda: messagebox.showerror('下载失败', f'无法下载 {display_name}'))
            self.root.after(0, lambda: self.status_var.set('下载失败'))

    def _on_download_selected(self):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        sel = self.file_list.selection()
        if not sel:
            messagebox.showinfo('提示', '请先选择要下载的文件')
            return

        folder = filedialog.askdirectory(title='选择下载目录')
        if not folder:
            return

        files_to_download = []
        for s in sel:
            name = self.file_list.item(s, 'values')[0]
            if not name.startswith('[D]'):
                files_to_download.append(name)

        if not files_to_download:
            return

        threading.Thread(target=self._do_batch_download, args=(files_to_download, folder), daemon=True).start()

    def _do_batch_download(self, filenames, folder):
        self.root.after(0, lambda: self.status_var.set(f'下载 {len(filenames)} 个文件...'))
        success = 0
        for fn in filenames:
            full_remote = str(Path(self.current_cloud_path) / fn) if self.current_cloud_path else fn
            save_path = str(Path(folder) / fn)
            if self.engine.cloud_download_file(full_remote, save_path):
                success += 1
        msg = f'下载完成 {success}/{len(filenames)}'
        self.root.after(0, lambda: self.status_var.set(msg))

    def _on_upload_file(self):
        import tkinter as tk
        from tkinter import filedialog, messagebox

        files = filedialog.askopenfilenames(title='选择要上传的文件')
        if not files:
            return

        threading.Thread(target=self._do_batch_upload, args=(files,), daemon=True).start()

    def _do_batch_upload(self, filepaths):
        self.root.after(0, lambda: self.status_var.set(f'上传 {len(filepaths)} 个文件...'))
        success = 0
        for fp in filepaths:
            if self.engine.cloud_upload_file(fp, self.current_cloud_path):
                success += 1
        msg = f'上传完成 {success}/{len(filepaths)}'
        self.root.after(0, lambda: self.status_var.set(msg))
        self.root.after(1000, lambda: self._load_file_list(self.current_cloud_path))

    def _on_new_folder(self):
        import tkinter as tk
        from tkinter import simpledialog, messagebox

        name = simpledialog.askstring('新建文件夹', '输入文件夹名称:', parent=self.root)
        if not name:
            return
        try:
            r = self.engine.session.post(
                self.engine._api_url('/api/create_folder'),
                json={'path': self.current_cloud_path, 'name': name},
                timeout=10,
            )
            if r.status_code == 200 and not r.json().get('error'):
                self._load_file_list(self.current_cloud_path)
            else:
                messagebox.showerror('失败', r.json().get('error', '创建失败'))
            r.close()
        except Exception as e:
            messagebox.showerror('错误', str(e))

    def _on_refresh_cloud(self):
        self._load_cloud_tree()
        if self.current_cloud_path:
            self._load_file_list(self.current_cloud_path)

    def _on_sync_now(self):
        self.status_var.set('正在同步...')
        self.status_dot.configure(bg='#f9e2af', fg='#f9e2af')
        threading.Thread(target=self.engine.sync_cycle, daemon=True).start()

    def _on_settings(self):
        self._show_settings()

    def _show_settings(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox

        win = tk.Toplevel(self.root)
        win.title('同步设置')
        win.geometry('600x550')
        win.configure(bg='#1e1e2e')
        win.transient(self.root)
        win.grab_set()

        cfg = self.engine.config

        # === 基本设置 ===
        basic_frame = ttk.LabelFrame(win, text='基本设置')
        basic_frame.pack(fill='x', padx=15, pady=(10, 5))

        basic_fields = [
            ('server_url', '服务器地址'),
            ('username', '用户名'),
            ('password', '密码'),
            ('sync_interval', '同步间隔（秒）'),
        ]
        entries = {}
        for i, (key, label) in enumerate(basic_fields):
            ttk.Label(basic_frame, text=label + ':').grid(row=i, column=0, padx=10, pady=3, sticky='w')
            e = ttk.Entry(basic_frame, width=35, show='*' if key == 'password' else None)
            e.insert(0, str(cfg.get(key, '')))
            e.grid(row=i, column=1, padx=10, pady=3, sticky='w')
            entries[key] = e

        # 自动下载开关
        auto_dl_var = tk.BooleanVar(value=cfg.get('auto_download', True))
        ttk.Checkbutton(basic_frame, text='开启自动下载（云端变更自动同步到本地）',
                        variable=auto_dl_var).grid(row=len(basic_fields), column=0, columnspan=2, padx=10, pady=4, sticky='w')

        # === 同步文件夹列表 ===
        folders_frame = ttk.LabelFrame(win, text='同步文件夹（本地 <-> 云端）')
        folders_frame.pack(fill='both', expand=True, padx=15, pady=(0, 5))

        cols = ('local', 'remote')
        folder_tree = ttk.Treeview(folders_frame, columns=cols, show='headings', height=5, selectmode='browse')
        folder_tree.heading('local', text='本地路径')
        folder_tree.heading('remote', text='云端路径')
        folder_tree.column('local', width=280)
        folder_tree.column('remote', width=180)
        folder_tree.pack(fill='both', expand=True, padx=5, pady=5)

        # 加载现有文件夹
        folder_list = list(cfg.get('sync_folders', []))
        for f in folder_list:
            folder_tree.insert('', 'end', values=(f.get('local', ''), f.get('remote', '')))

        btn_frame = ttk.Frame(folders_frame)
        btn_frame.pack(fill='x', padx=5, pady=(0, 5))

        def add_folder():
            p = filedialog.askdirectory(title='选择本地同步文件夹')
            if not p:
                return
            remote_name = os.path.basename(p)
            folder_list.append({'local': p, 'remote': remote_name})
            folder_tree.insert('', 'end', values=(p, remote_name))

        def remove_folder():
            sel = folder_tree.selection()
            if not sel:
                return
            vals = folder_tree.item(sel[0], 'values')
            for f in folder_list:
                if f.get('local') == vals[0]:
                    folder_list.remove(f)
                    break
            folder_tree.delete(sel[0])

        def edit_remote():
            sel = folder_tree.selection()
            if not sel:
                return
            vals = folder_tree.item(sel[0], 'values')
            from tkinter import simpledialog
            new_remote = simpledialog.askstring('编辑云端路径', '云端路径名:', initialvalue=vals[1], parent=win)
            if new_remote is not None:
                folder_tree.item(sel[0], values=(vals[0], new_remote.strip()))
                for f in folder_list:
                    if f.get('local') == vals[0]:
                        f['remote'] = new_remote.strip()

        ttk.Button(btn_frame, text='添加', command=add_folder).pack(side='left', padx=3)
        ttk.Button(btn_frame, text='删除', command=remove_folder).pack(side='left', padx=3)
        ttk.Button(btn_frame, text='编辑云端路径', command=edit_remote).pack(side='left', padx=3)

        # === 保存 ===
        def on_save():
            cfg['server_url'] = entries['server_url'].get().strip()
            cfg['username'] = entries['username'].get().strip()
            cfg['password'] = entries['password'].get().strip()
            try:
                cfg['sync_interval'] = max(10, int(entries['sync_interval'].get().strip()))
            except ValueError:
                cfg['sync_interval'] = 30
            cfg['auto_download'] = auto_dl_var.get()
            cfg['sync_folders'] = folder_list
            save_config(cfg)
            self.engine.config = cfg
            # 立即重新登录
            if self.engine.login():
                # 登录成功后立即触发一次同步
                self.status_var.set('设置已保存，正在同步...')
                self.status_dot.configure(bg='#f9e2af', fg='#f9e2af')
                threading.Thread(target=self.engine.sync_cycle, daemon=True).start()
                messagebox.showinfo('保存成功', '设置已保存，登录成功，已开始同步。')
            else:
                messagebox.showwarning('保存成功', '设置已保存，但登录失败，请检查服务器地址和账号密码。')
            win.destroy()

        def on_test():
            test_url = entries['server_url'].get().strip()
            test_user = entries['username'].get().strip()
            test_pass = entries['password'].get().strip()
            if not test_url or not test_user:
                messagebox.showwarning('提示', '请填写服务器地址和用户名')
                return
            # 临时更新引擎配置进行测试
            old_cfg = dict(self.engine.config)
            self.engine.config['server_url'] = test_url
            self.engine.config['username'] = test_user
            self.engine.config['password'] = test_pass
            if self.engine.login():
                messagebox.showinfo('连接成功', f'登录成功: {test_user}')
            else:
                messagebox.showerror('连接失败', f'无法登录，请检查地址和账号密码')
            # 恢复原配置（未保存前不改变）
            self.engine.config = old_cfg

        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text='测试连接', command=on_test).pack(side='left', padx=5)
        ttk.Button(btn_frame, text='保存设置', command=on_save).pack(side='left', padx=5)

    def _on_view_log(self):
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(self.root)
        win.title('同步日志')
        win.geometry('650x400')
        win.configure(bg='#1e1e2e')

        text = tk.Text(win, wrap='word', font=('Consolas', 10),
                       bg='#313244', fg='#cdd6f4', insertbackground='#cdd6f4')
        scrollbar = ttk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)

        logs = self.engine.db.get_logs(100)
        for log_entry in logs:
            ts = datetime.fromtimestamp(log_entry['timestamp']).strftime('%m-%d %H:%M:%S')
            text.insert('end', f"[{ts}] {log_entry['action']:10s} {log_entry['path']}  {log_entry['detail']}\n")
        text.configure(state='disabled')

        text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

    def _on_close(self):
        self.root.withdraw()

    # ========== 系统托盘 ==========
    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw

            def create_icon(color):
                img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.ellipse([8, 8, 56, 56], fill=color)
                draw.text((20, 18), 'CP Group', fill='white')
                return img

            def on_show(icon, item):
                self.root.after(0, self.root.deiconify)

            def on_sync(icon, item):
                threading.Thread(target=self.engine.sync_cycle, daemon=True).start()

            def on_pause(icon, item):
                self.engine.paused = not self.engine.paused
                status = 'paused' if self.engine.paused else 'synced'
                msg = '已暂停' if self.engine.paused else '已恢复'
                self.root.after(0, lambda: self.status_var.set(msg))
                icon.icon = create_icon({'synced': (34, 197, 94), 'paused': (156, 163, 175)}.get(status, (156, 163, 175)))

            def on_exit(icon, item):
                self.watcher.stop()
                icon.stop()
                self.root.after(0, self.root.destroy)

            menu = pystray.Menu(
                pystray.MenuItem('打开窗口', on_show),
                pystray.MenuItem('立即同步', on_sync),
                pystray.MenuItem('暂停/恢复', on_pause),
                pystray.MenuItem('退出', on_exit),
            )

            self._icon = pystray.Icon('CP Group Sync', create_icon((34, 197, 94)), 'CP Group 同步客户端', menu)

            # 后台运行托盘
            threading.Thread(target=self._icon.run, daemon=True).start()
            log.info('系统托盘已启动')
        except ImportError:
            log.warning('未安装 pystray/PIL，托盘不可用')
        except Exception as e:
            log.error('托盘启动失败: %s', e)

    def update_status(self, status, msg=''):
        if self.root:
            colors = {'synced': '#a6e3a1', 'syncing': '#f9e2af', 'error': '#f38ba8'}
            dot_color = colors.get(status, '#a6e3a1')
            self.root.after(0, lambda: self.status_var.set(msg))
            self.root.after(0, lambda: self.status_dot.configure(bg=dot_color, fg=dot_color))
            if self._icon:
                try:
                    from PIL import Image, ImageDraw
                    color_map = {'synced': (34, 197, 94), 'syncing': (234, 179, 8), 'error': (239, 68, 68)}
                    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)
                    draw.ellipse([8, 8, 56, 56], fill=color_map.get(status, (156, 163, 175)))
                    draw.text((20, 18), 'CP Group', fill='white')
                    self._icon.icon = img
                except Exception:
                    pass


# ========== 主程序 ==========
def main():
    cfg = load_config()

    if not cfg.get('username') or not cfg.get('sync_folders'):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox

        root = tk.Tk()
        root.title('CP Group 同步 - 初始设置')
        root.geometry('520x400')
        root.configure(bg='#1e1e2e')

        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background='#1e1e2e', foreground='#cdd6f4', font=('Microsoft YaHei UI', 10))

        ttk.Label(root, text='CP Group 同步客户端', font=('Microsoft YaHei UI', 16, 'bold'),
                  foreground='#89b4fa').pack(pady=(20, 10))

        # 基本设置
        basic_frame = ttk.Frame(root)
        basic_frame.pack(fill='x', padx=15, pady=5)

        basic_fields = [
            ('server_url', '服务器地址', False),
            ('username', '用户名', False),
            ('password', '密码', True),
            ('sync_interval', '同步间隔（秒）', False),
        ]
        entries = {}
        for i, (key, label, secret) in enumerate(basic_fields):
            ttk.Label(basic_frame, text=label + ':').grid(row=i, column=0, padx=5, pady=3, sticky='w')
            e = ttk.Entry(basic_frame, width=30, show='*' if secret else None)
            e.insert(0, str(cfg.get(key, '')))
            e.grid(row=i, column=1, padx=5, pady=3)
            entries[key] = e

        # 同步文件夹列表
        ttk.Label(root, text='同步文件夹:', font=('Microsoft YaHei UI', 10, 'bold'),
                  foreground='#89b4fa').pack(anchor='w', padx=15, pady=(10, 3))

        folder_frame = ttk.Frame(root)
        folder_frame.pack(fill='both', expand=True, padx=15)

        folder_tree = ttk.Treeview(folder_frame, columns=('local', 'remote'), show='headings', height=4)
        folder_tree.heading('local', text='本地路径')
        folder_tree.heading('remote', text='云端路径')
        folder_tree.column('local', width=250)
        folder_tree.column('remote', width=150)
        folder_tree.pack(fill='both', expand=True)

        folder_scroll = ttk.Scrollbar(folder_frame, orient='vertical', command=folder_tree.yview)
        folder_tree.configure(yscrollcommand=folder_scroll.set)

        folder_list = list(cfg.get('sync_folders', []))
        for f in folder_list:
            folder_tree.insert('', 'end', values=(f.get('local', ''), f.get('remote', '')))

        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill='x', padx=15, pady=5)

        def add_folder():
            p = filedialog.askdirectory(title='选择本地同步文件夹')
            if not p:
                return
            remote_name = os.path.basename(p)
            folder_list.append({'local': p, 'remote': remote_name})
            folder_tree.insert('', 'end', values=(p, remote_name))

        def remove_folder():
            sel = folder_tree.selection()
            if not sel:
                return
            vals = folder_tree.item(sel[0], 'values')
            for f in folder_list:
                if f.get('local') == vals[0]:
                    folder_list.remove(f)
                    break
            folder_tree.delete(sel[0])

        ttk.Button(btn_frame, text='添加文件夹', command=add_folder).pack(side='left', padx=3)
        ttk.Button(btn_frame, text='删除选中', command=remove_folder).pack(side='left', padx=3)

        def on_start():
            cfg['server_url'] = entries['server_url'].get().strip()
            cfg['username'] = entries['username'].get().strip()
            cfg['password'] = entries['password'].get().strip()
            try:
                cfg['sync_interval'] = max(10, int(entries['sync_interval'].get().strip()))
            except ValueError:
                cfg['sync_interval'] = 30
            cfg['sync_folders'] = folder_list
            if not cfg['sync_folders']:
                messagebox.showwarning('提示', '请至少添加一个同步文件夹')
                return
            save_config(cfg)
            root.destroy()

        ttk.Button(root, text='开始使用', command=on_start).pack(pady=15)

        root.mainloop()
        cfg = load_config()

    if not cfg.get('username') or not cfg.get('sync_folders'):
        return

    # 创建本地文件夹
    for folder in cfg['sync_folders']:
        lp = folder.get('local', '')
        if lp:
            Path(lp).mkdir(parents=True, exist_ok=True)

    db = SyncDB()
    engine = SyncEngine(cfg, db)

    # 登录
    if not engine.login():
        import tkinter.messagebox as mb
        mb.showerror('登录失败', '无法登录服务器，请检查设置')

    # 文件监控（所有同步文件夹）
    local_paths = [f['local'] for f in cfg['sync_folders'] if f.get('local')]
    watcher = FileWatcher(local_paths, lambda: threading.Thread(target=engine.sync_cycle, daemon=True).start())
    watcher.start()

    # 启动定时同步
    engine.start_sync_loop(cfg.get('sync_interval', 30))

    # 主窗口
    ui = MainWindow(engine, watcher)
    engine.on_status = lambda s, msg: ui.update_status(s, msg)

    # 首次同步
    threading.Thread(target=engine.sync_cycle, daemon=True).start()

    ui.run()
    watcher.stop()
    engine.close()


if __name__ == '__main__':
    main()
