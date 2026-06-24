"""MO — lightweight turn coordinator and taskboard lifecycle owner.

Gateway decides whether a visible board is allowed, runs Ghost planning for
work turns, parses Ghost's structured plan into task rows, creates the board
lazily when real tool work starts, holds the shared ``last_task_board``
reference, and records terminal snapshots. Agent/tool runtime gates row
progression after creation.
"""
from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING
import traceback

# Surfaces that must NEVER interleave into a Main-MO run: a Ghost/desktop turn is
# rejected while any turn is in flight (e.g. an active DEVMODE board), rather than
# sharing the single agent/session/board. See run_turn().
_SECONDARY_SURFACES = frozenset({"desktop", "ghost", "companion"})
_SECONDARY_BUSY_MESSAGE = (
    "MO is busy with a foreground task right now (it can only run one turn at a time "
    "yet) — try again in a moment."
)

from .backend_monitor import BackendMonitor, monitor_context
from .gateway_helpers import select_template
from .heartbeat import normalize_surface
from .runtime_work_signals import (
    looks_like_interrupted_resume_request,
    tool_is_runtime_work_signal,
)
from .tasking.task_board import (
    TaskBoard,
    board_update_event,
    clear_current_board_if_foreign_session,
    record_snapshot,
    resume_last_board,
)
from .tasking.task_board_registry import TaskBoardRegistry
from .owner_protocols import (
    is_devmode05_activation,
    is_owner_protocol_activation,
    is_vs05_activation,
    owner_protocol_name,
)
from .work_patterns import is_research_method_question

if TYPE_CHECKING:
    from .agent.agent import Agent


