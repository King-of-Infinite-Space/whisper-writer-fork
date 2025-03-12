import os
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QObject, pyqtSignal


class TrayIcon(QObject):
    open_settings = pyqtSignal()
    close_app = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.tray_icon = None
        self.create_tray_icon()

    def create_tray_icon(self):
        app = QApplication.instance()
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo-new.png')), app)

        tray_menu = QMenu()

        settings_action = QAction('Open Settings', app)
        settings_action.triggered.connect(self.open_settings.emit)
        tray_menu.addAction(settings_action)

        exit_action = QAction('Exit', app)
        exit_action.triggered.connect(self.close_app.emit)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)

    def show(self):
        if self.tray_icon:
            self.tray_icon.show()

    def hide(self):
        if self.tray_icon:
            self.tray_icon.hide()
