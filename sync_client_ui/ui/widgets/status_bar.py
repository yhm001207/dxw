from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PySide6.QtCore import Qt


class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('statusBar')
        self._setup()

    def _setup(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        self._sync_time = QLabel('上次同步: --')
        layout.addWidget(self._sync_time)

        self._file_count = QLabel('文件数: --')
        layout.addWidget(self._file_count)

        self._progress = QLabel('完成: --%')
        layout.addStretch()
        layout.addWidget(self._progress)

    def update_stats(self, stats):
        if not stats:
            return
        total = stats.get('total', 0)
        synced = stats.get('synced', 0)
        self._file_count.setText(f'文件数: {total}')
        if total > 0:
            pct = round(synced / total * 100)
            self._progress.setText(f'完成: {pct}%')

    def set_sync_time(self, text):
        self._sync_time.setText(f'上次同步: {text}')
