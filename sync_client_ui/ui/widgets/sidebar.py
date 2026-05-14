from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Signal, Qt


NAV_ITEMS = [
    ('dashboard', '🏠', '首页'),
    ('tasks', '📦', '备份任务'),
    ('files', '☁️', '文件管理'),
    ('logs', '📋', '日志'),
    ('settings', '⚙️', '设置'),
]


class NavButton(QPushButton):
    def __init__(self, page_id, icon, text):
        super().__init__(f'  {icon}  {text}')
        self._page_id = page_id
        self.setObjectName('navButton')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)

    def page_id(self):
        return self._page_id


class Sidebar(QWidget):
    page_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('sidebar')
        self.setMinimumWidth(180)
        self.setMaximumWidth(240)
        self._buttons = []
        self._setup()

    def _setup(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel('DXW 备份客户端')
        title.setObjectName('appTitle')
        layout.addWidget(title)

        subtitle = QLabel('文件自动备份工具')
        subtitle.setObjectName('appSubtitle')
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        for page_id, icon, text in NAV_ITEMS:
            btn = NavButton(page_id, icon, text)
            btn.clicked.connect(self._on_nav_click)
            self._buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        ver = QLabel('v1.0.0')
        ver.setAlignment(Qt.AlignCenter)
        ver.setObjectName('bottomVersion')
        layout.addWidget(ver)

    def _on_nav_click(self):
        btn = self.sender()
        if btn:
            self.set_active(btn.page_id())
            self.page_changed.emit(btn.page_id())

    def set_active(self, page_id):
        for btn in self._buttons:
            btn.setChecked(btn.page_id() == page_id)

    def get_active_id(self):
        for btn in self._buttons:
            if btn.isChecked():
                return btn.page_id()
        return 'dashboard'
