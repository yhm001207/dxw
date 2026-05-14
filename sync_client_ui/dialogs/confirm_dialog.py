from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt


class ConfirmDialog(QDialog):
    def __init__(self, title, message, confirm_text='确认', cancel_text='取消', parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        msg = QLabel(message)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        layout.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton(cancel_text)
        cancel_btn.setObjectName('secondaryBtn')
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton(confirm_text)
        confirm_btn.setObjectName('primaryBtn')
        confirm_btn.setFixedHeight(36)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)
