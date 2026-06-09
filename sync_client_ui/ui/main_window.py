import os
import sys
import time
import threading
import logging

from pathlib import Path
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                               QStackedWidget, QApplication, QLabel, QPushButton,
                               QMessageBox, QSystemTrayIcon, QMenu, QComboBox)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QPoint
from PySide6.QtGui import QIcon, QAction, QPainter, QColor, QBrush, QMouseEvent, QPixmap

from core.config import load, save, get_password, set_password
from core.sync_engine import SyncEngine
from ui.theme import get, set_mode, is_dark
from PySide6.QtCore import QTimer as QtCoreQTimer
from ui.animations import AnimatedStackedWidget, install_button_animations, ToastManager, ButtonBreathing
from ui.animations.fade_in_mixin import slide_in_widget


def _base_path():
    try:
        return sys._MEIPASS
    except AttributeError:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from ui.widgets.sidebar import Sidebar
from ui.widgets.top_bar import TopBar
from ui.widgets.bottom_bar import BottomBar
from ui.widgets.status_bar import StatusBar
from ui.pages.dashboard_page import DashboardPage
from ui.pages.tasks_page import TasksPage
from ui.pages.files_page import FilesPage
from ui.pages.logs_page import LogsPage
from ui.pages.settings_page import SettingsPage
from dialogs.add_task_wizard import AddTaskDialog
from dialogs.retention_dialog import RetentionDialog
from dialogs.confirm_dialog import ConfirmDialog
from dialogs.version_history import VersionHistoryDialog

log = logging.getLogger('dxw_sync')


