"""Tests for the desktop companion surface (Phase 2)."""
from core.heartbeat import SURFACE_ALIASES, normalize_surface
from interface.command_registry import COMMAND_BY_NAME


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
