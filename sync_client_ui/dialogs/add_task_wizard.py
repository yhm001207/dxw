import os

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QComboBox, QSpinBox,
                               QStackedWidget, QWidget, QFileDialog, QFormLayout)
from PySide6.QtCore import Qt


class AddTaskDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('添加备份任务')
        self.setMinimumSize(620, 680)
        self.resize(620, 680)

        self._local_path = ''
        self._remote_path = ''
        self._frequency = 0
        self._backup_type = 0
        self._retention = 5
        self._conflict = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        title = QLabel('添加备份任务')
        title.setStyleSheet('font-size: 20px; font-weight: bold;')
        layout.addWidget(title)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_page1())
        self._stack.addWidget(self._build_page2())
        self._stack.addWidget(self._build_page3())
        layout.addWidget(self._stack, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._back_btn = QPushButton('上一步')
        self._back_btn.setFixedHeight(36)
        self._back_btn.clicked.connect(self._go_back)
        btn_row.addWidget(self._back_btn)
        self._next_btn = QPushButton('下一步')
        self._next_btn.setObjectName('primaryBtn')
        self._next_btn.setFixedHeight(36)
        self._next_btn.clicked.connect(self._go_next)
        btn_row.addWidget(self._next_btn)
        cancel_btn = QPushButton('取消')
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._update_buttons()

    def _build_page1(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        stitle = QLabel('选择本地文件夹')
        stitle.setStyleSheet('font-size: 16px; font-weight: 600;')
        layout.addWidget(stitle)
        ssub = QLabel('选择要备份到云端的本地文件夹')
        ssub.setStyleSheet('color: #86909C;')
        layout.addWidget(ssub)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText('点击浏览选择文件夹...')
        self._path_edit.setMinimumHeight(36)
        layout.addWidget(self._path_edit)

        browse_btn = QPushButton('浏览')
        browse_btn.setFixedHeight(36)
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        hint = QLabel('提示：也可拖拽文件夹到上方输入框')
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def _build_page2(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        stitle = QLabel('备份策略')
        stitle.setStyleSheet('font-size: 16px; font-weight: 600;')
        layout.addWidget(stitle)
        ssub = QLabel('设置备份方式与策略')
        ssub.setStyleSheet('color: #86909C;')
        layout.addWidget(ssub)

        form = QFormLayout()
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._remote_edit = QLineEdit()
        self._remote_edit.setPlaceholderText('backup/my-folder')
        self._remote_edit.setMinimumHeight(36)
        form.addRow('云端路径:', self._remote_edit)

        self._freq_combo = QComboBox()
        self._freq_combo.setMinimumHeight(36)
        self._freq_combo.addItems(['手动', '实时同步', '按小时', '每日', '每周'])
        form.addRow('备份频率:', self._freq_combo)

        self._type_combo = QComboBox()
        self._type_combo.setMinimumHeight(36)
        self._type_combo.addItems(['增量备份', '全量备份'])
        form.addRow('备份方式:', self._type_combo)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)

        self._retention_mode = QComboBox()
        self._retention_mode.setMinimumHeight(36)
        self._retention_mode.addItems(['按版本数', '按天数'])
        self._retention_mode.currentIndexChanged.connect(self._on_retention_mode_changed)
        mode_row.addWidget(self._retention_mode)

        self._retention_spin = QSpinBox()
        self._retention_spin.setRange(0, 99)
        self._retention_spin.setValue(5)
        self._retention_spin.setSuffix(' 个版本')
        self._retention_spin.setMinimumHeight(36)
        self._retention_spin.setToolTip('保留最近几个历史版本，设为0则不保留历史版本')
        mode_row.addWidget(self._retention_spin)

        self._retention_days_spin = QSpinBox()
        self._retention_days_spin.setRange(1, 36500)
        self._retention_days_spin.setValue(30)
        self._retention_days_spin.setSuffix(' 天')
        self._retention_days_spin.setMinimumHeight(36)
        self._retention_days_spin.setToolTip('保留最近多少天内的历史版本，超期自动清理')
        self._retention_days_spin.hide()
        mode_row.addWidget(self._retention_days_spin)

        mode_row.addStretch()
        form.addRow('版本保留:', mode_row)

        self._conflict_combo = QComboBox()
        self._conflict_combo.setMinimumHeight(36)
        self._conflict_combo.addItems(['保留较新的版本', '本地优先', '云端优先', '保留两者'])
        form.addRow('冲突策略:', self._conflict_combo)

        layout.addLayout(form)
        layout.addStretch()
        return page

    def _build_page3(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        stitle = QLabel('确认')
        stitle.setStyleSheet('font-size: 16px; font-weight: 600;')
        layout.addWidget(stitle)
        ssub = QLabel('确认备份任务配置')
        ssub.setStyleSheet('color: #86909C;')
        layout.addWidget(ssub)

        self._summary = QLabel('')
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        layout.addStretch()
        return page

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, '选择要备份的文件夹')
        if folder:
            self._path_edit.setText(folder)

    def _update_buttons(self):
        idx = self._stack.currentIndex()
        self._back_btn.setVisible(idx > 0)
        if idx == 2:
            self._next_btn.setText('创建任务')
            self._next_btn.setObjectName('primaryBtn')
        else:
            self._next_btn.setText('下一步')
            self._next_btn.setObjectName('primaryBtn')
        self._next_btn.style().unpolish(self._next_btn)
        self._next_btn.style().polish(self._next_btn)

    def _go_back(self):
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._update_buttons()

    def _go_next(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            if not self._path_edit.text().strip():
                return
            self._stack.setCurrentIndex(1)
            self._update_buttons()
        elif idx == 1:
            if not self._remote_edit.text().strip():
                return
            self._update_summary()
            self._stack.setCurrentIndex(2)
            self._update_buttons()
        elif idx == 2:
            self._collect_data()
            self.accept()

    def _on_retention_mode_changed(self, idx):
        self._retention_spin.setVisible(idx == 0)
        self._retention_days_spin.setVisible(idx == 1)

    def _update_summary(self):
        freq_labels = ["手动", "实时同步", "每小时", "每日", "每周"]
        type_labels = ["增量备份", "全量备份"]
        conflict_labels = ["保留较新的版本", "本地优先", "云端优先", "保留两者"]
        parts = [
            f"本地文件夹: {self._path_edit.text().strip()}",
            f"云端路径: /{self._remote_edit.text().strip()}",
            f"备份频率: {freq_labels[self._freq_combo.currentIndex()]}",
            f"备份方式: {type_labels[self._type_combo.currentIndex()]}",
        ]
        if self._retention_mode.currentIndex() == 0:
            parts.append(f"版本保留: {self._retention_spin.value()} 个版本")
        else:
            parts.append(f"版本保留: {self._retention_days_spin.value()} 天")
        parts.append(f"冲突策略: {conflict_labels[self._conflict_combo.currentIndex()]}")
        self._summary.setText("\n".join(parts))

    def _collect_data(self):
        self._local_path = self._path_edit.text().strip()
        self._remote_path = self._remote_edit.text().strip()
        self._frequency = self._freq_combo.currentIndex()
        self._backup_type = self._type_combo.currentIndex()
        self._retention = self._retention_spin.value()
        self._retention_days = self._retention_days_spin.value()
        self._retention_mode_val = 'count' if self._retention_mode.currentIndex() == 0 else 'days'
        self._conflict = self._conflict_combo.currentIndex()

    def get_task_data(self):
        freq_map = ['manual', 'realtime', 'hourly', 'daily', 'weekly']
        type_map = ['incremental', 'full']
        conflict_map = ['keep_newer', 'local', 'remote', 'both']
        return {
            'local_path': self._local_path,
            'remote_path': self._remote_path,
            'frequency': freq_map[self._frequency],
            'backup_type': type_map[self._backup_type],
            'version_retention_mode': self._retention_mode_val,
            'version_retention_count': self._retention,
            'version_retention_days': self._retention_days,
            'conflict': conflict_map[self._conflict],
        }
