"""Private, revision-safe storage for Terminal Game Collaboration records."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, Iterator

from ..runtime.lock import file_byte_lock
from ..state.paths import GAME_COLLABORATION_DIR, resolve_state_path
from ..utils.atomic_write import atomic_write_json
from .model import bounded_copy, new_record, project_key_for, validate_record

_KEY_RE = re.compile(r"^[0-9a-f]{16}$")


class GameCollaborationError(RuntimeError):
    """Expected user-facing Game Collaboration failure."""


class GameCollaborationConflict(GameCollaborationError):
    """The record changed after the caller read its expected revision."""


class GameCollaborationStore:
    """Own the one canonical private record per normalized project root."""

    _thread_lock = threading.RLock()

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config if isinstance(config, dict) else {}
        self.root = Path(resolve_state_path(GAME_COLLABORATION_DIR, self.config))
        self.projects = self.root / "projects"
        self.lock_path = self.root / "records.lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with file_byte_lock(self.lock_path, self._thread_lock):
            yield

    @staticmethod
    def _check_key(project_key: str) -> str:
        key = str(project_key or "").strip().lower()
        if not _KEY_RE.fullmatch(key):
            raise GameCollaborationError("invalid Game Collaboration project")
        return key

    def _record_path(self, project_key: str) -> Path:
        return self.projects / self._check_key(project_key) / "record.json"

    def _revision_path(self, project_key: str, revision: int) -> Path:
        return self.projects / self._check_key(project_key) / "revisions" / f"{int(revision)}.json"

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GameCollaborationError("Game Collaboration record is unreadable") from exc
        try:
            return bounded_copy(validate_record(data))
        except (TypeError, ValueError) as exc:
            raise GameCollaborationError("Game Collaboration record is invalid") from exc

    def _load_unlocked(self, project_key: str) -> dict[str, Any] | None:
        path = self._record_path(project_key)
        return self._read_path(path) if path.exists() else None

    def load(self, project_key: str) -> dict[str, Any] | None:
        key = self._check_key(project_key)
        with self._locked():
            return self._load_unlocked(key)

    def create(self, project_root: str | Path, display_name: str = "") -> dict[str, Any]:
        record = new_record(project_root, display_name)
        key = record["project_key"]
        with self._locked():
            existing = self._load_unlocked(key)
            if existing is not None:
                return existing
            self._write_unlocked(record)
            return bounded_copy(record)

    def update(
        self,
        project_key: str,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        key = self._check_key(project_key)
        with self._locked():
            current = self._load_unlocked(key)
            if current is None:
                raise GameCollaborationError("Game Collaboration project not found")
            current_revision = int(current.get("record_revision", 0) or 0)
            if expected_revision is not None and current_revision != int(expected_revision):
                raise GameCollaborationConflict(
                    "Game project changed in another Terminal session; nothing was overwritten."
                )
            candidate = updater(deepcopy(current))
            if not isinstance(candidate, dict):
                raise GameCollaborationError("Game Collaboration update was invalid")
            candidate["record_revision"] = current_revision + 1
            candidate["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            result = bounded_copy(candidate)
            self._write_unlocked(result)
            return result

    def find(self, selector: str = "", *, project_root: str | Path | None = None) -> dict[str, Any] | None:
        wanted = str(selector or "").strip().casefold()
        root_key = project_key_for(project_root) if project_root else ""
        with self._locked():
            if root_key:
                exact = self._load_unlocked(root_key)
                if exact is not None and (not wanted or wanted in {root_key, str(exact.get("display_name", "")).casefold()}):
                    return exact
            if not self.projects.exists():
                return None
            matches: list[dict[str, Any]] = []
            for path in self.projects.glob("*/record.json"):
                try:
                    record = self._read_path(path)
                except GameCollaborationError:
                    continue
                if not wanted or wanted in {
                    str(record.get("project_key") or "").casefold(),
                    str(record.get("display_name") or "").casefold(),
                    str(record.get("project_root") or "").casefold(),
                }:
                    matches.append(record)
            if len(matches) > 1:
                raise GameCollaborationError("more than one Game Collaboration project matches; use its exact project path or name")
            return matches[0] if matches else None

    def list_records(self) -> list[dict[str, Any]]:
        with self._locked():
            if not self.projects.exists():
                return []
            rows: list[dict[str, Any]] = []
            for path in self.projects.glob("*/record.json"):
                try:
                    rows.append(self._read_path(path))
                except GameCollaborationError:
                    continue
            return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def _write_unlocked(self, record: dict[str, Any]) -> None:
        result = bounded_copy(record)
        key = self._check_key(str(result.get("project_key") or ""))
        path = self._record_path(key)
        atomic_write_json(path, result, ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write_json(
            self._revision_path(key, int(result["record_revision"])),
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
