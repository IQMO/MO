"""Plain/native terminal fallback loop for MO."""
from __future__ import annotations

import os
import sys
import traceback
from typing import Any

from . import input as _input_module
from .input import prompt_toolkit_input
from core.agent.agent_utils import visible_worker_state
from core.provider.provider import clean_provider_error
from core.sandbox import redact_sensitive_text


def read_native_user_input(agent: Any, console: Any) -> str:
    if _input_module.HAS_PROMPT_TOOLKIT and sys.stdin.isatty():
        return prompt_toolkit_input(agent)
    if console:
        return console.input("[dim] > [/dim]")
    lane_tag = f"[{agent.active_lane}]" if agent.active_lane else "[*]"
    return input(f"{lane_tag} > ")


def _startup_runtime_summary(agent: Any, gateway: Any) -> str:
    """Compact native startup orientation. /status owns full detail."""
    parts = [_startup_heartbeat_summary(agent), _startup_telegram_summary(agent)]
    workers = _startup_workers_summary(agent)
    if workers:
        parts.append(workers)
    attention = _startup_attention_summary(agent)
    if attention:
        parts.append(attention)
    return " · ".join(part for part in parts if part)


def _startup_heartbeat_summary(agent: Any) -> str:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    hb_cfg = cfg.get("heartbeat", {}) if isinstance(cfg.get("heartbeat", {}), dict) else {}
    if hb_cfg.get("enabled", True) is False:
        return "heartbeat disabled"
    return "heartbeat clear"


def _startup_telegram_summary(agent: Any) -> str:
    try:
        from core.telegram.gateway import TelegramGateway
        gateway = getattr(agent, "telegram_gateway", None) or getattr(agent, "_telegram_gateway", None)
        if gateway is None:
            gateway = TelegramGateway.from_agent(agent, gateway=getattr(agent, "gateway", None))
        st = gateway.status()
        if not st.get("enabled"):
            return "telegram disabled"
        if st.get("running"):
            return "telegram running"
        if not st.get("token_present"):
            return f"telegram blocked token missing {st.get('token_env')}"
        return "telegram queued"
    except Exception:
        return "telegram needs attention"


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
        pending = getattr(agent, "_pending_interrupted_work", {})
        if isinstance(pending, dict) and str(pending.get("user") or "").strip():
            parts.append("paused work available")
    except Exception:
        traceback.print_exc()
    try:
        if str(getattr(agent, "last_fallback_notice", "") or "").strip():
            parts.append("provider fallback active")
    except Exception:
        traceback.print_exc()
    try:
        scheduler = _startup_scheduler_summary(agent)
        if scheduler:
            parts.append(scheduler)
    except Exception:
        traceback.print_exc()
    return " · ".join(parts)


def _startup_scheduler_summary(agent: Any) -> str:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler", {}), dict) else {}
    service = getattr(agent, "scheduler_service", None)
    enabled = scheduler_cfg.get("enabled", False) is True or service is not None
    if not enabled:
        return ""
    thread = getattr(service, "_thread", None) if service is not None else None
    if thread is not None and getattr(thread, "is_alive", lambda: False)():
        return "scheduler running"
    if service is not None:
        return "scheduler paused"
    return "scheduler needs attention"


def _install_native_async_notices(agent: Any) -> None:
    """Install compact native notices for background worker completion."""
    def _notice(text: str) -> None:
        clean = str(text or "").strip()
        if clean:
            print(f"\n{clean}", flush=True)

    try:
        setattr(agent, "_native_async_notice", _notice)
    except Exception:
        traceback.print_exc()


def _safe_error_text(exc: Exception) -> str:
    clean = clean_provider_error(str(exc) or type(exc).__name__)
    return redact_sensitive_text(clean)


def _run_and_print_turn(agent: Any, gateway: Any, user_input: str) -> None:
    try:
        result = gateway.run_turn(user_input)
    except Exception as exc:
        detail = _safe_error_text(exc)
        result = "\n".join([
            "MO interface error: turn failed",
            "  where: native terminal turn runner",
            "Fix: try again or run /status; check monitor if this repeats.",
            f"  detail: {detail}",
        ])
    if hasattr(agent, "autosave_session"):
        agent.autosave_session()
    if result:
        print(result)
    board = getattr(gateway, "last_task_board", None)
    if board:
        print(board.render())


