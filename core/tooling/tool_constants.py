"""Shared tool/lane constants — single source of truth for sandbox enforcement."""

# Tools that mutate durable state — barred from read-only lanes. record_profile_fact
# writes the operator profile (facts.md, auto-injected into context), so a review/
# investigate lane must not be able to persist profile memory.
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "record_profile_fact"})
READ_ONLY_LANES = frozenset({"report", "review-only", "investigate", "prt-review-only"})

# Raw reads of these path shapes can disclose credential values before output
# redaction can help. Keep this path-based and narrow; content redaction remains
# deliberately conservative so normal source-code reads are not corrupted.
SECRET_READ_BASENAMES = frozenset({".env", ".netrc", "credentials"})
SECRET_READ_PREFIXES = frozenset({".env.", "id_rsa"})
SECRET_READ_SUFFIXES = frozenset({".pem", ".key"})
SECRET_READ_DIR_NAMES = frozenset({".ssh"})
SECRET_READ_PATH_SUFFIXES = ((".aws", "credentials"),)

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
