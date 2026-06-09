import os
import time
import json
import logging
import threading
from queue import Queue

from PySide6.QtCore import QObject, Signal

from core.config import load, save, get_password
from core.cloud_api import CloudAPI
from core.db import SyncDB

log = logging.getLogger('dxw_sync')


class SyncEngine(QObject):
    status_changed = Signal(str)
    progress_updated = Signal(int, int, str)
    task_completed = Signal(dict)
    sync_error = Signal(str)
    activity_added = Signal(str, str, str, str)
    stats_updated = Signal(dict)
    connection_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = load()
        self.api = CloudAPI(self.config.get('server_url', 'http://localhost:5000'))
        self.db = SyncDB()
        self._running = False
        self._paused = False
        self._syncing = False
        self._need_sync = False
        self._lock = threading.Lock()
        self._timer = None
        self._status = 'disconnected'
        self._sync_count_today = 0
        self._fail_count_today = 0

    @property
    def status(self):
        return self._status

    def connect_server(self):
        url = self.config.get('server_url', '')
        username = self.config.get('username', '')
        pwd = get_password(self.config)
        if not url or not username or not pwd:
            self._set_status('disconnected')
            self.connection_changed.emit(False)
            return False
        try:
            self.api = CloudAPI(url)
            self.api.login(username, pwd)
            self._set_status('connected')
            self.connection_changed.emit(True)
            return True
        except Exception as e:
            self._set_status('error')
            self.connection_changed.emit(False)
            self.sync_error.emit(f'连接失败: {e}')
            return False

    def connect_async(self, callback=None):
        threading.Thread(target=self._connect_worker, args=(callback,), daemon=True).start()

    def _connect_worker(self, callback):
        ok = self.connect_server()
        if callback:
            callback(ok)

    def start(self):
        self._running = True
        self.connect_async()
        self._schedule_next()

    def stop(self):
        self._running = False
        self._paused = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def pause(self):
        self._paused = True
        self._set_status('paused')

    def resume(self):
        self._paused = False
        self._set_status('connected')
        self._schedule_next()

    def sync_now(self):
        if self._syncing:
            self._need_sync = True
            return
        threading.Thread(target=self._sync_worker, daemon=True).start()

    def _schedule_next(self):
        if not self._running or self._paused:
            return
        interval_min = self.config.get('sync_interval', 60)
        if interval_min <= 0:
            return
        interval_sec = max(interval_min * 60, 30)
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(interval_sec, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        if not self._running or self._paused:
            return
        self.sync_now()

    def _set_status(self, s):
        self._status = s
        self.status_changed.emit(s)

    def _sync_worker(self):
        with self._lock:
            if self._syncing:
                return
            self._syncing = True

        try:
            if self._status not in ('connected', 'idle'):
                ok = self.connect_server()
                if not ok:
                    self.sync_error.emit('服务器未连接，请在设置中配置连接')
                    return

            self._set_status('syncing')
            self._do_sync()
            self._sync_count_today += 1
            self._set_status('connected' if not self._paused else 'paused')
        except PermissionError:
            self.sync_error.emit('会话过期，正在重新登录...')
            if self.connect_server():
                threading.Thread(target=self._sync_worker, daemon=True).start()
                return
            self._set_status('error')
        except Exception as e:
            self._fail_count_today += 1
            self.sync_error.emit(str(e))
            self._set_status('error')
        finally:
            self._syncing = False
            with self._lock:
                if self._need_sync:
                    self._need_sync = False
                    threading.Thread(target=self._sync_worker, daemon=True).start()
            self._schedule_next()

    def _do_sync(self):
        folders = self.config.get('sync_folders', [])
        total_files = 0
        current_file = 0
        changes_map = []

        for folder in folders:
            local = folder.get('local', '')
            remote = folder.get('remote', '')
            folder_id = folder.get('id', remote)
            if not os.path.isdir(local):
                continue

            self.progress_updated.emit(0, 1, f'扫描文件夹 ({os.path.basename(local)})...')
            local_files = self._scan_local(local)
            self.progress_updated.emit(0, 1, f'与服务端比对差异 ({os.path.basename(local)})...')
            changes = self.api.get_changes(remote, local_files)
            to_upload = changes.get('upload', [])
            to_download = changes.get('download', [])
            total_files += len(to_upload) + len(to_download)
            changes_map.append((local, remote, folder_id, to_upload, to_download))

        for local, remote, folder_id, to_upload, to_download in changes_map:
            auto_download = self.config.get('auto_download', False)

            for rel_path in to_upload:
                current_file += 1
                src = os.path.join(local, rel_path)
                if not os.path.isfile(src):
                    continue
                fsize = os.path.getsize(src)
                self.progress_updated.emit(current_file, total_files, rel_path)
                self.activity_added.emit('upload', rel_path, f'{fsize} B', '进行中')
                try:
                    retention_count = folder.get('version_retention_count', 0)
                    retention_mode = folder.get('version_retention_mode', 'count')
                    retention_days = folder.get('version_retention_days', 0)
                    with open(src, 'rb') as f:
                        self.api.upload(remote, rel_path, f, fsize, retention_count, retention_mode, retention_days)
                    self.db.log('INFO', 'upload', folder_id, rel_path, fsize, '完成')
                    self.activity_added.emit('upload', rel_path, f'{fsize} B', '完成')
                    self.db.update_file_state(folder_id, rel_path, os.path.getmtime(src), fsize, status='synced')
                except Exception as e:
                    self.db.log('ERROR', 'upload', folder_id, rel_path, fsize, str(e))
                    self.activity_added.emit('upload', rel_path, f'{fsize} B', f'失败: {e}')

            if auto_download:
                for rel_path in to_download:
                    current_file += 1
                    self.progress_updated.emit(current_file, total_files, rel_path)
                    try:
                        data = self.api.download_batch(remote, [rel_path])
                        dest = os.path.join(local, rel_path)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, 'wb') as f:
                            f.write(data)
                        self.db.log('INFO', 'download', folder_id, rel_path, len(data), '完成')
                        self.activity_added.emit('download', rel_path, f'{len(data)} B', '完成')
                    except Exception as e:
                        self.db.log('ERROR', 'download', folder_id, rel_path, 0, str(e))
                        self.activity_added.emit('download', rel_path, '0 B', f'失败: {e}')

        self.task_completed.emit({
            'synced': True, 'total_files': total_files,
            'failed': self._fail_count_today,
        })
        self.stats_updated.emit(self.db.get_total_stats())

    def _scan_local(self, local_path):
        files = {}
        try:
            for root, dirs, fnames in os.walk(local_path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
                for fn in fnames:
                    if fn.endswith(('.tmp', '.swp', '.swx', '~')):
                        continue
                    fp = os.path.join(root, fn)
                    try:
                        st = os.stat(fp)
                        rel = os.path.relpath(fp, local_path).replace(os.sep, '/')
                        files[rel] = [st.st_mtime, st.st_size]
                    except Exception:
                        pass
        except Exception:
            pass
        return {k: v for k, v in sorted(files.items())}

    def get_sync_stats(self):
        total = self.db.get_total_stats()
        total['sync_count_today'] = self._sync_count_today
        total['fail_count_today'] = self._fail_count_today
        return total
