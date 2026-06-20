"""Companion Guide mode: the 'companion-guide' lane blocks actuation (taking
control) but still allows reads/answers/code-edits."""
from core.sandbox import guard_tool_call
from core.tool_constants import ACTUATION_TOOLS


def _guard(name, lane):
    args = {"x": 1, "y": 1, "keys": "enter", "url": "https://example.com",
            "path": "f.py", "content": "x", "old_text": "a", "new_text": "b", "ref": "e1", "text": "t"}
    return guard_tool_call(name, args, lane=lane, allowed_roots=None, sandbox_config={"enabled": True})


def test_guide_lane_blocks_every_actuation_tool():
    for name in ACTUATION_TOOLS:
        reason = _guard(name, "companion-guide")
        assert reason and "GUIDE MODE" in reason, name


def test_guide_lane_allows_reads_and_code_edits():
    # Guide mode is NOT read-only — it only blocks actuation.
    assert _guard("read_file", "companion-guide") is None
    write = _guard("write_file", "companion-guide")
    assert not (write and "GUIDE MODE" in str(write))
    edit = _guard("edit_file", "companion-guide")
    assert not (edit and "GUIDE MODE" in str(edit))


def test_do_mode_normal_lane_allows_actuation():
    for name in ACTUATION_TOOLS:
        assert _guard(name, None) is None, name
