#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DXW 备份客户端 v2.0
PySide6 桌面 GUI - 文件自动备份到远程服务器云盘
"""

import sys
import os
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.config import load, get_password
from ui.main_window import MainWindow


def setup_logging():
    log_dir = os.path.expanduser('~')
    log_path = os.path.join(log_dir, '.dxw_sync.log')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler(),
        ],
    )


def main():
    setup_logging()
    log = logging.getLogger('dxw_sync')

    app = QApplication(sys.argv)
    app.setOrganizationName('DXW')
    app.setApplicationName('DXW 备份客户端')
    app.setApplicationVersion('2.0.0')

    font = QFont('Microsoft YaHei UI', 9)
    app.setFont(font)

    window = MainWindow()

    # Show first-run wizard if not configured
    config = load()
    if not config.get('username') or not get_password(config) or not config.get('sync_folders'):
        ret = QMessageBox.question(
            window, '首次使用',
            '检测到尚未配置服务器信息。是否立即配置？',
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            window.sidebar.set_active('settings')
            window.content_stack.setCurrentIndex(4)

    window.show()
    window.settings_page.load_config(config, get_password(config))

    # Start engine
    window.engine.start()

    # Load existing tasks into UI
    for folder in config.get('sync_folders', []):
        window.tasks_page.add_task_card(
            folder.get('id', folder.get('remote', '')),
            folder.get('local', ''),
            folder.get('remote', ''),
            folder.get('backup_type', 'incremental'),
        )

    # Initial sync
    if config.get('username') and get_password(config):
        if window.engine.connect_server():
            window.engine.sync_now()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
