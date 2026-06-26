"""Runtime-owned DEVMODE run manifest — ONE authoritative projection of a session's
outputs (monitor, economy, taskboard, artifacts, status) so the model never hand-tracks
its own counts.

This is NOT a protocol report and NOT model-authored: it is built only from runtime truth
(backend monitor, economy summary, taskboard, the session's own files). Model-authored
artifacts should cite or be reconciled against it; it is an index/projection, never a
second source of truth.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_text

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1

# SESSION-LOCAL artifact files a DEVMODE session dir is expected to contain. The manifest
# indexes all of them, recording missing ones as explicit entries rather than silent
# absence. NOTE: `longitudinal.md` is deliberately excluded — it is a GLOBAL cross-session
# record (`~/.mo/memory/devmode/longitudinal.md`, one level up), not a session artifact, so
# listing it here reported a false "missing" (it never lives in the session dir).
SESSION_ARTIFACT_NAMES = (
    "summary.md",
    "workflow.md",
    "catalog.md",
    "capability-matrix.md",
    "economy.md",
    MANIFEST_NAME,
)
_ARTIFACT_NAMES = SESSION_ARTIFACT_NAMES
_RUNTIME_OWNED = {"economy.md", MANIFEST_NAME}


def artifact_entry(path: Path) -> dict[str, Any]:
    """Metadata for one artifact file — explicit existence, byte size, content hash."""
    path = Path(path)
    entry: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "exists": False,
        "bytes": 0,
        "sha256": None,
        "runtime_owned": path.name in _RUNTIME_OWNED,
    }
    try:
        if path.is_file():
            data = path.read_bytes()
            entry["exists"] = True
            entry["bytes"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
    except Exception:
        pass
    return entry


def manifest_artifact_entry(path: Path) -> dict[str, Any]:
    """Metadata for manifest.json itself.

    A manifest cannot honestly include its own content hash or byte size: writing those
    values changes the file being hashed. Record that self-reference explicitly instead
    of indexing a stale previous manifest.
    """
    path = Path(path)
    return {
        "name": path.name,
        "path": str(path),
        "exists": True,
        "bytes": None,
        "sha256": None,
        "runtime_owned": True,
        "self_referential": True,
    }


def _artifact_entries(session_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in _ARTIFACT_NAMES:
        path = session_dir / name
        if name == MANIFEST_NAME:
            entries.append(manifest_artifact_entry(path))
        else:
            entries.append(artifact_entry(path))
    return entries


def _taskboard_projection(task_board: Any) -> dict[str, Any]:
    """Project the live/final taskboard into the manifest — per-row evidence truth so a
    final-token-only or zero-evidence row is visible, not hidden."""
    if task_board is None:
        return {"state": None, "open_count": 0, "tasks": []}
    tasks: list[dict[str, Any]] = []
    for t in getattr(task_board, "tasks", []) or []:
        evidence = list(getattr(t, "evidence", []) or [])
        non_final = [e for e in evidence if not str(e).startswith("final:")]
        tasks.append({
            "id": str(getattr(t, "id", "")),
            "title": str(getattr(t, "title", "")),
            "status": str(getattr(t, "status", "")),
            "evidence_count": len(evidence),
            "non_final_evidence_count": len(non_final),
            "final_token_only": bool(evidence) and not non_final,
        })
    try:
        open_count = int(task_board.open_count())
    except Exception:
        open_count = sum(1 for t in tasks if t["status"] in {"pending", "active", "blocked"})
    return {
        "state": getattr(task_board, "state", None),
        "open_count": open_count,
        "tasks": tasks,
    }


def build_devmode_manifest(
    session_dir: Path,
    *,
    economy: dict[str, Any] | None = None,
    frozen_tool_errors: int | None = None,
    run_session_ids: "list[str] | set[str] | None" = None,
    instance_ids: "list[str] | set[str] | None" = None,
    surface: str | None = None,
    status: str = "active",
    accepted_as_baseline: bool = False,
    monitor_path: str | None = None,
    task_board: Any = None,
    warnings: "list[str] | None" = None,
    reconciliations: "dict[str, str] | None" = None,
) -> dict[str, Any]:
    """Build the manifest dict from already-authoritative runtime data only."""
    session_dir = Path(session_dir)
    eco = dict(economy or {})

    def _i(key: str) -> int:
        try:
            return int(eco.get(key, 0) or 0)
        except Exception:
            return 0

    taskboard = _taskboard_projection(task_board)
    # Reconcile the top-level status with the authoritative taskboard so the manifest can
    # never contradict itself. A completed board with no open rows is a complete run, not
    # "active". Two things used to leave a finalized manifest at "active": late
    # economy-ledger writes (which pass status="active") landing after closeout, and the
    # turn-health critical-budget path that force-emits [OWNER_MAINTENANCE COMPLETE] WITHOUT going
    # through the normal finalize that sets status="complete" (observed live in the
    # 2026-06-24T0404 run: top-level status "active" with taskboard "completed"/open 0).
    # Clamping here covers every completion path. Only the default "active" is clamped; an
    # explicit non-default status (e.g. "blocked") is preserved.
    effective_status = status
    if (status == "active"
            and str(taskboard.get("state") or "") == "completed"
            and int(taskboard.get("open_count") or 0) == 0):
        effective_status = "complete"

    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": "devmode",
        "session_dir": str(session_dir),
        "run_session_ids": sorted(str(s) for s in (run_session_ids or [])),
        "instance_ids": sorted(str(s) for s in (instance_ids or [])),
        "surface": surface,
        "status": effective_status,
        "accepted_as_baseline": bool(accepted_as_baseline),
        "monitor": {
            "path": str(monitor_path) if monitor_path else None,
            "source": eco.get("source"),
        },
        "economy": {
            "provider_requests": _i("provider_requests"),
            "provider_responses": _i("provider_responses"),
            "provider_errors": _i("provider_errors"),
            "tool_calls": _i("tool_calls"),
            "tool_errors": _i("tool_errors"),
            "sandbox_blocked": _i("sandbox_blocked"),
            "compression_events": _i("compression_events"),
            # The per-tool error/blocked NAMES from the monitor — the authoritative
            # error ledger the model must cite, not hand-author. Closing this gap is
            # what makes a mis-attributed tool-error ledger (the T2206 false-clean,
            # where read_file was blamed for test_runner/edit_file errors) impossible
            # to pass off as runtime truth.
            "error_tools": sorted(str(t) for t in (eco.get("error_tools") or []) if str(t).strip()),
            "blocked_tools": sorted(str(t) for t in (eco.get("blocked_tools") or []) if str(t).strip()),
            "frozen_tool_errors": (int(frozen_tool_errors)
                                   if frozen_tool_errors is not None else _i("tool_errors")),
        },
        "taskboard": taskboard,
        "artifacts": _artifact_entries(session_dir),
        "reconciliations": dict(reconciliations or {}),
        "warnings": list(warnings or []),
    }


def write_devmode_manifest(session_dir: Path, manifest: dict[str, Any]) -> bool:
    """Atomically write manifest.json into the session dir. Best-effort: a failure here
    must never break closeout."""
    try:
        path = Path(session_dir) / MANIFEST_NAME
        atomic_write_text(
            path,
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def load_devmode_manifest(session_dir: Path) -> dict[str, Any] | None:
    """Read the session's manifest.json, or None if absent/unreadable."""
    try:
        path = Path(session_dir) / MANIFEST_NAME
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
