from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt


class ShimmerBlock(QWidget):
    def __init__(self, width, height, rounded=True, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._setup(rounded)

    def _setup(self, rounded):
        try:
            from ui.theme import is_dark
            dark = is_dark()
        except Exception:
            dark = False
        bg = '#2A2A2A' if dark else '#E5E6EB'
        r = 6 if rounded else 2
        self.setStyleSheet(f'background: {bg}; border-radius: {r}px;')


class SkeletonScreen(QWidget):
    CARD = 'card'
    LIST = 'list'
    TABLE = 'table'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._blocks = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _clear(self):
        for b in self._blocks:
            b.setParent(None)
            b.deleteLater()
        self._blocks.clear()

    def setup_card_skeleton(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = ShimmerBlock(120, 24)
        layout.addWidget(title)
        self._blocks.append(title)

        row = QHBoxLayout()
        row.setSpacing(12)
        for _ in range(4):
            card = ShimmerBlock(200, 120)
            row.addWidget(card)
            self._blocks.append(card)
        layout.addLayout(row)

        card2 = ShimmerBlock(800, 200)
        layout.addWidget(card2)
        self._blocks.append(card2)

        card3 = ShimmerBlock(800, 100)
        layout.addWidget(card3)
        self._blocks.append(card3)

        layout.addStretch()

    def setup_list_skeleton(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = ShimmerBlock(120, 24)
        layout.addWidget(title)
        self._blocks.append(title)

        for _ in range(5):
            row = ShimmerBlock(800, 48)
            layout.addWidget(row)
            self._blocks.append(row)

        layout.addStretch()

    def setup_table_skeleton(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        title = ShimmerBlock(120, 24)
        layout.addWidget(title)
        self._blocks.append(title)

        header = ShimmerBlock(800, 36)
        layout.addWidget(header)
        self._blocks.append(header)

        for _ in range(6):
            row = ShimmerBlock(800, 40)
            layout.addWidget(row)
            self._blocks.append(row)

        layout.addStretch()

    def show_skeleton(self, skeleton_type='card'):
        self._clear()
        if skeleton_type == self.CARD:
            self.setup_card_skeleton()
        elif skeleton_type == self.LIST:
            self.setup_list_skeleton()
        elif skeleton_type == self.TABLE:
            self.setup_table_skeleton()
        self.show()
        self.raise_()

    def hide_skeleton(self, on_finish=None):
        self._clear()
        self.hide()
        if on_finish:
            on_finish()
