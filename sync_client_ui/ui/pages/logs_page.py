import time

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QHeaderView, QFrame, QLineEdit, QComboBox,
                               QAbstractItemView, QFileDialog, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class LogsPage(QWidget):
    export_requested = Signal()
    clear_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup()

    def _setup(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel('📋 同步日志')
        title.setObjectName('pageTitle')
        header.addWidget(title)
        header.addStretch()

        export_btn = QPushButton('📥 导出日志')
        export_btn.setObjectName('secondaryBtn')
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.clicked.connect(self.export_requested.emit)
        header.addWidget(export_btn)

        clear_btn = QPushButton('🗑 清空日志')
        clear_btn.setObjectName('dangerBtn')
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self.clear_requested.emit)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText('🔍 搜索文件名...')
        self._search.setFixedWidth(250)
        filter_row.addWidget(self._search)

        self._filter = QComboBox()
        self._filter.addItems(['全部', 'INFO', 'ERROR', 'WARN'])
        self._filter.setFixedWidth(100)
        filter_row.addWidget(self._filter)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(['时间', '类型', '操作', '文件', '大小', '详情'])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self._table.setColumnWidth(0, 75)
        self._table.setColumnWidth(1, 80)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(4, 80)
        layout.addWidget(self._table, 1)

        self._count_label = QLabel('共 0 条记录')
        self._count_label.setObjectName('fileInfo')
        layout.addWidget(self._count_label)

    def populate(self, logs):
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(logs))
        for i, log in enumerate(logs):
            ts = log.get('timestamp', 0)
            time_str = time.strftime('%H:%M:%S', time.localtime(ts)) if ts else '-'
            level = log.get('level', '')
            action = log.get('action', '')
            path = log.get('rel_path', '')
            size = log.get('file_size', 0)
            detail = log.get('detail', '')

            level_icons = {'INFO': '✅', 'ERROR': '❌', 'WARN': '⚠️'}
            level_display = f"{level_icons.get(level, '•')} {level}"

            self._table.setItem(i, 0, QTableWidgetItem(time_str))
            self._table.setItem(i, 1, QTableWidgetItem(level_display))
            self._table.setItem(i, 2, QTableWidgetItem(action))
            self._table.setItem(i, 3, QTableWidgetItem(path))
            self._table.setItem(i, 4, QTableWidgetItem(self._fmt(size)))
            self._table.setItem(i, 5, QTableWidgetItem(detail))

        self._count_label.setText(f'共 {len(logs)} 条记录')
        self._table.setUpdatesEnabled(True)

    def _fmt(self, b):
        if b < 1024:
            return f'{b} B'
        elif b < 1024**2:
            return f'{b/1024:.1f} KB'
        elif b < 1024**3:
            return f'{b/1024**2:.1f} MB'
        return f'{b/1024**3:.1f} GB'
