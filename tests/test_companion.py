"""Tests for the desktop companion surface (Phase 2 + Phase 4)."""
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
    assert "companion" in spec.description.lower()


def test_companion_help_includes_command():
    """Phase 2: /companion appears in help output."""
    from interface.command_registry import SLASH_COMMAND_HELP
    assert "/companion" in SLASH_COMMAND_HELP


# ------------------------------------------------------------------
# Phase 4 — tray integration, action log, panic-stop
# ------------------------------------------------------------------

class TestCompanionPhase4Init:
    """CompanionSurface initializes Phase 4 attributes."""

    def test_companion_has_tray_attr(self):
        from interface.companion.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        assert cs._tray is None
        assert cs._action_log == []
        assert cs._panic_stop_requested is False

    def test_companion_default_mode_is_guide(self):
        from interface.companion.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        assert cs.mode == "guide"


class TestCompanionActionLog:
    """_log_action and action_log list management."""

    def test_log_action_appends_entry(self):
        from interface.companion.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        cs._log_action("submit", "test query")
        assert len(cs._action_log) == 1
        entry = cs._action_log[0]
        assert entry["kind"] == "submit"
        assert entry["detail"] == "test query"
        assert "time" in entry

    def test_log_action_truncates_detail(self):
        from interface.companion.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        cs._log_action("turn_complete", "x" * 300)
        assert len(cs._action_log[0]["detail"]) == 200

    def test_log_action_caps_at_50(self):
        from interface.companion.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        for i in range(60):
            cs._log_action("submit", f"query {i}")
        assert len(cs._action_log) == 50
        # Oldest entries dropped
        assert cs._action_log[0]["detail"] == "query 10"
        assert cs._action_log[-1]["detail"] == "query 59"


class TestCompanionPanicStop:
    """Panic-stop state management."""

    def test_panic_stop_sets_flag_and_logs(self):
        from interface.companion.companion import CompanionSurface
        cs = CompanionSurface(agent=None, gateway=None)
        cs.panic_stop()
        assert cs._panic_stop_requested is True
        # Should have logged the panic action
        assert any(e["kind"] == "panic_stop" for e in cs._action_log)

