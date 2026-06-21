"""Shared tool/lane constants — single source of truth for sandbox enforcement."""

MUTATING_TOOLS = frozenset({"write_file", "edit_file"})
READ_ONLY_LANES = frozenset({"report", "review-only", "investigate", "prt-review-only"})

# Computer-use actuation — these drive the operator's real mouse/keyboard or a
# real browser. They mutate machine state outside the workspace, so they are
# barred from read-only lanes alongside file-mutating tools.
# Desktop: pyautogui with FAILSAFE=True; Browser: CDP in isolated Chrome profile.
ACTUATION_TOOLS = frozenset({
    "move_pointer", "mouse_click", "type_text", "press_key",
    "browser_open", "browser_click", "browser_type", "browser_eval", "browser_close",
})

# Lanes that block actuation (taking control) but NOT file edits — the companion's
# Guide mode: "point and narrate, don't drive." Distinct from READ_ONLY_LANES so a
# guided session can still read, answer, and edit code; it just can't actuate.
ACTUATION_BLOCKED_LANES = frozenset({"companion-guide"})
