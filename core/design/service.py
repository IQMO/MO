"""Private-state persistence owner for portable ``.modesign`` artifacts."""
from __future__ import annotations

import re
import secrets
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime.lock import file_byte_lock
from core.state.paths import MO_DESIGNS_DIR, resolve_state_path
from core.utils.atomic_write import atomic_write_text

from .board import BoardState, parse_board
from .schema import (
    DESIGN_EXTENSION,
    SCHEMA_ID,
    DesignDocument,
    DesignHandoff,
    DesignWindow,
    parse_design,
    render_design,
)
from .session import initialize_design_session


_DESIGN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_REVISION_DIR = "revisions"
_REVISION_LIMIT = 40
_DESIGN_WRITE_THREAD_LOCK = threading.RLock()


def create_design(
    title: str,
    *,
    summary: str = "",
    project_root: str = "",
    context_query: str = "",
    origin_intent: str = "",
    config: dict[str, Any] | None = None,
) -> tuple[Path, DesignDocument]:
    now = _now()
    design_id = f"{_slug(title, limit=36)}-{secrets.token_hex(4)}"
    document = parse_design({
        "mo": SCHEMA_ID,
        "meta": {
            "id": design_id,
            "title": str(title or "Untitled Design").strip() or "Untitled Design",
            "summary": str(summary or ""),
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        },
        "window": {},
        "runtime": {"allow_scripts": False},
        "design": {"html": _starter_html(title), "css": _starter_css(), "script": ""},
        "handoff": {
            "objective": str(summary or ""),
            "acceptance": [],
            "decisions": [],
            "constraints": [],
            "project_root": str(project_root or ""),
            "context_query": str(context_query or summary or title or ""),
            "files": [],
            "symbols": [],
        },
        "board": {
            "version": "board/v1", "revision": 0, "elements": [],
            "decisions": [], "questions": [],
        },
    })
    path = _path_for_id(design_id, config=config)
    with file_byte_lock(_design_lock_path(path), _DESIGN_WRITE_THREAD_LOCK):
        _write(path, document)
        _write_revision(path, document)
    initialize_design_session(
        path,
        design_id=document.meta.id,
        title=document.meta.title,
        origin_intent=origin_intent,
    )
    _record_design_revision(document, previous_revision=0)
    return path, document


