"""Target-owned computer-use state shared by desktop and browser adapters.

The provider-facing tools stay small, but observation and actuation may no
longer communicate through process-global "last screen" assumptions.  This
module is deliberately dependency-free and lazy: it binds runtime state to the
current MO instance/session, scopes observations to one target revision, and
emits structured events for the completion gate.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any


TARGET_LEASE_SECONDS = 180.0
OBSERVATION_MAX_AGE_SECONDS = 120.0

COMPUTER_TOOLS = frozenset({
    "computer_targets", "computer_observe", "computer_act", "point_on_screen",
    "phone_context", "phone_click", "phone_set_text", "phone_scroll", "phone_key",
    "phone_files", "phone_storage_report", "phone_file_read", "phone_file_delete",
    "phone_capabilities", "phone_system_status", "phone_cache_report",
    "phone_cache_trim", "phone_packages", "phone_package_action",
    "phone_shell",
})


@dataclass(frozen=True)
class ComputerTarget:
    target_id: str
    owner_id: str
    kind: str
    identity: str
    label: str
    revision: int
    bounds: tuple[int, int, int, int] | None
    lease_expires_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    owner_id: str
    target_id: str
    target_revision: int
    origin: str
    trust: str
    foreground_identity: str
    captured_at: float
    signature: str = ""
    native_revision: str = ""
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComputerEvent:
    sequence: int
    event: str
    tool: str
    owner_id: str
    target_id: str = ""
    target_kind: str = ""
    target_revision: int = 0
    observation_id: str = ""
    observation_signature: str = ""
    status: str = ""
    state_changed: bool | None = None
    error_class: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "tool": self.tool,
            "owner_id": self.owner_id,
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "target_revision": self.target_revision,
            "observation_id": self.observation_id,
            "observation_signature": self.observation_signature,
            "status": self.status,
            "state_changed": self.state_changed,
            "error_class": self.error_class,
        }


@dataclass
class _OwnerState:
    targets: dict[str, ComputerTarget] = field(default_factory=dict)
    active_by_kind: dict[str, str] = field(default_factory=dict)
    observations: dict[str, Observation] = field(default_factory=dict)
    events: list[ComputerEvent] = field(default_factory=list)
    sequence: int = 0
    observation_sequence: int = 0


_LOCK = threading.RLock()
_STATES: dict[str, _OwnerState] = {}
_NATIVE_LOCK = threading.RLock()
_NATIVE_CONTEXT = threading.local()


def _native_input_path() -> Path:
    """Use one user-stable resource, independent of profile and sandbox TEMP."""
    import tempfile

    root = str(os.environ.get("LOCALAPPDATA") or "").strip()
    return Path(root or tempfile.gettempdir()) / "mo-native-input.lock"


def _native_revision() -> str:
    try:
        return _native_input_path().with_suffix(".revision").read_text(encoding="ascii")
    except FileNotFoundError:
        return ""


def _begin_native_input() -> None:
    from core.utils.atomic_write import atomic_write_text

    if getattr(_NATIVE_CONTEXT, "prior_revision", None) is None:
        _NATIVE_CONTEXT.prior_revision = _native_revision()
        # Publish before input: a process killed mid-action cannot leave another
        # owner's pre-action observation looking current.
        atomic_write_text(_native_input_path().with_suffix(".revision"), uuid.uuid4().hex)


@contextmanager
def native_desktop_scope(*, invalidate: bool = False):
    """Serialize native capture/input, never conversations or browser work.

    An input attempt may partially execute before an error. Its revision is
    published before dispatch; only this owner's admitted pre-input observation
    remains usable inside the atomic operation. File locks release on exit.
    """
    if getattr(_NATIVE_CONTEXT, "active", False):
        if invalidate:
            _begin_native_input()
        yield
        return
    from core.runtime.lock import file_byte_lock

    with file_byte_lock(_native_input_path(), _NATIVE_LOCK):
        _NATIVE_CONTEXT.active = True
        _NATIVE_CONTEXT.owner = current_owner_id()
        _NATIVE_CONTEXT.prior_revision = None
        try:
            if invalidate:
                _begin_native_input()
            yield
        finally:
            _NATIVE_CONTEXT.active = False
            _NATIVE_CONTEXT.owner = None
            _NATIVE_CONTEXT.prior_revision = None


def native_input_held() -> bool:
    """Whether some MO owner (this process or another) is inside ``native_desktop_scope`` right
    now, injecting input; probed without waiting. A key event seen then is MO's own."""
    if not _NATIVE_LOCK.acquire(blocking=False):
        return True                                    # another thread of this process acts
    try:
        path = _native_input_path()
        if not path.exists():
            return False
        with path.open("a+b") as handle:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                return True                            # another process holds it
        return False
    finally:
        _NATIVE_LOCK.release()


