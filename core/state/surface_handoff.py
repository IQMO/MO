"""MO Everywhere bridge for bounded, thread-scoped continuity events."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .continuity_events import (
    DEFAULT_MAX_AGE_SECONDS,
    ContinuityEvent,
    ContinuityEventError,
    LocalContinuityStore,
    default_thread_id,
    new_event,
    normalize_continuity_target,
    opaque_id,
    validate_continuity_source,
)
from .device import device_identity
from .paths import resolve_state_path


SurfaceHandoff = ContinuityEvent


def enabled(config: dict[str, Any] | None) -> bool:
    block = (config or {}).get("consistent_everywhere")
    if not isinstance(block, dict) or block.get("enabled") is not True:
        return False
    if Path(resolve_state_path("run/everywhere.disabled", config or {})).is_file():
        return False
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    if "enabled" in continuity:
        return continuity.get("enabled") is True
    return True


def publish_completed_turn(
    agent: Any,
    *,
    user_input: str,
    final_text: str,
    route_source: str,
    status: str = "ok",
) -> ContinuityEvent | None:
    """Append one bounded state event after a visible user turn.

    The historical function name remains for callers, but failure/cancellation
    now produces an explicit interrupt event instead of disappearing or being
    mislabeled as a completion.
    """
    return publish_turn_state(
        agent,
        user_input=user_input,
        final_text=final_text,
        route_source=route_source,
        status=status,
    )


def publish_turn_state(
    agent: Any,
    *,
    user_input: str,
    final_text: str = "",
    route_source: str,
    status: str = "ok",
    source_slot: str = "",
    kind: str = "",
    next_step: str = "",
) -> ContinuityEvent | None:
    """Publish one prompt-safe state transition for a trusted human surface."""
    config = _config(agent)
    if not enabled(config):
        return None
    state = getattr(agent, "_thread_state", None)
    if state is not None and getattr(state, "surface_handoff_suppressed", False):
        return None
    try:
        surface = validate_continuity_source(route_source)
    except ContinuityEventError:
        return None
    intent = str(user_input or "").strip()
    if not intent or intent.startswith("/"):
        return None

    event_status, event_kind, fallback_outcome = _event_state(status, kind)
    outcome = str(final_text or "").strip() or fallback_outcome
    if event_kind == "turn" and not outcome:
        return None

    identity = device_identity(config)
    raw_slot = str(source_slot or _active_surface_slot(agent)).strip()[:160]
    opaque_slot = opaque_id(surface, raw_slot)
    project_id, repo_id, commit_id = _project_evidence(agent)
    store = LocalContinuityStore(config)
    thread_id = store.thread_for_surface(
        surface,
        raw_slot,
        project_id=project_id,
        device_id=identity["device_id"],
    )
    try:
        event = new_event(
            source_device_id=identity["device_id"],
            source_surface=surface,
            source_slot=opaque_slot,
            thread_id=thread_id,
            intent=intent,
            outcome=outcome,
            project_id=project_id,
            repo_id=repo_id,
            commit_id=commit_id,
            source_environment=_environment(config, surface),
            kind=event_kind,
            status=event_status,
            next_step=next_step,
            max_age_seconds=_max_age(config),
        )
    except ContinuityEventError:
        return None
    store.append_outbound(event)
    store.prune(max_rows=_max_local_rows(config))
    return event


def _event_state(status: str, kind: str) -> tuple[str, str, str]:
    raw = str(status or "").strip().lower()
    if raw in {"ok", "completed", "complete", "success"}:
        return "completed", str(kind or "turn"), ""
    if raw in {"active", "running", "queued"}:
        return "active", str(kind or "focus"), ""
    if raw in {"paused", "interrupted"}:
        return "paused", str(kind or "interrupt"), "Turn paused before completion."
    if raw in {"cancelled", "canceled", "cancelling"}:
        return "cancelled", str(kind or "interrupt"), "Turn cancelled before completion."
    return "failed", str(kind or "interrupt"), "Turn failed before completion."


def pending_handoff(
    agent: Any,
    *,
    target_surface: str,
    target_key: str,
) -> ContinuityEvent | None:
    """Return an unconsumed event only when its thread is unambiguous/bound."""
    config = _config(agent)
    if not enabled(config):
        return None
    store = LocalContinuityStore(config)
    project_id, repo_id, commit_id = _project_evidence(agent)
    return store.pending_for(
        target_surface=normalize_continuity_target(target_surface),
        target_slot=str(target_key or ""),
        project_id=project_id,
        repo_id=repo_id,
        commit_id=commit_id,
    )


def render_handoff_context(record: ContinuityEvent) -> str:
    """Render bounded provider-only orientation, never a second transcript."""
    lines = [
        "### Bounded continuity event from another MO surface",
        "Orientation only. Verify live files, repository state, capabilities, and task truth before acting or making factual claims.",
        f"Source: {record.source_surface}; state: {record.status}; recorded: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.created_at))}.",
        f"Previous intent: {record.intent}",
        f"Recorded outcome: {record.outcome}",
    ]
    if record.next_step:
        lines.append(f"Safe next step: {record.next_step}")
    if record.capability_note:
        lines.append(record.capability_note)
    return "\n".join(lines)


def mark_handoff_consumed(
    agent: Any,
    record: ContinuityEvent,
    *,
    target_surface: str,
    target_key: str,
) -> None:
    LocalContinuityStore(_config(agent)).consume(
        record,
        target_surface=normalize_continuity_target(target_surface),
        target_slot=str(target_key or ""),
    )


def continuity_thread_choices(agent: Any, *, target_surface: str, target_key: str) -> list[dict[str, Any]]:
    """Return bounded choices when automatic continuation would be ambiguous."""
    config = _config(agent)
    if not enabled(config):
        return []
    return LocalContinuityStore(config).thread_choices(
        target_surface=normalize_continuity_target(target_surface),
        target_slot=str(target_key or ""),
    )


def bind_continuity_thread(
    agent: Any,
    *,
    target_surface: str,
    target_key: str,
    thread_id: str,
) -> None:
    LocalContinuityStore(_config(agent)).bind(
        normalize_continuity_target(target_surface),
        str(target_key or ""),
        thread_id,
    )


def reset_continuity_thread(
    agent: Any,
    *,
    target_surface: str,
    target_key: str,
) -> None:
    """Return a surface to its own deterministic thread."""
    config = _config(agent)
    surface = normalize_continuity_target(target_surface)
    slot = str(target_key or "")
    project_id, _repo_id, _commit_id = _project_evidence(agent)
    LocalContinuityStore(config).bind(
        surface,
        slot,
        default_thread_id(
            surface,
            slot,
            project_id=project_id,
            device_id=device_identity(config)["device_id"],
        ),
        project_id=project_id,
    )


def has_cross_surface_continuity_binding(
    agent: Any,
    *,
    target_surface: str,
    target_key: str,
) -> bool:
    """Return whether a surface is following a non-default continuity thread.

    A surface's own deterministic thread is created lazily and is not a user
    selection.  Any different binding came from an unambiguous continuation or
    an explicit selector choice and remains sticky until another explicit
    selection changes it.
    """
    config = _config(agent)
    if not enabled(config):
        return False
    surface = normalize_continuity_target(target_surface)
    slot = str(target_key or "")
    binding = LocalContinuityStore(config).binding(surface, slot)
    if not binding:
        return False
    project_id, _repo_id, _commit_id = _project_evidence(agent)
    own_thread = default_thread_id(
        surface,
        slot,
        project_id=project_id,
        device_id=device_identity(config)["device_id"],
    )
    return str(binding.get("thread_id") or "") != own_thread


def local_continuity_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return LocalContinuityStore(config or {}).status()


def _config(agent: Any) -> dict[str, Any]:
    value = getattr(agent, "config", {})
    return value if isinstance(value, dict) else {}


def _active_surface_slot(agent: Any) -> str:
    state = getattr(agent, "_thread_state", None)
    scoped = getattr(state, "surface_session_slot", "") if state is not None else ""
    if str(scoped or "").strip():
        return str(scoped).strip()[:160]
    current = getattr(getattr(agent, "_sessions", None), "current_name", "")
    if str(current or "").strip():
        return str(current).strip()[:160]
    return str(getattr(getattr(agent, "session", None), "session_id", "main") or "main")[:160]


def _project_evidence(agent: Any) -> tuple[str, str, str]:
    raw = getattr(agent, "project_cwd", "") or getattr(agent, "agent_root", "") or ""
    try:
        root = Path(raw).expanduser().resolve(strict=False) if raw else None
    except OSError:
        root = None
    if root is None:
        return "", "", ""
    project_id = opaque_id("project", str(root))
    git_dir, common_dir = _git_dirs(root)
    if git_dir is None:
        return project_id, "", ""
    remote = _origin_url(common_dir)
    repo_id = opaque_id("repo", _canonical_repo_remote(remote) or str(root))
    commit_id = _git_head(git_dir, common_dir)
    return project_id, repo_id, commit_id


def _git_dirs(root: Path) -> tuple[Path | None, Path | None]:
    marker = root / ".git"
    if marker.is_dir():
        return marker, marker
    try:
        raw = marker.read_text(encoding="utf-8", errors="strict").strip()
    except OSError:
        return None, None
    if not raw.lower().startswith("gitdir:"):
        return None, None
    git_dir = Path(raw.split(":", 1)[1].strip()).expanduser()
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve(strict=False)
    try:
        relative = (git_dir / "commondir").read_text(encoding="utf-8", errors="strict").strip()
        common_dir = (git_dir / relative).resolve(strict=False)
    except OSError:
        common_dir = git_dir
    return git_dir, common_dir


def _origin_url(common_dir: Path | None) -> str:
    if common_dir is None:
        return ""
    try:
        lines = (common_dir / "config").read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError:
        return ""
    in_origin = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = stripped.lower() == '[remote "origin"]'
        elif in_origin and stripped.lower().startswith("url") and "=" in stripped:
            return stripped.split("=", 1)[1].strip()
    return ""


def _canonical_repo_remote(value: Any) -> str:
    """Return one cross-device identity for equivalent network Git remotes.

    A workstation commonly uses ``https://host/owner/repo.git`` while a VPS
    uses ``user@ssh-alias:owner/repo``.  Host aliases are device-local and the
    transport is not repository identity, so use the remote repository path.
    Local filesystem remotes retain their resolved absolute identity.
    """
    remote = str(value or "").strip().replace("\\", "/")
    if not remote:
        return ""
    path = ""
    host = ""
    network = False
    if "://" in remote:
        try:
            parsed = urlsplit(remote)
        except ValueError:
            return remote
        network = bool(parsed.hostname)
        host = _canonical_git_host(parsed.hostname)
        path = unquote(parsed.path or "")
    elif not re.match(r"^[A-Za-z]:/", remote):
        match = re.match(r"^(?:[^@/:\s]+@)?([^/:\s]+):(.+)$", remote)
        if match:
            network = True
            host = _canonical_git_host(match.group(1))
            path = match.group(2)
    if not network:
        try:
            return "local:" + Path(remote).expanduser().resolve(strict=False).as_posix()
        except OSError:
            return "local:" + remote
    clean = "/".join(part for part in path.split("/") if part not in {"", "."})
    if clean.endswith(".git"):
        clean = clean[:-4]
    clean = clean.strip("/")
    return f"{host}/{clean}" if host and clean else remote


def _canonical_git_host(value: Any) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    aliases = {
        "github": "github.com",
        "gitlab": "gitlab.com",
        "bitbucket": "bitbucket.org",
    }
    if host in aliases:
        return aliases[host]
    for prefix, canonical in aliases.items():
        if host.startswith(prefix + "-") or host.startswith(prefix + "_"):
            return canonical
    return host


def _git_head(git_dir: Path, common_dir: Path) -> str:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="strict").strip()
    except OSError:
        return ""
    if not head.startswith("ref: "):
        return _clean_commit(head)
    ref = head[5:].strip()
    for base in (git_dir, common_dir):
        try:
            value = (base / ref).read_text(encoding="utf-8", errors="strict").strip()
        except OSError:
            continue
        clean = _clean_commit(value)
        if clean:
            return clean
    try:
        packed = (common_dir / "packed-refs").read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError:
        return ""
    for line in packed:
        value, _, name = line.partition(" ")
        if name.strip() == ref:
            return _clean_commit(value)
    return ""


def _clean_commit(value: Any) -> str:
    commit = str(value or "").strip().lower()
    if len(commit) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in commit):
        return ""
    return commit


def _environment(config: dict[str, Any], surface: str) -> str:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    role = str(block.get("device_role") or "").strip().lower()
    if role in {"workstation", "server", "mobile"}:
        return role
    if surface in {"telegram", "api"}:
        return "server"
    return "workstation"


def _max_age(config: dict[str, Any]) -> float:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    raw = continuity.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS)
    try:
        return max(60.0, min(30 * 24 * 60 * 60, float(raw or DEFAULT_MAX_AGE_SECONDS)))
    except (TypeError, ValueError):
        return float(DEFAULT_MAX_AGE_SECONDS)


def _max_local_rows(config: dict[str, Any]) -> int:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    try:
        return max(100, min(10_000, int(continuity.get("max_local_rows", 2_000) or 2_000)))
    except (TypeError, ValueError):
        return 2_000
