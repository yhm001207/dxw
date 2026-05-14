from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QLineEdit, QSpinBox, QComboBox,
                               QCheckBox, QScrollArea, QFrame, QGroupBox,
                                QFormLayout, QFileDialog,
                                QGridLayout)
from PySide6.QtCore import Qt, Signal


class SettingsPage(QWidget):
    test_connection_requested = Signal()
    save_requested = Signal()
    browse_folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._setup()
        self._disable_combo_wheel()

    def _disable_combo_wheel(self):
        for child in self.findChildren(QComboBox):
            child.setFocusPolicy(Qt.StrongFocus)

    def _info(self, text):
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(text)
        layout.addWidget(label)
        icon = QLabel('ⓘ')
        icon.setToolTip(text)
        icon.setObjectName('infoIcon')
        icon.setStyleSheet('font-size: 15px; cursor: pointer;')
        layout.addWidget(icon)
        layout.addStretch()
        return w

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

        title = QLabel('⚙️ 设置')
        title.setObjectName('pageTitle')
        layout.addWidget(title)

        # Server group
        server_group = QGroupBox('服务器')
        sform = QFormLayout(server_group)
        sform.setSpacing(8)

        self._server_url = QLineEdit('http://localhost:5000')
        self._server_url.setPlaceholderText('http://your-server:5000')
        self._server_url.setToolTip('DXW 服务器的完整地址，格式：http://ip:端口')
        sform.addRow(self._info('服务器地址:'), self._server_url)

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(5000)
        self._port.setToolTip('服务器监听的端口号，默认 5000')
        sform.addRow(self._info('端口号:'), self._port)

        self._username = QLineEdit()
        self._username.setPlaceholderText('用户名')
        self._username.setToolTip('登录 DXW 服务器的用户名')
        sform.addRow(self._info('用户名:'), self._username)

        self._password = QLineEdit()
        self._password.setPlaceholderText('密码')
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setToolTip('登录 DXW 服务器的密码')
        sform.addRow(self._info('密码:'), self._password)

        test_row = QHBoxLayout()
        self._test_btn = QPushButton('🧪 测试连接')
        self._test_btn.setObjectName('primaryBtn')
        self._test_btn.setCursor(Qt.PointingHandCursor)
        self._test_btn.setToolTip('验证服务器地址和账号密码是否正确')
        test_row.addWidget(self._test_btn)

        self._test_result = QLabel('')
        test_row.addWidget(self._test_result)
        test_row.addStretch()
        sform.addRow('', test_row)

        layout.addWidget(server_group)

        # Backup group
        backup_group = QGroupBox('备份设置')
        bform = QFormLayout(backup_group)
        bform.setSpacing(8)

        self._backup_freq = QComboBox()
        self._backup_freq.addItems(['手动', '实时同步', '按小时', '每日', '每周'])
        self._backup_freq.setToolTip('选择备份的执行频率：手动=仅手动触发，实时=文件变更即备份，按小时/每日/每周=定时自动备份')
        bform.addRow(self._info('备份频率:'), self._backup_freq)

        self._interval = QSpinBox()
        self._interval.setRange(1, 1440)
        self._interval.setValue(60)
        self._interval.setSuffix(' 分钟')
        self._interval.setToolTip('两次自动备份之间的间隔时间（分钟），仅对定时备份模式有效')
        bform.addRow(self._info('同步间隔:'), self._interval)

        self._backup_type = QComboBox()
        self._backup_type.addItems(['增量备份', '全量备份'])
        self._backup_type.setToolTip('增量备份=只上传新增或修改的文件（更快），全量备份=每次都重新上传所有文件（更完整）')
        bform.addRow(self._info('备份方式:'), self._backup_type)

        self._conflict = QComboBox()
        self._conflict.addItems(['保留较新的版本', '本地优先', '云端优先', '保留两者'])
        self._conflict.setToolTip('当本地和云端文件同时修改时的处理策略：保留较新的版本/本地优先/云端优先/保留两者')
        bform.addRow(self._info('冲突处理:'), self._conflict)

        self._retry = QSpinBox()
        self._retry.setRange(0, 10)
        self._retry.setValue(3)
        self._retry.setSuffix(' 次')
        self._retry.setToolTip('备份失败时自动重试的次数，设为 0 则不重试')
        bform.addRow(self._info('失败重试:'), self._retry)

        layout.addWidget(backup_group)

        # Network group
        net_group = QGroupBox('网络')
        nform = QFormLayout(net_group)
        nform.setSpacing(8)

        self._upload_limit = QSpinBox()
        self._upload_limit.setRange(0, 1000)
        self._upload_limit.setValue(0)
        self._upload_limit.setSuffix(' MB/s')
        self._upload_limit.setSpecialValueText('不限速')
        self._upload_limit.setToolTip('限制上传速度，避免占用全部带宽，设为不限速以获得最快备份速度')
        nform.addRow(self._info('上传限速:'), self._upload_limit)

        self._download_limit = QSpinBox()
        self._download_limit.setRange(0, 1000)
        self._download_limit.setValue(0)
        self._download_limit.setSuffix(' MB/s')
        self._download_limit.setSpecialValueText('不限速')
        self._download_limit.setToolTip('限制下载速度，设为不限速以获得最快恢复速度')
        nform.addRow(self._info('下载限速:'), self._download_limit)

        self._proxy = QLineEdit()
        self._proxy.setPlaceholderText('http://proxy:port (可选)')
        self._proxy.setToolTip('如果服务器需要通过代理访问，在此输入代理地址，格式：http://ip:端口')
        nform.addRow(self._info('代理:'), self._proxy)

        layout.addWidget(net_group)

        # General group
        gen_group = QGroupBox('通用')
        gform = QFormLayout(gen_group)
        gform.setSpacing(8)

        self._auto_start = QCheckBox('开机自启')
        self._auto_start.setToolTip('开启后，Windows 启动时自动运行本客户端')
        gform.addRow(self._info(''), self._auto_start)

        self._minimize_tray = QCheckBox('最小化到托盘')
        self._minimize_tray.setChecked(True)
        self._minimize_tray.setToolTip('关闭窗口时最小化到系统托盘，而不是完全退出')
        gform.addRow(self._info(''), self._minimize_tray)

        self._auto_download = QCheckBox('自动从服务器下载文件')
        self._auto_download.setToolTip('开启后，服务器上新增或修改的文件会自动下载到本地，关闭则只上传本地文件，不从服务器拉取')
        gform.addRow(self._info(''), self._auto_download)

        self._notify_success = QCheckBox('同步完成通知')
        self._notify_success.setToolTip('备份完成后显示系统通知')
        gform.addRow(self._info(''), self._notify_success)

        self._notify_failure = QCheckBox('同步失败通知')
        self._notify_failure.setChecked(True)
        self._notify_failure.setToolTip('备份失败时显示系统通知，建议保持开启')
        gform.addRow(self._info(''), self._notify_failure)

        self._ui_mode = QComboBox()
        self._ui_mode.addItems(['☀️ 浅色模式', '🌙 深色模式', '💠 科幻模式'])
        self._ui_mode.setToolTip('切换界面主题风格')
        gform.addRow(self._info('界面主题:'), self._ui_mode)

        layout.addWidget(gen_group)

        # Save btn
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._save_btn = QPushButton('💾 保存设置')
        self._save_btn.setObjectName('primaryBtn')
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setMinimumWidth(160)
        self._save_btn.setToolTip('保存所有设置并尝试连接到服务器')
        self._save_btn.setMinimumHeight(40)
        save_row.addWidget(self._save_btn)
        layout.addLayout(save_row)

        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def load_config(self, cfg, password=''):
        if not cfg:
            return
        self._server_url.setText(cfg.get('server_url', 'http://localhost:5000'))
        self._port.setValue(cfg.get('port', 5000))
        self._username.setText(cfg.get('username', ''))
        if password:
            self._password.setText(password)
        self._auto_start.setChecked(cfg.get('auto_start', False))
        self._minimize_tray.setChecked(cfg.get('minimize_to_tray', True))
        self._auto_download.setChecked(cfg.get('auto_download', False))
        self._notify_success.setChecked(cfg.get('notify_success', False))
        self._notify_failure.setChecked(cfg.get('notify_failure', True))
        mode = cfg.get('ui_mode', 'light')
        mode_map = {'light': 0, 'dark': 1, 'sci_fi': 2}
        self._ui_mode.setCurrentIndex(mode_map.get(mode, 0))
        self._interval.setValue(cfg.get('sync_interval', 60))
        self._retry.setValue(cfg.get('retry_count', 3))
        self._proxy.setText(cfg.get('proxy', ''))

        freq_map = {'manual': 0, 'realtime': 1, 'hourly': 2, 'daily': 3, 'weekly': 4}
        idx = freq_map.get(cfg.get('backup_frequency', 'manual'), 0)
        self._backup_freq.setCurrentIndex(idx)

        type_map = {'incremental': 0, 'full': 1}
        self._backup_type.setCurrentIndex(type_map.get(cfg.get('backup_type', 'incremental'), 0))

        conflict_map = {'keep_newer': 0, 'local': 1, 'remote': 2, 'both': 3}
        self._conflict.setCurrentIndex(conflict_map.get(cfg.get('conflict_strategy', 'keep_newer'), 0))

    def get_config(self):
        freq_map = ['manual', 'realtime', 'hourly', 'daily', 'weekly']
        type_map = ['incremental', 'full']
        conflict_map = ['keep_newer', 'local', 'remote', 'both']
        return {
            'server_url': self._server_url.text(),
            'port': self._port.value(),
            'username': self._username.text(),
            'sync_interval': self._interval.value(),
            'backup_frequency': freq_map[self._backup_freq.currentIndex()],
            'backup_type': type_map[self._backup_type.currentIndex()],
            'conflict_strategy': conflict_map[self._conflict.currentIndex()],
            'retry_count': self._retry.value(),
            'proxy': self._proxy.text(),
            'auto_start': self._auto_start.isChecked(),
            'minimize_to_tray': self._minimize_tray.isChecked(),
            'auto_download': self._auto_download.isChecked(),
            'notify_success': self._notify_success.isChecked(),
            'notify_failure': self._notify_failure.isChecked(),
            'ui_mode': ['light', 'dark', 'sci_fi'][self._ui_mode.currentIndex()],
        }

    def set_test_result(self, ok, msg):
        color = '#0FC6C2' if ok else '#F53F3F'
        self._test_result.setStyleSheet(f'color: {color}; font-size: 14px;')
        self._test_result.setText(msg)

    @property
    def server_url(self):
        return self._server_url

    @property
    def username(self):
        return self._username

    @property
    def password(self):
        return self._password

    @property
    def dark_mode(self):
        return self._dark_mode

    @property
    def save_btn(self):
        return self._save_btn

    @property
    def test_btn(self):
        return self._test_btn
