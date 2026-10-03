"""MO Design tool adapter; schema/state/rendering remain in their owners."""
from __future__ import annotations

import json
import os
from typing import Any

from core.design.session import (
    complete_design_session,
    mark_design_baseline,
    reopen_design_session,
)


_DEFAULT_READ_CHARS = 4_800
_MAX_READ_CHARS = 5_600
_VISUAL_INTENTS = frozenset({"current_state", "refinement", "new_concept"})


def execute_mo_design(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    action = str(args.pop("action", "") or "").strip().lower()
    try:
        if action == "open":
            return _open(args)
        if action == "update":
            return _update(args)
        if action == "show":
            return _show(args)
        if action == "read":
            return _read(args)
        if action == "list":
            return _list(args)
        if action == "complete":
            return _complete(args)
        if action == "board_read":
            return _board_read(args)
        if action == "board_propose":
            return _board_propose(args)
        return "Error: mo_design action must be open, update, show, read, list, complete, board_read, or board_propose"
    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError) as exc:
        message = str(exc) or type(exc).__name__
        if action == "board_propose":
            message += ". Call board_read and follow its operation_contract before retrying"
        return f"Error: {message}"


def _open(args: dict[str, Any]) -> str:
    from core.design.service import create_design

    if str(args.get("design_id") or "").strip():
        raise ValueError(
            "mo_design open creates a new artifact and does not accept design_id; "
            "use action=show to reopen the existing design"
        )
    if args.get("operations") not in (None, "", []):
        raise ValueError(
            "mo_design open does not accept Board operations; open view=board first, "
            "then call board_read and board_propose"
        )
    title = str(args.get("title") or "Untitled Design")
    board_view = str(args.get("view") or "").strip().lower() == "board"
    project_root = str(args.get("project_root") or "")
    visual_intent = "" if board_view else _visual_intent(args.get("visual_intent"), project_root)
    if project_root and visual_intent != "current_state":
        raise ValueError(
            "Selected-project Design must begin as current_state; use action=list, then read/show an existing matching Design before creating another. Verify its current surface before refining it"
        )
    surface = str(args.get("_mo_surface") or "terminal")
    target = _board_terminal_target(project_root=project_root, surface=surface)
    if board_view and target is not None:
        from core.design.terminal_handoff import terminal_board_binding

        if terminal_board_binding(target):
            raise RuntimeError(
                "This MO terminal already has a standalone Board. Close it before opening another."
            )
    path, document = create_design(
        title,
        summary=str(args.get("summary") or ""),
        project_root=project_root,
        context_query=str(args.get("context_query") or ""),
        origin_intent=visual_intent,
    )
    status = _open_design_view(
        path,
        document.meta.id,
        board_view=board_view,
        target=target,
        surface=surface,
    )
    return json.dumps({
        "status": status,
        "design_id": document.meta.id,
        "path": str(path),
        "view": "board" if board_view else "preview",
        "visual_intent": visual_intent or None,
        "next": (
            "Call mo_design action=board_read, then board_propose for any MO marks; the user reviews the draft."
            if board_view else
            "Call mo_design action=update with this design_id; send only changed fields and preserve the existing visual and brief."
        ),
    }, ensure_ascii=False)


def _update(args: dict[str, Any]) -> str:
    from core.design.service import find_design, load_design, update_design
    from core.design.streaming import clear_live_preview

    design_id = str(args.get("design_id") or "")
    current_path = find_design(design_id)
    current = load_design(current_path)
    session = reopen_design_session(
        current_path,
        design_id=current.meta.id,
        title=current.meta.title,
    )
    requested_intent = _visual_intent(
        args.get("visual_intent") or session.get("origin_intent"), current.handoff.project_root,
    )
    if current.handoff.project_root and requested_intent == "refinement":
        _require_current_baseline(current, session)
    base_revision = _pending_source_revision(current_path, current)
    path, document = update_design(
        design_id,
        base_revision=base_revision,
        title=args.get("title"),
        summary=args.get("summary"),
        html=args.get("html"),
        css=args.get("css"),
        script=args.get("script"),
        edits=args.get("edits"),
        allow_scripts=args.get("allow_scripts"),
        window=args.get("window"),
        handoff=args.get("handoff"),
    )
    if current.handoff.project_root and requested_intent == "current_state":
        mark_design_baseline(
            path,
            design_id=document.meta.id,
            title=document.meta.title,
            revision=document.meta.revision,
        )
    clear_live_preview(document.meta.id)
    return json.dumps({
        "status": "updated",
        "design_id": document.meta.id,
        "revision": document.meta.revision,
        "path": str(path),
        "preview": "hot-reloaded",
        "visual_intent": requested_intent,
    }, ensure_ascii=False)


