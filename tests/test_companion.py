"""Tests for the desktop companion surface (Phase 2 + Phase 4)."""
import subprocess
import sys
from pathlib import Path

from core.heartbeat import SURFACE_ALIASES, normalize_surface
from interface.command_registry import COMMAND_BY_NAME


# ------------------------------------------------------------------
# Phase 2 — surface registration
# ------------------------------------------------------------------

def test_desktop_surface_is_proper_not_terminal_alias():
    """Phase 2: desktop is now its own surface, not aliased to terminal."""
    assert SURFACE_ALIASES.get("desktop") == "desktop"
    assert SURFACE_ALIASES.get("companion") == "desktop"


def test_normalize_desktop_surface():
    """normalize_surface should keep desktop as its own surface."""
    assert normalize_surface("desktop") == "desktop"
    assert normalize_surface("companion") == "desktop"


def test_companion_slash_command_registered():
    """Phase 2: /companion command is in the registry."""
    spec = COMMAND_BY_NAME.get("/companion")
    assert spec is not None
    assert spec.name == "/companion"
    assert spec.category == "Work"
    # Desktop surface now presents as "Ghost" (the /companion command name is kept
    # for back-compat until the entity/command merge; description is Ghost-branded).
    assert "ghost" in spec.description.lower()


def test_companion_help_includes_command():
    """Phase 2: /companion appears in help output."""
    from interface.command_registry import SLASH_COMMAND_HELP
    assert "/companion" in SLASH_COMMAND_HELP


def test_ghost_window_subcommand_and_companion_alias_route_to_window():
    """`/ghost window [show|hide]` and the back-compat `/companion` both drive the
    desktop Ghost window through one shared helper."""
    from core.agent.agent_slash import AgentSlashCommands
    obj = AgentSlashCommands.__new__(AgentSlashCommands)
    calls = []

    class FakeWindow:
        def show(self): calls.append("show")
        def hide(self): calls.append("hide")
        def toggle(self): calls.append("toggle")

    obj._companion = FakeWindow()
    assert obj._cmd_ghost("window") == "[GHOST WINDOW TOGGLED]"
    assert obj._cmd_ghost("window show") == "[GHOST WINDOW SHOWN]"
    assert obj._cmd_companion("hide") == "[GHOST WINDOW HIDDEN]"   # alias still works
    assert calls == ["toggle", "show", "hide"]


def test_ghost_window_when_disabled_matches_companion_alias():
    from core.agent.agent_slash import AgentSlashCommands
    obj = AgentSlashCommands.__new__(AgentSlashCommands)
    obj._companion = None
    msg = obj._cmd_ghost("window")
    assert "disabled" in msg.lower()
    assert obj._cmd_companion("") == msg   # alias gives the identical message


# ------------------------------------------------------------------
# Phase 4 — tray integration, action log, panic-stop
# ------------------------------------------------------------------

