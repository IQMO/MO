"""Computer-use Steps 3-4: desktop actuation + overlay tools — registration,
sandbox lane, and arg handling (no real mouse/keyboard/overlay fired)."""
import tools
from core.sandbox import guard_tool_call
from core.tool_constants import ACTUATION_TOOLS
from tools.desktop import execute_move_pointer, execute_press_key

DESKTOP_TOOLS = ["screen_size", "point_on_screen", "move_pointer", "mouse_click", "type_text", "press_key"]


def test_desktop_tools_registered():
    for name in DESKTOP_TOOLS:
        assert name in tools.TOOL_EXECUTORS, name
        assert any(d["function"]["name"] == name for d in tools.TOOL_DEFINITIONS), name


def test_actuation_tools_blocked_in_read_only_lane():
    for name in ACTUATION_TOOLS:
        reason = guard_tool_call(name, {"keys": "enter", "x": 1, "y": 1, "text": "t"},
                                 lane="report", allowed_roots=None, sandbox_config={})
        assert reason and "LANE LOCKED" in reason, name


def test_actuation_tools_allowed_in_normal_lane():
    for name in ACTUATION_TOOLS:
        reason = guard_tool_call(name, {"keys": "enter", "x": 1, "y": 1, "text": "t"},
                                 lane=None, allowed_roots=None, sandbox_config={})
        assert not reason, f"{name}: {reason}"


def test_point_on_screen_is_safe_even_in_read_only_lane():
    # Guided pointing actuates nothing, so it is not in ACTUATION_TOOLS.
    assert "point_on_screen" not in ACTUATION_TOOLS
    reason = guard_tool_call("point_on_screen", {"x": 1, "y": 1},
                             lane="report", allowed_roots=None, sandbox_config={})
    assert not reason


def test_actuation_tools_validate_args_without_raising():
    assert "requires" in execute_move_pointer({}).lower()
    assert "requires" in execute_press_key({}).lower()