class Gateway:
    """Turn coordinator plus shared taskboard lifecycle owner."""

    def __init__(self, agent: "Agent", monitor: BackendMonitor | None = None):
        self.agent = agent
        try:
            setattr(self.agent, "gateway", self)
        except Exception:
            traceback.print_exc()
        self.monitor = monitor or BackendMonitor()
        try:
            from .backend_monitor import set_monitor
            set_monitor(self.monitor)
        except Exception:
            traceback.print_exc()
        self.last_task_board: TaskBoard | None = None
        self.task_board_registry = TaskBoardRegistry()
        # One turn at a time on the shared agent/session/board. A secondary surface
        # (Ghost/desktop) turn is rejected if it can't take this immediately, so it can
        # never interleave into a Main-MO run; Main/primary turns wait for the lock.
        self._turn_lock = threading.Lock()
        # D5 fix: attempt to resume the last incomplete board from the ledger.
        self.last_resumable_board: TaskBoard | None = resume_last_board()
        session = getattr(self.agent, "session", None)
        self.monitor.emit("session_event", {
            "kind": "gateway_attached",
            "session_id": str(getattr(session, "session_id", "") or ""),
            "turn_count": int(getattr(session, "turn_count", 0) or 0),
            "messages": len(getattr(session, "messages", []) or []),
            "slot": str(getattr(getattr(self.agent, "_sessions", None), "current_name", "") or ""),
        })

    def should_show_task_board(self, user_input: str) -> bool:
        """Show a board for any real work turn; skip only chat/greetings/commands."""
        text = str(user_input or "").lower().strip()
        if text.startswith("/"):
            return False
        if is_vs05_activation(text):
            return True
        if is_research_method_question(text):
            return False
        return select_template(text) != "simple_chat"

    def resumable_board(self) -> TaskBoard | None:
        """D5 fix: return the last incomplete board from ledger, or None.

        Callers can offer resume via UI (e.g., TUI hint line, /resume command).
        """
        return self.last_resumable_board

    def run_turn(
        self,
        user_input: str,
        on_board_update: object = None,
        on_token: object = None,
        on_activity: object = None,
        on_proposal: object = None,
        cancel_event: object = None,
        route_source: str = "user",
        on_assistant_text: object = None,
        on_board_event: object = None,
        on_action: object = None,
    ) -> str:
        """Admit one turn at a time on the shared agent, then run it.

        A secondary surface (Ghost/desktop) turn is REJECTED outright while any turn is
        already in flight, so it can never clear the Main board or append into the Main
        conversation mid-run (e.g. during a DEVMODE05 run). Primary turns block until the
        in-flight turn releases. The lock is always released, even on error.
        """
        secondary = route_source in _SECONDARY_SURFACES
        if secondary:
            if not self._turn_lock.acquire(blocking=False):
                try:
                    self.monitor.emit("session_event", {
                        "kind": "secondary_turn_rejected_busy",
                        "route_source": route_source,
                    })
                except Exception:
                    traceback.print_exc()
                return _SECONDARY_BUSY_MESSAGE
        else:
            self._turn_lock.acquire()
        try:
            return self._run_turn_impl(
                user_input,
                on_board_update=on_board_update,
                on_token=on_token,
                on_activity=on_activity,
                on_proposal=on_proposal,
                cancel_event=cancel_event,
                route_source=route_source,
                on_assistant_text=on_assistant_text,
                on_board_event=on_board_event,
                on_action=on_action,
            )
        finally:
            self._turn_lock.release()

    def _run_turn_impl(
        self,
        user_input: str,
        on_board_update: object = None,
        on_token: object = None,
        on_activity: object = None,
        on_proposal: object = None,
        cancel_event: object = None,
        route_source: str = "user",
        on_assistant_text: object = None,
        on_board_event: object = None,
        on_action: object = None,
    ) -> str:
        """Execute a turn; create a taskboard lazily on first tool activity."""
        turn_id = f"turn-{int(time.time() * 1000)}"
        started = time.time()
        session = getattr(self.agent, "session", None)
        session_id = str(getattr(session, "session_id", "") or "")
        surface = normalize_surface(route_source)
        instance_id = str(getattr(self.agent, "instance_id", "") or "")
        result_text = ""
        status = "ok"
        # A secondary (desktop/Ghost) surface turn must NOT clobber the Main board
        # that the TUI / heartbeat / /status all read from the shared last_task_board.
        # Run it against its own registry slot and restore Main in the finally below;
        # primary turns keep the original behavior exactly.
        secondary = route_source in _SECONDARY_SURFACES
        board_slot = surface if secondary else "main"
        saved_main_board = self.last_task_board
        saved_previous_board = getattr(self, "previous_task_board", None)
        self.previous_task_board = self.last_task_board
        self.last_task_board = None
        self.task_board_registry.clear_board(board_slot)
        # A new session must not inherit a PRIOR session's board — neither in-memory (a
        # persistent gateway carries last_task_board across sessions) nor in the persisted
        # current.json (watchers / status / resume fast-path read it). Same-session state
        # is preserved and the ledger stays authoritative (live mo-1782304565: an IAM05
        # turn showed mo-1782300201's stale DEVMODE05 board). Primary turns only —
        # secondary surfaces keep their own slot and restore Main in the finally.
        if not secondary and session_id:
            prev = self.previous_task_board
            if prev is not None and str(getattr(prev, "session_id", "") or "") not in ("", session_id):
                self.previous_task_board = None
            try:
                clear_current_board_if_foreign_session(session_id)
            except Exception:
                traceback.print_exc()
        resume_intent = _has_pending_resume_intent(self.agent, user_input)
        board_objective = _board_objective_text(self.agent, user_input, resume_intent=resume_intent)

        previous_route_source = getattr(self.agent, "_current_route_source", "")
        try:
            setattr(self.agent, "_current_route_source", route_source)
        except Exception:
            traceback.print_exc()
        # R2: clear any stale pre-vision snapshot left by a prior (e.g. background
        # worker) turn so THIS turn's capture_screen restore uses this turn's
        # provider; restored in the finally below.
        try:
            setattr(self.agent, "_pre_vision_provider", None)
        except Exception:
            traceback.print_exc()

        with monitor_context(
            turn_id=turn_id,
            session_id=session_id,
            surface=surface,
            route_source=route_source,
            instance_id=instance_id,
        ):
            self.monitor.emit("turn_start", {
                "input": str(user_input or "")[:300],
                "route_source": route_source,
                "surface": surface,
                "instance_id": instance_id,
                "messages": len(getattr(session, "messages", []) or []),
            })
            try:
                from .heartbeat import record_heartbeat
                record_heartbeat(self.agent, gateway=self, surface=route_source, event="turn_start")
            except Exception:
                traceback.print_exc()

            try:
                # Ghost planning runs for all work turns — not just ghost-routed.
                # Ghost generates intent guardrails AND structured taskboard rows.
                ghost_plan_text = ""
                ghost_plan_rows: list[dict[str, object]] = []
                # ALL owner protocols (DEVMODE05/VS05/IFDEV05/IAM05) skip the generic
                # ghost board-seeding. Previously only DEVMODE05/VS05 did, so IAM05/IFDEV05
                # fell through and inherited a DEVMODE05-flavored ghost board their own
                # closeout never advances — a stale open board tripping the generic
                # done-claim gate (live mo-1782300201). A protocol drives its own task
                # truth (or none); it must never get a contaminated cross-protocol board.
                protocol_activation = is_owner_protocol_activation(user_input)
                if protocol_activation:
                    try:
                        setattr(self.agent, "_pending_turn_proposal", "")
                    except Exception:
                        traceback.print_exc()
                    self.monitor.emit("session_event", {
                        "kind": "protocol_ghost_proposal_skipped",
                        "protocol": owner_protocol_name(user_input),
                    })
                elif self.should_show_task_board(user_input) and hasattr(self.agent, "propose_work"):
                    raw_proposal = self.agent.propose_work(user_input, monitor=self.monitor)
                    if raw_proposal:
                        ghost_plan_text, ghost_plan_rows = _parse_ghost_proposal(raw_proposal)
                        if on_proposal:
                            on_proposal(ghost_plan_text)

                # Runtime-aware lazy board creation. The pre-turn heuristic is a
                # display/proposal hint; actual board materialization waits for
                # a tool/work signal and rejects simple read-only orientation.
                board_holder = [None]

                def _lazy_create_board(tool_name: str = "", arguments: dict | None = None):
                    if board_holder[0]:
                        return board_holder[0]
                    if not _runtime_should_create_board(self.agent, user_input, route_source, tool_name, arguments, resume_intent=resume_intent):
                        return None
                    
                    if resume_intent and getattr(self, "previous_task_board", None):
                        board = self.previous_task_board
                        if board.state in ("abandoned", "blocked"):
                            board.state = "active"
                    else:
                        board = _new_gateway_board(turn_id, session_id, board_objective, rows=ghost_plan_rows if ghost_plan_rows else None)
                    self.last_task_board = board
                    self.task_board_registry.set_board(board_slot, board)
                    board_holder[0] = board
                    record_snapshot(board, "created", source="gateway")
                    event = board_update_event(board, update="created")
                    self.task_board_registry.record_event(board_slot, board, update="created", event=event)
                    if on_board_update:
                        on_board_update(event["rich"])
                    if on_board_event:
                        on_board_event(event)
                    self.monitor.emit("taskboard", event)
                    return board

                kwargs = _agent_run_kwargs(
                    self.agent,
                    on_token=on_token,
                    on_activity=on_activity,
                    on_first_tool=_lazy_create_board,
                    cancel_event=cancel_event,
                    on_board_update=on_board_update,
                    on_assistant_text=on_assistant_text,
                    on_board_event=on_board_event,
                    on_action=on_action,
                )

                result_text = self.agent.run_turn(
                    user_input,
                    monitor=self.monitor,
                    **kwargs,
                )
                result_text = _continue_devmode05_after_runtime_boundary(
                    self.agent,
                    user_input,
                    result_text,
                    monitor=self.monitor,
                    callbacks=kwargs,
                    cancel_event=cancel_event,
                    on_activity=on_activity,
                )
                result_text = _continue_work_after_runtime_boundary(
                    self.agent,
                    user_input,
                    result_text,
                    monitor=self.monitor,
                    callbacks=kwargs,
                    cancel_event=cancel_event,
                    on_activity=on_activity,
                    has_open_board=lambda: bool(self.last_task_board and self.last_task_board.open_count() > 0),
                )
                result_text = _block_open_protocol_board_at_turn_end(
                    self.agent,
                    user_input,
                    result_text,
                    self.last_task_board,
                    monitor=self.monitor,
                )

            except Exception as exc:
                status = "error"
                self.monitor.emit("turn_error", {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                })
                if self.last_task_board is not None:
                    try:
                        # Park the work so "proceed please" can resume it
                        self.agent._pending_interrupted_work = {
                            "user": user_input,
                            "reason": "error",
                            "changed": True,
                        }
                    except Exception as e:
                        self.monitor.emit("turn_error_park_failed", {"error": str(e)[:200]})
                raise
            finally:
                # R2: undo any per-turn capture_screen vision-provider switch so the
                # next turn (and concurrent surfaces sharing this agent) start from
                # the configured provider. No-op unless a screenshot switched it.
                try:
                    restore = getattr(self.agent, "restore_vision_provider", None)
                    if callable(restore):
                        restore()
                except Exception:
                    traceback.print_exc()
                elapsed_ms = int((time.time() - started) * 1000)
                if self.last_task_board is not None:
                    event, state = terminal_board_event(self.last_task_board, status)
                    record_terminal_snapshot(self.last_task_board, event, source="gateway", state=state)
                    self.task_board_registry.record_event(board_slot, self.last_task_board, update=event)
                self.monitor.emit("turn_end", {
                    "status": status,
                    "duration_ms": elapsed_ms,
                    "result_chars": len(str(result_text or "")),
                    "has_task_board": self.last_task_board is not None,
                })
                try:
                    from .heartbeat import record_heartbeat
                    record_heartbeat(self.agent, gateway=self, surface=route_source, event="turn_end", extra={"status": status, "duration_ms": elapsed_ms})
                except Exception:
                    traceback.print_exc()
                try:
                    setattr(self.agent, "_current_route_source", previous_route_source)
                except Exception:
                    traceback.print_exc()
                # Restore the Main board after a secondary (desktop/Ghost) turn so a
                # desktop turn never clobbers the board the TUI/heartbeat/status read.
                if secondary:
                    self.last_task_board = saved_main_board
                    self.previous_task_board = saved_previous_board

            return result_text


