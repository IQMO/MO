"""Bounded public presentation contract for MO's native cube companion.

This module adapts existing Everywhere authority, jobs, presence, and skin
semantics into a renderer-neutral wire object.  It does not own any of those
source states and never accepts a client-side presentation as authority.
"""
from __future__ import annotations

import json
import math
import secrets
import threading
import unicodedata
from collections.abc import Mapping
from typing import Any

from .registry import DEVICE_CAPABILITIES, DEVICE_SCOPES


CUBE_SCHEMA = "mo.cube.presentation"
CUBE_VERSION = 1
MAX_CUBE_BYTES = 4 * 1024
MAX_LABEL_CHARS = 80
MAX_DETAIL_CHARS = 240
DEFAULT_STALE_AFTER_SECONDS = 15

FORMATIONS = frozenset({"cluster", "row", "face"})
OWNERS = frozenset({"default", "presence", "job", "mcp", "role", "system"})
PHASES = frozenset({"idle", "active", "working", "success", "warning", "error"})
TOKENS = frozenset({"neutral", "brand", "ok", "warn", "error", "action"})
CAPABILITIES = DEVICE_CAPABILITIES
SCOPES = DEVICE_SCOPES
JOB_STATES = frozenset({"queued", "running", "cancelling", "completed", "cancelled", "failed", "interrupted"})
ACTIVE_JOB_STATES = frozenset({"queued", "running", "cancelling"})
CANCELLABLE_JOB_STATES = frozenset({"queued", "running"})
ACTION_ORDER = (
    "upload_attachment",
    "file_transfer",
    "cancel_job",
    "conversation_read",
    "conversation_write",
    "continuity_bind",
    "continuity_read",
    "continuity_sync",
    "send_text",
    "self_revoke",
    "view_overview",
)
ACTION_SET = frozenset(ACTION_ORDER)


class CubeProtocolError(ValueError):
    """A server-generated cube could not satisfy the public wire contract."""


class CubeStream:
    """One process-local stream epoch with monotonically increasing sequence."""

    def __init__(self, epoch: str | None = None) -> None:
        self.epoch = _required_epoch(epoch or secrets.token_urlsafe(18))
        self._sequence = -1
        self._lock = threading.Lock()

    def next(self) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            return {"epoch": self.epoch, "sequence": self._sequence}


