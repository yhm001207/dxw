from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QPoint


def slide_in_widget(widget, duration=200, direction='up', distance=15, on_finish=None):
    parent = widget.parentWidget()
    if not parent:
        if on_finish:
            on_finish()
        return

    orig_pos = widget.pos()
    start_pos = QPoint(orig_pos.x(), orig_pos.y() - distance) if direction == 'up' else QPoint(orig_pos.x() + distance, orig_pos.y())
    widget.move(start_pos)

    anim = QPropertyAnimation(widget, b"pos")
    anim.setDuration(duration)
    anim.setStartValue(start_pos)
    anim.setEndValue(orig_pos)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    if on_finish:
        anim.finished.connect(on_finish)

    anim.start()


def fade_out_widget(widget, duration=200, direction='up', distance=15, on_finish=None):
    orig_pos = widget.pos()
    end_pos = QPoint(orig_pos.x(), orig_pos.y() - distance) if direction == 'up' else QPoint(orig_pos.x() + distance, orig_pos.y())

    anim = QPropertyAnimation(widget, b"pos")
    anim.setDuration(duration)
    anim.setStartValue(orig_pos)
    anim.setEndValue(end_pos)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    if on_finish:
        anim.finished.connect(on_finish)

    anim.start()
