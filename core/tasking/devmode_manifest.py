"""Runtime-owned DEVMODE run manifest — ONE authoritative projection of a session's
outputs (monitor, economy, taskboard, artifacts, status) so the model never hand-tracks
its own counts.

This is NOT a protocol report and NOT model-authored: it is built only from runtime truth
(backend monitor, economy summary, taskboard, the session's own files). Model-authored
artifacts should cite or be reconciled against it; it is an index/projection, never a
second source of truth. See docs/proposals/devmode-runtime-output-manifest-plan.md.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_text

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1

# Artifact files a DEVMODE session dir is expected to contain. The manifest indexes all
# of them, recording missing ones as explicit entries rather than silent absence.
_ARTIFACT_NAMES = (
    "summary.md",
    "workflow.md",
    "catalog.md",
    "capability-matrix.md",
    "economy.md",
    "longitudinal.md",
    MANIFEST_NAME,
)
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

    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": "devmode",
        "session_dir": str(session_dir),
        "run_session_ids": sorted(str(s) for s in (run_session_ids or [])),
        "instance_ids": sorted(str(s) for s in (instance_ids or [])),
        "surface": surface,
        "status": status,
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
            "frozen_tool_errors": (int(frozen_tool_errors)
                                   if frozen_tool_errors is not None else _i("tool_errors")),
        },
        "taskboard": _taskboard_projection(task_board),
        "artifacts": [artifact_entry(session_dir / name) for name in _ARTIFACT_NAMES],
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