def run_native_terminal_loop(agent: Any, gateway: Any, console: Any) -> None:
    """Native-scroll terminal loop: output is printed into normal scrollback."""
    _install_native_async_notices(agent)
    print(f"MO v1.0 — {agent.provider_name} / {agent.model}")
    print(f"Project: {getattr(agent, 'project_cwd', os.environ.get('MO_PROJECT_CWD') or os.getcwd())}")
    runtime = _startup_runtime_summary(agent, gateway)
    if runtime:
        print(f"Runtime: {runtime}")
    from .layout import STARTUP_HINT
    print(f"{STARTUP_HINT}, /exit to quit.")
    if _input_module.HAS_PROMPT_TOOLKIT and sys.stdin.isatty():
        print("Native terminal loop enabled. Set MO_TUI=1 for the fixed prompt-toolkit TUI.")
    print()

    while True:
        try:
            user_input = read_native_user_input(agent, console)
        except (EOFError, KeyboardInterrupt):
            break
        user_input = str(user_input).strip()
        if not user_input:
            continue
        if user_input.startswith("/"):
            try:
                cmd_result = agent.process_slash_command(user_input)
            except Exception as exc:  # a local command must never kill the REPL
                print(f"Command failed: {user_input.split()[0]} ({type(exc).__name__}: {_safe_error_text(exc)})")
                continue
            if cmd_result is None:
                print(f"Unknown command: {user_input.split()[0]}")
                continue
            if cmd_result == "[EXIT]":
                break
            if cmd_result == "[GOAL_START]":
                run_goal_plain(agent)
                continue
            if cmd_result == "[GOAL_CONTINUE]":
                continue_goal_plain(agent)
                continue
            if cmd_result == "[RETRY]":
                retry_input = getattr(agent, "_retry_pending_input", "")
                agent._retry_pending_input = ""
                if retry_input:
                    _run_and_print_turn(agent, gateway, retry_input)
                continue
            if cmd_result == "[RUN_TURN]":
                pending_input = getattr(agent, "_slash_pending_input", "")
                agent._slash_pending_input = ""
                if pending_input:
                    _run_and_print_turn(agent, gateway, pending_input)
                continue
            print(cmd_result)
            continue
        _run_and_print_turn(agent, gateway, user_input)


def run_goal_plain(agent: Any) -> None:
    """Run a goal lifecycle in the plain terminal fallback."""
    from core.goal import GoalRunner

    objective = getattr(agent, "_goal_pending_objective", "")
    budget = getattr(agent, "_goal_pending_budget", None)
    if not objective:
        print("No objective set.")
        return
    if not getattr(agent, "_goal_runner", None):
        agent._goal_runner = GoalRunner(agent)
    runner = agent._goal_runner
    result = runner.start(objective, budget=budget)
    print(result)
    while getattr(agent, "_goal_active", False):
        result = runner.continue_goal()
        print(result)
        if result.startswith(("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]")):
            break


def continue_goal_plain(agent: Any) -> None:
    runner = getattr(agent, "_goal_runner", None)
    if not runner:
        print("No active goal to continue.")
        return
    while getattr(agent, "_goal_active", False):
        result = runner.continue_goal()
        print(result)
        if result.startswith(("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]")):
            break


def record_session(agent: Any) -> None:
    # Idempotent: the normal exit path and the atexit/hard-close backstop may
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
    try:
        closeout = agent.save_session_closeout(reason="terminal exit") if hasattr(agent, "save_session_closeout") else None
        input_tokens = sum(e.get("input_tokens", 0) for e in agent.session.token_log)
        output_tokens = sum(e.get("output_tokens", 0) for e in agent.session.token_log)
        agent.profile.record_session(
            turns=agent.session.turn_count,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
        )
        if hasattr(agent, "autosave_session"):
            agent.autosave_session(closeout=closeout)
    except Exception:
        traceback.print_exc()
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