def _require_current_baseline(document: Any, session: dict[str, Any]) -> None:
    try:
        baseline_revision = int(session.get("baseline_revision") or 0)
    except (TypeError, ValueError):
        baseline_revision = 0
    if baseline_revision < 1:
        raise ValueError(
            "Selected-project refinement requires a verified current_state baseline first; "
            "update this Design with visual_intent=current_state before refining it"
        )


def _visual_intent(value: Any, project_root: str) -> str:
    intent = str(value or "").strip().lower()
    if not intent:
        return "current_state" if str(project_root or "").strip() else "new_concept"
    if intent not in _VISUAL_INTENTS:
        raise ValueError("visual_intent must be current_state, refinement, or new_concept")
    return intent


def _show(args: dict[str, Any]) -> str:
    from core.design.service import find_design, load_design

    path = find_design(str(args.get("design_id") or ""))
    document = load_design(path)
    reopen_design_session(
        path,
        design_id=document.meta.id,
        title=document.meta.title,
    )
    board_view = str(args.get("view") or "").strip().lower() == "board"
    surface = str(args.get("_mo_surface") or "terminal")
    target = _board_terminal_target(
        project_root=str(args.get("project_root") or document.handoff.project_root or ""),
        surface=surface,
    )
    status = _open_design_view(
        path,
        document.meta.id,
        board_view=board_view,
        target=target,
        surface=surface,
    )
    return json.dumps({
        "status": status, "design_id": document.meta.id, "path": str(path),
        "view": "board" if board_view else "preview",
    })


def _open_design_view(
    path: Any,
    design_id: str,
    *,
    board_view: bool,
    target: dict[str, Any] | None,
    surface: str,
) -> str:
    if not board_view:
        _record_design_origin(path, target)
        return _open_preview(path, design_id, surface=surface)
    return _launch_standalone_board(path, design_id, target)


def _open_preview(path: Any, design_id: str, *, surface: str = "terminal") -> str:
    """Route by the invoking conversation, not its executable host."""
    from mo_desktop.desktop_launch import desktop_resident_status, request_mo_desktop_summon

    terminal_synced = surface == "terminal"
    resident = desktop_resident_status()
    if resident.state == "ready" and resident.lock_owner is not None:
        if request_mo_desktop_summon(resident.lock_owner, design_id, terminal_synced=terminal_synced):
            return "opening"
    from mo_desktop.mo_renderer import launch_renderer

    launch_renderer(path, terminal_synced=terminal_synced)
    return "opened"


def _board_terminal_target(*, project_root: str = "", surface: str = "terminal") -> dict[str, Any] | None:
    if surface != "terminal":
        from mo_desktop.design_studio.routing import current_terminal_target

        return current_terminal_target(project_root=project_root)
    from core.runtime.instance import get_instance_id

    return {"instance_id": get_instance_id(), "pid": os.getpid()}


def _record_design_origin(path: Any, target: dict[str, Any] | None) -> None:
    """Persist the exact opening terminal so final Handoff can return to it."""
    if target is None:
        return
    from core.design.board import set_board_origin

    set_board_origin(path, target)


def _launch_standalone_board(
    path: Any,
    design_id: str,
    target: dict[str, Any] | None,
) -> str:
    from core.design.board import set_board_origin
    from core.design.terminal_handoff import release_terminal_board, reserve_terminal_board
    from mo_desktop.mo_renderer import focus_renderer_pid, launch_renderer

    if target is None:
        launch_renderer(path, standalone_board=True)
        return "opened"
    exact_target = target
    reservation = reserve_terminal_board(design_id, exact_target)
    status = str(reservation.get("status") or "")
    if status != "reserved":
        renderer_pid = int(reservation.get("renderer_pid") or 0)
        return "focused" if renderer_pid > 0 and focus_renderer_pid(renderer_pid) else "opening"
    token = str(reservation.get("token") or "")
    instance_id = str(reservation.get("instance_id") or "")
    try:
        set_board_origin(path, exact_target)
        launch_renderer(
            path,
            standalone_board=True,
            board_link_instance=instance_id,
            board_link_token=token,
        )
    except Exception:
        release_terminal_board(instance_id, token)
        raise
    return "opened"


