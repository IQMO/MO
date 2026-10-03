"""PRT admission over the existing per-Agent Gateway turn owner."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class PrtAdmission:
    """Result of this Agent's bounded review admission."""

    acquired: bool
    reason: str = ""
    detail: str = ""
    pinned_revision: str = ""


def _workspace(agent: Any) -> Path:
    raw = getattr(agent, "workspace", None) or getattr(agent, "project_cwd", None) or os.getcwd()
    return Path(str(raw)).resolve(strict=False)


def _revision(workspace: Path, diff_ref: str) -> str:
    # Use the diff owner's identity for every target, not a second ref/path
    # heuristic: `git rev-parse .` can succeed while returning the literal dot.
    try:
        from core.review.diff_review import resolve_review_target

        target = resolve_review_target(workspace, diff_ref)
        return f"{target.reviewed_oid}:{target.evidence_digest}"
    except Exception:
        return str(diff_ref or "HEAD").strip()


@contextmanager
def acquire_prt_execution(
    agent: Any,
    diff_ref: str = "HEAD",
    *,
    timeout_s: float = 120.0,
) -> Iterator[PrtAdmission]:
    """Protect this Agent's provider turn; source coherence belongs to the snapshot.

    Other terminals, goals and workers do not own the review's copied source.
    The bounded wait is local to this Agent, never a repository-wide lease.
    """
    workspace = _workspace(agent)
    pinned_revision = _revision(workspace, diff_ref)
    gateway = getattr(agent, "gateway", None)
    turn_scope = getattr(gateway, "prt_turn_scope", None)
    scope = turn_scope(timeout_s=max(0.0, float(timeout_s or 0.0))) if callable(turn_scope) else nullcontext(True)
    with scope as acquired:
        if not acquired:
            yield PrtAdmission(False, "foreground_busy", "this terminal's foreground turn did not release before the bounded wait expired", pinned_revision)
            return
        if _revision(workspace, diff_ref) != pinned_revision:
            yield PrtAdmission(False, "target_changed", "the requested review target changed while this terminal waited for its foreground turn", pinned_revision)
            return
        yield PrtAdmission(True, pinned_revision=pinned_revision)


__all__ = ["PrtAdmission", "acquire_prt_execution"]
