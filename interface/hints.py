"""Dynamic rotating hints for the MO idle line.

Non-comment lines from the optional ``hints.txt`` under the resolved MO home
replace the built-in defaults for that process. Rotation is time-based: a new
hint appears every HINT_INTERVAL seconds.
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
    "MO keeps private state in its home; tools use only sandbox-approved paths.",
    "MO brokers secret values and redacts or blocks them from logs and answers.",
    "Every tool call passes path, network, and secret checks before dispatch.",
    # ── Core architecture ──
    "The Gateway coordinates turns, tools, reviews, and taskboard truth.",
    "Current requests and project conventions own implementation choices.",
    "Evidence-first: verify with files, logs, tests, or current runtime checks.",
    "Current rules and requests outrank memory, profile, and graph hints.",
    "Use taskboards for real multi-step work; simple questions stay boardless.",
    # ── Design work ──
    "MO preserves verified project visuals unless you request a visual change.",
    # ── MO Desktop ──
    "Win+Alt+M opens MO Desktop, a companion separate from this Terminal.",
    "Desktop explanations cannot actuate; explicit actions still pass Gateway.",
    "Desktop sync shares terminal focus as context, not as a second reply.",
    # ── MO's eyes (perception) ──
    "MO reads images, screenshots, and text PDFs with suitable provider support.",
    "Screen capture stays within your request or active Live Control session.",
    # ── MO's visuals ──
    "Ask MO to show numeric results as a chart or compact terminal table.",
    "Terminal-native visuals use your skin; source images retain their colours.",
    "One concise visual can replace, not duplicate, a long prose result.",
    # ── Live work status ──
    "MO's activity lane names the live stage, such as Reading or Checking.",
    # ── Reach ──
    "MO can search or read the web and open a discovered URL when requested.",
    "Computer-use observes a scoped target before any approved action.",
    # ── Tasks & goals ──
    "Use /goal for autonomous multi-step work with execution and verification.",
    "The taskboard reflects evidence-backed progress, never estimated progress.",
    "Task contracts declare required evidence, checks, and acceptance criteria.",
    # ── PRT ──
    "Use /prt for an explicit local review; trusted GitHub handles PR review.",
    # ── Learning ──
    "/learning shows pending suggestions and the local skills you approved.",
    "MO can adapt to confirmed build, fix, design, and review feedback.",
    # ── Provider ──
    "Use /model to choose a configured provider, model, and thinking level.",
    "Provider fallbacks are reported with current failure evidence.",
    # ── Scheduler ──
    "Scheduled tasks can keep reminders, follow-ups, and periodic checks.",
    # ── Command palette & keybindings ──
    "Press F4 to browse commands, subcommands, and recent choices.",
    "Ctrl+J inserts a newline; Ctrl+C cancels busy work or exits while idle.",
    "Large pastes are capped at 12K characters and wait for Enter before sending.",
    # ── Slash commands: session & state ──
    "/status inspects current health; /now shows open work and its location.",
    "/dashboard summarizes bounded work, profile, runtime, and graph state.",
    "/doctor checks MO setup; add --json for machine-readable output.",
    "/usage shows token and cache metrics; /heartbeat checks continuity.",
    "/profile manages operator context; /settings shows active configuration.",
    "/session saves locally; /session share publishes one named conversation.",
    "/projects shows recent project history; /telegram manages remote access.",
    # ── Slash commands: workflow ──
    "/reload refreshes config and the system prompt; /model changes providers.",
    "Ctrl+E sharpens the draft in your language; Esc restores the original.",
    "Use extrathink when a hard problem needs a stricter second reasoning pass.",
    "Use mapthis for a 2–8-worker project map saved as PROJECT-MAP.md.",
    "/undo removes one exchange; /retry reruns it; /clear resets the transcript.",
    # ── Slash commands: tools ──
    "/init checks and initializes your private MO home.",
    "/update fast-forwards a clean MO checkout when upstream changes exist.",
    "/structural-graph builds or reports the project relationship index.",
    # ── Code intelligence ──
    "Graph results orient searches; current source and tests remain proof.",
    # ── Customization ──
    "/hints on|off saves whether rotating idle tips appear.",
    # ── Keyboard shortcuts ──
    "Up on an empty editor restores your latest queued message or steer.",
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
# Restart MO to pick up edits to the resolved MO-home hints file.
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