def _continue_devmode05_after_runtime_boundary(
    agent: object,
    user_input: str,
    result_text: str,
    *,
    monitor: BackendMonitor,
    callbacks: dict[str, object],
    cancel_event: object = None,
    on_activity: object = None,
) -> str:
    """Resume DEVMODE05 in fresh turns after recoverable runtime budgets.

    This is intentionally narrower than the reverted autonomous runner: it does
    not take over DEVMODE05 generally, and it does not auto-resume sandbox,
    permission, provider-error, or safety boundaries.
    """
    if not is_devmode05_activation(user_input):
        return result_text

    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    max_continuations = _safe_positive_int(agent_cfg.get("devmode05_auto_continuation_max_turns"), 20)
    continuation_count = 0
    current = str(result_text or "")

    while _devmode05_runtime_boundary_needs_continuation(current):
        if continuation_count >= max_continuations:
            monitor.emit("session_event", {
                "kind": "devmode05_auto_continuation_limit",
                "continuations": continuation_count,
                "result_preview": current[:300],
            })
            return current
        if getattr(cancel_event, "is_set", lambda: False)():
            return "[ABORTED] Current turn stopped."

        continuation_count += 1
        if callable(on_activity):
            on_activity("DEVMODE05: continuing after runtime boundary...")
        monitor.emit("session_event", {
            "kind": "devmode05_auto_continuation",
            "continuation": continuation_count,
            "boundary_preview": current[:300],
        })
        current = str(agent.run_turn(
            _devmode05_continuation_prompt(current, continuation_count),
            monitor=monitor,
            **callbacks,
        ) or "")

    return current


