"""Low-level, capped, inert fetching for skill imports.

- HTTP GET via stdlib ``urllib`` behind an injectable opener (so the pipeline is
  testable offline and network use is explicit).
- Local file/dir reads with traversal safety and caps.
- Snapshot storage under profile state via ``resolve_state_path`` (never the checkout).

Nothing here executes fetched content. Network is refused unless the caller passes
``network_allowed=True`` (the runtime grants that, not this module).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ...state.paths import resolve_state_path
from ...utils.atomic_write import atomic_write_text

MAX_BYTES = 2 * 1024 * 1024      # 2 MB per source total
MAX_FILE_BYTES = 512 * 1024      # 512 KB per file
MAX_FILES = 60                   # cap files per import
_TEXT_SUFFIXES = frozenset({
    ".md", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
    ".yml", ".toml", ".cfg", ".ini", ".sh", ".go", ".rs", ".java", ".rb", ".html",
})

# An opener returns (status, body_bytes). Injectable for tests / offline use.
Opener = Callable[[str], "tuple[int, bytes]"]


def default_opener(url: str, *, timeout: float = 12.0) -> "tuple[int, bytes]":
    req = Request(url, headers={"User-Agent": "mo-skill-import/1.0"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - explicit, capped, gated
        return getattr(resp, "status", 200), resp.read(MAX_BYTES + 1)


def http_get(url: str, *, opener: Opener | None = None, network_allowed: bool = False) -> tuple[int, str]:
    """Fetch one URL as text, capped. Refuses network unless explicitly allowed."""
    if not network_allowed and opener is None:
        return 0, ""  # network not approved and no injected opener
    use = opener or default_opener
    try:
        status, body = use(url)
    except Exception:
        return 0, ""
    if len(body) > MAX_BYTES:
        body = body[:MAX_BYTES]
    try:
        return int(status), body.decode("utf-8", errors="replace")
    except Exception:
        return int(status), ""


def same_origin(base_url: str, candidate_url: str) -> bool:
    """True when candidate shares host (and is https/http) with base — docs-site default."""
    try:
        b, c = urlparse(base_url), urlparse(candidate_url)
    except Exception:
        return False
    if c.scheme not in ("http", "https"):
        return False
    return bool(c.netloc) and c.netloc.lower() == b.netloc.lower()


def digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def source_hash(files: dict[str, str]) -> str:
    """Stable content hash over the (path, content) set — drives refresh detection."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(str(files[path] or "").encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()


def build_catalog(files: dict[str, str]) -> dict[str, Any]:
    """File catalog: per-file digest + byte size, plus totals."""
    entries = []
    total = 0
    for path in sorted(files):
        content = str(files[path] or "")
        size = len(content.encode("utf-8", errors="replace"))
        total += size
        entries.append({"path": path, "bytes": size, "digest": digest(content)[:16]})
    return {"files": entries, "file_count": len(entries), "byte_count": total}


def read_local_source(local_path: str) -> dict[str, str]:
    """Read a local file or directory into a capped {relpath: text} map, text-only."""
    base = Path(os.path.expanduser(local_path))
    files: dict[str, str] = {}
    total = 0
    if base.is_file():
        candidates = [base]
        root = base.parent
    elif base.is_dir():
        candidates = sorted(p for p in base.rglob("*") if p.is_file())
        root = base
    else:
        return {}
    for path in candidates:
        if len(files) >= MAX_FILES or total >= MAX_BYTES:
            break
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            data = path.read_bytes()[:MAX_FILE_BYTES]
        except OSError:
            continue
        text = data.decode("utf-8", errors="replace")
        rel = str(path.relative_to(root)).replace("\\", "/")
        files[rel] = text
        total += len(data)
    return files


def snapshot_root(source_hash_value: str, *, config: dict[str, Any] | None = None) -> Path:
    """Profile-state directory for a source snapshot. Never under the checkout."""
    safe = "".join(ch for ch in str(source_hash_value or "anon") if ch.isalnum())[:16] or "anon"
    return Path(resolve_state_path(f"memory/skill_imports/{safe}", config or {}))


def write_snapshot(root: Path, files: dict[str, str]) -> None:
    """Write fetched source files under the snapshot dir as inert references."""
    for rel, content in files.items():
        safe_rel = Path(rel.replace("\\", "/"))
        if ".." in safe_rel.parts or safe_rel.is_absolute():
            continue
        atomic_write_text(str(root / "source" / safe_rel), str(content or "")[:MAX_FILE_BYTES], encoding="utf-8")
