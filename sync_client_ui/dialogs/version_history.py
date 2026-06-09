import json
import threading

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QListWidget, QListWidgetItem,
                               QMessageBox)
from PySide6.QtCore import Qt, Signal


class VersionHistoryDialog(QDialog):
    versions_loaded = Signal(object)

    def __init__(self, api, file_path, parent=None):
        super().__init__(parent)
        self._api = api
        self._file_path = file_path
        self.setWindowTitle('历史版本')
        self.setMinimumSize(500, 400)
        self.resize(550, 450)

        self.versions_loaded.connect(self._on_versions_loaded)

        self._setup()
        self._load()

    def _setup(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f'📋 历史版本: {self._file_path.split("/")[-1]}')
        title.setStyleSheet('font-size: 16px; font-weight: bold;')
        layout.addWidget(title)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        restore_btn = QPushButton('🔄 恢复选中版本')
        restore_btn.setObjectName('primaryBtn')
        restore_btn.clicked.connect(self._on_restore)
        btn_row.addWidget(restore_btn)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _load(self):
        self._list.clear()
        self._list.addItem('正在加载...')

        def worker():
            try:
                data = self._api.get_versions(self._file_path)
                self.versions_loaded.emit(data.get('versions', []))
            except Exception as e:
                self.versions_loaded.emit(str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_versions_loaded(self, result):
        self._list.clear()
        if isinstance(result, str):
            self._list.addItem(f'加载失败: {result}')
            return
        if not result:
            self._list.addItem('暂无历史版本')
            return
        for v in result:
            size = v.get('size', 0)
            if size < 1024:
                size_str = f'{size} B'
            elif size < 1024**2:
                size_str = f'{size/1024:.1f} KB'
            else:
                size_str = f'{size/1024**2:.1f} MB'
            text = f'{v["mtime_str"]}  |  {size_str}  |  {v["name"]}'
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, v['name'])
            item.setToolTip(v['name'])
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_restore(self):
        item = self._list.currentItem()
        if not item:
            QMessageBox.warning(self, '提示', '请先选择一个版本')
            return
        version_name = item.data(Qt.UserRole)
        if not version_name:
            return
        reply = QMessageBox.question(
            self, '确认恢复',
            f'确定要恢复到版本 {version_name}？\n当前文件将被备份为新版本。',
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                ok, data = self._api.restore_version(self._file_path, version_name)
                if ok:
                    QMessageBox.information(self, '成功', '版本恢复成功！')
                    self.accept()
                else:
                    QMessageBox.warning(self, '失败', data)
            except Exception as e:
                QMessageBox.warning(self, '错误', str(e))
