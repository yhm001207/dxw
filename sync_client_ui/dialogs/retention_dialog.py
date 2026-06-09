
import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QComboBox, QSpinBox, QFormLayout)
from PySide6.QtCore import Qt


class RetentionDialog(QDialog):
    def __init__(self, task_name, current_mode="count", current_count=5, current_days=30, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"版本保留设置 - {task_name}")
        self.setMinimumSize(400, 250)
        self.resize(420, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel(f"📂 版本保留设置\n任务: {task_name}")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        desc = QLabel("设置该任务保留历史版本的方式和数量")
        desc.setStyleSheet("color: #86909C; font-size: 13px;")
        layout.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)

        self._retention_mode = QComboBox()
        self._retention_mode.setMinimumHeight(36)
        self._retention_mode.addItems(["按版本数", "按天数"])
        self._retention_mode.setCurrentIndex(0 if current_mode == "count" else 1)
        self._retention_mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._retention_mode)

        self._retention_spin = QSpinBox()
        self._retention_spin.setRange(0, 99)
        self._retention_spin.setValue(current_count)
        self._retention_spin.setSuffix(" 个版本")
        self._retention_spin.setMinimumHeight(36)
        if current_mode != "count":
            self._retention_spin.hide()

        self._retention_days_spin = QSpinBox()
        self._retention_days_spin.setRange(0, 365)
        self._retention_days_spin.setValue(current_days)
        self._retention_days_spin.setSuffix(" 天")
        self._retention_days_spin.setMinimumHeight(36)
        if current_mode != "days":
            self._retention_days_spin.hide()

        mode_row.addWidget(self._retention_spin)
        mode_row.addWidget(self._retention_days_spin)
        mode_row.addStretch()
        form.addRow("保留模式:", mode_row)

        layout.addLayout(form)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.setFixedHeight(36)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _on_mode_changed(self, idx):
        self._retention_spin.setVisible(idx == 0)
        self._retention_days_spin.setVisible(idx == 1)

    def get_retention_data(self):
        return {
            "version_retention_mode": "count" if self._retention_mode.currentIndex() == 0 else "days",
            "version_retention_count": self._retention_spin.value(),
            "version_retention_days": self._retention_days_spin.value(),
        }
