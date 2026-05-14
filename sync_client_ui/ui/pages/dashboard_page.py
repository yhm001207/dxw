from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from ui.animations.numeric_animator import NumericAnimator
from ui.animations.fade_in_mixin import slide_in_widget


class StatCard(QFrame):
    def __init__(self, icon, value, label, color='#1677FF'):
        super().__init__()
        self.setProperty('class', 'stat-card')
        self.setMinimumHeight(120)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet('font-size: 26px;')
        layout.addWidget(icon_label)

        self._value = QLabel(value)
        self._value.setObjectName('statValue')
        self._value.setStyleSheet(f'font-size: 30px; font-weight: bold; color: {color};')
        layout.addWidget(self._value)

        self._label = QLabel(label)
        self._label.setObjectName('statLabel')
        layout.addWidget(self._label)

    def set_value(self, v):
        if isinstance(v, str):
            self._value.setText(v)
            return
        if not hasattr(self, '_animator'):
            self._animator = NumericAnimator(self._value)
        self._animator.animate(v, duration=500)


class ActivityItem(QFrame):
    def __init__(self, icon, text, detail, status):
        super().__init__()
        self.setProperty('class', 'activity-item')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(20)
        layout.addWidget(icon_lbl)

        self._text = QLabel(text)
        self._text.setObjectName('activityText')
        layout.addWidget(self._text)

        layout.addStretch()

        self._detail = QLabel(detail)
        self._detail.setObjectName('activityDetail')
        layout.addWidget(self._detail)

        colors = {'完成': '#0FC6C2', '失败': '#F53F3F', '进行中': '#1677FF'}
        c = colors.get(status, '#86909C')
        self._status = QLabel(status)
        self._status.setStyleSheet(f'font-size: 13px; color: {c}; font-weight: 600;')
        layout.addWidget(self._status)


class DashboardPage(QWidget):
    sync_requested = Signal()
    add_task_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._activities = []
        self._setup()

    def _setup(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet('background-color: transparent;')

        inner = QWidget()
        inner.setStyleSheet('background-color: transparent;')
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel('🏠 首页')
        title.setObjectName('pageTitle')
        layout.addWidget(title)

        # Stats row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)

        self._stat_backup = StatCard('📊', '0', '今日成功备份', '#1677FF')
        stats_row.addWidget(self._stat_backup)

        self._stat_fail = StatCard('❌', '0', '今日失败', '#F53F3F')
        stats_row.addWidget(self._stat_fail)

        self._stat_files = StatCard('📦', '0', '总文件数', '#0FC6C2')
        stats_row.addWidget(self._stat_files)

        self._stat_storage = StatCard('💾', '0 B', '已备份数据', '#FF7D00')
        stats_row.addWidget(self._stat_storage)

        layout.addLayout(stats_row)

        # Recent activity
        activity_card = QFrame()
        activity_card.setProperty('class', 'card')
        activity_card.setMinimumHeight(200)
        alayout = QVBoxLayout(activity_card)
        alayout.setContentsMargins(0, 0, 0, 0)
        alayout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 8)
        htitle = QLabel('📋 最近活动')
        htitle.setObjectName('sectionTitle')
        header.addWidget(htitle)
        header.addStretch()
        header.setSpacing(0)
        alayout.addLayout(header)

        self._activity_container = QVBoxLayout()
        self._activity_container.setSpacing(0)
        self._activity_container.setContentsMargins(0, 0, 0, 0)
        alayout.addLayout(self._activity_container)

        self._activity_container.addWidget(self._empty_activity())

        layout.addWidget(activity_card)

        self._activity_card_ref = activity_card

        # Quick actions
        actions_card = QFrame()
        actions_card.setProperty('class', 'card')
        axlayout = QVBoxLayout(actions_card)
        axlayout.setContentsMargins(16, 12, 16, 12)
        axlayout.setSpacing(8)

        axtitle = QLabel('快速操作')
        axtitle.setObjectName('sectionTitle')
        axlayout.addWidget(axtitle)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.sync_btn = QPushButton('立即同步')
        self.sync_btn.setObjectName('primaryBtn')
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        self.sync_btn.clicked.connect(lambda: self.sync_requested.emit())
        btn_row.addWidget(self.sync_btn)

        add_btn = QPushButton('新建任务')
        add_btn.setObjectName('secondaryBtn')
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(lambda: self.add_task_requested.emit())
        btn_row.addWidget(add_btn)

        btn_row.addStretch()
        axlayout.addLayout(btn_row)

        layout.addWidget(actions_card)
        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _empty_activity(self):
        w = QLabel('暂无活动记录，开始备份后将在此显示')
        w.setAlignment(Qt.AlignCenter)
        w.setObjectName('emptyHint')
        w.setStyleSheet('font-size: 14px; padding: 32px;')
        return w

    def add_activity(self, action, path, size, status):
        icons = {'upload': '⬆', 'download': '⬇'}
        icon = icons.get(action, '➡')
        item = ActivityItem(icon, path, size, status)
        if self._activity_container.count() > 0:
            old = self._activity_container.takeAt(0)
            if old and old.widget():
                old.widget().deleteLater()
        self._activity_container.insertWidget(0, item)
        slide_in_widget(item, duration=300, direction='down', distance=15)
        while self._activity_container.count() > 50:
            old = self._activity_container.takeAt(self._activity_container.count() - 1)
            if old and old.widget():
                old.widget().deleteLater()
        slide_in_widget(item, duration=300, direction='down', distance=15)
        while self._activity_container.count() > 50:
            old = self._activity_container.takeAt(0)
            if old and old.widget():
                old.widget().deleteLater()
        self._activity_container.insertWidget(0, item)
        while self._activity_container.count() > 50:
            old = self._activity_container.takeAt(self._activity_container.count() - 1)
            if old and old.widget():
                old.widget().deleteLater()

    def update_stats(self, stats):
        if not stats:
            return
        self._stat_files.set_value(stats.get('total', 0))
        self._stat_backup.set_value(stats.get('sync_count_today', 0))
        self._stat_fail.set_value(stats.get('fail_count_today', 0))
        ts = stats.get('total_size', 0)
        self._stat_storage.set_value(self._fmt(ts))

    def _fmt(self, b):
        if b < 1024:
            return f'{b} B'
        elif b < 1024**2:
            return f'{b/1024:.1f} KB'
        elif b < 1024**3:
            return f'{b/1024**2:.1f} MB'
        return f'{b/1024**3:.1f} GB'
