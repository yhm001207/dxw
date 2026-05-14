from PySide6.QtWidgets import QStackedWidget
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QPoint


class AnimatedStackedWidget(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._animating = False
        self._duration = 250

    def setCurrentIndex(self, index: int):
        if self._animating or index == self.currentIndex():
            super().setCurrentIndex(index)
            return

        current = self.currentWidget()
        if not current:
            super().setCurrentIndex(index)
            return

        next_w = self.widget(index)
        if not next_w:
            super().setCurrentIndex(index)
            return

        self._animating = True
        w = max(self.width(), 800)

        next_w.move(w, 0)
        super().setCurrentIndex(index)

        slide_in = QPropertyAnimation(next_w, b"pos")
        slide_in.setDuration(self._duration)
        slide_in.setStartValue(QPoint(w, 0))
        slide_in.setEndValue(QPoint(0, 0))
        slide_in.setEasingCurve(QEasingCurve.OutQuint)
        slide_in.finished.connect(self._on_done)

        slide_out = QPropertyAnimation(current, b"pos")
        slide_out.setDuration(self._duration)
        slide_out.setStartValue(QPoint(0, 0))
        slide_out.setEndValue(QPoint(-w // 3, 0))
        slide_out.setEasingCurve(QEasingCurve.OutQuint)
        slide_out.start()
        current._slideout = slide_out

        slide_in.start()
        next_w._slidein = slide_in

    def _on_done(self):
        self._animating = False
        for i in range(self.count()):
            w = self.widget(i)
            if w:
                w.move(0, 0)

    def set_duration(self, ms: int):
        self._duration = ms