def compose_cube(
    *,
    stream: CubeStream,
    generated_at: float,
    capability: str,
    scopes: Any = (),
    visual: Any = None,
    job: Any = None,
    transient: Any = None,
    locally_disabled: bool = False,
    file_transfer_enabled: bool = False,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Compose one principal-aware, privacy-bounded CubePresentationV1 object."""
    current = _required_timestamp(generated_at)
    stale_after = _bounded_stale_after(stale_after_seconds)
    clean_capability = _capability(capability)
    clean_scopes = _scopes(scopes)
    clean_job = _job(job)
    formation = _formation(_field(visual, "formation", "cluster"))

    if locally_disabled:
        phase, token, label, detail, semantic_owner = (
            "error", "error", "unavailable", "MO Everywhere is locally disabled", "system"
        )
    elif clean_job is not None:
        phase, token, label = _job_semantic(clean_job["status"])
        detail, semantic_owner = "", "job"
    else:
        phase = _phase(_field(visual, "phase", "idle"))
        token = _token(_field(visual, "token", "neutral"))
        label = _safe_text(_field(visual, "label", phase), MAX_LABEL_CHARS) or phase
        detail = _safe_text(_field(visual, "detail", ""), MAX_DETAIL_CHARS)
        semantic_owner = "presence"

    allowed_actions = [] if locally_disabled else derive_allowed_actions(
        clean_capability,
        clean_scopes,
        clean_job,
        file_transfer_enabled=file_transfer_enabled,
    )
    cube = {
        "schema": CUBE_SCHEMA,
        "version": CUBE_VERSION,
        "stream": stream.next(),
        "generated_at": current,
        "stale_after_seconds": stale_after,
        "persistent": {"formation": formation, "owner": "default"},
        "semantic": {
            "phase": phase,
            "token": token,
            "label": _safe_text(label, MAX_LABEL_CHARS),
            "detail": _safe_text(detail, MAX_DETAIL_CHARS),
            "owner": semantic_owner,
        },
        "transient": _transient(transient, current),
        "authority": {
            "capability": clean_capability,
            "scopes": clean_scopes,
            "allowed_actions": allowed_actions,
        },
        "job": clean_job,
    }
    size = cube_json_size(cube)
    if size > MAX_CUBE_BYTES:
        raise CubeProtocolError(f"cube presentation exceeds {MAX_CUBE_BYTES} bytes")
    return cube


def derive_allowed_actions(
    capability: str,
    scopes: Any,
    job: Any = None,
    *,
    file_transfer_enabled: bool = False,
) -> list[str]:
    """Derive display affordances from current server authority and job state."""
    clean_capability = _capability(capability)
    clean_scopes = set(_scopes(scopes))
    clean_job = _job(job)
    status = str((clean_job or {}).get("status") or "")
    cancellable = bool((clean_job or {}).get("cancellable"))
    actions: set[str] = {"self_revoke"}
    if "attachment_upload" in clean_scopes:
        actions.add("upload_attachment")
    if clean_capability in {"view", "control"}:
        actions.add("view_overview")
    if "continuity_read" in clean_scopes:
        actions.add("continuity_read")
    if "continuity_sync" in clean_scopes:
        actions.add("continuity_sync")
    if clean_capability == "control":
        if "conversation_read" in clean_scopes:
            actions.add("conversation_read")
        if "conversation_write" in clean_scopes:
            actions.add("conversation_write")
        if file_transfer_enabled and "file_transfer" in clean_scopes:
            actions.add("file_transfer")
        if status not in ACTIVE_JOB_STATES:
            actions.add("send_text")
        if cancellable:
            actions.add("cancel_job")
        if "continuity_read" in clean_scopes:
            actions.add("continuity_bind")
    return [action for action in ACTION_ORDER if action in actions]


def normalize_cube_payload(payload: Any, *, now: float | None = None) -> dict[str, Any] | None:
    """Fail-closed reference decoder used by public conformance fixtures.

    Unknown optional fields are ignored. Unknown presentation enums receive the
    documented inert fallback. Required fields, sizes, and scalar bounds remain
    strict; authority is intersected with what its capability/scopes/job permit.
    """
    if not isinstance(payload, Mapping) or cube_json_size(payload) > MAX_CUBE_BYTES:
        return None
    version = payload.get("version")
    if (
        payload.get("schema") != CUBE_SCHEMA
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != CUBE_VERSION
    ):
        return None
    stream = payload.get("stream")
    persistent = payload.get("persistent")
    semantic = payload.get("semantic")
    authority = payload.get("authority")
    if not all(isinstance(item, Mapping) for item in (stream, persistent, semantic, authority)):
        return None
    try:
        epoch = _decoded_epoch(stream.get("epoch"))
        sequence = _required_int(stream.get("sequence"), minimum=0, maximum=2**63 - 1)
        generated_at = _decoded_timestamp(payload.get("generated_at"))
        stale_after = _strict_stale_after(payload.get("stale_after_seconds"))
        label = _strict_text(semantic.get("label"), MAX_LABEL_CHARS)
        detail = _strict_text(semantic.get("detail"), MAX_DETAIL_CHARS)
        raw_scopes = authority.get("scopes")
        raw_actions = authority.get("allowed_actions")
        if not isinstance(raw_scopes, list) or len(raw_scopes) > len(SCOPES):
            return None
        if not isinstance(raw_actions, list) or len(raw_actions) > 16:
            return None
        if not all(isinstance(item, str) for item in raw_scopes + raw_actions):
            return None
        formation = _decoded_enum(persistent.get("formation"), FORMATIONS, "cluster")
        persistent_owner = _decoded_enum(persistent.get("owner"), OWNERS, "default")
        phase = _decoded_enum(semantic.get("phase"), PHASES, "idle")
        token = _decoded_token(semantic.get("token"))
        semantic_owner = _decoded_enum(semantic.get("owner"), OWNERS, "default")
        capability = _decoded_enum(authority.get("capability"), CAPABILITIES, "notify")
    except CubeProtocolError:
        return None

    scopes = sorted(set(raw_scopes) & SCOPES)
    clean_job = _decoded_job(payload.get("job"))
    try:
        transient_now = generated_at if now is None else _required_timestamp(now)
        clean_transient = _decoded_transient(payload.get("transient"), transient_now)
    except CubeProtocolError:
        return None
    permitted = set(
        derive_allowed_actions(
            capability,
            scopes,
            clean_job,
            # The decoder still intersects this structural permission with the
            # exact action advertised by the server below. Runtime
            # availability itself remains a server-owned decision.
            file_transfer_enabled=True,
        )
    )
    requested = {str(item) for item in raw_actions if str(item) in ACTION_SET}
    normalized = {
        "schema": CUBE_SCHEMA,
        "version": CUBE_VERSION,
        "stream": {"epoch": epoch, "sequence": sequence},
        "generated_at": generated_at,
        "stale_after_seconds": stale_after,
        "persistent": {
            "formation": formation,
            "owner": persistent_owner,
        },
        "semantic": {
            "phase": phase,
            "token": token,
            "label": label,
            "detail": detail,
            "owner": semantic_owner,
        },
        "transient": clean_transient,
        "authority": {
            "capability": capability,
            "scopes": scopes,
            "allowed_actions": [action for action in ACTION_ORDER if action in requested and action in permitted],
        },
        "job": clean_job,
    }
    return normalized if cube_json_size(normalized) <= MAX_CUBE_BYTES else None


def cube_json_size(value: Any) -> int:
    """Return compact UTF-8 wire size, or a rejecting sentinel for invalid JSON."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError):
        return MAX_CUBE_BYTES + 1
    return len(encoded.encode("utf-8"))


def _job(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    job_id = _safe_id(_field(value, "job_id", ""))
    status = str(_field(value, "status", "") or "").strip().lower()
    if not job_id or status not in JOB_STATES:
        return None
    return {
        "job_id": job_id,
        "status": status,
        "cancellable": status in CANCELLABLE_JOB_STATES and bool(
            _field(value, "cancellable", not bool(_field(value, "cancel_requested", False)))
        ),
    }


def _decoded_job(value: Any) -> dict[str, Any] | None:
    if value is None or not isinstance(value, Mapping):
        return None
    job_id = value.get("job_id")
    status = value.get("status")
    cancellable = value.get("cancellable")
    if not isinstance(job_id, str) or not _valid_ascii_id(job_id):
        return None
    if not isinstance(status, str) or status not in JOB_STATES or not isinstance(cancellable, bool):
        return None
    return {
        "job_id": job_id,
        "status": status,
        "cancellable": status in CANCELLABLE_JOB_STATES and cancellable,
    }


def _job_semantic(status: str) -> tuple[str, str, str]:
    return {
        "queued": ("working", "brand", "queued"),
        "running": ("working", "brand", "working"),
        "cancelling": ("working", "warn", "cancelling"),
        "completed": ("success", "ok", "done"),
        "cancelled": ("warning", "warn", "cancelled"),
        "failed": ("error", "error", "failed"),
        "interrupted": ("error", "error", "interrupted"),
    }[status]


def _transient(value: Any, current: float) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    kind = str(value.get("kind") or "").strip().lower()
    try:
        priority = _required_int(value.get("priority"), minimum=0, maximum=3)
        expires_at = _required_timestamp(value.get("expires_at"))
    except CubeProtocolError:
        return None
    if kind not in {"activity", "notice"} or expires_at <= current:
        return None
    return {
        "kind": kind,
        "priority": priority,
        "expires_at": expires_at,
        "label": _safe_text(value.get("label"), MAX_LABEL_CHARS),
        "detail": _safe_text(value.get("detail"), MAX_DETAIL_CHARS),
        "owner": _owner(value.get("owner")),
    }


def _decoded_transient(value: Any, current: float) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CubeProtocolError("cube transient is invalid")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in {"activity", "notice"}:
        raise CubeProtocolError("cube transient is invalid")
    priority = _required_int(value.get("priority"), minimum=0, maximum=3)
    expires_at = _decoded_timestamp(value.get("expires_at"))
    label = _strict_text(value.get("label"), MAX_LABEL_CHARS)
    detail = _strict_text(value.get("detail"), MAX_DETAIL_CHARS)
    owner = _decoded_enum(value.get("owner"), OWNERS, "default")
    if expires_at <= current:
        return None
    return {
        "kind": kind,
        "priority": priority,
        "expires_at": expires_at,
        "label": label,
        "detail": detail,
        "owner": owner,
    }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _formation(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in FORMATIONS else "cluster"


def _owner(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in OWNERS else "default"


def _phase(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in PHASES else "idle"


def _token(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if clean == "err":
        clean = "error"
    return clean if clean in TOKENS else "neutral"


def _capability(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in CAPABILITIES else "notify"


def _scopes(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    try:
        clean = {str(value or "").strip().lower() for value in values}
    except TypeError:
        clean = set()
    return sorted(clean & SCOPES)[:8]


def _safe_id(value: Any) -> str:
    return "".join(
        ch for ch in str(value or "").strip()
        if ch.isascii() and (ch.isalnum() or ch in "-_.")
    )[:64]


def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "")
    clean = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)
    return " ".join(clean.split())[:limit]


def _strict_text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise CubeProtocolError("cube text is invalid")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise CubeProtocolError("cube text is invalid")
    return value


def _required_epoch(value: Any) -> str:
    epoch = str(value or "")
    if not (1 <= len(epoch) <= 80) or any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in epoch):
        raise CubeProtocolError("cube stream epoch is invalid")
    return epoch


def _decoded_epoch(value: Any) -> str:
    if not isinstance(value, str):
        raise CubeProtocolError("cube stream epoch is invalid")
    return _required_epoch(value)


def _required_timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise CubeProtocolError("cube timestamp is invalid")
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        raise CubeProtocolError("cube timestamp is invalid") from None
    if not math.isfinite(stamp) or stamp < 0:
        raise CubeProtocolError("cube timestamp is invalid")
    return stamp


def _decoded_timestamp(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CubeProtocolError("cube timestamp is invalid")
    return _required_timestamp(value)


def _decoded_enum(value: Any, allowed: frozenset[str], fallback: str) -> str:
    if not isinstance(value, str):
        raise CubeProtocolError("cube enum is invalid")
    return value if value in allowed else fallback


def _decoded_token(value: Any) -> str:
    if not isinstance(value, str):
        raise CubeProtocolError("cube token is invalid")
    return "error" if value == "err" else value if value in TOKENS else "neutral"


def _valid_ascii_id(value: str) -> bool:
    return 1 <= len(value) <= 64 and all(
        ch.isascii() and (ch.isalnum() or ch in "-_.") for ch in value
    )


def _required_int(value: Any, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CubeProtocolError("cube integer is invalid")
    if value < minimum or (maximum is not None and value > maximum):
        raise CubeProtocolError("cube integer is out of bounds")
    return value


def _strict_stale_after(value: Any) -> int:
    return _required_int(value, minimum=5, maximum=60)


def _bounded_stale_after(value: Any) -> int:
    try:
        return max(5, min(60, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_STALE_AFTER_SECONDS
