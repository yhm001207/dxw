from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QApplication
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer, QPoint, Qt


class ToastWidget(QWidget):
    def __init__(self, message, toast_type='info', duration=3000, parent=None):
        super().__init__(parent)
        self._type = toast_type
        self._duration = duration
        self._setup(message)

    def _setup(self, message):
        style_map = {
            'success': {'bg': '#E6FFFB', 'border': '#0FC6C2', 'icon': '✅'},
            'error': {'bg': '#FFF1F0', 'border': '#F53F3F', 'icon': '❌'},
            'warning': {'bg': '#FFF7E6', 'border': '#FF7D00', 'icon': '⚠️'},
            'info': {'bg': '#E6F4FF', 'border': '#1677FF', 'icon': 'ℹ️'},
        }

        cfg = style_map.get(self._type, style_map['info'])

        self.setFixedWidth(340)
        self.setStyleSheet(f"""
            background: {cfg['bg']};
            border: 1px solid {cfg['border']};
            border-radius: 8px;
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        icon = QLabel(cfg['icon'])
        icon.setStyleSheet('font-size: 16px; background: transparent;')
        layout.addWidget(icon)

        text = QLabel(message)
        text.setWordWrap(True)
        text.setStyleSheet('font-size: 13px; background: transparent;')
        layout.addWidget(text, 1)

        close_btn = QPushButton('✕')
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet('background: transparent; border: none; font-size: 12px;')
        close_btn.clicked.connect(self._on_close)
        layout.addWidget(close_btn)

    def show_with_animation(self, index=0):
        parent = self.parent()
        if not parent:
            return

        self.adjustSize()
        pw = parent.width()
        base_y = 50 + index * (self.height() + 8)
        self.move(pw, base_y)
        self.show()
        self.raise_()

        target_x = pw - self.width() - 20
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(200)
        anim.setStartValue(QPoint(pw, base_y))
        anim.setEndValue(QPoint(target_x, base_y))
        anim.setEasingCurve(QEasingCurve.OutBack)
        anim.start()
        self._enter_anim = anim

        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._on_close)
        self._close_timer.start(self._duration)

    def _on_close(self):
        if hasattr(self, '_closing') and self._closing:
            return
        self._closing = True
        if hasattr(self, '_close_timer') and self._close_timer:
            self._close_timer.stop()

        try:
            ToastManager._instance._toasts = [t for t in ToastManager._instance._toasts if t is not self]
        except Exception:
            pass

        pos = self.pos()
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(150)
        anim.setStartValue(pos)
        anim.setEndValue(QPoint(pos.x(), pos.y() - 8))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self.deleteLater)
        anim.start()
        self._exit_anim = anim


class ToastManager:
    _instance = None
    _toasts = []

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ToastManager()
        return cls._instance

    def _get_parent(self, parent):
        if parent:
            return parent
        app = QApplication.instance()
        if app:
            for w in app.topLevelWidgets():
                if w.isVisible():
                    return w
        return None

    def _cleanup(self):
        alive = []
        for t in self._toasts:
            try:
                if t.isVisible():
                    alive.append(t)
            except RuntimeError:
                pass
        self._toasts = alive

    def show(self, message, toast_type='info', duration=3000, parent=None):
        parent = self._get_parent(parent)
        if not parent:
            return
        self._cleanup()
        toast = ToastWidget(message, toast_type, duration, parent)
        self._toasts.append(toast)
        toast.show_with_animation(index=len(self._toasts) - 1)

    def success(self, message, duration=3000, parent=None):
        self.show(message, 'success', duration, parent)

    def error(self, message, duration=4000, parent=None):
        self.show(message, 'error', duration, parent)

    def warning(self, message, duration=3500, parent=None):
        self.show(message, 'warning', duration, parent)

    def info(self, message, duration=3000, parent=None):
        self.show(message, 'info', duration, parent)
