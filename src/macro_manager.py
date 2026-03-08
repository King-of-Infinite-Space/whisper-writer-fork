from enums import KeyCode, InputEvent
from event_bus import EventBus
from output_manager import UinputBackend
from input_manager import KeyChord
from config_manager import ConfigManager
import time
import threading

class MacroManager:
    """Manages custom hotkey sequences and macros."""
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.uinput_backend = None
        
        # List of registered macro handlers
        self.macro_handlers = [
            self._create_kde_clipboard_macro()
        ]
        
        self.event_bus.subscribe("input_event", self.handle_input)
        self.event_bus.subscribe("initialization_successful", self.setup_output)

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

    def _create_kde_clipboard_macro(self):
        """Factory method that returns a stateful handler for the KDE Clipboard Macro."""
        # Encapsulated state just for this macro
        chord = KeyChord({
            frozenset({KeyCode.META_LEFT, KeyCode.META_RIGHT}),
            KeyCode.V
        })
        state = {'waiting_for_paste': False}

        def handler(key, event_type):
            was_active = chord.is_active()
            is_active = chord.update(key, event_type)
            
            # Detect activation (release of the chord)
            if was_active and not is_active:
                state['waiting_for_paste'] = True
                ConfigManager.log_print("KDE Clipboard activation detected, waiting for Enter...")
                return

            # Detect Enter while waiting
            if state['waiting_for_paste'] and event_type == InputEvent.KEY_PRESS:
                if key == KeyCode.ENTER or key == KeyCode.NUMPAD_ENTER:
                    ConfigManager.log_print("Enter detected after KDE Clipboard activation. Triggering Paste.")
                    state['waiting_for_paste'] = False
                    if self.uinput_backend:
                        def delayed_paste():
                            """Waits a tiny bit for KDE Clipboard to close and return focus, then pastes."""
                            time.sleep(0.12)
                            self.uinput_backend.paste()
                            
                        # Run paste in a separate thread to avoid blocking the main event loop
                        threading.Thread(target=delayed_paste).start()
                elif key not in [KeyCode.UP, KeyCode.DOWN, KeyCode.LEFT, KeyCode.RIGHT, 
                               KeyCode.META_LEFT, KeyCode.META_RIGHT, KeyCode.SHIFT_LEFT, 
                               KeyCode.SHIFT_RIGHT, KeyCode.ALT_LEFT, KeyCode.ALT_RIGHT,
                               KeyCode.CTRL_LEFT, KeyCode.CTRL_RIGHT]:
                    # Any other non-navigation key cancels the waiting state
                    state['waiting_for_paste'] = False

        return handler

    def cleanup(self):
        """Clean up resources and unsubscribe from events."""
        self.event_bus.unsubscribe("input_event", self.handle_input)
        self.event_bus.unsubscribe("initialization_successful", self.setup_output)
        if self.uinput_backend:
            self.uinput_backend.cleanup()
            self.uinput_backend = None