def current_owner_id() -> str:
    """Stable actuation owner for the current instance/session/surface."""
    try:
        from core.runtime.backend_monitor import current_monitor_context
        from core.runtime.instance import get_instance_id

        ctx = current_monitor_context()
        instance = str(ctx.get("instance_id") or get_instance_id() or "process")
        session = str(ctx.get("session_id") or "session")
        surface = str(ctx.get("surface") or ctx.get("route_source") or "terminal")
    except Exception:
        instance, session, surface = f"pid-{os.getpid()}", "session", "terminal"
    return f"{instance}:{session}:{surface}".lower()


def _state(owner_id: str | None = None) -> _OwnerState:
    owner = owner_id or current_owner_id()
    with _LOCK:
        return _STATES.setdefault(owner, _OwnerState())


def _stable_id(kind: str, identity: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{identity}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{kind}-{digest}"


def bind_target(
    *,
    kind: str,
    identity: str,
    label: str = "",
    bounds: tuple[int, int, int, int] | None = None,
    metadata: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> ComputerTarget:
    """Bind or renew one owned target; identity/bounds changes advance revision."""
    owner = owner_id or current_owner_id()
    kind = str(kind or "desktop").strip().lower()
    identity = str(identity or "").strip()
    if not identity:
        raise ValueError("computer target identity is required")
    target_id = _stable_id(kind, identity)
    now = time.time()
    with _LOCK:
        state = _state(owner)
        previous_id = state.active_by_kind.get(kind)
        if previous_id and previous_id != target_id:
            state.targets.pop(previous_id, None)
            state.observations.pop(previous_id, None)
        previous = state.targets.get(target_id)
        revision = previous.revision if previous else 1
        clean_metadata = dict(metadata or {})
        if previous and (
            previous.bounds != bounds
            or previous.metadata != clean_metadata
        ):
            revision += 1
            state.observations.pop(target_id, None)
        target = ComputerTarget(
            target_id=target_id,
            owner_id=owner,
            kind=kind,
            identity=identity,
            label=str(label or identity),
            revision=revision,
            bounds=bounds,
            lease_expires_at=now + TARGET_LEASE_SECONDS,
            metadata=clean_metadata,
        )
        state.targets[target_id] = target
        state.active_by_kind[kind] = target_id
        return target


def active_target(kind: str, *, owner_id: str | None = None) -> ComputerTarget | None:
    owner = owner_id or current_owner_id()
    with _LOCK:
        state = _state(owner)
        target_id = state.active_by_kind.get(str(kind or "").lower())
        target = state.targets.get(target_id or "")
        if target is None or target.lease_expires_at < time.time():
            if target_id:
                state.targets.pop(target_id, None)
                state.observations.pop(target_id, None)
                state.active_by_kind.pop(str(kind or "").lower(), None)
            return None
        return target


def current_targets(*, owner_id: str | None = None) -> list[ComputerTarget]:
    """Return the current owner's live targets without exposing other sessions."""
    owner = owner_id or current_owner_id()
    now = time.time()
    with _LOCK:
        state = _state(owner)
        live: list[ComputerTarget] = []
        for kind, target_id in list(state.active_by_kind.items()):
            target = state.targets.get(target_id)
            if target is None or target.lease_expires_at < now:
                state.targets.pop(target_id, None)
                state.observations.pop(target_id, None)
                state.active_by_kind.pop(kind, None)
                continue
            live.append(target)
        return sorted(live, key=lambda item: (item.kind, item.label.casefold(), item.target_id))


def record_observation(
    tool: str,
    *,
    target: ComputerTarget,
    origin: str,
    trust: str = "external_untrusted",
    foreground_identity: str = "",
    signature: str = "",
    emit_event: bool = True,
) -> Observation:
    owner = target.owner_id
    now = time.time()
    native_revision = _native_revision() if target.kind in {"desktop", "screen"} else ""
    with _LOCK:
        state = _state(owner)
        live = state.targets.get(target.target_id)
        if live is None:
            raise RuntimeError("computer target lease is no longer active")
        state.observation_sequence += 1
        observation = Observation(
            observation_id=f"obs-{state.observation_sequence}-{live.target_id[-8:]}",
            owner_id=owner,
            target_id=live.target_id,
            target_revision=live.revision,
            origin=str(origin or "unknown"),
            trust=str(trust or "external_untrusted"),
            foreground_identity=str(foreground_identity or live.label),
            captured_at=now,
            signature=str(signature or ""),
            native_revision=native_revision,
        )
        state.observations[live.target_id] = observation
        if emit_event:
            _append_event(
                state,
                event="observation",
                tool=tool,
                owner_id=owner,
                target=live,
                observation=observation,
                status="observed",
            )
        return observation


def latest_observation(kind: str, *, owner_id: str | None = None) -> Observation | None:
    target = active_target(kind, owner_id=owner_id)
    if target is None:
        return None
    with _LOCK:
        return _state(target.owner_id).observations.get(target.target_id)


def attach_latest_observation_facts(
    kind: str,
    facts: dict[str, Any],
    *,
    target_id: str = "",
    owner_id: str | None = None,
) -> Observation | None:
    """Attach bounded semantic facts only to the exact current observation."""
    target = active_target(kind, owner_id=owner_id)
    if target is None:
        return None
    if target_id and target.target_id != str(target_id):
        return None
    with _LOCK:
        state = _state(target.owner_id)
        current = state.observations.get(target.target_id)
        if current is None or current.target_revision != target.revision:
            return None
        updated = Observation(
            observation_id=current.observation_id,
            owner_id=current.owner_id,
            target_id=current.target_id,
            target_revision=current.target_revision,
            origin=current.origin,
            trust=current.trust,
            foreground_identity=current.foreground_identity,
            captured_at=current.captured_at,
            signature=current.signature,
            native_revision=current.native_revision,
            facts=dict(facts or {}),
        )
        state.observations[target.target_id] = updated
        return updated


def native_input_revision() -> str:
    """Return the process-independent revision used to detect native input attempts."""
    return _native_revision()


def validate_action(
    kind: str,
    *,
    owner_id: str | None = None,
    target_id: str = "",
    observation_id: str = "",
    point: tuple[int, int] | None = None,
    require_observation: bool = True,
    max_age: float = OBSERVATION_MAX_AGE_SECONDS,
) -> tuple[ComputerTarget | None, Observation | None, str | None]:
    """Validate lease, target, revision, observation age, and optional point."""
    target = active_target(kind, owner_id=owner_id)
    if target is None:
        return None, None, f"Error: no owned {kind} target is active; observe and bind the target before acting."
    if target_id and target.target_id != str(target_id):
        return target, None, "Error: requested computer target is not the active owned target."
    with _LOCK:
        observation = _state(target.owner_id).observations.get(target.target_id)
    if require_observation:
        if observation is None:
            return target, None, "Error: the target has no current observation; observe it again before acting."
        if target.kind in {"desktop", "screen"}:
            admitted_revision = (
                getattr(_NATIVE_CONTEXT, "prior_revision", None)
                if getattr(_NATIVE_CONTEXT, "owner", None) == observation.owner_id else None
            )
            if observation.native_revision not in {_native_revision(), admitted_revision}:
                return target, observation, "Error: the native input revision changed; observe again before acting."
        if observation.target_revision != target.revision:
            return target, observation, "Error: the target changed after it was observed; observe it again before acting."
        if observation_id and observation.observation_id != str(observation_id):
            return target, observation, "Error: the requested observation is stale or belongs to another target."
        if time.time() - observation.captured_at > max(0.1, float(max_age or OBSERVATION_MAX_AGE_SECONDS)):
            return target, observation, "Error: the target observation expired; observe it again before acting."
    if point is not None:
        if target.bounds is None:
            return target, observation, "Error: the owned target has no valid bounds for coordinate actuation."
        x, y = point
        left, top, right, bottom = target.bounds
        if not (left <= int(x) <= right and top <= int(y) <= bottom):
            return target, observation, f"Error: point ({int(x)},{int(y)}) is outside owned target {target.label!r}."
    return target, observation, None


def record_action(
    tool: str,
    *,
    target: ComputerTarget | None,
    observation: Observation | None,
    status: str,
    state_changed: bool | None = None,
    error_class: str = "",
    invalidate: bool = True,
) -> ComputerEvent:
    owner = target.owner_id if target is not None else current_owner_id()
    if target is not None and target.kind in {"desktop", "screen"} and invalidate and status == "executed":
        with native_desktop_scope(invalidate=True):
            pass
    with _LOCK:
        state = _state(owner)
        live = state.targets.get(target.target_id) if target is not None else None
        if live is not None and invalidate and status == "executed":
            live = ComputerTarget(
                target_id=live.target_id,
                owner_id=live.owner_id,
                kind=live.kind,
                identity=live.identity,
                label=live.label,
                revision=live.revision + 1,
                bounds=live.bounds,
                lease_expires_at=time.time() + TARGET_LEASE_SECONDS,
                metadata=dict(live.metadata),
            )
            state.targets[live.target_id] = live
            state.observations.pop(live.target_id, None)
        return _append_event(
            state,
            event="action",
            tool=tool,
            owner_id=owner,
            target=live or target,
            observation=observation,
            status=status,
            state_changed=state_changed,
            error_class=error_class,
        )


def invalidate_target(kind: str, *, reason: str = "invalidated", owner_id: str | None = None) -> None:
    target = active_target(kind, owner_id=owner_id)
    if target is None:
        return
    with _LOCK:
        state = _state(target.owner_id)
        state.targets.pop(target.target_id, None)
        state.observations.pop(target.target_id, None)
        state.active_by_kind.pop(kind, None)
        _append_event(
            state,
            event="invalidation",
            tool="runtime",
            owner_id=target.owner_id,
            target=target,
            observation=None,
            status=reason,
        )


def invalidate_current_targets(*, reason: str = "interrupted") -> None:
    """Invalidate every target leased by the current owner (cancel/panic path)."""
    owner = current_owner_id()
    with _LOCK:
        state = _state(owner)
        for kind, target_id in list(state.active_by_kind.items()):
            target = state.targets.pop(target_id, None)
            state.observations.pop(target_id, None)
            state.active_by_kind.pop(kind, None)
            if target is not None:
                _append_event(
                    state,
                    event="invalidation",
                    tool="runtime",
                    owner_id=owner,
                    target=target,
                    observation=None,
                    status=reason,
                )


def clear_current_events() -> None:
    with _LOCK:
        _state().events.clear()


def release_current_owner() -> None:
    """Release a closed conversation's native leases and adapter references only."""
    from . import apps, policy, uia
    from tools.screen import release_capture_owner

    owner = current_owner_id()
    with native_desktop_scope():
        policy.clear_pending_confirmation()
        uia.release_owner(owner)
        apps.reset_app_cache(owner_id=owner)
        release_capture_owner(owner)
        with _LOCK:
            _STATES.pop(owner, None)


def drain_current_events() -> list[dict[str, Any]]:
    with _LOCK:
        state = _state()
        events = [event.as_dict() for event in state.events]
        state.events.clear()
        return events


def reset_runtime_state() -> None:
    """Test/process shutdown helper; never persisted and never called by imports."""
    with _LOCK:
        _STATES.clear()


def _append_event(
    state: _OwnerState,
    *,
    event: str,
    tool: str,
    owner_id: str,
    target: ComputerTarget | None,
    observation: Observation | None,
    status: str,
    state_changed: bool | None = None,
    error_class: str = "",
) -> ComputerEvent:
    state.sequence += 1
    item = ComputerEvent(
        sequence=state.sequence,
        event=event,
        tool=str(tool or ""),
        owner_id=owner_id,
        target_id=target.target_id if target else "",
        target_kind=target.kind if target else "",
        target_revision=target.revision if target else 0,
        observation_id=observation.observation_id if observation else "",
        observation_signature=(
            hashlib.sha256(observation.signature.encode("utf-8", errors="replace")).hexdigest()[:20]
            if observation is not None and observation.signature
            else ""
        ),
        status=status,
        state_changed=state_changed,
        error_class=error_class,
    )
    state.events.append(item)
    try:
        from ..runtime.backend_monitor import get_monitor

        monitor = get_monitor()
        if monitor is not None:
            monitor.emit("computer_event", {
                **item.as_dict(),
                "bounds": target.bounds if target is not None else None,
                "observation_target_revision": observation.target_revision if observation else None,
                "observation_age_ms": max(0, round((time.time() - observation.captured_at) * 1000, 3)) if observation else None,
            })
    except Exception:
        # The canonical receipt exists independently of diagnostic persistence.
        pass
    return item
