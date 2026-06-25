import threading
from types import SimpleNamespace

import interface.tui_app as tui_app_module
from interface.tui_app import LOGO_LINES, TuiAppMixin


def test_tui_logo_lines_preserve_current_boot_banner():
    assert LOGO_LINES == (
        "  █   █   ███ ",
        "  ██ ██  █   █",
        "  █ █ █  █   █",
        "  █   █   ███ ",
    )


def test_tui_app_enables_mouse_wheel_scroll_contract(monkeypatch):
    created = {}

    class FakeBuffer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.text = ""
            self.cursor_position = 0

    class FakeApplication:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.output = SimpleNamespace(get_size=lambda: SimpleNamespace(rows=24, columns=80))

        def run(self):
            return None

        def invalidate(self):
            return None

    class Harness(TuiAppMixin):
        def __init__(self):
            self.agent = None
            self.gateway = None
            self._refresh_stop = threading.Event()
            self._input_history = object()
            self._input_buf = None
            self._app = None
            self.busy = False
            self._goal_running = False
            self._goal_worker_active = False
            self._ghost_panel_open = False
            self.added = []

        def _add(self, style, text):
            self.added.append((style, text))

        def _on_input_changed(self, _buff):
            return None

    monkeypatch.setattr(tui_app_module, "Buffer", FakeBuffer)
    monkeypatch.setattr(tui_app_module, "Application", FakeApplication)
    monkeypatch.setattr(tui_app_module, "Layout", lambda root, focused_element: (root, focused_element))
    monkeypatch.setattr(tui_app_module, "build_tui_style", lambda: "style")
    monkeypatch.setattr("interface.keybindings.build_tui_key_bindings", lambda tui: "kb")
    monkeypatch.setattr("interface.layout.build_tui_root", lambda tui, input_buf, prefix: "root")
    monkeypatch.setattr("interface.layout.prompt_prefix", lambda: "prefix")

    harness = Harness()
    harness.run()

    assert created["full_screen"] is False
    # mouse_support=True routes the wheel to the ScrollUp/ScrollDown bindings
    # (transcript scroll); native text selection uses Shift+drag.
    assert created["mouse_support"] is True
    assert created["key_bindings"] == "kb"
    assert created["style"] == "style"
    assert harness.added[:4] == [("class:logo", line) for line in LOGO_LINES]