def _continue_work_after_runtime_boundary(
    agent: object,
    user_input: str,
    result_text: str,
    *,
    monitor: BackendMonitor,
    callbacks: dict[str, object],
    cancel_event: object = None,
    on_activity: object = None,
    has_open_board: object = None,
) -> str:
    """Resume ordinary authorized work after recoverable runtime budgets.

    Goal mode already owns its loop, and DEVMODE05 has a stricter protocol
    loop. This only bridges plain work turns that already produced an open
    taskboard and hit a local budget boundary.
    """
    if is_devmode05_activation(user_input):
        return result_text
    if not _callable_bool(has_open_board):
        return result_text

    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    max_continuations = _safe_positive_int(agent_cfg.get("work_auto_continuation_max_turns"), 3)
    continuation_count = 0
    current = str(result_text or "")

    while _work_runtime_boundary_needs_continuation(current):
        if continuation_count >= max_continuations:
            monitor.emit("session_event", {
                "kind": "work_auto_continuation_limit",
                "continuations": continuation_count,
                "result_preview": current[:300],
            })
            return current
        if getattr(cancel_event, "is_set", lambda: False)():
            return "[ABORTED] Current turn stopped."

        continuation_count += 1
        if callable(on_activity):
            on_activity("MO: continuing active work after runtime boundary...")
        monitor.emit("session_event", {
            "kind": "work_auto_continuation",
            "continuation": continuation_count,
            "boundary_preview": current[:300],
        })
        current = str(agent.run_turn(
            _work_continuation_prompt(user_input, current, continuation_count),
            monitor=monitor,
            **callbacks,
        ) or "")

    return current