class TestCompanionPhase4Init:
    """CompanionSurface initializes Phase 4 attributes."""

    def test_companion_has_tray_attr(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        assert cs._tray is None
        assert cs._action_log == []
        assert cs._panic_stop_requested is False

    def test_companion_accepts_top_level_config(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None, companion_config={"tray_enabled": True})
        assert cs._companion_cfg["tray_enabled"] is True

    def test_companion_default_mode_is_guide(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        assert cs.mode == "guide"


class TestCompanionActionLog:
    """_log_action and action_log list management."""

    def test_log_action_appends_entry(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        cs._log_action("submit", "test query")
        assert len(cs._action_log) == 1
        entry = cs._action_log[0]
        assert entry["kind"] == "submit"
        assert entry["detail"] == "test query"
        assert "time" in entry

    def test_log_action_truncates_detail(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        cs._log_action("turn_complete", "x" * 300)
        assert len(cs._action_log[0]["detail"]) == 200

    def test_log_action_caps_at_50(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        for i in range(60):
            cs._log_action("submit", f"query {i}")
        assert len(cs._action_log) == 50
        # Oldest entries dropped
        assert cs._action_log[0]["detail"] == "query 10"
        assert cs._action_log[-1]["detail"] == "query 59"


class TestCompanionActionCoverage:
    """The action log must reflect what MO actually does, not just request+reply."""

    def _cs(self):
        from interface.ghost_desktop.companion import CompanionSurface
        return CompanionSurface(agent=None, gateway=None)

    def test_on_action_logs_tool_with_summary(self):
        cs = self._cs()
        cs._on_action({"tool": "click", "summary": "click(x=120, y=340)"})
        entry = cs._action_log[-1]
        assert entry["kind"] == "action"
        assert "click" in entry["detail"] and "120" in entry["detail"]

    def test_on_action_logs_blocked_and_error(self):
        cs = self._cs()
        cs._on_action({"tool": "run", "summary": "run(rm -rf /)", "blocked": True})
        cs._on_action({"tool": "write_file", "summary": "write_file(x)", "error": True})
        kinds = [e["kind"] for e in cs._action_log]
        assert "blocked" in kinds and "action_error" in kinds

    def test_board_event_is_logged(self):
        cs = self._cs()
        cs._on_board_event({"kind": "task_completed", "text": "Captured the screen"})
        assert any(e["kind"] == "task_completed" for e in cs._action_log)

    def test_run_turn_passes_on_action_to_gateway(self):
        cs = self._cs()
        captured = {}

        class FakeGateway:
            last_task_board = None

            def run_turn(self, user_input, **kwargs):
                captured.update(kwargs)
                return "done"

        class FakeAgent:
            def lane_scope(self, _lane):
                from contextlib import nullcontext
                return nullcontext()

            def isolated_session(self, _session):
                from contextlib import nullcontext
                return nullcontext()

        cs._agent = FakeAgent()
        cs._gateway = FakeGateway()
        cs._run_turn("hi")
        assert callable(captured.get("on_action"))
        assert captured["on_action"] == cs._on_action

    def test_run_turn_uses_isolated_ghost_session(self):
        """A desktop turn must run inside Ghost's OWN session, not Main MO's, so the
        desktop conversation can never bleed into a running Main/DEVMODE session."""
        cs = self._cs()
        entered = {}

        class FakeGateway:
            last_task_board = None

            def run_turn(self, user_input, **kwargs):
                return "done"

        class FakeAgent:
            def lane_scope(self, _lane):
                from contextlib import nullcontext
                return nullcontext()

            def isolated_session(self, session):
                from contextlib import contextmanager

                @contextmanager
                def _cm():
                    entered["session"] = session
                    yield
                return _cm()

        cs._agent = FakeAgent()
        cs._gateway = FakeGateway()
        cs._run_turn("what do you see")
        assert cs._ghost_session is not None
        assert entered.get("session") is cs._ghost_session  # ran on the Ghost session


class TestCompanionPanicStop:
    """Panic-stop state management."""

    def test_panic_stop_sets_flag_and_logs(self):
        from interface.ghost_desktop.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        cs.panic_stop()
        assert cs._panic_stop_requested is True
        # Should have logged the panic action
        assert any(e["kind"] == "panic_stop" for e in cs._action_log)


def test_companion_stop_stops_tray(monkeypatch):
    from interface.ghost_desktop.companion import CompanionSurface

    stopped = []

    class FakeTray:
        def stop(self):
            stopped.append(True)

    cs = CompanionSurface(agent=None, gateway=None)
    cs._tray = FakeTray()
    monkeypatch.setattr(cs, "_post_gui_event", lambda _event: False)

    cs.stop()

    assert stopped == [True]
    assert cs._running is False


def test_companion_module_entrypoint_help():
    """Regression: tray startup targets `python -m interface.ghost_desktop`."""
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "interface.ghost_desktop", "--help"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "Run MO Ghost" in result.stdout


def test_legacy_companion_entrypoint_still_forwards():
    """Back-compat: existing run-at-startup shortcuts invoke `python -m
    interface.companion`, which must still forward to the renamed package."""
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "interface.companion", "--help"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "Run MO Ghost" in result.stdout


def test_ghost_surface_config_prefers_ghost_with_legacy_fallback():
    from interface.ghost_desktop.companion import ghost_surface_config
    assert ghost_surface_config({"ghost": {"enabled": True}}) == {"enabled": True}
    assert ghost_surface_config({"desktop_companion": {"enabled": True}}) == {"enabled": True}
    # new key wins when both are present
    assert ghost_surface_config({"ghost": {"a": 1}, "desktop_companion": {"a": 2}}) == {"a": 1}
    assert ghost_surface_config(None) == {}
    assert ghost_surface_config("nope") == {}


def test_ghost_session_uses_desktop_persona():
    """The desktop Ghost runs on its own isolated session with the Ghost persona
    augmenting (not replacing) the main MO system prompt."""
    from types import SimpleNamespace
    from interface.ghost_desktop.companion import CompanionSurface
    agent = SimpleNamespace(system_message="You are MO. Base rules here.", _session=None)
    cs = CompanionSurface(agent=agent, gateway=None)
    sess = cs._ensure_ghost_session()
    assert "You are MO." in sess.system_message                      # main prompt preserved
    assert "Ghost — MO's desktop presence" in sess.system_message    # persona added
    assert cs._ensure_ghost_session() is sess                        # cached, created once


def test_start_companion_passes_companion_config(monkeypatch):
    import interface.ghost_desktop.companion as companion_module

    captured = {}

    class DummyCompanion:
        def __init__(self, agent, gateway, voice_config=None, companion_config=None):
            captured["agent"] = agent
            captured["gateway"] = gateway
            captured["voice_config"] = voice_config
            captured["companion_config"] = companion_config

        def start(self):
            return True

    monkeypatch.setattr(companion_module, "CompanionSurface", DummyCompanion)
    monkeypatch.setattr(companion_module, "acquire_runtime_lock", lambda **_kwargs: object())
    agent = type("Agent", (), {
        "config": {
            "desktop_companion": {
                "enabled": True,
                "run_in_terminal": True,  # opt back into terminal co-hosting
                "tray_enabled": True,
                "voice": {"stt_enabled": True},
            }
        }
    })()
    gateway = object()

    result = companion_module.start_companion_if_enabled(agent, gateway)

    assert result is not None
    assert captured["gateway"] is gateway
    assert captured["voice_config"] == {"stt_enabled": True}
    assert captured["companion_config"]["tray_enabled"] is True


def test_terminal_does_not_cohost_ghost_desktop_by_default(monkeypatch):
    """Ghost Desktop is its own process: the terminal must NOT start it in-thread
    unless run_in_terminal is explicitly set, so it survives terminal restarts."""
    from interface.ghost_desktop import companion as companion_module

    started = []
    monkeypatch.setattr(companion_module, "acquire_runtime_lock",
                        lambda **_k: started.append(True) or object())
    agent = type("Agent", (), {
        "config": {"desktop_companion": {"enabled": True, "tray_enabled": True}}
    })()

    assert companion_module.start_companion_if_enabled(agent, object()) is None
    assert started == []  # never even reached for the lock — fully decoupled


def test_hotkey_registration_stores_and_removes_handle(monkeypatch):
    import sys
    import types
    from interface.ghost_desktop.companion import CompanionSurface

    removed = []
    keyboard = types.SimpleNamespace(
        add_hotkey=lambda _hotkey, _callback: "hotkey-handle",
        remove_hotkey=lambda handle: removed.append(handle),
    )
    monkeypatch.setitem(sys.modules, "keyboard", keyboard)

    cs = CompanionSurface(agent=None, gateway=None)
    cs._try_register_hotkey()
    assert cs._hotkey_listener == "hotkey-handle"

    cs._unregister_hotkey()
    assert removed == ["hotkey-handle"]
    assert cs._hotkey_listener is None


def test_post_gui_event_uses_thread_safe_queue():
    from interface.ghost_desktop.companion import CompanionSurface

    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()

    assert cs._post_gui_event("<<CompanionShow>>") is True
    assert cs._gui_events.get_nowait() == "<<CompanionShow>>"


def test_gui_status_updates_drain_on_gui_thread():
    from interface.ghost_desktop.companion import CompanionSurface

    updates = []

    class FakeLabel:
        def config(self, **kwargs):
            updates.append(kwargs)

    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._status_label = FakeLabel()

    cs._set_status("Done", "#44cc88")
    assert updates == []

    cs._drain_gui_events({})
    assert updates == [{"text": "Done", "fg": "#44cc88"}]


def test_companion_geometry_opens_near_pointer_and_flips_at_edges():
    from interface.ghost_desktop.companion import (
        WINDOW_WIDTH as W, WINDOW_HEIGHT as H, WINDOW_OFFSET as O,
        companion_geometry_near_pointer,
    )

    # Near the top-left: offset down-right from the pointer.
    assert companion_geometry_near_pointer(100, 100, 1920, 1080) == f"{W}x{H}+{100 + O}+{100 + O}"
    # Near the bottom-right: flip so the window stays on-screen.
    flip_x = 1900 - W - O
    flip_y = 1060 - H - O
    assert companion_geometry_near_pointer(1900, 1060, 1920, 1080) == f"{W}x{H}+{flip_x}+{flip_y}"


def test_voice_input_is_hidden_when_not_configured():
    from interface.ghost_desktop.companion import CompanionSurface

    assert CompanionSurface(agent=None, gateway=None)._voice_input_configured() is False
    assert CompanionSurface(
        agent=None,
        gateway=None,
        voice_config={"stt_enabled": False},
    )._voice_input_configured() is False
    assert CompanionSurface(
        agent=None,
        gateway=None,
        voice_config={"stt_enabled": True},
    )._voice_input_configured() is True


def test_voice_unavailable_message_separates_transcription_from_capture():
    from interface.ghost_desktop.companion import CompanionSurface

    class FakeRecognizer:
        available = False
        _load_error = "faster-whisper not installed"

    class FakeRecorder:
        available = True

    class FakeVoice:
        stt_available = False
        recognizer = FakeRecognizer()
        recorder = FakeRecorder()

    cs = CompanionSurface(agent=None, gateway=None, voice_config={"stt_enabled": True})
    cs._voice = FakeVoice()

    message = cs._voice_input_unavailable_message()

    assert "faster-whisper" in message
    assert "sounddevice" not in message