class MainWindow(QMainWindow):
    _files_loaded = Signal(list, str)
    _file_error = Signal(str)
    _user_root_loaded = Signal(str)
    _upload_progress = Signal(int, str)
    _upload_done = Signal()

    def __init__(self):
        super().__init__()
        self.config = load()
        # 迁移旧版本配置：为没有 id 的任务分配唯一标识
        import uuid
        folders = self.config.get("sync_folders", [])
        migrated = False
        for f in folders:
            if not f.get("id"):
                f["id"] = str(uuid.uuid4())[:8]
                migrated = True
        if migrated:
            from core.config import save
            try:
                save(self.config)
            except Exception:
                pass  # 文件被占用时忽略
        self.engine = SyncEngine(self)

        self.setWindowTitle('DXW 备份客户端')
        self.setMinimumSize(800, 600)
        self.resize(1200, 800)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        self._setup_ui()
        self._connect_signals()
        self._load_stylesheet()
        self._load_existing_tasks()
        self._setup_tray()

        self._set_window_icon()

        self._previous_mode = self.config.get('ui_mode', 'light')

        self._log_refresh_timer = QTimer(self)
        self._log_refresh_timer.setSingleShot(True)
        self._log_refresh_timer.timeout.connect(self._do_refresh_logs)

        # File page thread-safe signals
        self._files_loaded.connect(self.files_page.show_files)
        self._file_error.connect(self.files_page.show_error)
        self._user_root_loaded.connect(lambda p: (self.files_page.set_user_root(p), self.files_page.show_root()))

        self._upload_progress.connect(self.files_page.set_progress)
        self._upload_done.connect(self.files_page.clear_progress)

        # Reconnect engine signals after UI is ready
        self.engine.activity_added.connect(self._on_activity)
        self.engine.progress_updated.connect(self._on_progress)
        self.engine.status_changed.connect(self._on_status)
        self.engine.connection_changed.connect(self._on_connection)
        self.engine.sync_error.connect(self._on_error)
        self.engine.task_completed.connect(self._on_task_completed)
        self.engine.stats_updated.connect(self._on_stats)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._titlebar = QWidget()
        self._titlebar.setObjectName('customTitleBar')
        self._titlebar.setFixedHeight(32)
        tl = QHBoxLayout(self._titlebar)
        tl.setContentsMargins(12, 0, 8, 0)
        tl.setSpacing(0)
        self._title_label = QLabel('DXW 备份客户端')
        self._title_label.setStyleSheet('font-size: 12px;')
        tl.addWidget(self._title_label)
        tl.addStretch()

        for icon, flag in [('🗕', 0), ('🗖', 1), ('✕', 2)]:
            btn = QPushButton(icon)
            btn.setFixedSize(36, 28)
            btn.setObjectName('titleBtn')
            btn.setCursor(Qt.PointingHandCursor)
            if flag == 0:
                btn.clicked.connect(self.showMinimized)
            elif flag == 1:
                btn.clicked.connect(lambda: self.showNormal() if self.isMaximized() else self.showMaximized())
            elif flag == 2:
                btn.clicked.connect(self.close)
            tl.addWidget(btn)

        self._titlebar.mousePressEvent = self._start_drag
        self._titlebar.mouseMoveEvent = self._do_drag
        self._titlebar.hide()
        main_layout.addWidget(self._titlebar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = Sidebar(self)
        body_layout.addWidget(self.sidebar)

        right = QWidget()
        right.setStyleSheet('background-color: transparent;')
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.top_bar = TopBar(self)
        right_layout.addWidget(self.top_bar)

        self.content_stack = AnimatedStackedWidget()
        self.content_stack.setStyleSheet('background-color: transparent;')

        self.dashboard_page = DashboardPage(self)
        self.tasks_page = TasksPage(self)
        self.files_page = FilesPage(self)
        self.logs_page = LogsPage(self)
        self.settings_page = SettingsPage(self)

        self.content_stack.addWidget(self.dashboard_page)
        self.content_stack.addWidget(self.tasks_page)
        self.content_stack.addWidget(self.files_page)
        self.content_stack.addWidget(self.logs_page)
        self.content_stack.addWidget(self.settings_page)

        right_layout.addWidget(self.content_stack, 1)

        self.bottom_bar = BottomBar(self)
        right_layout.addWidget(self.bottom_bar)

        self.status_bar = StatusBar(self)
        right_layout.addWidget(self.status_bar)

        body_layout.addWidget(right, 1)
        main_layout.addWidget(body, 1)

    def _connect_signals(self):
        self.sidebar.page_changed.connect(self._on_page_changed)
        self.bottom_bar.minimize_btn.clicked.connect(self._minimize_to_tray)
        self.bottom_bar.pause_btn.clicked.connect(self._toggle_pause)
        self.top_bar._theme_btn.mousePressEvent = lambda e: self._toggle_theme()

        self.dashboard_page.sync_requested.connect(self._on_sync_now)
        self.dashboard_page.add_task_requested.connect(self._on_add_task)

        self.tasks_page.add_task_requested.connect(self._on_add_task)
        self.tasks_page.task_sync_requested.connect(self._on_task_sync)
        self.tasks_page.task_pause_requested.connect(self._on_task_pause)
        self.tasks_page.task_delete_requested.connect(self._on_task_delete)
        self.tasks_page.task_edit_requested.connect(self._on_edit_task)
        self.tasks_page.task_retention_requested.connect(self._on_edit_retention)

        self.files_page.navigate.connect(self._on_file_navigate)
        self.files_page.refresh.connect(self._on_file_refresh)
        self.files_page.upload.connect(self._on_file_upload)
        self.files_page.upload_files.connect(self._on_file_upload_dropped)
        self.files_page.download.connect(self._on_file_download)
        self.files_page.view_versions.connect(self._on_view_versions)
        self.files_page.delete_file_signal.connect(self._on_file_delete)

        self.logs_page.export_requested.connect(self._on_export_logs)
        self.logs_page.clear_requested.connect(self._on_clear_logs)

        self.settings_page.test_btn.clicked.connect(self._on_test_connection)
        self.settings_page.save_btn.clicked.connect(self._on_save_settings)

        self._sync_breathing = ButtonBreathing(self.dashboard_page.sync_btn)

        install_button_animations()

    def _load_stylesheet(self):
        mode = self.config.get('ui_mode', 'light')
        qss_path = os.path.join(_base_path(), 'resources', 'styles', f'{mode}.qss')
        if os.path.exists(qss_path):
            with open(qss_path, 'r', encoding='utf-8') as f:
                app = QApplication.instance()
                app.setStyleSheet(f.read())
        set_mode(mode)
        self.top_bar._update_theme_icon()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, lambda: self._set_titlebar_theme(mode))
        self._fix_combo_views(mode)

    def _fix_combo_views(self, mode):
        from PySide6.QtGui import QPalette, QColor
        bg = '#FFFFFF' if mode == 'light' else ('#1F1F1F' if mode == 'dark' else '#0A0E17')
        fg = '#1D2129' if mode == 'light' else ('#F5F5F5' if mode == 'dark' else '#E0F7FF')
        for w in QApplication.instance().allWidgets():
            if isinstance(w, QComboBox):
                try:
                    view = w.view()
                    if view:
                        p = view.palette()
                        p.setColor(QPalette.Base, QColor(bg))
                        p.setColor(QPalette.Text, QColor(fg))
                        p.setColor(QPalette.Window, QColor(bg))
                        view.setPalette(p)
                        view.setStyleSheet(f'background: {bg}; color: {fg};')
                except Exception:
                    pass

    def _set_titlebar_theme(self, mode):
        is_custom = mode in ('dark', 'sci_fi')
        if is_custom:
            flags = Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint
            self.setWindowFlags(flags)
            self._titlebar.show()
        else:
            self._titlebar.hide()
            self.setWindowFlags(Qt.Window)
        self.show()

    def _start_drag(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _do_drag(self, event):
        if hasattr(self, '_drag_pos') and event.buttons() == Qt.LeftButton:
            self.move(self.pos() + event.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = event.globalPosition().toPoint()

    def _set_window_icon(self):
        try:
            icon_path = self._icon_path()
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
        except Exception:
            pass

    @staticmethod
    def _icon_path():
        if getattr(sys, 'frozen', False):
            return Path(sys._MEIPASS) / 'icon111.ico'
        return Path(__file__).resolve().parent.parent.parent / 'icon111.ico'

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self)
        self._set_tray_color('#86909C')
        self._tray.setToolTip('DXW 备份客户端')

        menu = QMenu()
        show_action = QAction('📂 打开主窗口', self)
        show_action.triggered.connect(self.showNormal)
        menu.addAction(show_action)

        sync_action = QAction('▶ 立即同步', self)
        sync_action.triggered.connect(self._on_sync_now)
        menu.addAction(sync_action)

        menu.addSeparator()

        quit_action = QAction('✕ 退出', self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activate)

        if self.config.get('minimize_to_tray', True):
            self._tray.show()

    def _on_tray_activate(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _minimize_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            self.showMinimized()

    def _toggle_pause(self):
        if self.engine.status == 'paused':
            self.engine.resume()
            self.bottom_bar.pause_btn.setText('⏸ 暂停同步')
        else:
            self.engine.pause()
            self.bottom_bar.pause_btn.setText('▶ 恢复同步')

    def _on_page_changed(self, page_id):
        mapping = {
            'dashboard': 0,
            'tasks': 1,
            'files': 2,
            'logs': 3,
            'settings': 4,
        }
        idx = mapping.get(page_id, 0)
        self.content_stack.setCurrentIndex(idx)
        if page_id == 'logs':
            self._refresh_logs()
        elif page_id == 'files':
            self._refresh_files()

        page_widgets = {
            'dashboard': self.dashboard_page,
            'tasks': self.tasks_page,
            'files': self.files_page,
            'logs': self.logs_page,
            'settings': self.settings_page,
        }
        w = page_widgets.get(page_id)
        if w:
            from PySide6.QtWidgets import QLabel, QFrame
            title_label = w.findChild(QLabel, 'pageTitle')
            if title_label:
                slide_in_widget(title_label, duration=200, direction='up', distance=10)
            sections = w.findChildren(QLabel, 'sectionTitle')
            for i, s in enumerate(sections):
                delay = i * 60
                QtCoreQTimer.singleShot(delay, lambda s=s: slide_in_widget(s, duration=200, direction='up', distance=10))

            if page_id == 'dashboard':
                stat_cards = w.findChildren(QFrame)
                stat_cards = [c for c in stat_cards if c.property('class') == 'stat-card']
                for i, card in enumerate(stat_cards):
                    delay = 80 + i * 100
                    QtCoreQTimer.singleShot(delay, lambda c=card: slide_in_widget(c, duration=250, direction='up', distance=20))

    def _refresh_logs(self):
        self._log_refresh_timer.start(300)

    def _do_refresh_logs(self):
        logs = self.engine.db.get_logs(limit=200)
        self.logs_page.populate(logs)

    def _on_sync_now(self):
        self.engine.sync_now()

    def _load_existing_tasks(self):
        folders = self.config.get('sync_folders', [])
        for f in folders:
            task_id = f.get('id')
            if not task_id:
                continue
            self.tasks_page.add_task_card(
                task_id, f.get('local', ''), f.get('remote', ''),
                f.get('backup_type', 'incremental'),
                f.get('status', 'idle'),
                version_retention_count=int(f.get('version_retention_count', 0) or 0),
                version_retention_mode=f.get('version_retention_mode', 'count'),
                version_retention_days=int(f.get('version_retention_days', 0) or 0)
            )

    def _on_add_task(self):
        dlg = AddTaskDialog(self)
        if dlg.exec():
            data = dlg.get_task_data()
            folders = self.config.get('sync_folders', [])
            import uuid
            task_id = str(uuid.uuid4())[:8]
            folders.append({
                'id': task_id,
                'local': data['local_path'],
                'remote': data['remote_path'],
                'frequency': data['frequency'],
                'backup_type': data['backup_type'],
                'version_retention_count': data['version_retention_count'],
                'version_retention_mode': data['version_retention_mode'],
                'version_retention_days': data['version_retention_days'],
                'conflict': data['conflict'],
            })
            self.config['sync_folders'] = folders
            save(self.config)
            self.tasks_page.add_task_card(
                task_id, data['local_path'], data['remote_path'],
                data['backup_type'],
                version_retention_count=data['version_retention_count'],
                version_retention_mode=data['version_retention_mode'],
                version_retention_days=data['version_retention_days']
            )

            self.engine.sync_now()

    def _on_task_sync(self, task_id):
        self.engine.sync_now()

    def _on_task_pause(self, task_id):
        self._toggle_pause()

    def _on_task_delete(self, task_id):
        dlg = ConfirmDialog('删除任务', '确定删除此备份任务？\n此操作不会删除本地或云端文件。', '删除', '取消', self)
        if dlg.exec():
            folders = self.config.get('sync_folders', [])
            self.config['sync_folders'] = [f for f in folders if f.get('id') != task_id]
            save(self.config)
            self.tasks_page.remove_task_card(task_id)


    def _on_edit_task(self, task_id):
        try:
            folders = self.config.get('sync_folders', [])
            folder = None
            for f in folders:
                if f.get('id') == task_id:
                    folder = f
                    break
            if not folder:
                # 再用索引试试（主要是 None 的情况）
                for f in folders:
                    if f.get('local') and task_id and f.get('local') == task_id:
                        folder = f
                        break
            if not folder:
                return

            dlg = AddTaskDialog(self)
            # Pre-fill the dialog with existing data
            dlg._path_edit.setText(folder.get('local', ''))
            dlg._remote_edit.setText(folder.get('remote', ''))
            freq_map = ['manual', 'realtime', 'hourly', 'daily', 'weekly']
            type_map = ['incremental', 'full']
            conflict_map = ['keep_newer', 'local', 'remote', 'both']
            freq = folder.get('frequency', 'manual')
            if freq in freq_map:
                dlg._freq_combo.setCurrentIndex(freq_map.index(freq))
            btype = folder.get('backup_type', 'incremental')
            if btype in type_map:
                dlg._type_combo.setCurrentIndex(type_map.index(btype))
            conflict = folder.get('conflict', 'keep_newer')
            if conflict in conflict_map:
                dlg._conflict_combo.setCurrentIndex(conflict_map.index(conflict))
            mode = folder.get('version_retention_mode', 'count')
            dlg._retention_mode.setCurrentIndex(0 if mode == 'count' else 1)
            rc = int(folder.get('version_retention_count', 5) or 5)
            dlg._retention_spin.setValue(rc)
            rd = int(folder.get('version_retention_days', 30) or 30)
            dlg._retention_days_spin.setValue(rd)
            if dlg.exec():
                data = dlg.get_task_data()
                folder['local'] = data['local_path']
                folder['remote'] = data['remote_path']
                folder['frequency'] = data['frequency']
                folder['backup_type'] = data['backup_type']
                folder['version_retention_count'] = data['version_retention_count']
                folder['version_retention_mode'] = data['version_retention_mode']
                folder['version_retention_days'] = data['version_retention_days']
                folder['conflict'] = data['conflict']
                self.config['sync_folders'] = folders
                save(self.config)
                self.tasks_page.remove_task_card(task_id)
                self.tasks_page.add_task_card(
                    task_id, data['local_path'], data['remote_path'],
                    data['backup_type'],
                    version_retention_count=data['version_retention_count'],
                    version_retention_mode=data['version_retention_mode'],
                    version_retention_days=data['version_retention_days']
                )
        except Exception as e:
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '编辑错误', f'无法打开编辑: {e}')
    def _on_edit_retention(self, task_id):
        try:
            folders = self.config.get('sync_folders', [])
            folder = None
            for f in folders:
                if f.get('id') == task_id:
                    folder = f
                    break
            if not folder:
                return

            task_name = os.path.basename(folder.get('local', '')) or folder.get('local', '')
            current_mode = folder.get('version_retention_mode', 'count')
            current_count = int(folder.get('version_retention_count', 5) or 5)
            current_days = int(folder.get('version_retention_days', 30) or 30)

            dlg = RetentionDialog(task_name, current_mode, current_count, current_days, self)
            if dlg.exec():
                data = dlg.get_retention_data()
                folder['version_retention_mode'] = data['version_retention_mode']
                folder['version_retention_count'] = data['version_retention_count']
                folder['version_retention_days'] = data['version_retention_days']
                self.config['sync_folders'] = folders
                save(self.config)
                self.tasks_page.remove_task_card(task_id)
                self.tasks_page.add_task_card(
                    task_id, folder['local'], folder['remote'],
                    folder.get('backup_type', 'incremental'),
                    version_retention_count=data['version_retention_count'],
                    version_retention_mode=data['version_retention_mode'],
                    version_retention_days=data['version_retention_days']
                )
                # 已保存配置，引擎会自动检测到变更
        except Exception as e:
            import traceback
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '保留设置错误', f'无法保存保留设置: {e}')

    def _on_view_versions(self, file_path):
        if self.engine.status not in ('connected', 'syncing'):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '提示', '请先连接服务器')
            return
        import json
        try:
            data = json.loads(file_path) if file_path.startswith('{') else None
        except Exception:
            data = None
        if data and isinstance(data, dict):
            path = data.get('path', file_path)
        else:
            path = file_path
        dlg = VersionHistoryDialog(self.engine.api, path, self)
        dlg.exec()

    def _refresh_files(self):
        folders = [f.get('remote', '') for f in self.config.get('sync_folders', [])]
        self.files_page.set_backup_folders(folders)

        if self.engine.status not in ('connected', 'syncing'):
            self.files_page.show_root()
            return

        def load():
            try:
                path = self.engine.api.get_user_dir_path()
                if path:
                    self._user_root_loaded.emit(path)
            except Exception:
                self._file_error.emit('无法连接到服务器获取文件列表')

        t = threading.Thread(target=load, daemon=True)
        t.start()

    def _on_file_navigate(self, path):
        if self.engine.status not in ('connected', 'syncing'):
            self.files_page.show_error('无法浏览: 服务器未连接')
            return

        def load():
            try:
                entries = self.engine.api.list_dir(path)
                self._files_loaded.emit(entries, path)
            except Exception:
                self._file_error.emit('无法打开文件夹 (连接失败)')

        t = threading.Thread(target=load, daemon=True)
        t.start()

    def _on_file_refresh(self):
        path = self.files_page.current_path()
        if path:
            self._on_file_navigate(path)
        else:
            self._refresh_files()

    def _on_file_upload(self, local_folder):
        from PySide6.QtWidgets import QInputDialog
        remote_path, ok = QInputDialog.getText(self, '上传到云端', '请输入上传到的云端目录路径:',
                                               text=self.files_page.current_path())
        if not ok or not remote_path.strip():
            return
        remote_path = remote_path.strip().strip('/')

        def load():
            try:
                for fname in os.listdir(local_folder):
                    fpath = os.path.join(local_folder, fname)
                    if os.path.isfile(fpath):
                        self.engine.api.upload(fpath, fname, open(fpath, 'rb'), os.path.getsize(fpath))
                from PySide6.QtCore import QMetaObject, Qt
                QMetaObject.invokeMethod(self, '_refresh_files', Qt.QueuedConnection)
            except Exception as e:
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(self, '_show_dl_err', Qt.QueuedConnection, Q_ARG(str, str(e)))
        threading.Thread(target=load, daemon=True).start()

    def _on_file_upload_dropped(self, file_paths):
        if not file_paths:
            return
        remote_root = self.files_page.current_path()
        if not remote_root:
            remote_root = self.files_page._user_root
        if not remote_root:
            return

        total = len(file_paths)
        self._upload_progress.emit(0, f'准备上传 {total} 个文件...')

        def load():
            api = self.engine.api
            done = 0
            for fpath in file_paths:
                fname = os.path.basename(fpath)
                size = os.path.getsize(fpath)
                self._upload_progress.emit(0, f'上传中: {fname}')
                try:
                    with open(fpath, 'rb') as f:
                        api.upload(remote_root, fname, f, size)
                    done += 1
                except Exception as e:
                    self._upload_progress.emit(0, f'{fname} 失败')
                    log.error('上传失败 %s: %s', fname, e)
                pct = int(done / total * 100)
                self._upload_progress.emit(pct, f'已完成 {done}/{total}')
            self._upload_done.emit()
            self._refresh_files()
        threading.Thread(target=load, daemon=True).start()

    def _on_file_download(self, payload):
        import json
        try:
            info = json.loads(payload)
            if 'paths' in info:
                paths = info['paths']
                dest = info['dest']
            else:
                paths = [info['path']]
                dest = info['dest']
        except (json.JSONDecodeError, KeyError):
            paths = [payload]
            from PySide6.QtWidgets import QFileDialog
            dest = QFileDialog.getExistingDirectory(self, '选择下载目录')
            if not dest:
                return

        def load():
            try:
                for path in paths:
                    data = self.engine.api.download_file(path)
                    local = os.path.join(dest, os.path.basename(path))
                    with open(local, 'wb') as f:
                        f.write(data)
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(self, '_show_dl_ok', Qt.QueuedConnection, Q_ARG(str, dest))
            except Exception as e:
                from PySide6.QtCore import QMetaObject, Qt, Q_ARG
                QMetaObject.invokeMethod(self, '_show_dl_err', Qt.QueuedConnection, Q_ARG(str, str(e)))
        threading.Thread(target=load, daemon=True).start()

    @Slot(str)
    def _show_dl_ok(self, path):
        QMessageBox.information(self, '下载完成', f'已保存到:\n{path}')

    @Slot(str)
    def _show_dl_err(self, msg):
        QMessageBox.warning(self, '下载失败', msg)

    def _on_file_delete(self, filepath):
        pass

    def _on_test_connection(self):
        url = self.settings_page.server_url.text()
        username = self.settings_page.username.text()
        password = self.settings_page.password.text()

        from core.cloud_api import CloudAPI
        from core.config import set_password
        api = CloudAPI(url)
        ok, msg = api.test_connection(url, username, password)
        self.settings_page.set_test_result(ok, msg)
        if ok:
            self.config['server_url'] = url
            self.config['username'] = username
            if password:
                set_password(self.config, password)
            self.engine.config = self.config
            self.engine.connect_async()

    def _on_save_settings(self):
        new_config = self.settings_page.get_config()
        pwd = self.settings_page.password.text()
        self.config.update(new_config)
        if pwd:
            set_password(self.config, pwd)
        save(self.config)
        self.engine.config = self.config
        self.engine.connect_async()
        self._update_auto_start()
        QMessageBox.information(self, '设置', '设置已保存')

    def _update_auto_start(self):
        import winreg
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Run'
        app_name = 'DXW同步客户端'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ)
            if self.config.get('auto_start', False):
                if getattr(sys, 'frozen', False):
                    exe_path = os.path.abspath(sys.argv[0])
                else:
                    exe_path = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            log.warning('设置开机自启失败: %s', e)

        mode = new_config.get('ui_mode', 'light')
        if mode != self._previous_mode:
            self._previous_mode = mode
            self._load_stylesheet()

    def _on_export_logs(self):
        import csv
        from pathlib import Path
        dest = Path.home() / 'Desktop' / f'dxw_sync_logs_{int(time.time())}.csv'
        logs = self.engine.db.get_logs(limit=10000)
        try:
            with open(dest, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(['时间', '级别', '操作', '文件', '大小', '详情'])
                for log in logs:
                    ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(log['timestamp']))
                    w.writerow([ts, log['level'], log['action'], log['rel_path'], log['file_size'], log['detail']])
            QMessageBox.information(self, '导出成功', f'日志已导出到:\n{dest}')
        except Exception as e:
            QMessageBox.warning(self, '导出失败', str(e))

    def _on_clear_logs(self):
        dlg = ConfirmDialog('清空日志', '确定清空所有同步日志？', '清空', '取消', self)
        if dlg.exec():
            self.engine.db.clear_logs()
            self.logs_page.populate([])

    def _on_activity(self, action, path, size, status):
        self.dashboard_page.add_activity(action, path, size, status)

    def _on_progress(self, current, total, filename):
        if filename.startswith('扫描') or filename.startswith('比对'):
            self.top_bar.set_task(filename)
        elif total == 0:
            self.top_bar.set_task(f'正在扫描: {filename}')
        else:
            self.top_bar.set_task(f'正在备份: {filename}')

    def _on_status(self, status):
        self.top_bar.set_status(status)
        if status == 'syncing':
            self._sync_breathing.start()
        else:
            self._sync_breathing.stop()
        tray_colors = {
            'connected': '#0FC6C2',
            'syncing': '#1677FF',
            'paused': '#FF7D00',
            'error': '#F53F3F',
            'disconnected': '#86909C',
            'idle': '#0FC6C2',
        }
        self._set_tray_color(tray_colors.get(status, '#86909C'))

    def _on_connection(self, ok):
        self.top_bar.set_status('connected' if ok else 'error')
        self._set_tray_color('#0FC6C2' if ok else '#F53F3F')

    def _on_error(self, msg):
        self.top_bar.set_status('error')
        self._set_tray_color('#F53F3F')
        self.top_bar.set_task(f'错误: {msg}')
        self.dashboard_page.add_activity('upload', f'错误: {msg}', '', '失败')
        ToastManager.instance().error(msg, parent=self)

    def _on_task_completed(self, result):
        self.top_bar.set_task('空闲')
        self._set_tray_color('#0FC6C2')
        self.status_bar.update_stats(self.engine.get_sync_stats())
        self.status_bar.set_sync_time('刚刚')
        self.dashboard_page.update_stats(self.engine.get_sync_stats())
        if self.sidebar.get_active_id() == 'logs':
            self._refresh_logs()
        ToastManager.instance().success('同步完成', parent=self)

    def _on_stats(self, stats):
        self.status_bar.update_stats(stats)
        self.dashboard_page.update_stats(stats)

    def _set_tray_color(self, color):
        from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush
        pix = QPixmap(20, 20)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(color)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 18, 18)
        p.end()
        self._tray.setIcon(QIcon(pix))

    def _toggle_theme(self):
        from ui.theme import next_mode
        mode = next_mode()
        self.config['ui_mode'] = mode
        self._previous_mode = mode
        self._load_stylesheet()
        mode_map = {'light': 0, 'dark': 1, 'sci_fi': 2}
        self.settings_page._ui_mode.setCurrentIndex(mode_map.get(mode, 0))

    def _quit_app(self):
        self.engine.stop()
        self.engine.db.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.config.get('minimize_to_tray', True) and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            self._quit_app()
            event.accept()
