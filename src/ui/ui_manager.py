from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon

from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from ui.tray_icon import TrayIcon
from config_manager import ConfigManager


class UIManager:
    """
    The UIManager class is responsible for managing all user interface components of
    the application. It handles the creation and interaction of various windows (main, settings,
    status) and the system tray icon. This class serves as the central point for UI-related
    operations and events.
    """
    def __init__(self, event_bus):
        """Initialize the UIManager with the event bus."""
        self.event_bus = event_bus
        self.is_closing = False
        self.status_update_mode = "Window"

        self.main_window = MainWindow()
        self.settings_window = SettingsWindow()
        self.status_window = StatusWindow()
        self.tray_icon = TrayIcon()

        self.setup_connections()

    def setup_connections(self):
        """Establish connections between UI components and their corresponding actions."""
        self.main_window.open_settings.connect(self.settings_window.show)
        self.main_window.start_listening.connect(self.handle_start_listening)
        self.main_window.close_app.connect(self.initiate_close)
        self.tray_icon.open_settings.connect(self.settings_window.show)
        self.tray_icon.close_app.connect(self.initiate_close)
        self.event_bus.subscribe("quit_application", self.quit_application)
        self.event_bus.subscribe("profile_state_change", self.handle_profile_state_change)
        self.event_bus.subscribe("transcription_error", self.show_error_message)
        self.event_bus.subscribe("initialization_successful", self.hide_main_window)

    def handle_start_listening(self):
        """Handle the start listening event."""
        self.event_bus.emit("start_listening")

    def hide_main_window(self):
        """Hide the main window after successful initialization."""
        self.main_window.hide_main_window()

    def handle_profile_state_change(self, message):
        """Handle changes in profile states, updating status based on the chosen mode."""
        ConfigManager.log_print(message)
        if self.status_update_mode == "Window":
            self.show_status_window(message)
        elif self.status_update_mode == "Notification":
            self.show_notification(message)

    def show_status_window(self, message):
        """Display a status message in the status window."""
        if message:
            self.status_window.show_message(message)
        else:
            self.status_window.hide()

    def show_notification(self, message):
        """Display a desktop notification."""
        if not message:
            message = "Finished."

        self.tray_icon.tray_icon.showMessage(
            "WhisperWriter Status",
            message,
            QIcon(),
            3000
        )

    def show_error_message(self, message):
        """Display an error message in a QMessageBox."""
        ConfigManager.log_print(f"Transcription error: {message}")
        QMessageBox.critical(None, "Transcription Error", message)

    def show_settings_with_error(self, error_message: str):
        """Show the settings window with a detailed error message."""
        QMessageBox.critical(self.main_window, "Initialization Error", error_message)
        self.settings_window.show()
        self.main_window.show()

    def initiate_close(self):
        """Initiate the application closing process, ensuring it only happens once."""
        if not self.is_closing:
            self.is_closing = True
            self.event_bus.emit("close_app")

    def quit_application(self):
        """Quit the QApplication instance, effectively closing the application."""
        QApplication.instance().quit()

    def run_event_loop(self):
        """Start and run the Qt event loop, returning the exit code when finished."""
        return QApplication.instance().exec()
