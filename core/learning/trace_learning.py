"""Deterministic trace-to-learning suggestion mining.

Trace learning is a boundary-time canary. It reads already-recorded trace facts
and writes inert suggestions only; it never calls providers, changes task truth,
or applies profile/workflow learning without explicit operator confirmation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..backend_monitor import redact_monitor_text
from ..env_utils import int_env
from ..number_utils import as_int as _as_int
from .proactive_learning import LearningSuggestion, SuggestionEvidence

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_MAX_EVENTS = 1_000


def analyze_trace_file(
    path: str | Path,
    *,
    max_bytes: int | None = None,
    max_events: int | None = None,
) -> list[LearningSuggestion]:
    """Return inert learning suggestions from one `.trace` file.

    The analyzer is deliberately small and bounded. It only inspects event and
    validation metadata that `mo_trace.py` already redacts/captures.
    """
    trace_path = Path(path)
    trace = _load_trace(trace_path, max_bytes=max_bytes)
    if not trace:
        return []
    events = _bounded_events(trace.get("events"), max_events=max_events)
    validation = trace.get("validation") if isinstance(trace.get("validation"), list) else []
    session_id = redact_monitor_text(str(trace.get("session_id") or trace_path.stem), 120)
    mode = str(trace.get("mode") or "").strip().lower()
    suggestions: list[LearningSuggestion] = []

    tool_errors = [event for event in events if event.get("type") == "tool_result" and _payload_flag(event, "error", "blocked")]
    if tool_errors:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="trace:tool_errors",
            recommendation=(
                f"Trace detected {len(tool_errors)} tool error/block event(s). Review whether future similar work needs earlier "
                "file/log/test verification before completion claims."
            ),
            evidence=_event_evidence(tool_errors, "tool error"),
            salt=str(len(tool_errors)),
        ))

    provider_errors = [event for event in events if event.get("type") == "provider_error" or event.get("payload", {}).get("event") == "provider_error"]
    if provider_errors:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="trace:provider_errors",
            recommendation=(
                f"Trace detected {len(provider_errors)} provider error event(s). Treat provider/model stability as suspect "
                "for this session and verify outputs before relying on them."
            ),
            evidence=_event_evidence(provider_errors, "provider error"),
            salt=str(len(provider_errors)),
        ))

    sandbox_blocks = [event for event in events if event.get("type") == "sandbox_blocked"]
    if sandbox_blocks:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="trace:sandbox_blocks",
            recommendation=(
                f"Trace detected {len(sandbox_blocks)} sandbox block event(s). Preserve sandbox boundaries and ask for "
                "explicit approval before risky filesystem, shell, deploy, or secret operations."
            ),
            evidence=_event_evidence(sandbox_blocks, "sandbox block"),
            salt=str(len(sandbox_blocks)),
        ))

    turn_limits = [event for event in events if event.get("type") == "turn_limit"]
    if turn_limits:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="trace:turn_limits",
            recommendation=(
                f"Trace detected {len(turn_limits)} turn-limit event(s). Future similar work may need smaller scoped steps, "
                "earlier verification, or a handoff before continuing."
            ),
            evidence=_event_evidence(turn_limits, "turn limit"),
            salt=str(len(turn_limits)),
        ))

    has_provider_activity = any(str(event.get("type") or "").startswith("provider_") for event in events)
    has_meaningful_output = len(str(trace.get("stdout") or "")) >= 10
    if (mode == "run" or has_provider_activity or has_meaningful_output) and not any(event.get("type") == "memory_index" for event in events):
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="trace:no_memory_index",
            recommendation="Trace contained provider/output activity but no memory_index event. Check whether turn memory indexing is disabled, skipped, or failing.",
            evidence=_validation_evidence(validation, "Memory indexed") or (SuggestionEvidence(session_id, "no memory_index event observed"),),
            salt="no-memory-index",
        ))

    if (mode == "run" or has_provider_activity) and not any(event.get("type") == "turn_context" for event in events):
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="trace:no_context_bridge",
            recommendation="Trace contained provider activity but no turn_context event. Check context bridge capture before trusting context-health claims.",
            evidence=_validation_evidence(validation, "Context activity") or (SuggestionEvidence(session_id, "no turn_context event observed"),),
            salt="no-context-bridge",
        ))

    return [item for item in suggestions if item.evidence]


def analyze_runtime_closeout(
    closeout_meta: Any,
    audit_deltas: dict[str, list[dict[str, Any]]] | None = None,
    *,
    max_events: int | None = None,
) -> list[LearningSuggestion]:
    """Return inert suggestions from normal session closeout metadata.

    This path lets ordinary `mo.py` / `mo.bat` sessions benefit from the same
    learning canary without requiring `--trace` or a background watcher.
    """
    meta = _meta_dict(closeout_meta)
    if not meta:
        return []
    session_id = redact_monitor_text(str(meta.get("session_id") or "closeout"), 120)
    suggestions: list[LearningSuggestion] = []
    unresolved = [str(item or "") for item in meta.get("unresolved_preview") or []]
    unresolved_text = "\n".join(unresolved).lower()

    tool_blocks = _bounded_events((audit_deltas or {}).get("tool"), max_events=max_events)
    tool_blocks = [entry for entry in tool_blocks if isinstance(entry, dict) and bool(entry.get("blocked"))]
    if tool_blocks:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="closeout:tool_blocks",
            recommendation=(
                f"Closeout audit detected {len(tool_blocks)} blocked tool call(s). Review whether future similar work needs "
                "earlier sandbox-aware planning or explicit operator approval."
            ),
            evidence=_audit_evidence(tool_blocks, "tool block"),
            salt=f"tool-blocks:{len(tool_blocks)}",
        ))

    provider_errors = _bounded_events((audit_deltas or {}).get("provider"), max_events=max_events)
    provider_errors = [entry for entry in provider_errors if isinstance(entry, dict) and str(entry.get("event") or "") == "provider_error"]
    if provider_errors:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="closeout:provider_errors",
            recommendation=(
                f"Closeout audit detected {len(provider_errors)} provider error(s). Treat this session's provider lane as "
                "potentially unstable and verify outputs before relying on them."
            ),
            evidence=_audit_evidence(provider_errors, "provider error"),
            salt=f"provider-errors:{len(provider_errors)}",
        ))

    task_blocked = _as_int(meta.get("task_blocked"))
    if task_blocked > 0:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="closeout:blocked_tasks",
            recommendation=(
                f"Session closed with {task_blocked} blocked task(s). Preserve blockers and next actions before resuming "
                "instead of treating the work as complete."
            ),
            evidence=_closeout_evidence(session_id, unresolved, "blocked task"),
            salt=f"blocked:{task_blocked}",
        ))

    task_open = _as_int(meta.get("task_open"))
    if task_open > 0:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="closeout:open_taskboard",
            recommendation=(
                f"Session closed with {task_open} open taskboard item(s). Future continuation should start from the open "
                "items and re-verify evidence before completion claims."
            ),
            evidence=_closeout_evidence(session_id, unresolved, "open taskboard"),
            salt=f"open:{task_open}",
        ))

    dirty_count = _as_int(meta.get("dirty_count"))
    if dirty_count > 0:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="closeout:dirty_workspace",
            recommendation=(
                f"Session closed with {dirty_count} dirty workspace item(s). Carry forward git status explicitly and do not "
                "claim clean completion until the workspace is checked."
            ),
            evidence=_closeout_evidence(session_id, unresolved, "dirty workspace"),
            salt=f"dirty:{dirty_count}",
        ))

    pressure = _as_float(meta.get("pressure"))
    if pressure >= 0.85 or "trimmed" in unresolved_text:
        suggestions.append(_suggestion(
            session_id=session_id,
            kind="closeout:context_pressure",
            recommendation=(
                "Session closed under high context pressure or with trimmed history. Use handoff/closeout notes as orientation "
                "only and re-read files/tests before factual claims."
            ),
            evidence=_closeout_evidence(session_id, unresolved, "context pressure"),
            salt=f"pressure:{round(pressure, 2)}:{'trimmed' in unresolved_text}",
        ))

    return [item for item in suggestions if item.evidence]


def apply_trace_learning_suggestion(profile: Any, suggestion: LearningSuggestion) -> str:
    """Apply an explicitly confirmed trace suggestion where safe.

    Diagnostic findings are acknowledged only. Behavior/profile writes are kept
    to high-confidence operational classes and still dedupe through Profile.
    """
    kind = suggestion.kind
    if kind in {
        "trace:tool_errors",
        "trace:sandbox_blocks",
        "trace:turn_limits",
        "closeout:tool_blocks",
        "closeout:blocked_tasks",
        "closeout:open_taskboard",
        "closeout:dirty_workspace",
        "closeout:context_pressure",
    }:
        if not profile or not hasattr(profile, "append_profile_learning"):
            return "confirmed; profile learning unavailable"
        insights = _profile_insights_for(kind, suggestion.recommendation)
        if not insights:
            return "confirmed diagnostic; no profile change"
        source = "trace-learning:" + suggestion.id.replace(":", "-")[:120]
        try:
            profile.append_profile_learning(source, insights)
            return "confirmed and added profile learning"
        except Exception:
            return "confirmed; profile learning write failed"
    return "confirmed diagnostic; no profile change"


def _load_trace(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    limit = max_bytes if max_bytes is not None else int_env("MO_TRACE_LEARNING_MAX_BYTES", DEFAULT_MAX_BYTES)
    try:
        if not path.exists() or path.stat().st_size > max(1, int(limit or DEFAULT_MAX_BYTES)):
            return {}
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _bounded_events(raw: Any, *, max_events: int | None = None) -> list[dict[str, Any]]:
    limit = max_events if max_events is not None else int_env("MO_TRACE_LEARNING_MAX_EVENTS", DEFAULT_MAX_EVENTS)
    if not isinstance(raw, list):
        return []
    events = [event for event in raw if isinstance(event, dict)]
    return events[: max(1, int(limit or DEFAULT_MAX_EVENTS))]


def _payload_flag(event: dict[str, Any], *keys: str) -> bool:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return any(bool(payload.get(key)) for key in keys)


def _event_evidence(events: list[dict[str, Any]], label: str) -> tuple[SuggestionEvidence, ...]:
    out: list[SuggestionEvidence] = []
    for idx, event in enumerate(events[:4], 1):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool = str(payload.get("tool") or payload.get("provider") or payload.get("reason") or event.get("type") or label)
        snippet = redact_monitor_text(f"{label}: {tool}", 220)
        out.append(SuggestionEvidence(turn_id=f"event-{idx}", snippet=snippet))
    return tuple(out)


def _audit_evidence(entries: list[dict[str, Any]], label: str) -> tuple[SuggestionEvidence, ...]:
    out: list[SuggestionEvidence] = []
    for idx, entry in enumerate(entries[:4], 1):
        marker = str(entry.get("tool") or entry.get("event") or entry.get("reason") or label)
        out.append(SuggestionEvidence(turn_id=f"audit-{idx}", snippet=redact_monitor_text(f"{label}: {marker}", 220)))
    return tuple(out)


def _closeout_evidence(session_id: str, unresolved: list[str], label: str) -> tuple[SuggestionEvidence, ...]:
    if unresolved:
        return tuple(
            SuggestionEvidence(turn_id=session_id, snippet=redact_monitor_text(f"{label}: {item}", 220))
            for item in unresolved[:4]
            if str(item or "").strip()
        )
    return (SuggestionEvidence(turn_id=session_id, snippet=redact_monitor_text(label, 220)),)


def _validation_evidence(validation: list[Any], name: str) -> tuple[SuggestionEvidence, ...]:
    for row in validation:
        if isinstance(row, dict) and str(row.get("name") or "") == name:
            return (SuggestionEvidence(turn_id="validation", snippet=redact_monitor_text(str(row.get("message") or ""), 220)),)
    return ()


def _suggestion(
    *,
    session_id: str,
    kind: str,
    recommendation: str,
    evidence: tuple[SuggestionEvidence, ...],
    salt: str,
) -> LearningSuggestion:
    material = "\0".join([session_id, kind, salt, "\0".join(item.snippet for item in evidence)])
    digest = hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return LearningSuggestion(
        id=f"learning-suggestion:{kind}:{digest}",
        kind=kind,
        recommendation=redact_monitor_text(recommendation, 500),
        evidence=evidence,
        promotion="trace-derived; requires explicit /learning confirm before profile/workflow use",
    )


def _profile_insights_for(kind: str, recommendation: str) -> dict[str, list[str]]:
    if kind in {"trace:tool_errors", "closeout:tool_blocks"}:
        return {"core_traits": ["When trace or closeout evidence shows tool errors or blocked tool results, verify files, logs, tests, and runtime before claiming completion"]}
    if kind == "trace:sandbox_blocks":
        return {"core_traits": ["Respect sandbox boundaries; ask for explicit approval before risky filesystem, shell, deploy, or secret operations"]}
    if kind == "trace:turn_limits":
        return {"evolution": ["When work hits turn limits, split scope, preserve a handoff, and resume only from verified evidence"]}
    if kind in {"closeout:blocked_tasks", "closeout:open_taskboard"}:
        return {"core_traits": ["When a closeout has open or blocked taskboard items, preserve blockers and next actions instead of implying completion"]}
    if kind == "closeout:dirty_workspace":
        return {"core_traits": ["Before completion claims after a closeout, check git status and carry forward dirty workspace state explicitly"]}
    if kind == "closeout:context_pressure":
        return {"evolution": ["When closeout shows context pressure or trimmed history, use handoff notes as orientation and re-read files/tests before factual claims"]}
    text = redact_monitor_text(str(recommendation or ""), 220)
    return {"evolution": [text]} if text else {}


def _meta_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_meta"):
        try:
            meta = value.as_meta()
            return meta if isinstance(meta, dict) else {}
        except Exception:
            return {}
    return {}


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
