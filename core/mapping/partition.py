"""The mapthis "brain": build the project file inventory and split it into N
balanced, non-overlapping, subsystem-coherent slices — one per worker.

Reuses MO's own file discovery (``code_graph._discover_files``, the same set the
structural graph indexes) so the map covers exactly what MO sees, with a bounded
walk fallback. Partitioning is deterministic: every file lands in exactly one
slice (so worker ``claimed_paths`` never overlap) and slice sizes differ by at
most one, while keeping each subsystem's files together where possible.
"""
from __future__ import annotations

import os
from pathlib import Path


def _norm(path: object) -> str:
    return str(path or "").replace("\\", "/").strip()


def project_file_inventory(root: str | Path | None = None) -> tuple[list[str], Path]:
    """Return (sorted unique relative file paths, resolved project root)."""
    from ..graph.structural_graph import project_root

    base = project_root(root)
    files: list[str] = []
    try:
        from ..graph.code_graph import _discover_files

        files = list(_discover_files(base))
    except Exception:
        files = []
    if not files:  # non-git project or discovery unavailable → bounded walk
        files = _fallback_walk(base)
    seen = sorted({_norm(f) for f in files if _norm(f)})
    return seen, base


def _fallback_walk(base: Path, *, limit: int = 5000) -> list[str]:
    """Bounded, gitignore-agnostic walk used only when discovery is unavailable."""
    skip = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}
    out: list[str] = []
    base = Path(base)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            try:
                rel = str(Path(dirpath, name).relative_to(base))
            except ValueError:
                continue
            out.append(rel)
            if len(out) >= limit:
                return out
    return out


def subsystem_key(path: str, *, depth: int = 2) -> str:
    """A coherent grouping key: the top ``depth`` path segments (dirs), so a file
    stays grouped with its subsystem (e.g. ``core/agent``). Root files group as
    ``(root)``."""
    parts = [p for p in _norm(path).split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[: min(depth, len(parts) - 1)])


def slice_scope_label(files: list[str]) -> str:
    """Return a truthful compact label for a possibly mixed mapper slice."""
    normalized = [_norm(path) for path in files if _norm(path)]
    areas = {
        path.split("/", 1)[0] if "/" in path else "(root)"
        for path in normalized
    }
    subsystems = {subsystem_key(path) for path in normalized}
    if not areas:
        return "empty slice"
    if len(areas) == 1:
        area = next(iter(areas))
        if len(subsystems) == 1:
            return next(iter(subsystems))
        return f"{area} · {len(subsystems)} subsystems"
    return f"mixed · {len(areas)} areas · {len(subsystems)} subsystems"


# Tests are project surface, not architecture — they collapse into ONE lightweight
# slice instead of consuming a deep mapping budget (198 test files were eating ~40%).
_TEST_DIRS = ("tests", "test")


def _is_test_file(path: str) -> bool:
    # Directory-based only — the tests tree collapses; a stray ``test_*.py`` that lives
    # inside a real subsystem (e.g. a diagnostic) stays with its code, not miscounted.
    parts = _norm(path).split("/")
    return bool(parts) and parts[0] in _TEST_DIRS


def decide_worker_count(file_count: int, *, subsystem_count: int = 0, per_worker: int = 120, lo: int = 2, hi: int = 8) -> int:
    """MO sizes the number of mapping agents by the project — not a hardcoded 4:
    ~one worker per ``per_worker`` files, clamped to ``[lo, hi]``, and never more than
    there are coherent subsystems to split."""
    n = max(1, round(max(0, int(file_count)) / max(1, per_worker)))
    n = max(lo, min(hi, n))
    if subsystem_count:
        n = min(n, max(lo, int(subsystem_count)))
    return n


def _file_weight(path: str, base: object) -> int:
    """Content-weight proxy for balancing — byte size (a stat, no read); min 1."""
    if base is None:
        return 1
    try:
        return max(1, (Path(base) / path).stat().st_size)
    except Exception:
        return 1


def _weighted_subsystem_chunks(files: list[str], n: int, base: object) -> list[list[str]]:
    """Group files by subsystem (kept whole/coherent) and greedily pack the groups
    into ``n`` slices balanced by content WEIGHT — so a slice of dense core files and a
    slice of thin utilities carry comparable real work, not just equal file counts."""
    if n <= 1 or len(files) <= 1:
        return [list(files)] if files else []
    groups: dict[str, list[str]] = {}
    for f in files:
        groups.setdefault(subsystem_key(f), []).append(f)
    weights = {sub: sum(_file_weight(f, base) for f in fs) for sub, fs in groups.items()}
    bins: list[list[str]] = [[] for _ in range(n)]
    bin_w = [0] * n
    # heaviest subsystems first, each into the currently-lightest bin (greedy balance)
    for sub in sorted(groups, key=lambda s: (-weights[s], s)):
        i = min(range(n), key=lambda j: bin_w[j])
        bins[i].extend(groups[sub])
        bin_w[i] += weights[sub]
    return [sorted(b) for b in bins if b]


def partition_files(files: list[str], n: int = 4, base: object = None) -> list[list[str]]:
    """Split files into balanced, non-overlapping, subsystem-coherent slices. Tests
    collapse into one lightweight slice; the rest are packed by content weight across
    the remaining slices. Every file lands in exactly one slice; empties are dropped."""
    uniq = sorted({_norm(f) for f in (files or []) if _norm(f)})
    if not uniq:
        return []
    tests = [f for f in uniq if _is_test_file(f)]
    code = [f for f in uniq if not _is_test_file(f)]
    code_n = max(1, n - (1 if tests else 0))
    slices = _weighted_subsystem_chunks(code, code_n, base) if code else []
    if tests:
        slices.append(tests)
    return [s for s in slices if s]


def slice_summary(slice_files: list[str]) -> dict[str, object]:
    """Compact description of one slice: file count and the subsystems it spans."""
    subs = sorted({subsystem_key(f) for f in slice_files})
    return {"files": len(slice_files), "subsystems": subs}
