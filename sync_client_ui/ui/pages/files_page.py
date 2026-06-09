import os
import json
import threading

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QTreeWidget, QTreeWidgetItem,
                                QFrame, QHeaderView, QMenu, QProgressBar)
from PySide6.QtCore import Qt, Signal
from ui.animations.progress_animator import AnimatedProgressBar
from ui.animations.extra_effects import DragHighlightEffect


class FilesPage(QWidget):
    navigate = Signal(str)
    refresh = Signal()
    upload = Signal(str)
    upload_files = Signal(list)
    download = Signal(str)
    delete_file_signal = Signal(str)
    view_versions = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ''
        self._user_root = ''
        self._backup_folders = []
        self.setAcceptDrops(True)
        self._setup()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._drag_highlight.activate()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drag_highlight.deactivate()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drag_highlight.deactivate()
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.upload_files.emit(paths)

    def _setup(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel('☁️ 云端文件管理')
        title.setObjectName('pageTitle')
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        self._breadcrumb = QLabel('我的云盘 /')
        self._breadcrumb.setObjectName('breadcrumb')
        layout.addWidget(self._breadcrumb)

        self._progress_bar = AnimatedProgressBar()
        self._progress_bar.setObjectName('taskProgress')
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._upload_btn = QPushButton('⬆ 上传')
        self._upload_btn.setObjectName('primaryBtn')
        self._upload_btn.setCursor(Qt.PointingHandCursor)
        self._upload_btn.clicked.connect(self._on_upload_click)
        action_row.addWidget(self._upload_btn)

        self._download_btn = QPushButton('⬇ 下载')
        self._download_btn.setObjectName('secondaryBtn')
        self._download_btn.setCursor(Qt.PointingHandCursor)
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(self._on_download_click)
        action_row.addWidget(self._download_btn)

        self._delete_btn = QPushButton('🗑 删除')
        self._delete_btn.setObjectName('secondaryBtn')
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setEnabled(False)
        action_row.addWidget(self._delete_btn)

        action_row.addStretch()

        refresh_btn = QPushButton('🔄 刷新')
        refresh_btn.setObjectName('textBtn')
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh.emit)
        action_row.addWidget(refresh_btn)

        layout.addLayout(action_row)

        hint = QLabel('💡 支持拖拽文件到此处上传')
        hint.setObjectName('fileInfo')
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        self._tree = QTreeWidget()
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.header().setStretchLastSection(True)
        self._tree.setHeaderLabels(['名称', '大小', '修改时间', '类型'])
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self._tree, 1)

        self._info = QLabel('')
        self._info.setObjectName('fileInfo')
        layout.addWidget(self._info)


        self._drag_highlight = DragHighlightEffect(self._tree)

    def set_progress(self, value, text=''):
        self._progress_bar.setValue(value)
        if text:
            self._progress_bar.setFormat(text)
        self._progress_bar.show()

    def clear_progress(self):
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat('')
        self._progress_bar.hide()

    def show_error(self, msg):
        self._tree.clear()
        self._breadcrumb.setText(msg)

    def set_backup_folders(self, folders):
        self._backup_folders = folders

    def show_root(self):
        self._tree.clear()
        self._current_path = self._user_root
        self._breadcrumb.setText('我的云盘 /')
        self._info.setText('')
        if self._backup_folders and self._user_root:
            for f in self._backup_folders:
                name = os.path.basename(f) if f else f
                item = QTreeWidgetItem([name, '-', '-', '文件夹'])
                item.setData(0, Qt.UserRole, 'folder')
                self._tree.addTopLevelItem(item)
        elif self._backup_folders and not self._user_root:
            self._tree.clear()
            item = QTreeWidgetItem(['正在连接服务器以加载文件列表...', '', '', ''])
            self._tree.addTopLevelItem(item)
        else:
            self._tree.clear()
            item = QTreeWidgetItem(['暂无备份目录，请先在"备份任务"中添加任务', '', '', ''])
            self._tree.addTopLevelItem(item)

    def show_files(self, entries, current_path):
        self._tree.clear()
        self._current_path = current_path

        parts = current_path.replace(self._user_root, '').strip('\\/').split('\\') if current_path else []
        parts = [p for p in parts if p]
        text = '我的云盘 / ' + ' / '.join(parts) if parts else '我的云盘 /'
        self._breadcrumb.setText(text)

        if current_path and current_path != self._user_root:
            parent = QTreeWidgetItem(['..', '', '', '文件夹'])
            parent.setData(0, Qt.UserRole, '..')
            self._tree.addTopLevelItem(parent)

        for entry in entries:
            name = entry.get('name', '')
            typ = entry.get('type', '')
            size = entry.get('size', '-')
            mtime = entry.get('mtime', '-')
            full_path = entry.get('path', '')

            is_dir = typ == 'directory'
            item = QTreeWidgetItem([name, str(size) if not is_dir else '-', mtime, '文件夹' if is_dir else '文件'])
            item.setData(0, Qt.UserRole, full_path)
            self._tree.addTopLevelItem(item)

        self._info.setText(f'{len(entries)} 项')

    def set_user_root(self, root):
        self._user_root = root

    def current_path(self):
        return self._current_path

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        ftype = item.text(3)
        if not data or data in ('..', 'folder') or ftype == '文件夹':
            return
        menu = QMenu(self)
        dl_action = menu.addAction('⬇ 下载到本地...')
        ver_action = menu.addAction('📋 查看历史版本')
        action = menu.exec(self._tree.mapToGlobal(pos))
        if action == dl_action:
            from PySide6.QtWidgets import QFileDialog
            folder = QFileDialog.getExistingDirectory(self, '选择下载目录')
            if folder:
                self.download.emit(json.dumps({'path': data, 'dest': folder}))
        elif action == ver_action:
            self.view_versions.emit(data)

    def _on_upload_click(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, '选择要上传的文件')
        if files:
            self.upload_files.emit(files)

    def _on_download_click(self):
        selected = self._tree.selectedItems()
        paths = []
        for it in selected:
            data = it.data(0, Qt.UserRole)
            if data and data not in ('..', 'folder'):
                paths.append(data)
        if paths:
            import json
            from PySide6.QtWidgets import QFileDialog
            dest = QFileDialog.getExistingDirectory(self, '选择下载目录')
            if dest:
                self.download.emit(json.dumps({'paths': paths, 'dest': dest}))

    def _on_upload_click(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, '选择要上传的文件')
        if files:
            self.upload_files.emit(files)

    def _on_download_click(self):
        selected = self._tree.selectedItems()
        paths = []
        for it in selected:
            data = it.data(0, Qt.UserRole)
            if data and data not in ('..', 'folder'):
                paths.append(data)
        if paths:
            import json
            from PySide6.QtWidgets import QFileDialog
            dest = QFileDialog.getExistingDirectory(self, '选择下载目录')
            if dest:
                self.download.emit(json.dumps({'paths': paths, 'dest': dest}))

    def _on_selection(self):
        selected = self._tree.selectedItems()
        has_files = any(it.data(0, Qt.UserRole) not in ('..', 'folder') for it in selected)
        self._download_btn.setEnabled(has_files)
        self._delete_btn.setEnabled(has_files)
        self._info.setText(f'选中 {len(selected)} 项')

    def _on_double_click(self, item, col):
        data = item.data(0, Qt.UserRole)
        name = item.text(0)
        ftype = item.text(3)

        is_folder = ftype == '文件夹' or data == 'folder' or data == '..'

        if data == '..':
            parent = os.path.dirname(self._current_path)
            if parent and len(parent) >= len(self._user_root):
                self.navigate.emit(parent)
            else:
                self.show_root()
        elif data and is_folder:
            if data == 'folder':
                full = os.path.join(self._user_root, name) if self._user_root else name
                self.navigate.emit(full)
            else:
                self.navigate.emit(data)
        elif data:
            self.download.emit(data)