def _read(args: dict[str, Any]) -> str:
    from core.design.schema import render_design
    from core.design.service import find_design, load_design, load_design_revision

    path = find_design(str(args.get("design_id") or ""))
    current = load_design(path)
    source_revision = args.get("revision")
    if source_revision is None:
        source_revision = _pending_source_revision(path, current)
    source_path, document = load_design_revision(
        current.meta.id,
        source_revision if source_revision is not None else current.meta.revision,
    )
    text = render_design(document)
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        requested = int(args.get("max_chars") or _DEFAULT_READ_CHARS)
    except (TypeError, ValueError):
        requested = _DEFAULT_READ_CHARS
    max_chars = max(800, min(requested, _MAX_READ_CHARS))
    end = min(len(text), offset + max_chars)
    chunk = text[offset:end] if offset < len(text) else ""
    next_offset = end if end < len(text) else None
    continuation = (
        f"Next offset: {next_offset} (request mo_design read with revision={document.meta.revision} and this offset to continue)."
        if next_offset is not None else
        "End of Design source."
    )
    return (
        f"Path: {source_path}\n"
        f"Source chunk: offset={offset}, chars={len(chunk)}, total={len(text)}\n"
        f"{chunk}\n"
        f"[{continuation}]"
    )


def _pending_source_revision(path: Any, document: Any) -> int | None:
    """Return the exact Studio-selected source while one Design request is pending."""
    from core.design.session import load_design_session

    session = load_design_session(
        path,
        design_id=document.meta.id,
        title=document.meta.title,
    )
    pending = session.get("pending")
    if not isinstance(pending, dict):
        return None
    source = pending.get("source_revision")
    return int(source) if isinstance(source, int) and not isinstance(source, bool) else None


def _list(args: dict[str, Any]) -> str:
    from core.design.service import list_designs

    return json.dumps({"designs": list_designs(limit=int(args.get("limit") or 24))}, ensure_ascii=False, indent=2)


def _complete(args: dict[str, Any]) -> str:
    from core.design.service import find_design, load_design, load_design_revision

    path = find_design(str(args.get("design_id") or ""))
    document = load_design(path)
    revision = int(args.get("revision") or document.meta.revision)
    _revision_path, accepted = load_design_revision(document.meta.id, revision)
    session = complete_design_session(
        path,
        design_id=document.meta.id,
        title=accepted.meta.title,
        revision=accepted.meta.revision,
    )
    return json.dumps({
        "status": "completed",
        "design_id": document.meta.id,
        "revision": int((session.get("lifecycle") or {}).get("revision") or 0),
        "path": str(path),
    }, ensure_ascii=False)


def _board_read(args: dict[str, Any]) -> str:
    from core.design.board import (
        board_operation_contract,
        board_spatial_context,
        board_summary,
        load_board_working,
    )

    path, document = _resolve_board_document(args)
    working = load_board_working(
        path,
        design_id=document.meta.id,
        design_revision=document.meta.revision,
        committed=document.board,
    )
    board = working["board"]
    selected = {
        str(item) for item in (args.get("element_ids") if isinstance(args.get("element_ids"), list) else [])[:256]
        if isinstance(item, str)
    }
    elements = [
        row for row in board.to_dict()["elements"]
        if not selected or str(row.get("id") or "") in selected
    ][:256]
    return json.dumps({
        "status": "ok",
        "design_id": document.meta.id,
        "design_revision": document.meta.revision,
        "summary": board_summary(board),
        "operation_contract": board_operation_contract(),
        "board": {**board.to_dict(), "elements": elements},
        "spatial": board_spatial_context(board),
        "truncated": len(elements) < len(board.elements),
        "draft_pending": bool(working.get("draft")),
    }, ensure_ascii=False)


def _board_propose(args: dict[str, Any]) -> str:
    from core.design.board import save_board_draft

    operations = args.get("operations")
    if not isinstance(operations, list):
        raise ValueError("mo_design board_propose requires an operations list")
    path, document = _resolve_board_document(args)
    draft = save_board_draft(
        path,
        design_id=document.meta.id,
        design_revision=document.meta.revision,
        committed=document.board,
        operations=operations,
        allow_overlap=args.get("allow_overlap") is True,
    )
    return json.dumps({
        "status": "drafted",
        "design_id": document.meta.id,
        "draft_id": draft["id"],
        "board_revision": draft["board"]["revision"],
        "next": "The trusted Studio UI will show this as an MO draft. The user must Accept or Reject it.",
    }, ensure_ascii=False)


def _resolve_board_document(args: dict[str, Any]) -> tuple[Any, Any]:
    """Resolve Board work through the exact live terminal binding when present."""
    from core.design.service import find_design, load_design
    from core.design.terminal_handoff import terminal_board_binding

    requested = str(args.get("design_id") or "").strip()
    target = _board_terminal_target(project_root=str(args.get("project_root") or ""))
    binding = terminal_board_binding(target) if target else None
    if binding:
        bound = str(binding.get("design_id") or "").strip()
        if requested and requested != bound:
            raise RuntimeError(
                "This terminal is connected to a different live Board; omit design_id to use it"
            )
        requested = bound
    if not requested:
        raise ValueError(
            "mo_design Board work requires design_id unless this terminal has an active linked Board"
        )
    path = find_design(requested)
    return path, load_design(path)


__all__ = ["execute_mo_design"]
