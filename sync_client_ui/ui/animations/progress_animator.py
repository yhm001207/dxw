from PySide6.QtWidgets import QProgressBar
from PySide6.QtCore import QPropertyAnimation, QEasingCurve


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim_value = 0

    def setValue(self, value: int):
        current = self._anim_value if hasattr(self, '_anim_value') else self.value()
        target = max(0, min(100, value))

        if target == current:
            return

        self._anim_value = target

        anim = QPropertyAnimation(self, b"value")
        anim.setDuration(200)
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
