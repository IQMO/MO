"""Short messages between running MO processes on this machine.

MO terminals on one project coordinate through their heartbeats; this adds the one thing heartbeats cannot do:
say something to another MO ("I take the transition, you keep the icon row"). A message is coordination data for
the receiver's next turn, never an instruction and never a trigger: nothing wakes a process. The handoff queue
stays the owner of turns and typed controls (one pending item per terminal); messages are many and lighter, so
they live in their own append-only ledger beside the heartbeat, bounded by the heartbeat's pruner.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from ..state.paths import resolve_state_path
from ..utils.atomic_write import atomic_write_json
from .backend_monitor import redact_monitor_text

LEDGER = "run/mo-messages.jsonl"
CURSORS = "run/mo-messages-read"
MAX_TEXT = 600
MAX_SHOWN = 5
MAX_AGE_SECONDS = 24 * 3600
PROJECT = "project"


def normalized_cwd(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        resolved = str(Path(text).expanduser().resolve())
    except (OSError, RuntimeError):
        resolved = text
    return os.path.normcase(resolved).rstrip("\\/")


def _ledger(config: dict | None) -> Path:
    return Path(resolve_state_path(LEDGER, config or {}))


def _cursor(config: dict | None, instance_id: str) -> Path:
    safe = "".join(ch for ch in str(instance_id) if ch.isalnum() or ch in "-_")[:40] or "unknown"
    return Path(resolve_state_path(f"{CURSORS}/{safe}.json", config or {}))


def send(config: dict | None, *, sender: dict[str, Any], to: str, text: str) -> dict[str, Any]:
    """Append one message from ``sender`` (instance_id, slot, cwd) to an instance id, a slot, or ``project``
    (every MO working in the sender's folder)."""
    target = " ".join(str(to or "").split())[:60]
    body = redact_monitor_text(" ".join(str(text or "").split()), MAX_TEXT)
    if not target or not body:
        raise ValueError("a message needs a recipient (instance id, slot or 'project') and text")
    row = {
        "id": secrets.token_hex(6),
        "at": time.time(),
        "from_instance": str(sender.get("instance_id") or "")[:40],
        "from_slot": redact_monitor_text(str(sender.get("slot") or ""), 60),
        "cwd": normalized_cwd(sender.get("cwd")),
        "to": target,
        "text": body,
    }
    path = _ledger(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    from .heartbeat import _prune_heartbeat_ledger
    _prune_heartbeat_ledger(path)
    return row


def unread(config: dict | None, *, instance_id: str, slot: str = "", cwd: str = "") -> list[dict[str, Any]]:
    """Messages for this process not yet shown to it: addressed to its instance id or slot, or to ``project``
    from another MO in the same folder. Oldest first, at most five, none older than a day."""
    path = _ledger(config)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    try:
        seen = float(json.loads(_cursor(config, instance_id).read_text(encoding="utf-8")).get("at") or 0.0)
    except (OSError, ValueError, TypeError, AttributeError):
        seen = 0.0
    here = normalized_cwd(cwd)
    floor = max(seen, time.time() - MAX_AGE_SECONDS)
    mine = {str(instance_id), str(slot)} - {""}
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
            at = float(row.get("at") or 0.0)
        except (ValueError, TypeError, AttributeError):
            continue
        if at <= floor or row.get("from_instance") == instance_id:
            continue
        to = str(row.get("to") or "")
        if to in mine or (to == PROJECT and here and row.get("cwd") == here):
            rows.append(row)
    return rows[-MAX_SHOWN:]


def mark_delivered(config: dict | None, instance_id: str, rows: list[dict[str, Any]]) -> None:
    """Remember the newest message shown, so the next turn shows only newer ones."""
    if not rows:
        return
    newest = max(float(row.get("at") or 0.0) for row in rows)
    path = _cursor(config, instance_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"at": newest})


def render(rows: list[dict[str, Any]]) -> str:
    """One line per message for the turn's awareness note, with the sender's address to reply to."""
    now = time.time()
    lines = []
    for row in rows:
        minutes = max(0, int((now - float(row.get("at") or now)) // 60))
        age = "just now" if minutes == 0 else f"{minutes} min ago"
        sender = str(row.get("from_instance") or "?")
        slot = str(row.get("from_slot") or "")
        label = f"MO {sender}" + (f" ({slot})" if slot and slot != sender else "")
        scope = " to everyone here" if row.get("to") == PROJECT else ""
        lines.append(f"- {label}{scope}, {age}: {row.get('text')}")
    return "\n".join(lines)
