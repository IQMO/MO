"""Shared tool/lane constants — single source of truth for sandbox enforcement."""

MUTATING_TOOLS = frozenset({"write_file", "edit_file"})
READ_ONLY_LANES = frozenset({"report", "review-only", "investigate", "prt-review-only"})

# Computer-use desktop actuation — these drive the operator's real mouse/keyboard
# (pyautogui, failsafe-on). They mutate machine state outside the workspace, so
# they are barred from read-only lanes alongside file-mutating tools.
ACTUATION_TOOLS = frozenset({"move_pointer", "mouse_click", "type_text", "press_key"})
