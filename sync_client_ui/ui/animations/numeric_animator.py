from PySide6.QtCore import QTimer


class NumericAnimator:
    def __init__(self, label_widget):
        self._label = label_widget
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._current = 0
        self._target = 0
        self._start = 0
        self._elapsed = 0
        self._duration = 300
        self._prefix = ''
        self._suffix = ''
        self._on_update = None

    def animate(self, target_value, duration=300, prefix='', suffix='', on_update=None):
        self._timer.stop()

        text = self._label.text()
        nums = ''.join(c for c in text if c.isdigit() or c in '.-')
        try:
            self._current = int(float(nums)) if nums else 0
        except ValueError:
            self._current = 0

        self._target = target_value
        self._start = self._current
        self._duration = duration
        self._elapsed = 0
        self._prefix = prefix
        self._suffix = suffix
        self._on_update = on_update

        self._timer.start(8)

    def _tick(self):
        self._elapsed += 8
        progress = min(1.0, self._elapsed / self._duration)
        eased = self._ease_out_cubic(progress)
        value = self._start + (self._target - self._start) * eased

        if self._on_update:
            self._on_update(round(value))
        else:
            self._label.setText(f'{self._prefix}{round(value)}{self._suffix}')

        if progress >= 1.0:
            self._timer.stop()
            if self._on_update:
                self._on_update(self._target)
            else:
                self._label.setText(f'{self._prefix}{self._target}{self._suffix}')

    def _ease_out_cubic(self, t):
        return 1 - pow(1 - t, 3)

    def stop(self):
        self._timer.stop()
