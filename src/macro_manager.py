import os
import importlib.util
import time
import threading
from enums import KeyCode, InputEvent
from event_bus import EventBus
from output_manager import UinputBackend
from input_manager import KeyChord
from config_manager import ConfigManager


class MacroManager:
    """Manages custom hotkey sequences and macros."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.uinput_backend = None
        self.macros_folder = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "macros"
        )

        # List of registered macro handlers
        self.macro_handlers = []
        self._load_macros()

        self.event_bus.subscribe("input_event", self.handle_input)
        self.event_bus.subscribe("initialization_successful", self.setup_output)

    def _load_macros(self):
        """Dynamically loads macros from the macros folder."""
        if not os.path.exists(self.macros_folder):
            ConfigManager.log_print(f"Macros folder not found: {self.macros_folder}")
            return

        for filename in sorted(
            f
            for f in os.listdir(self.macros_folder)
            if f.endswith(".py") and not f.startswith("_")
        ):
            module_name = filename[:-3]
            file_path = os.path.join(self.macros_folder, filename)
            try:
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "create_macro"):
                    macro_handler = module.create_macro(self)
                    self.macro_handlers.append(macro_handler)
                    # ConfigManager.log_print(f"Loaded macro: {module_name}")
            except Exception as e:
                ConfigManager.log_print(f"Error loading macro {filename}: {e}")

    def setup_output(self):
        """Initialize uinput backend once the application is ready."""
        try:
            self.uinput_backend = UinputBackend(None)
        except Exception as e:
            ConfigManager.log_print(f"Failed to initialize uinput for macros: {e}")

    def handle_input(self, event):
        key, event_type = event

        # Delegate the event to all registered macro handlers
        for handler in self.macro_handlers:
            handler(key, event_type)

    def cleanup(self):
        """Clean up resources and unsubscribe from events."""
        self.event_bus.unsubscribe("input_event", self.handle_input)
        self.event_bus.unsubscribe("initialization_successful", self.setup_output)
        if self.uinput_backend:
            self.uinput_backend.cleanup()
            self.uinput_backend = None