def _devmode05_runtime_boundary_needs_continuation(result_text: str) -> bool:
    text = _terminal_marker_text(result_text)
    if not text:
        return False
    if text.startswith(("[max provider requests]", "[max tool rounds]")):
        return True
    if text.startswith(("[devmode05 continuation capsule]", "[devmode05 continuation]")):
        return True
    if not text.startswith("[devmode05 blocked]"):
        return False
    recoverable_markers = (
        "budget exhaustion",
        "tool budget",
        "tool rounds",
        "max provider",
        "max tool",
        "request limit",
        "turn limit",
        "persistently blocked after budget",
        "continuation required in the next fresh turn",
        "no more tools allowed this turn",
        "current runtime instruction explicitly forbids further tool calls",
        "tool-use gate",
    )
    return any(marker in text for marker in recoverable_markers)


def _work_runtime_boundary_needs_continuation(result_text: str) -> bool:
    text = _terminal_marker_text(result_text)
    if not text:
        return False
    if text.startswith(("[max provider requests]", "[max tool rounds]")):
        return True
    if text.startswith(("[work continuation capsule]", "[work continuation]")):
        return True
    if not text.startswith("[work blocked]"):
        return False
    recoverable_markers = (
        "budget exhaustion",
        "tool budget",
        "tool rounds",
        "max provider",
        "max tool",
        "request limit",
        "turn limit",
        "persistently blocked after budget",
        "continuation required in the next fresh turn",
        "no more tools allowed this turn",
        "current runtime instruction explicitly forbids further tool calls",
        "tool-use gate",
    )
    return any(marker in text for marker in recoverable_markers)


def _terminal_marker_text(result_text: str) -> str:
    """Normalize harmless Markdown wrapping before runtime boundary markers."""
    text = str(result_text or "").lstrip()
    if not text:
        return ""
    text = re.sub(r"^(?:[-*_]{3,}\s*)+", "", text).lstrip()
    text = re.sub(r"^(?:#{1,6}\s*)+", "", text).lstrip()
    text = re.sub(r"^(?:[*_`~>\s]+)+", "", text).lstrip()
    return " ".join(text.lower().split())


def _devmode05_continuation_prompt(previous_boundary: str, continuation_count: int) -> str:
    boundary = " ".join(str(previous_boundary or "").split())[:1200]
    return (
        f"DEVMODE05 CONTINUATION {continuation_count}. "
        "Continue the active DEVMODE05 protocol from the preserved handoff/capsule and current repo evidence. "
        "Any 'do not call tools' or 'no more tools this turn' instruction in the previous capsule applied only to the exhausted prior turn; this is the fresh continuation turn and tools are available again. "
        "Do not ask the operator to re-explain scope, do not redo completed discovery, and do not stop at a progress report. "
        "Resume from the exact next unresolved action. "
        "Stop only with [DEVMODE05 COMPLETE] when complete, or [DEVMODE05 BLOCKED] for a real non-recoverable tool/provider/timeout/sandbox/permission/safety boundary. "
        f"Previous runtime boundary/capsule: {boundary}"
    )


