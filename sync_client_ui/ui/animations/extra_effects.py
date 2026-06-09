from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton


class StatusDotsAnimation:
    def __init__(self, label: QLabel, base_text='同步中'):
        self._label = label
        self._base = base_text
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._count = 0
        self._running = False

    def start(self):
        self._count = 0
        self._running = True
        self._label.setText(f'{self._base}...')
        self._timer.start(400)

    def stop(self):
        self._running = False
        self._timer.stop()
        self._label.setText(self._base)

    def _tick(self):
        if not self._running:
            return
        self._count = (self._count + 1) % 4
        dots = '.' * self._count
        self._label.setText(f'{self._base}{dots}')


class ButtonBreathing:
    def __init__(self, button: QPushButton):
        self._btn = button
        self._timer = QTimer()
        self._timer.timeout.connect(self._toggle)
        self._on = True
        self._base_style = ''

    def start(self):
        self._base_style = self._btn.styleSheet()
        self._on = True
        self._apply()
        self._timer.start(600)

    def stop(self):
        self._timer.stop()
        try:
            self._btn.setStyleSheet(self._base_style)
        except Exception:
            pass

    def _toggle(self):
        self._on = not self._on
        self._apply()

    def _apply(self):
        try:
            if self._on:
                self._btn.setStyleSheet(
                    self._base_style + 'QPushButton { border: 2px solid rgba(0,238,255,0.6) !important; }'
                )
            else:
                self._btn.setStyleSheet(
                    self._base_style + 'QPushButton { border: 2px solid rgba(0,238,255,0.2) !important; }'
                )
        except Exception:
            pass


class DragHighlightEffect:
    def __init__(self, widget):
        self._widget = widget
        self._timer = QTimer()
        self._timer.timeout.connect(self._toggle)
        self._on = False
        self._active = False
        self._base_style = ''

    def activate(self):
        if self._active:
            return
        self._active = True
        self._base_style = self._widget.styleSheet()
        self._on = True
        self._apply()
        self._timer.start(500)

    def deactivate(self):
        self._active = False
        self._timer.stop()
        try:
            self._widget.setStyleSheet(self._base_style)
        except Exception:
            pass

    def _toggle(self):
        self._on = not self._on
        self._apply()

    def _apply(self):
        try:
            if self._on:
                self._widget.setStyleSheet(
                    self._base_style + 'QWidget { border: 2px dashed #1677FF !important; background: rgba(22,119,255,0.05) !important; }'
                )
            else:
                self._widget.setStyleSheet(
                    self._base_style + 'QWidget { border: 2px dashed #4096FF !important; background: rgba(22,119,255,0.02) !important; }'
                )
        except Exception:
            pass


class EmptyStatePulse:
    def __init__(self, label: QLabel):
        self._label = label
        self._timer = QTimer()
        self._timer.timeout.connect(self._toggle)
        self._on = True

    def start(self):
        self._on = True
        self._timer.start(2000)

    def stop(self):
        self._timer.stop()
        try:
            self._label.setStyleSheet('font-size: 14px; padding: 32px;')
        except Exception:
            pass

    def _toggle(self):
        self._on = not self._on
        try:
            if self._on:
                self._label.setStyleSheet('font-size: 14px; padding: 32px; color: #86909C;')
            else:
                self._label.setStyleSheet('font-size: 14px; padding: 32px; color: #A9AEB8;')
        except Exception:
            pass
