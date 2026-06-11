"""Shared tool/lane constants — single source of truth for sandbox enforcement."""

MUTATING_TOOLS = frozenset({"write_file", "edit_file"})
READ_ONLY_LANES = frozenset({"report", "review-only", "investigate", "prt-review-only"})
