"""Dynamic rotating hints for the MO idle line.

Hints are loaded from ``~/.mo/hints.txt`` (one per line, # comments ignored).
When the file is missing or empty, built-in defaults are used.
Rotation is time-based: a new hint appears every HINT_INTERVAL seconds.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from core.state.paths import mo_home

HINT_INTERVAL: float = 20.0
"""Seconds between hint rotations on the idle line."""

HINTS_FILE_NAME: str = "hints.txt"
"""File under MO home containing user hints (one per line)."""

DEFAULT_HINTS: tuple[str, ...] = (
    # ── Privacy & safety ──
    "MO runs locally — your code never leaves your machine unless you push.",
    "MO never prints secrets — keys, tokens, and passwords are redacted automatically.",
    "Every tool call passes through MO's sandbox — path boundaries, network policy, and secret redaction enforced at dispatch.",
    # ── Core architecture ──
    "The Gateway routes every message: simple chat, build work, PRT review, or Ghost proposal — all through one central path.",
    "MO's agent core dispatches every turn with DNA rules: prefer existing tokens, no new deps without approval, evidence-first.",
    "Evidence-first: MO verifies with files, logs, tests, and runtime checks before claiming anything.",
    "The context bridge layers priority: system prompt > taskboard truth > profile > memory — live evidence always wins.",
    # ── Design DNA ──
    "MO's design DNA: prefer existing tokens/components, hate duplication, simplify remorselessly, no dependencies without approval.",
    # ── Ghost ──
    "Ghost (Alt+G) is your side-check assistant — proposals never auto-execute. Ctrl+O expands/collapses Ghost details.",
    # ── Tasks & goals ──
    "Use /goal for multi-step work — MO plans, executes, and verifies autonomously.",
    "The taskboard is truth — UI shows real worker progress, not estimates. Tasks require tool evidence to advance.",
    "MO's tasking engine uses contracts: each task declares required evidence, verification, and acceptance criteria.",
    # ── PRT ──
    "PRT auto-reviews significant commits — keep scores above 4.5. Use /prt for a deep review with auto-fix.",
    # ── Learning ──
    "/learning shows pending suggestions and approved local skills learned from your feedback.",
    "MO detects your work patterns (build, fix, design, review) and adapts its process automatically.",
    # ── Provider ──
    "Use /model to check which AI provider is currently active.",
    "MO audits providers silently — latency caps, failover, and capacity management keep prompts flowing.",
    # ── Scheduler ──
    "MO can schedule long-term tasks — set reminders, follow-ups, and periodic checks that survive sessions.",
    # ── Command palette & keybindings ──
    "F4 opens the command palette — browse all slash commands, drill into subcommands, see recent history.",
    "Ctrl+J inserts a newline for multi-line input. Ctrl+C cancels busy work; when idle it exits MO.",
    "Paste large text safely — MO caps at 12K chars and holds it until you press Enter to send.",
    # ── Slash commands: session & state ──
    "/status shows session health, token usage, and provider state.",
    "/usage shows token consumption and compression savings. /heartbeat checks surface continuity.",
    "/profile shows or edits your operator profile. /settings shows current configuration.",
    "/session saves your current conversation; /resume brings it back. /new starts a clean session.",
    "/projects lists your project history. /telegram manages remote gateway approval.",
    # ── Slash commands: workflow ──
    "/think sets reasoning level (high/medium/low). /reload refreshes config and system prompt.",
    "Press Ctrl+E to rewrite your typed message into a sharper prompt in your own language and tone; Esc reverts it.",
    "/undo removes the last exchange. /retry re-runs your last prompt. /clear starts a fresh transcript.",
    # ── Slash commands: tools ──
    "/init checks your private MO home. /migrate dry-runs or applies legacy state migration.",
    "/structural-graph shows or builds MO's code graph — ask MO 'who calls X' to see relationships.",
    "Type /help to see all slash commands and shortcuts.",
    # ── Customization ──
    "Hooks: map runtime events to your own shell commands via ~/.mo/hooks.yaml.",
    "/moon on|off toggles the animated MO logo glow. /hints on|off toggles these rotating tips.",
    # ── Code intelligence ──
    "The structural graph maps your codebase; ask MO 'who calls X' to see relationships.",
)


def hints_file_path() -> Path:
    """Return the resolved path to the hints file."""
    return mo_home() / HINTS_FILE_NAME


def load_hints() -> list[str]:
    """Load hints from the user file, falling back to built-in defaults."""
    path = hints_file_path()
    hints: list[str] = []
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    hints.append(stripped)
    except Exception:
        pass

    if not hints:
        hints = list(DEFAULT_HINTS)

    # Shuffle once per load so the time-based index yields varied order
    rng = random.Random()
    rng.shuffle(hints)
    return hints


# Module-level cache: loaded once, shuffled once per process.
# Restart MO to pick up edits to ~/.mo/hints.txt.
_HINTS_CACHE: list[str] | None = None


def get_hints() -> list[str]:
    """Return the cached hint list, loading on first access."""
    global _HINTS_CACHE
    if _HINTS_CACHE is None:
        _HINTS_CACHE = load_hints()
    return _HINTS_CACHE


def current_hint(now: float | None = None) -> str:
    """Return the current rotating hint based on wall-clock time.

    Changes every HINT_INTERVAL seconds. Empty string if no hints loaded.
    """
    hints = get_hints()
    if not hints:
        return ""
    current = time.time() if now is None else float(now)
    index = int(current / HINT_INTERVAL) % len(hints)
    return hints[index]
