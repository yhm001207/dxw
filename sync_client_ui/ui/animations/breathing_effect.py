from PySide6.QtCore import QTimer


class BreathingEffect:
    def __init__(self, target):
        self._target = target
        self._timer = QTimer()
        self._timer.timeout.connect(self._toggle)
        self._on = True
        self._state = 'disconnected'
        self._color_a = '#86909C'
        self._color_b = '#A9A9A9'

    def set_state(self, state):
        self._state = state
        colors = {
            'connected': ('#0FC6C2', '#7FF0ED'),
            'syncing': ('#1677FF', '#6AA5FF'),
            'paused': ('#FF7D00', '#FFB366'),
            'error': ('#F53F3F', '#FA8080'),
            'disconnected': ('#86909C', '#A9A9A9'),
            'idle': ('#0FC6C2', '#7FF0ED'),
        }
        c = colors.get(state, colors['disconnected'])
        self._color_a = c[0]
        self._color_b = c[1]

        if state in ('syncing', 'error'):
            self._timer.start(120)
        elif state == 'connected':
            self._timer.start(600)
        elif state == 'paused':
            self._timer.start(800)
        else:
            self._timer.stop()
            self._apply(self._color_a)

    def _toggle(self):
        self._on = not self._on
        self._apply(self._color_a if self._on else self._color_b)

    def _apply(self, color):
        try:
            self._target.setStyleSheet(f'color: {color}; font-size: 12px;')
        except Exception:
            pass

    def stop(self):
        self._timer.stop()
