"""Build a bounded skeleton digest for a slice — the fix for the token explosion.

The first cut had each worker READ its ~118 whole files one by one, so context
grew every step (10k -> 133k tokens, 27+ requests, millions of tokens). Instead,
the pipeline skeletonizes the slice's files LOCALLY (AST parse via
``code_skeleton`` — zero model tokens) into one compact, capped digest of
signatures/structure, and hands that to the worker as a single bounded input.
The worker maps only from that structure and must omit details the digest does
not contain.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..tooling.code_skeleton import code_skeleton

_PY = {".py", ".pyi"}
_TEXT_HEAD = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".tsx", ".sh", ".bat", ".ps1"}


@dataclass(frozen=True)
class SliceDigest:
    text: str
    shown: int
    total: int
    omitted: int

    def coverage(self) -> dict[str, int]:
        return {"shown": self.shown, "total": self.total, "omitted": self.omitted}


def _deepest_files(files: list[str], base: object, k: int) -> list[str]:
    """The ``k`` largest files in the slice — a cheap proxy for its most substantial /
    central modules, which earn a larger skeleton instead of a thin one."""
    if base is None or k <= 0:
        return []
    sized: list[tuple[int, str]] = []
    for f in files:
        try:
            sized.append(((Path(base) / str(f)).stat().st_size, str(f)))
        except Exception:
            continue
    sized.sort(reverse=True)
    return [f for _size, f in sized[:k]]


def build_slice_digest(
    files: list[str],
    base: str | Path,
    *,
    per_file_chars: int | None = None,
    total_chars: int = 150000,
    deep_k: int = 8,
    deep_chars: int = 3000,
) -> str:
    """Return a bounded-deep skeleton digest of ``files`` (relative to ``base``).

    Bounded, but not shallow: the bulk of files get a skeleton sized to a fair share of
    the budget (so a mapper sees real signatures + docstrings, not five truncated
    lines), and the slice's ``deep_k`` largest/most-substantial files get a richer skeleton
    and lead the digest so more of the architecture spine is represented — the difference
    between mapping and guessing. The total stays hard-capped so a richer digest can
    never make a worker's input balloon."""
    return build_slice_digest_result(
        files,
        base,
        per_file_chars=per_file_chars,
        total_chars=total_chars,
        deep_k=deep_k,
        deep_chars=deep_chars,
    ).text


def build_slice_digest_result(
    files: list[str],
    base: str | Path,
    *,
    per_file_chars: int | None = None,
    total_chars: int = 150000,
    deep_k: int = 8,
    deep_chars: int = 3000,
) -> SliceDigest:
    """Build one digest and return its exact bounded coverage receipt."""
    base = Path(base)
    files = [str(f) for f in files]
    if per_file_chars is None:
        # each file's fair share of the budget, floored so it's never thin, capped so
        # one huge slice doesn't blow past the total on the bulk alone.
        per_file_chars = max(500, min(2000, total_chars // max(1, len(files))))
    deep = _deepest_files(files, base, deep_k)
    deep_set = set(deep)
    ordered = deep + [f for f in files if f not in deep_set]  # substantial files first
    parts: list[str] = []
    used = 0
    shown = 0
    for rel in ordered:
        if used >= total_chars:
            break
        cap = deep_chars if rel in deep_set else per_file_chars
        path = base / rel
        try:
            if path.stat().st_size > 400_000:  # skip very large files — note only
                body = f"(large file, {path.stat().st_size // 1024} KB — skeleton skipped)"
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
                ext = path.suffix.lower()
                if ext in _PY:
                    body = code_skeleton(text, max_chars=cap)
                elif ext in _TEXT_HEAD:
                    body = "\n".join(text.splitlines()[:10])[:cap]
                else:
                    body = f"({ext or 'no-ext'} file, {len(text)} chars)"
        except Exception:
            continue
        body = (body or "").strip() or "(empty or unparseable)"
        entry = f"### {rel}\n{body}\n"
        parts.append(entry)
        used += len(entry)
        shown += 1
    digest = "\n".join(parts)
    if len(digest) > total_chars:
        digest = digest[:total_chars].rsplit("\n", 1)[0]
    omitted = len(files) - shown
    if omitted > 0:
        digest += (
            f"\n\n[digest cap: {shown}/{len(files)} files shown; {omitted} omitted — "
            "make no claims about the omitted files in this pass]"
        )
    return SliceDigest(text=digest, shown=shown, total=len(files), omitted=max(0, omitted))
