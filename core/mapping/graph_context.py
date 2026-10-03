"""Per-slice structural context — MO's graph brain, shared with each mapper worker.

The workers map from file skeletons alone; they cannot see the cross-file wiring MO's
structural graph already knows: who reaches this slice, what it depends on, and the
architectural backbone. This hands each worker its slice's REAL graph facts, so its
"entry points & wiring" is grounded in MO's brain instead of inferred from local
imports — MO is the brain that maps and dispatches; the workers should map with its
knowledge, not blind.

Reuses MO's OWN graph (``get_callers``/``get_callees`` + the structural backbone) and
NEVER builds one just for this — returns "" when no graph exists, so the skeleton-only
behavior is fully preserved. Never raises.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SYMBOL_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M)


def _slice_symbols(files: list[str], base: Any, *, max_files: int, max_symbols: int) -> list[str]:
    """Public top-level ``def``/``class`` names from the slice's most-substantial files —
    a local, zero-model extraction (the symbols other slices would reach into)."""
    from .digest import _deepest_files

    picks = _deepest_files(files, base, max_files) or [str(f) for f in files][:max_files]
    names: list[str] = []
    for rel in picks:
        try:
            text = (Path(base) / str(rel)).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _SYMBOL_RE.finditer(text):
            name = m.group(1)
            if not name.startswith("_") and name not in names:
                names.append(name)
                if len(names) >= max_symbols:
                    return names
    return names


def slice_graph_context(files: list[str], base: Any, *, max_symbols: int = 12, top: int = 8) -> str:
    """A compact structural-context block for one slice, from MO's graph. '' when no
    graph exists (then the worker maps from skeletons only, as before)."""
    try:
        from ..graph.structural_graph import graph_exists, graph_status
        if not base or not graph_exists(base) or graph_status(base).get("stale"):
            return ""
        from ..graph.callgraph import get_callees, get_callers
    except Exception:
        return ""

    slice_files = {str(f) for f in files}
    cwd = str(base)
    reached_by: set[str] = set()
    depends_on: set[str] = set()
    for sym in _slice_symbols(files, base, max_files=6, max_symbols=max_symbols):
        try:
            for c in get_callers(sym, cwd=cwd):
                cf = str(c.get("caller_file") or "")
                if cf and cf not in slice_files:   # a caller in ANOTHER slice = a real entry point
                    reached_by.add(str(c.get("caller_label") or cf))
        except Exception:
            pass
        try:
            for c in get_callees(sym, cwd=cwd):
                cf = str(c.get("callee_file") or "")
                if cf and cf not in slice_files:   # a callee in ANOTHER slice = a cross-slice dep
                    depends_on.add(str(c.get("callee_label") or cf))
        except Exception:
            pass

    lines: list[str] = []
    if reached_by:
        lines.append("- Reached by (other parts of the project call into this slice): "
                     + ", ".join(sorted(reached_by)[:top]))
    if depends_on:
        lines.append("- Depends on (this slice calls out to other slices): "
                     + ", ".join(sorted(depends_on)[:top]))
    try:
        from .synthesize import structural_backbone_lines
        backbone = structural_backbone_lines(base, max_chars=400)
        if backbone:
            lines.append("- Architectural backbone (whole project): " + " · ".join(backbone[:3]))
    except Exception:
        pass

    if not lines:
        return ""
    return ("### Structural context (from MO's graph — the cross-file wiring the skeletons "
            "cannot show). Ground your Entry-points/wiring and Notable sections in these facts:\n"
            + "\n".join(lines) + "\n\n")


def graph_orientation_status(base: Any) -> dict[str, Any]:
    """Project graph provenance already computed by the structural-graph owner."""
    try:
        from ..graph.structural_graph import graph_status

        status = graph_status(base)
    except Exception:
        return {"available": False, "reason": "unavailable"}
    if not status.get("available"):
        return {"available": False, "reason": str(status.get("load_error") or "missing")}
    if status.get("stale"):
        return {"available": False, "reason": "stale"}
    return {
        "available": True,
        "source_kind": str(status.get("source_kind") or "native"),
        "confidence_breakdown": dict(status.get("confidence_breakdown") or {}),
        "provenance_breakdown": dict(status.get("provenance_breakdown") or {}),
    }