def _work_continuation_prompt(original_user_input: str, previous_boundary: str, continuation_count: int) -> str:
    original = " ".join(str(original_user_input or "").split())[:700]
    boundary = " ".join(str(previous_boundary or "").split())[:1200]
    return (
        f"WORK CONTINUATION {continuation_count}. "
        "Continue the active operator-authorized work from the preserved handoff, taskboard, and current repo evidence. "
        "Any 'do not call tools' or 'no more tools this turn' instruction in the previous capsule applied only to the exhausted prior turn; this is the fresh continuation turn and tools are available again. "
        "Do not ask the operator to re-explain scope, do not redo completed discovery, and do not stop at a progress report. "
        "Resume from the exact next unresolved action and keep working until the taskboard is complete or a real non-recoverable boundary is reached. "
        "Stop only with a normal final answer when complete, or `[WORK BLOCKED]` for a real non-recoverable tool/provider/timeout/sandbox/permission/safety boundary. "
        f"Original request: {original}. Previous runtime boundary/capsule: {boundary}"
    )


def _safe_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _callable_bool(callback: object) -> bool:
    if not callable(callback):
        return False
    try:
        return bool(callback())
    except Exception:
        traceback.print_exc()
        return False


def _runtime_should_create_board(
    agent: object,
    user_input: str,
    route_source: str,
    tool_name: str = "",
    arguments: dict | None = None,
    *,
    resume_intent: bool = False,
) -> bool:
    """Decide board creation: always yes for work turns, no for research-method questions.

    Previously had complex heuristic gates; now simplified: if should_show_task_board
    passed, and we reached a first tool call, create the board.
    """
    _ = agent, route_source
    text = str(user_input or "")
    if is_research_method_question(text):
        return False
    if is_devmode05_activation(text):
        return True
    if is_vs05_activation(text):
        return True
    if resume_intent:
        return True
    if select_template(text) != "simple_chat":
        return True
    # For simple_chat, still create board if the tool is a real work signal (edit, shell, etc.)
    return tool_is_runtime_work_signal(tool_name, arguments or {})


def _board_objective_text(agent: object, user_input: str, *, resume_intent: bool = False) -> str:
    """Use the resumed work objective when the user explicitly resumes parked work."""
    if resume_intent:
        pending = getattr(agent, "_pending_interrupted_work", {})
        if isinstance(pending, dict):
            prior = str(pending.get("user") or "").strip()
            if prior:
                return prior
    return str(user_input or "")


def _has_pending_resume_intent(agent: object, user_input: str) -> bool:
    pending = getattr(agent, "_pending_interrupted_work", {})
    if not isinstance(pending, dict) or not str(pending.get("user") or "").strip():
        return False
    return looks_like_interrupted_resume_request(user_input)


def _agent_run_kwargs(agent: object, **callbacks: object) -> dict[str, object]:
    """Return only callback kwargs accepted by the agent's run_turn signature."""
    import inspect

    sig = inspect.signature(agent.run_turn)
    accepts_var_kwargs = any(param.kind == param.VAR_KEYWORD for param in sig.parameters.values())
    return {
        name: value
        for name, value in callbacks.items()
        if accepts_var_kwargs or name in sig.parameters
    }


def terminal_board_event(board: TaskBoard, status: str) -> tuple[str, str]:
    """Map final turn status to the terminal taskboard ledger event/state."""
    if status != "ok":
        return "abandoned", "abandoned"
    if board.tasks and board.open_count() == 0:
        return "completed", "completed"
    if not board.tasks:
        return "updated", "active"
    if any(task.status == "blocked" for task in board.tasks):
        return "blocked", "blocked"
    return "updated", "active"

# Legacy alias — internal callers use the public name.
_terminal_board_event = terminal_board_event


