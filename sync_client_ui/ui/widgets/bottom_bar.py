from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt


class BottomBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('bottomBar')
        self._setup()

    def _setup(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(16)

        self._version_label = QLabel('v1.0.0')
        layout.addWidget(self._version_label)

        layout.addStretch()

        self._minimize_btn = QPushButton('🔼 最小化到托盘')
        self._minimize_btn.setObjectName('textBtn')
        self._minimize_btn.setCursor(Qt.PointingHandCursor)
        self._minimize_btn.setFixedHeight(28)
        layout.addWidget(self._minimize_btn)

        self._pause_btn = QPushButton('⏸ 暂停同步')
        self._pause_btn.setObjectName('textBtn')
        self._pause_btn.setCursor(Qt.PointingHandCursor)
        self._pause_btn.setFixedHeight(28)
        layout.addWidget(self._pause_btn)

    @property
    def minimize_btn(self):
        return self._minimize_btn

    @property
    def pause_btn(self):
        return self._pause_btn
