"""TaskManager — single-file task persistence for the MO task board.

TaskManager stores current board truth as ``memory/taskboards/current.json``
so resume and contract checks can read state directly without scanning the
append-only ledger. The ledger remains the durable audit trail; current.json
is a fast-access working copy.

Architecture:
    record_snapshot()  ──writes──▶  ledger (append-only audit)
                       ──writes──▶  current.json (direct access)
    resume_last_board() ──reads──▶  current.json (fast path)
                       ──reads──▶  ledger (fallback)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class TaskManager:
    """Persist current task board state as a single JSON file.

    The manager is intentionally thin: it does not own task lifecycle or
    validation — those belong to ``TaskBoard`` and ``check_task_board_contract``.
    """

    def __init__(self, root: str | Path, *, tasks_dir: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.tasks_dir = Path(tasks_dir) if tasks_dir else self.root / "memory" / "taskboards"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.current_file = self.tasks_dir / "current.json"
        self._data: dict[str, Any] = {}
        self._load()

    # ── persistence ──────────────────────────────────────────

    def _load(self) -> None:
        if not self.current_file.exists():
            self._data = {"tasks": [], "updated_at": ""}
            return
        try:
            raw = self.current_file.read_text(encoding="utf-8")
            self._data = json.loads(raw)
            if not isinstance(self._data, dict):
                self._data = {"tasks": [], "updated_at": ""}
        except (json.JSONDecodeError, OSError):
            self._data = {"tasks": [], "updated_at": ""}

    def save(self, board_snapshot: dict[str, Any]) -> None:
        """Write the current board snapshot to current.json."""
        tasks_list = list(board_snapshot.get("tasks") or [])
        self._data = {
            "board_id": str(board_snapshot.get("board_id") or ""),
            "turn_id": str(board_snapshot.get("turn_id") or ""),
            "session_id": str(board_snapshot.get("session_id") or ""),
            "title": str(board_snapshot.get("title") or ""),
            "objective": str(board_snapshot.get("objective") or ""),
            "source": str(board_snapshot.get("source") or "gateway"),
            "state": str(board_snapshot.get("state") or "active"),
            "tasks": tasks_list,
            "created_at": float(board_snapshot.get("created_at") or 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.current_file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def clear(self) -> None:
        """Archive the current session then remove current.json."""
        if self._data.get("tasks"):
            self._archive()
        self._data = {"tasks": [], "updated_at": ""}
        try:
            if self.current_file.exists():
                self.current_file.unlink()
        except OSError:
            pass

    def _archive(self) -> None:
        archive_dir = self.tasks_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        archive_path = archive_dir / f"session_{ts}.json"
        try:
            archive_path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ── queries ──────────────────────────────────────────────

    def load_tasks(self) -> list[dict[str, Any]]:
        """Return the current persisted task list as plain dicts."""
        return list(self._data.get("tasks") or [])

    def load_snapshot(self) -> dict[str, Any]:
        """Return the full current.json payload (shallow copy)."""
        return dict(self._data)

    @property
    def has_active(self) -> bool:
        state = str(self._data.get("state") or "")
        return state not in ("completed", "")

    @property
    def task_count(self) -> int:
        return len(self._data.get("tasks") or [])

    @property
    def board_id(self) -> str:
        return str(self._data.get("board_id") or "")

    @property
    def turn_id(self) -> str:
        return str(self._data.get("turn_id") or "")


def task_manager_for_root(root: str | Path | None = None) -> TaskManager:
    """Return a TaskManager scoped to *root* (defaults to cwd)."""
    return TaskManager(root or Path.cwd())