def _block_open_protocol_board_at_turn_end(
    agent: object,
    user_input: str,
    result_text: str,
    board: TaskBoard | None,
    *,
    monitor: BackendMonitor,
) -> str:
    """Block protocol turns that claim terminal status with open task rows."""
    if not board or not board.tasks or board.open_count() <= 0:
        return result_text
    protocol = ""
    if is_devmode05_activation(user_input):
        protocol = "DEVMODE05"
    elif is_vs05_activation(user_input):
        protocol = "VS05"
    if not protocol:
        return result_text
    normalized = str(result_text or "").upper()
    if f"[{protocol} COMPLETE]" not in normalized and f"[{protocol} BLOCKED]" not in normalized:
        return result_text

    task_id = board.active_task_id() or board.first_ready_pending_id()
    if task_id:
        board.block(task_id, "protocol terminal marker returned with open taskboard rows")
    try:
        agent._pending_interrupted_work = {
            "user": user_input,
            "reason": "open_protocol_taskboard",
            "changed": True,
        }
    except Exception:
        traceback.print_exc()
    monitor.emit("protocol_open_taskboard_blocked", {
        "protocol": protocol,
        "board_id": board.board_id,
        "open_count": board.open_count(),
        "task_id": str(task_id or ""),
    })
    return (
        f"[{protocol} BLOCKED] Protocol returned a terminal marker while the taskboard still has "
        f"{board.open_count()} open row(s). Resume and close the board with evidence before final closeout."
    )


def record_terminal_snapshot(
    board: TaskBoard,
    event: str,
    *,
    source: str = "gateway",
    state: str | None = None,
) -> None:
    """Record a terminal board snapshot through Gateway's lifecycle.

    All terminal snapshot writes — whether from turn-end, goal-end, or
    external consumers — should route through this function so Gateway
    owns the final ledger truth.
    """
    record_snapshot(board, event, source=source, state=state or "active")


def _new_gateway_board(
    turn_id: str,
    session_id: str,
    user_input: str,
    *,
    title: str | None = None,
    rows: list[dict[str, object]] | None = None,
) -> TaskBoard:
    """Create board from Ghost's planned rows, or a single-row fallback."""
    board = TaskBoard(turn_id=turn_id, session_id=session_id, source="gateway")
    target = str(title or "").strip() or user_input[:80]
    proto = is_owner_protocol_activation(user_input)
    if is_devmode05_activation(user_input):
        rows = _devmode05_gateway_phase_rows()
    elif is_vs05_activation(user_input):
        rows = _vs05_gateway_phase_rows()
    elif not rows and not proto:
        # No Ghost plan and NOT an owner protocol: seed the matching build/reasoning
        # work procedure so the board carries the proven evidence-gated phases instead
        # of one generic row. Owner protocols are EXCLUDED here — DEVMODE05/VS05 use the
        # explicit phase rows above, IFDEV05 drives its own board, and IAM05 is boardless.
        # None may inherit a generic work-procedure board (build_verify/project_audit/…),
        # which has no protocol closeout and would trip the done-claim gate. Live
        # mo-1782307665: "Run IAM05 on …" got a build_verify board through THIS fallback
        # even though the ghost proposal was already skipped (9555983 closed only ghost).
        rows = _work_procedure_rows(user_input, target)
    if rows:
        board.set_rows(f"{target}", rows, objective=user_input[:200])
    elif not proto:
        # Ghost unavailable and no matching procedure — single-row fallback, non-protocol
        # turns only. An owner protocol with no explicit rows stays an EMPTY board: the
        # protocol/model owns its task truth and an empty board can't trip done-claim.
        board.set_rows(
            f"{target}",
            [{"id": "1", "text": f"Work on {target}", "status": "active", "kind": "edit", "completion_gate": "tool", "depends_on": []}],
            objective=user_input[:200],
        )
    return board


def _work_procedure_rows(user_input: str, target: str = "") -> list[dict[str, object]] | None:
    """Seed rows from the matching build/reasoning work procedure, if any.

    The procedure is selected from *user_input*; *target* (the board objective) is
    anchored onto the active first row so the board still shows what is being
    worked on. Fail-open: any error yields None so board creation always falls
    back to the single-row default and never breaks a turn.
    """
    try:
        from .work_patterns import procedure_for
        from .tasking.procedure import procedure_rows

        procedure = procedure_for(user_input)
        return procedure_rows(procedure, objective=target) if procedure else None
    except Exception:
        return None


