"""Detect code changed without its directly-affected documentation updated.

MO's contract (AGENTS.md, the system prompt) says to update directly affected
documentation when behavior changes. That was convention only: no gate checked
it, so a turn could change product behavior and leave ``CHANGELOG.md``/``MAP.md``
untouched with nothing catching it.

This gate supplies the missing deterministic check. It makes NO model call: it
compares the paths modified this turn against the tracked documentation owners
and returns at most one bounded nudge.

Deliberately HIGH-PRECISION, mirroring ``capture_detection``: it fires only for
tracked product-source edits, never for docs-only, test-only, or scratch work.
Gated by ``gates.documentation_update`` (default on).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Product-source roots whose behavior changes carry a documentation obligation.
_SOURCE_ROOTS = ("core/", "interface/", "tools/", "mo_desktop/", "mo_everyday/", "clients/")
# Documentation owners that satisfy the obligation when touched in the same turn.
_DOC_PATHS = (
    "changelog.md",
    "map.md",
    "readme.md",
    "capabilities.md",
    "faq.md",
    "maintaining.md",
)


@dataclass(frozen=True)
class DocumentationGateResult:
    instruction: str = ""
    blocked_text: str = ""


def _normalize(path: Any) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("./").casefold()


def _is_source_path(path: str) -> bool:
    return path.endswith(".py") and any(path.startswith(root) for root in _SOURCE_ROOTS)


def _is_doc_path(path: str) -> bool:
    return path.endswith(".md") and any(path.endswith(owner) for owner in _DOC_PATHS)


def documentation_update_signal(turn_modified_files: Any) -> list[str]:
    """Return tracked source paths edited this turn with no doc updated alongside.

    Empty when the turn touched no tracked source, or when it also updated a
    documentation owner. Scratch, tests, and docs-only turns never fire.
    """
    source_paths: list[str] = []
    doc_touched = False
    for entry in turn_modified_files or ():
        raw = entry[0] if isinstance(entry, (list, tuple)) and entry else entry
        path = _normalize(raw)
        if not path:
            continue
        if _is_doc_path(path):
            doc_touched = True
        elif _is_source_path(path):
            source_paths.append(path)
    if not source_paths or doc_touched:
        return []
    return sorted(set(source_paths))


def run_documentation_gate(
    agent: Any,
    turn_modified_files: Any,
    *,
    fired: set,
    on_activity: Any = None,
    monitor: Any = None,
) -> DocumentationGateResult:
    """Return one documentation nudge when source changed without its docs.

    Gated by ``gates.documentation_update`` (default on). Never blocks: the
    provider remains the author of any documentation change.
    """
    cfg = getattr(agent, "config", {})
    cfg = cfg if isinstance(cfg, dict) else {}
    gates_cfg = cfg.get("gates") if isinstance(cfg.get("gates"), dict) else {}
    if not gates_cfg.get("documentation_update", True):
        return DocumentationGateResult()
    if "documentation_update" in fired:
        return DocumentationGateResult()
    changed = documentation_update_signal(turn_modified_files)
    if not changed:
        return DocumentationGateResult()
    fired.add("documentation_update")
    if monitor:
        try:
            monitor.emit("documentation_update", {"paths": changed[:8]})
        except Exception:
            pass
    if on_activity:
        on_activity("source changed without its docs - checking documentation...")
    shown = ", ".join(f"`{path}`" for path in changed[:6])
    return DocumentationGateResult(
        instruction=(
            "[DOCUMENTATION] This turn changed product source ("
            f"{shown}) but updated no directly-affected documentation. Check whether the "
            "change alters documented behavior, structure, or capability; if it does, update "
            "the owning doc (CHANGELOG.md for shipped behavior, MAP.md for structure, "
            "README/CAPABILITIES/FAQ for user-facing behavior) in this turn. If no "
            "documentation is affected, state that explicitly in your answer and continue."
        )
    )


__all__ = [
    "DocumentationGateResult",
    "documentation_update_signal",
    "run_documentation_gate",
]
