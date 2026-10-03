"""Shared Terminal startup and session-lifecycle helpers."""
from __future__ import annotations

import traceback
from typing import Any

from core.agent.agent_utils import visible_worker_state


def _startup_runtime_summary(agent: Any, gateway: Any) -> str:
    """Compact native startup orientation. /status owns full detail."""
    workers = _startup_workers_summary(agent)
    return workers or "No work running"


def _startup_workers_summary(agent: Any) -> str:
    try:
        registry = getattr(agent, "workers", None)
        active = registry.active() if registry and hasattr(registry, "active") else []
        if not active:
            return ""
        counts: dict[str, int] = {}
        for record in active:
            state = _visible_worker_state(str(getattr(record, "state", "") or "running"))
            counts[state] = counts.get(state, 0) + 1
        return "workers " + ", ".join(f"{count} {state}" for state, count in sorted(counts.items()))
    except Exception:
        return "workers needs attention"


def _visible_worker_state(state: str) -> str:
    return visible_worker_state(state)


def _startup_attention_summary(agent: Any) -> str:
    """Actionable startup-only hints; full detail belongs to /status."""
    parts: list[str] = []
    try:
        notice = str(getattr(agent, "last_fallback_notice", "") or "").strip()
        change_kind = str(getattr(agent, "last_model_change_kind", "") or "")
        if notice and change_kind in {"", "provider_fallback"}:
            parts.append("provider fallback active")
    except Exception:
        traceback.print_exc()
    try:
        if tuple(getattr(agent, "state_layout_warnings", ()) or ()):
            parts.append("state layout needs /doctor layout")
    except Exception:
        traceback.print_exc()
    try:
        scheduler_summary = getattr(agent, "_status_scheduler_summary", lambda: "")()
        if scheduler_summary:
            parts.append("scheduler " + scheduler_summary.partition(" ·")[0])
    except Exception:
        traceback.print_exc()
    return " · ".join(parts)


def record_session(agent: Any) -> None:
    # Idempotent: the normal exit path and the interpreter-exit backstop may
    # both call this — run the closeout bookkeeping at most once per session.
    if getattr(agent, "_session_recorded", False):
        return
    try:
        agent._session_recorded = True
    except Exception:
        pass
    try:
        runtime = getattr(agent, "worker_runtime", None)
        if runtime and hasattr(runtime, "wait_for"):
            runtime.wait_for(kinds={"prt"}, timeout=3.0)
    except Exception:
        traceback.print_exc()
    closeout = None
    try:
        if hasattr(agent, "save_session_closeout"):
            closeout = agent.save_session_closeout(reason="terminal exit")
    except Exception:
        try:
            agent._last_session_closeout_status = "failed"
        except Exception:
            pass
    try:
        input_tokens = sum(e.get("input_tokens", 0) for e in agent.session.token_log)
        output_tokens = sum(e.get("output_tokens", 0) for e in agent.session.token_log)
        agent.profile.record_session(
            turns=agent.session.turn_count,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
        )
    except Exception:
        traceback.print_exc()
    snapshot_saved = None
    try:
        if hasattr(agent, "autosave_session"):
            snapshot_saved = agent.autosave_session(closeout=closeout)
    except Exception:
        snapshot_saved = False
    try:
        notice = agent.session_closeout_notice(snapshot_saved=snapshot_saved is True)
        if notice:
            print(notice)
    except Exception:
        pass
    # Cleanup stale empty entries from episodic memory.
    try:
        memory = getattr(agent, "memory", None)
        if memory:
            with memory._connect() as conn:
                conn.execute("DELETE FROM turns WHERE length(assistant) < 10")
                try:
                    conn.execute("DELETE FROM turns_fts WHERE turn_id NOT IN (SELECT turn_id FROM turns)")
                except Exception:
                    traceback.print_exc()
    except Exception:
        traceback.print_exc()
