import os

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QFrame, QProgressBar,
                               QSizePolicy)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from ui.animations.fade_in_mixin import slide_in_widget
from ui.animations.progress_animator import AnimatedProgressBar


class TaskCard(QFrame):
    delete_clicked = Signal(str)
    pause_clicked = Signal(str)
    sync_clicked = Signal(str)
    edit_clicked = Signal(str)

    def __init__(self, task_id, local_path, remote_path, strategy, status='idle'):
        super().__init__()
        self._task_id = task_id
        self.setObjectName('taskCard')
        self._setup(local_path, remote_path, strategy, status)

    def _setup(self, local, remote, strategy, status):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        icon = QLabel('📁')
        icon.setStyleSheet('font-size: 18px;')
        row1.addWidget(icon)

        name = QLabel(os.path.basename(local) or local)
        name.setObjectName('taskName')
        row1.addWidget(name)

        row1.addStretch()

        status_colors = {'idle': '#86909C', 'syncing': '#1677FF', 'paused': '#FF7D00', 'error': '#F53F3F', 'completed': '#0FC6C2'}
        c = status_colors.get(status, '#86909C')
        self._status_label = QLabel(status)
        self._status_label.setStyleSheet(f'font-size: 13px; color: {c}; font-weight: 600;')
        row1.addWidget(self._status_label)
        layout.addLayout(row1)

        path = QLabel(f'{local}  →  /{remote}')
        path.setObjectName('taskPath')
        layout.addWidget(path)

        self._progress = AnimatedProgressBar()
        self._progress.setObjectName('taskProgress')
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._detail = QLabel('就绪')
        self._detail.setObjectName('taskDetail')
        self._detail.setStyleSheet('font-size: 13px;')
        layout.addWidget(self._detail)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addStretch()

        sync_btn = QPushButton('📥 立即同步')
        sync_btn.setObjectName('textBtn')
        sync_btn.setCursor(Qt.PointingHandCursor)
        sync_btn.clicked.connect(lambda: self.sync_clicked.emit(self._task_id))
        row2.addWidget(sync_btn)

        pause_btn = QPushButton('⏸ 暂停')
        pause_btn.setObjectName('textBtn')
        pause_btn.setCursor(Qt.PointingHandCursor)
        pause_btn.clicked.connect(lambda: self.pause_clicked.emit(self._task_id))
        row2.addWidget(pause_btn)

        edit_btn = QPushButton('✏️ 编辑')
        edit_btn.setObjectName('textBtn')
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self._task_id))
        row2.addWidget(edit_btn)

        del_btn = QPushButton('🗑️ 删除')
        del_btn.setObjectName('textBtn')
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self._task_id))
        row2.addWidget(del_btn)

        layout.addLayout(row2)

    def set_progress(self, value, text=''):
        self._progress.setValue(value)
        if text:
            self._detail.setText(text)

    def set_status(self, status):
        colors = {'idle': '#86909C', 'syncing': '#1677FF', 'paused': '#FF7D00', 'error': '#F53F3F', 'completed': '#0FC6C2'}
        c = colors.get(status, '#86909C')
        self._status_label.setStyleSheet(f'font-size: 13px; color: {c}; font-weight: 600;')
        self._status_label.setText(status)

    @property
    def task_id(self):
        return self._task_id


class TasksPage(QWidget):
    add_task_requested = Signal()
    task_sync_requested = Signal(str)
    task_pause_requested = Signal(str)
    task_edit_requested = Signal(str)
    task_delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = {}
        self._setup()

    def _setup(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet('background-color: transparent;')

        inner = QWidget()
        inner.setStyleSheet('background-color: transparent;')
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel('📦 备份任务')
        title.setObjectName('pageTitle')
        header.addWidget(title)
        header.addStretch()

        add_btn = QPushButton('添加任务')
        add_btn.setObjectName('primaryBtn')
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(lambda: self.add_task_requested.emit())
        header.addWidget(add_btn)
        layout.addLayout(header)

        self._task_container = QVBoxLayout()
        self._task_container.setSpacing(8)
        layout.addLayout(self._task_container)

        self._empty_widget = QLabel('暂无备份任务\n点击上方"添加任务"按钮创建第一个备份')
        self._empty_widget.setAlignment(Qt.AlignCenter)
        self._empty_widget.setObjectName('emptyHint')
        self._empty_widget.setStyleSheet('font-size: 16px; padding: 80px;')
        self._task_container.addWidget(self._empty_widget)

        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def add_task_card(self, task_id, local_path, remote_path, strategy, status='idle'):
        if task_id in self._cards:
            return
        if self._empty_widget:
            self._task_container.removeWidget(self._empty_widget)
            self._empty_widget.deleteLater()
            self._empty_widget = None

        card = TaskCard(task_id, local_path, remote_path, strategy, status)
        card.delete_clicked.connect(lambda tid: self.task_delete_requested.emit(tid))
        card.pause_clicked.connect(lambda tid: self.task_pause_requested.emit(tid))
        card.sync_clicked.connect(lambda tid: self.task_sync_requested.emit(tid))
        card.edit_clicked.connect(lambda tid: self.task_edit_requested.emit(tid))
        self._cards[task_id] = card
        self._task_container.addWidget(card)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, lambda: slide_in_widget(card, duration=300, direction='down', distance=20))

    def remove_task_card(self, task_id):
        card = self._cards.pop(task_id, None)
        if card:
            self._task_container.removeWidget(card)
            card.deleteLater()
        if not self._cards:
            self._show_empty()

    def _show_empty(self):
        self._empty_widget = QLabel('暂无备份任务\n点击上方"添加任务"按钮创建第一个备份')
        self._empty_widget.setAlignment(Qt.AlignCenter)
        self._empty_widget.setObjectName('emptyHint')
        self._empty_widget.setStyleSheet('font-size: 16px; padding: 80px;')
        self._task_container.addWidget(self._empty_widget)
