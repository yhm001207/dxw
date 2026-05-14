import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path.home() / '.dxw_sync_state.db'


class SyncDB:
    def __init__(self, db_path=None):
        self._path = db_path or str(DB_PATH)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute('SELECT sync_status FROM file_state LIMIT 1')
            except sqlite3.OperationalError:
                cur.execute('DROP TABLE IF EXISTS file_state')
            try:
                cur.execute('SELECT level FROM sync_log LIMIT 1')
            except sqlite3.OperationalError:
                cur.execute('DROP TABLE IF EXISTS sync_log')
            cur.executescript('''
                CREATE TABLE IF NOT EXISTS file_state (
                    folder_id TEXT,
                    rel_path TEXT,
                    mtime REAL,
                    size INTEGER,
                    md5 TEXT,
                    sync_status TEXT DEFAULT 'pending',
                    updated_at REAL,
                    PRIMARY KEY (folder_id, rel_path)
                );
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    level TEXT,
                    action TEXT,
                    folder_id TEXT,
                    rel_path TEXT,
                    file_size INTEGER,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_log_time ON sync_log(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_log_level ON sync_log(level);
            ''')
            self._conn.commit()

    def log(self, level, action, folder_id='', rel_path='', file_size=0, detail=''):
        with self._lock:
            import time
            self._conn.execute(
                'INSERT INTO sync_log (timestamp, level, action, folder_id, rel_path, file_size, detail) VALUES (?,?,?,?,?,?,?)',
                (time.time(), level, action, folder_id, rel_path, file_size, detail)
            )
            self._conn.commit()

    def get_logs(self, limit=200, offset=0, level=None):
        with self._lock:
            if level:
                rows = self._conn.execute(
                    'SELECT * FROM sync_log WHERE level=? ORDER BY timestamp DESC LIMIT ? OFFSET ?',
                    (level, limit, offset)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    'SELECT * FROM sync_log ORDER BY timestamp DESC LIMIT ? OFFSET ?',
                    (limit, offset)
                ).fetchall()
            cols = ['id', 'timestamp', 'level', 'action', 'folder_id', 'rel_path', 'file_size', 'detail']
            return [dict(zip(cols, r)) for r in rows]

    def get_log_count(self, level=None):
        with self._lock:
            if level:
                return self._conn.execute('SELECT COUNT(*) FROM sync_log WHERE level=?', (level,)).fetchone()[0]
            return self._conn.execute('SELECT COUNT(*) FROM sync_log').fetchone()[0]

    def clear_logs(self):
        with self._lock:
            self._conn.execute('DELETE FROM sync_log')
            self._conn.commit()

    def update_file_state(self, folder_id, rel_path, mtime, size, md5='', status='synced'):
        with self._lock:
            import time
            self._conn.execute(
                'INSERT OR REPLACE INTO file_state (folder_id, rel_path, mtime, size, md5, sync_status, updated_at) VALUES (?,?,?,?,?,?,?)',
                (folder_id, rel_path, mtime, size, md5, status, time.time())
            )
            self._conn.commit()

    def get_folder_stats(self, folder_id):
        with self._lock:
            total = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE folder_id=?', (folder_id,)).fetchone()[0]
            synced = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE folder_id=? AND sync_status="synced"', (folder_id,)).fetchone()[0]
            pending = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE folder_id=? AND sync_status="pending"', (folder_id,)).fetchone()[0]
            failed = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE folder_id=? AND sync_status="failed"', (folder_id,)).fetchone()[0]
            return {'total': total, 'synced': synced, 'pending': pending, 'failed': failed}

    def get_total_stats(self):
        with self._lock:
            total = self._conn.execute('SELECT COUNT(*) FROM file_state').fetchone()[0]
            synced = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE sync_status="synced"').fetchone()[0]
            pending = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE sync_status="pending"').fetchone()[0]
            failed = self._conn.execute('SELECT COUNT(*) FROM file_state WHERE sync_status="failed"').fetchone()[0]
            total_size = self._conn.execute('SELECT COALESCE(SUM(size),0) FROM file_state').fetchone()[0]
            return {'total': total, 'synced': synced, 'pending': pending, 'failed': failed, 'total_size': total_size}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
