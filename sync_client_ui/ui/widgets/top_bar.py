from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt


class TopBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('topBar')
        self._setup()

    def _setup(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        self._status_dot = QLabel('●')
        self._status_dot.setStyleSheet('font-size: 12px;')
        layout.addWidget(self._status_dot)

        self._status_label = QLabel('未连接')
        self._status_label.setObjectName('statusLabel')
        layout.addWidget(self._status_label)

        layout.addSpacing(16)

        sep1 = QLabel('|')
        sep1.setStyleSheet('font-size: 16px;')
        layout.addWidget(sep1)

        layout.addSpacing(8)

        speed_icon = QLabel('▲')
        speed_icon.setStyleSheet('font-size: 13px;')
        layout.addWidget(speed_icon)

        self._speed_label = QLabel('0 B/s')
        layout.addWidget(self._speed_label)

        layout.addSpacing(16)

        sep2 = QLabel('|')
        sep2.setStyleSheet('font-size: 16px;')
        layout.addWidget(sep2)

        layout.addSpacing(8)

        task_icon = QLabel('⏳')
        task_icon.setStyleSheet('font-size: 13px;')
        layout.addWidget(task_icon)

        self._task_label = QLabel('无进行中任务')
        layout.addWidget(self._task_label)

        layout.addStretch()

        self._quota_label = QLabel('💾 -')
        self._quota_label.setStyleSheet('font-size: 14px;')
        self._quota_label.setToolTip('云端存储空间用量（仅显示，不可点击）')
        layout.addWidget(self._quota_label)

        layout.addSpacing(8)

        self._theme_btn = QLabel('🌙')
        self._theme_btn.setStyleSheet('font-size: 16px; padding: 4px;')
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self._theme_btn)

    def set_status(self, status):
        colors = {
            'connected': '#0FC6C2',
            'syncing': '#1677FF',
            'paused': '#FF7D00',
            'error': '#F53F3F',
            'disconnected': '#86909C',
        }
        labels = {
            'connected': '已连接',
            'syncing': '同步中...',
            'paused': '已暂停',
            'error': '连接错误',
            'disconnected': '未连接',
            'idle': '空闲',
        }
        color = colors.get(status, '#9CA3AF')
        label = labels.get(status, status)
        self._status_dot.setStyleSheet(f'color: {color}; font-size: 12px;')
        self._status_label.setText(label)

    def set_speed(self, speed_text):
        self._speed_label.setText(speed_text)

    def set_task(self, task_text):
        self._task_label.setText(task_text)

    def set_quota(self, used, total):
        self._quota_label.setText(f'💾 {self._fmt_bytes(used)} / {self._fmt_bytes(total)}')

    def _update_theme_icon(self):
        from ui.theme import current_mode
        m = current_mode()
        self._theme_btn.setText({'light': '☀️', 'dark': '🌙', 'sci_fi': '💠'}.get(m, '🌙'))

    def _fmt_bytes(self, b):
        if b < 1024:
            return f'{b} B'
        elif b < 1024**2:
            return f'{b/1024:.1f} KB'
        elif b < 1024**3:
            return f'{b/1024**2:.1f} MB'
        return f'{b/1024**3:.1f} GB'