def update_design(
    design_id: str,
    *,
    base_revision: int | None = None,
    expected_revision: int | None = None,
    title: str | None = None,
    summary: str | None = None,
    html: str | None = None,
    css: str | None = None,
    script: str | None = None,
    edits: list[dict[str, Any]] | None = None,
    allow_scripts: bool | None = None,
    window: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    board: BoardState | dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[Path, DesignDocument]:
    path = find_design(design_id, config=config)
    with file_byte_lock(_design_lock_path(path), _DESIGN_WRITE_THREAD_LOCK):
        current = load_design(path)
        if expected_revision is not None and current.meta.revision != expected_revision:
            raise ValueError("The Design changed. Reload before saving your edits.")
        return _commit_design_update(
            path,
            current,
            base_revision=base_revision,
            title=title,
            summary=summary,
            html=html,
            css=css,
            script=script,
            edits=edits,
            allow_scripts=allow_scripts,
            window=window,
            handoff=handoff,
            board=board,
            config=config,
        )


def _commit_design_update(
    path: Path,
    current: DesignDocument,
    *,
    base_revision: int | None = None,
    title: str | None = None,
    summary: str | None = None,
    html: str | None = None,
    css: str | None = None,
    script: str | None = None,
    edits: list[dict[str, Any]] | None = None,
    allow_scripts: bool | None = None,
    window: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    board: BoardState | dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[Path, DesignDocument]:
    """Commit one already-locked update against ``current``."""
    base = current
    if base_revision is not None:
        _base_path, base = load_design_revision(
            current.meta.id,
            int(base_revision),
            config=config,
        )
    _write_revision(path, current)
    content = replace(
        base.design,
        html=base.design.html if html is None else str(html),
        css=base.design.css if css is None else str(css),
        script=base.design.script if script is None else str(script),
        edits=base.design.edits if edits is None else tuple(edits),
    )
    runtime = replace(
        base.runtime,
        allow_scripts=base.runtime.allow_scripts if allow_scripts is None else bool(allow_scripts),
    )
    next_window = _replace_dataclass(base.window, window or {}, DesignWindow)
    next_handoff = _replace_dataclass(base.handoff, handoff or {}, DesignHandoff)
    next_board = base.board if board is None else (
        board if isinstance(board, BoardState) else parse_board(board)
    )
    updated = DesignDocument(
        meta=replace(
            current.meta,
            title=base.meta.title if title is None else str(title),
            summary=base.meta.summary if summary is None else str(summary),
            revision=current.meta.revision + 1,
            updated_at=_now(),
        ),
        window=next_window,
        runtime=runtime,
        design=content,
        handoff=next_handoff,
        board=next_board,
    )
    validated = parse_design(updated.to_dict())
    _write(path, validated)
    _write_revision(path, validated)
    _record_design_revision(validated, previous_revision=current.meta.revision)
    return path, validated


def _record_design_revision(document: DesignDocument, *, previous_revision: int) -> None:
    from hashlib import sha256
    from core.runtime.backend_monitor import get_monitor

    monitor = get_monitor()
    if monitor is not None:
        monitor.emit("session_event", {
            "kind": "design_revision", "design_id": document.meta.id,
            "revision": document.meta.revision, "previous_revision": previous_revision,
            "content_digest": sha256(render_design(document).encode("utf-8")).hexdigest(),
        })


def checkpoint_board(
    design_id: str,
    *,
    expected_design_revision: int,
    expected_board_revision: int,
    board: BoardState | dict[str, Any],
    config: dict[str, Any] | None = None,
) -> tuple[Path, DesignDocument]:
    """Compare and commit one Board burst under the design's writer lock."""
    path = find_design(design_id, config=config)
    with file_byte_lock(_design_lock_path(path), _DESIGN_WRITE_THREAD_LOCK):
        current = load_design(path)
        expected_design = int(expected_design_revision)
        if current.meta.revision != expected_design:
            raise ValueError(
                f"MO Design changed from r{expected_design} to r{current.meta.revision}; "
                "reload before saving the Board"
            )
        expected_board = int(expected_board_revision)
        if current.board.revision != expected_board:
            raise ValueError(
                f"MO Board changed from r{expected_board} to r{current.board.revision}; "
                "reload before saving"
            )
        candidate = board if isinstance(board, BoardState) else parse_board(board)
        if candidate.revision <= current.board.revision:
            raise ValueError("MO Board checkpoint must advance its revision")
        return _commit_design_update(path, current, board=candidate, config=config)


def list_design_revisions(
    design_id: str,
    *,
    config: dict[str, Any] | None = None,
    limit: int = _REVISION_LIMIT,
) -> list[dict[str, Any]]:
    """Return bounded private revision metadata without exposing filesystem paths."""
    path = find_design(design_id, config=config)
    current = load_design(path)
    rows: dict[int, dict[str, Any]] = {}
    revision_root = path.parent / _REVISION_DIR
    for candidate in revision_root.glob(f"r*{DESIGN_EXTENSION}") if revision_root.is_dir() else ():
        try:
            document = load_design(candidate)
        except (OSError, ValueError):
            continue
        if document.meta.id != current.meta.id or document.meta.revision > current.meta.revision:
            continue
        rows[document.meta.revision] = _revision_row(document, current=False)
    rows[current.meta.revision] = _revision_row(current, current=True)
    ordered = sorted(rows.values(), key=lambda row: int(row["revision"]), reverse=True)
    return ordered[:max(1, min(_REVISION_LIMIT, int(limit or _REVISION_LIMIT)))]


def load_design_revision(
    design_id: str,
    revision: int,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[Path, DesignDocument]:
    """Load one exact saved revision without changing the active artifact."""
    path = find_design(design_id, config=config)
    current = load_design(path)
    selected = int(revision)
    if selected == current.meta.revision:
        return path, current
    snapshot_path = _revision_path(path, selected)
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"MO Design revision r{selected} was not found")
    snapshot = load_design(snapshot_path)
    if snapshot.meta.id != current.meta.id or snapshot.meta.revision != selected:
        raise ValueError("MO Design revision does not belong to this design")
    return snapshot_path, snapshot


def delete_design_revision(
    design_id: str,
    revision: int,
    *,
    config: dict[str, Any] | None = None,
) -> None:
    """Delete one non-current private snapshot after its exact owner is verified."""
    path = find_design(design_id, config=config)
    with file_byte_lock(_design_lock_path(path), _DESIGN_WRITE_THREAD_LOCK):
        current = load_design(path)
        selected = int(revision)
        if selected == current.meta.revision:
            raise ValueError("The current MO Design revision cannot be deleted")
        snapshot_path, _snapshot = load_design_revision(
            design_id,
            selected,
            config=config,
        )
        snapshot_path.unlink()


def load_design(path: str | Path) -> DesignDocument:
    target = Path(path).expanduser().resolve(strict=False)
    if target.suffix.casefold() != DESIGN_EXTENSION:
        raise ValueError(f"MO Design opens only {DESIGN_EXTENSION} files")
    import zipfile

    if zipfile.is_zipfile(target):
        from .archive import read_archive

        return read_archive(target)[0]
    with target.open("rb") as stream:
        from .schema import MAX_DOCUMENT_BYTES

        return parse_design(stream.read(MAX_DOCUMENT_BYTES + 1))


def import_design(path: str | Path, *, config: dict[str, Any] | None = None) -> tuple[Path, DesignDocument]:
    """Open an external artifact as an isolated editable private Design copy."""
    import zipfile

    target = Path(path).expanduser().resolve(strict=True)
    document = load_design(target)
    canonical = _path_for_id(document.meta.id, config=config).resolve(strict=False)
    if target == canonical:
        return target, document
    data = document.to_dict()
    if zipfile.is_zipfile(target):
        from .archive import import_archive_images, read_archive

        _document, manifest = read_archive(target)
        imported = import_archive_images(target, manifest, config=config)
        for row in data.get("board", {}).get("elements", []):
            if row["kind"] == "image":
                row["attachment_id"] = imported[row["attachment_id"]]
    data["meta"]["id"] = f"{_slug(document.meta.title, limit=36)}-{secrets.token_hex(4)}"
    document = parse_design(data)
    destination = _path_for_id(document.meta.id, config=config)
    with file_byte_lock(_design_lock_path(destination), _DESIGN_WRITE_THREAD_LOCK):
        _write(destination, document)
        _write_revision(destination, document)
    initialize_design_session(destination, design_id=document.meta.id, title=document.meta.title)
    return destination, document


def find_design(design_id: str, *, config: dict[str, Any] | None = None) -> Path:
    clean = str(design_id or "").strip().lower()
    if not _DESIGN_ID_RE.fullmatch(clean):
        raise ValueError("invalid MO Design id")
    target = _path_for_id(clean, config=config)
    if not target.is_file():
        raise FileNotFoundError("MO Design was not found")
    return target


def list_designs(*, config: dict[str, Any] | None = None, limit: int = 24) -> list[dict[str, Any]]:
    root = Path(resolve_state_path(MO_DESIGNS_DIR, config))
    rows: list[tuple[float, dict[str, Any]]] = []
    for path in root.glob(f"*/*{DESIGN_EXTENSION}") if root.is_dir() else ():
        try:
            stat = path.stat()
            document = load_design(path)
            rows.append((stat.st_mtime, {
                "id": document.meta.id,
                "title": document.meta.title,
                "summary": document.meta.summary,
                "revision": document.meta.revision,
                "updated_at": document.meta.updated_at,
                "path": str(path),
                "filename": path.name,
            }))
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda item: item[0], reverse=True)
    return [row for _stamp, row in rows[:max(1, min(100, int(limit or 24)))]]


def _path_for_id(design_id: str, *, config: dict[str, Any] | None = None) -> Path:
    root = Path(resolve_state_path(MO_DESIGNS_DIR, config))
    return root / design_id / f"{design_id}{DESIGN_EXTENSION}"


def _design_lock_path(path: Path) -> Path:
    return path.parent / ".design.lock"


def _write(path: Path, document: DesignDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, render_design(document), encoding="utf-8")


def _write_revision(path: Path, document: DesignDocument) -> None:
    target = _revision_path(path, document.meta.revision)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        atomic_write_text(target, render_design(document), encoding="utf-8")
    snapshots = sorted(target.parent.glob(f"r*{DESIGN_EXTENSION}"))
    for stale in snapshots[:-_REVISION_LIMIT]:
        try:
            stale.unlink()
        except OSError:
            pass


def _revision_path(path: Path, revision: int) -> Path:
    selected = int(revision)
    if selected < 1:
        raise ValueError("MO Design revision must be positive")
    return path.parent / _REVISION_DIR / f"r{selected:06d}{DESIGN_EXTENSION}"


def _revision_row(document: DesignDocument, *, current: bool) -> dict[str, Any]:
    return {
        "revision": document.meta.revision,
        "title": document.meta.title,
        "summary": document.meta.summary,
        "updated_at": document.meta.updated_at,
        "current": bool(current),
    }


def _replace_dataclass(current: Any, values: dict[str, Any], expected: type) -> Any:
    if not isinstance(values, dict):
        raise ValueError(f"{expected.__name__} update must be an object")
    allowed = set(current.__dataclass_fields__)
    extra = sorted(str(key) for key in values if key not in allowed)
    if extra:
        raise ValueError(f"unsupported {expected.__name__} field: {extra[0]}")
    normalized = dict(values)
    for key in ("acceptance", "decisions", "constraints", "files", "symbols", "hints"):
        if key in normalized and isinstance(normalized[key], list):
            normalized[key] = tuple(normalized[key])
    return replace(current, **normalized)


def _slug(value: str, *, limit: int) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "design").lower()).strip("-")
    return (text or "design")[:limit].rstrip("-") or "design"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _starter_html(title: str) -> str:
    safe = str(title or "Your idea").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<main class="mo-start">'
        '<div class="mo-orbit"><i></i><i></i><i></i><i></i></div>'
        f'<p>READY TO SHAPE</p><h1>{safe}</h1>'
        '<span>Describe what you want to visualize, or attach a project and name the surface to map.</span>'
        '</main>'
    )


def _starter_css() -> str:
    return """
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--mo-preview-bg);color:var(--mo-preview-text)}
.mo-start{text-align:center}.mo-start p{margin:18px 0 5px;color:var(--mo-preview-brand);font-size:11px;font-weight:750;letter-spacing:.22em}
.mo-start{max-width:560px;padding:28px}.mo-start h1{margin:0;font-size:clamp(28px,5vw,54px);letter-spacing:-.04em}.mo-start span{display:block;margin:10px auto 0;max-width:460px;color:var(--mo-preview-muted);line-height:1.55}
.mo-orbit{position:relative;width:54px;height:54px;margin:auto}
.mo-orbit i{position:absolute;width:20px;height:20px;border-radius:var(--mo-preview-cube-radius);background:var(--mo-preview-cube);box-shadow:0 0 calc(12px * var(--mo-preview-cube-glow)) color-mix(in srgb,var(--mo-preview-cube) 24%,transparent)}
.mo-orbit i:nth-child(1){left:0;top:0}.mo-orbit i:nth-child(2){right:0;top:0}.mo-orbit i:nth-child(3){left:0;bottom:0}.mo-orbit i:nth-child(4){right:0;bottom:0}
""".strip()