def _devmode05_gateway_phase_rows() -> list[dict[str, object]]:
    """Fallback DEVMODE05 rows when Ghost does not provide a structured plan."""
    return [
        {
            "id": "1",
            "text": "Boot protocol and load prior session context",
            "status": "active",
            "kind": "inspect",
            "completion_gate": "tool",
            "depends_on": [],
        },
        {
            "id": "2",
            "text": "Run live-trace rewind and baseline-plus-delta capability matrix",
            "status": "pending",
            "kind": "verify",
            "completion_gate": "tool",
            "depends_on": ["1"],
        },
        {
            "id": "3",
            "text": "Catalog verified findings and choose the highest-priority lane",
            "status": "pending",
            "kind": "verify",
            "completion_gate": "tool",
            "depends_on": ["2"],
        },
        {
            "id": "4",
            "text": "Fix validated DEVMODE05 findings without duplicating existing features",
            "status": "pending",
            "kind": "edit",
            "completion_gate": "tool",
            "depends_on": ["3"],
        },
        {
            "id": "5",
            "text": "Verify behavior, cost, handoff, compression, and task truth",
            "status": "pending",
            "kind": "verify",
            "completion_gate": "tool",
            "depends_on": ["4"],
        },
        {
            "id": "6",
            "text": "Write summary, close task truth, and produce final DEVMODE05 report",
            "status": "pending",
            "kind": "report",
            "completion_gate": "final",
            "depends_on": ["5"],
        },
    ]


def _vs05_gateway_phase_rows() -> list[dict[str, object]]:
    """Fallback VS05 rows when Ghost does not provide a structured plan."""
    return [
        {
            "id": "1",
            "text": "Capture current-MO target, reference roles, scope, and read-only boundary",
            "status": "active",
            "kind": "inspect",
            "completion_gate": "tool",
            "depends_on": [],
        },
        {
            "id": "2",
            "text": "Build current-MO baseline from structured evidence before broad reads",
            "status": "pending",
            "kind": "inspect",
            "completion_gate": "tool",
            "depends_on": ["1"],
        },
        {
            "id": "3",
            "text": "Build comparison matrix against current MO with reference evidence",
            "status": "pending",
            "kind": "verify",
            "completion_gate": "tool",
            "depends_on": ["2"],
        },
        {
            "id": "4",
            "text": "Classify adopt, reject, defer, by-design, and unknown items",
            "status": "pending",
            "kind": "verify",
            "completion_gate": "tool",
            "depends_on": ["3"],
        },
        {
            "id": "5",
            "text": "Write VS05 artifacts and approval-ready closeout",
            "status": "pending",
            "kind": "report",
            "completion_gate": "final",
            "depends_on": ["4"],
        },
    ]


def _parse_ghost_proposal(raw: str) -> tuple[str, list[dict[str, object]]]:
    """Split Ghost's proposal into text context and structured task rows.
    
    Ghost outputs: intent text --- JSON tasks block.
    Returns (text_context, rows_list).
    """
    import json
    text_part = raw
    rows: list[dict[str, object]] = []
    
    # Try splitting on "---" separator
    if "---" in raw:
        parts = raw.split("---", 1)
        text_part = parts[0].strip()
        json_candidate = parts[1].strip()
    else:
        # No separator — try to find JSON block anywhere
        json_candidate = raw
    
    # Strip markdown code fences if present
    json_candidate = re.sub(r'```(?:json)?\s*', '', json_candidate)
    json_candidate = re.sub(r'```\s*$', '', json_candidate)

    # Try to extract and parse JSON — try both {"tasks": [...]} and bare [...] formats
    for extractor in (_extract_json_object, _extract_json_array):
        try:
            rows = extractor(json_candidate)
            if rows:
                break
        except (ValueError, json.JSONDecodeError):
            continue
    
    return text_part, rows


def _extract_json_object(text: str) -> list[dict[str, object]]:
    """Extract {"tasks": [...]} from text. Returns rows list or raises."""
    import json
    start = text.index("{")
    end = text.rindex("}") + 1
    parsed = json.loads(text[start:end])
    if isinstance(parsed, dict) and "tasks" in parsed:
        task_list = parsed["tasks"]
        if isinstance(task_list, list) and task_list and isinstance(task_list[0], dict):
            return task_list
    return []


def _extract_json_array(text: str) -> list[dict[str, object]]:
    """Extract [{...}, ...] from text. Returns rows list or raises."""
    import json
    start = text.index("[")
    end = text.rindex("]") + 1
    parsed = json.loads(text[start:end])
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed
    return []
