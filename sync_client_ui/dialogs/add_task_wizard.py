import os

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QComboBox, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QFileDialog,
                               QWizard, QWizardPage, QFormLayout, QMessageBox)
from PySide6.QtCore import Qt


class FolderSelectPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle('选择本地文件夹')
        self.setSubTitle('选择要备份到云端的本地文件夹')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        self._path = QLineEdit()
        self._path.setPlaceholderText('点击浏览选择文件夹...')
        self._path.setMinimumHeight(36)
        layout.addWidget(self._path)

        browse_btn = QPushButton('浏览')
        browse_btn.setFixedHeight(36)
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        hint = QLabel('提示：也可拖拽文件夹到上方输入框')
        layout.addWidget(hint)

        layout.addStretch()

        self.registerField('local_path*', self._path)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, '选择要备份的文件夹')
        if folder:
            self._path.setText(folder)


class StrategyPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle('备份策略')
        self.setSubTitle('设置备份方式与策略')
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._remote = QLineEdit()
        self._remote.setPlaceholderText('backup/my-folder')
        self._remote.setMinimumHeight(36)
        layout.addRow('云端路径:', self._remote)

        self._freq = QComboBox()
        self._freq.setMinimumHeight(36)
        self._freq.addItems(['手动', '实时同步', '按小时', '每日', '每周'])
        layout.addRow('备份频率:', self._freq)

        self._type = QComboBox()
        self._type.setMinimumHeight(36)
        self._type.addItems(['增量备份', '全量备份'])
        layout.addRow('备份方式:', self._type)

        self._retention = QSpinBox()
        self._retention.setRange(1, 365)
        self._retention.setValue(30)
        self._retention.setSuffix(' 天')
        self._retention.setMinimumHeight(36)
        self._retention.setToolTip('超过此天数的历史版本将被自动清理，以节省云端空间')
        layout.addRow('版本保留:', self._retention)

        self._conflict = QComboBox()
        self._conflict.setMinimumHeight(36)
        self._conflict.addItems(['保留较新的版本', '本地优先', '云端优先', '保留两者'])
        layout.addRow('冲突策略:', self._conflict)

        self.registerField('remote_path*', self._remote)
        self.registerField('frequency', self._freq)
        self.registerField('backup_type', self._type)
        self.registerField('retention', self._retention)
        self.registerField('conflict', self._conflict)


class ConfirmPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle('确认')
        self.setSubTitle('确认备份任务配置')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(4)
        self._summary = QLabel('')
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        layout.addStretch()

    def initializePage(self):
        local = self.field('local_path')
        remote = self.field('remote_path')
        freq = self.field('frequency')
        bt = self.field('backup_type')
        ret = self.field('retention')
        conflict_map = ['保留较新的版本', '本地优先', '云端优先', '保留两者']
        conflict = conflict_map[self.field('conflict')]
        freq_map = ['manual', 'realtime', 'hourly', 'daily', 'weekly']
        freq_labels = ['手动', '实时同步', '每小时', '每天', '每周']
        freq_label = freq_labels[freq_map.index(freq)] if freq in freq_map else freq
        self._summary.setText(
            f'本地文件夹: {local}\n'
            f'云端路径: /{remote}\n'
            f'备份频率: {freq_label}\n'
            f'备份方式: {bt}\n'
            f'版本保留: {ret} 天\n'
            f'冲突策略: {conflict}'
        )


class AddTaskWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('添加备份任务')
        self.setMinimumSize(540, 500)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setContentsMargins(0, 0, 0, 0)
        self.addPage(FolderSelectPage())
        self.addPage(StrategyPage())
        self.addPage(ConfirmPage())

        self.button(QWizard.StretchButton).setVisible(False)
        self.button(QWizard.FinishButton).setText('创建任务')
        self.button(QWizard.FinishButton).setMinimumHeight(36)
        self.button(QWizard.CancelButton).setMinimumHeight(36)
        self.button(QWizard.NextButton).setMinimumHeight(36)
        self.button(QWizard.BackButton).setMinimumHeight(36)

    def get_task_data(self):
        freq_map = ['manual', 'realtime', 'hourly', 'daily', 'weekly']
        type_map = ['incremental', 'full']
        conflict_map = ['keep_newer', 'local', 'remote', 'both']
        return {
            'local_path': self.field('local_path'),
            'remote_path': self.field('remote_path'),
            'frequency': freq_map[self.field('frequency')],
            'backup_type': type_map[self.field('backup_type')],
            'retention': self.field('retention'),
            'conflict': conflict_map[self.field('conflict')],
        }
