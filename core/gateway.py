"""MO — lightweight turn coordinator and taskboard lifecycle owner.

Gateway decides whether a visible board is allowed, creates it lazily when real
tool work starts, publishes structured state changes, and records terminal
snapshots. Agent/tool runtime gates row progression after creation.
"""
from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable
import traceback

from . import local_extensions
from .runtime.backend_monitor import BackendMonitor, active_monitor, monitor_context
from .runtime.heartbeat import record_heartbeat
from .runtime.surface_identity import DESKTOP_SURFACES, normalize_runtime_surface
from .runtime.turn_intent import BOARD_MODEL_PLAN, BOARD_NONE, BOARD_PROCEDURE, BOARD_RESUME, TurnIntent, classify_turn
from .runtime.work_signals import (
    looks_like_interrupted_resume_request,
    tool_is_runtime_work_signal,
)
from .session.session import assistant_result_is_incomplete, assistant_result_is_request_limit
from .tasking.task_board import (
    TaskBoard,
    board_update_event,
    clear_current_board_if_empty,
    clear_current_board_if_foreign_session,
    record_snapshot,
    resume_last_board,
)
from .tasking import task_evidence
from .context.work_patterns import is_research_method_question

if TYPE_CHECKING:
    from .agent.agent import Agent
    from mo_desktop.intent import DesktopActionAdmission


@dataclass
class GatewayTurnState:
    """Gateway-owned admitted state for one outer user turn."""

    turn_id: str
    started_at: float
    session_id: str
    runtime_surface: str
    route_source_raw: str
    model_slot: str
    instance_id: str
    secondary: bool
    task_board: TaskBoard | None
    saved_active_board: TaskBoard | None
    resume_intent: bool
    resume_board: TaskBoard | None
    manual_resume_board: TaskBoard | None
    turn_intent: TurnIntent
    desktop_action_admission: "DesktopActionAdmission | None"
    board_objective: str
    previous_route_source_raw: str
    previous_runtime_surface: str
    previous_model_slot: str
    previous_turn_intent: TurnIntent | None
    previous_desktop_action_admission: object
    previous_surface_session_identity: object
    user_input: str


# ── safe attribute / call helpers ──────────────────────────────────────
def _mail_turn(user_input: str) -> bool:
    from .mail.intent import is_mail_sensitive_request

    return is_mail_sensitive_request(user_input, include_approval=True)


def _safe_setattr(obj: object, name: str, value: object) -> None:
    """Guard ``setattr`` against proxy/mock objects that reject it."""
    try:
        setattr(obj, name, value)
    except Exception:
        traceback.print_exc()


def _safe_call(fn: Callable[[], object]) -> object | None:
    """Guard zero-arg callables (e.g. lambda helpers) that may fail."""
    try:
        return fn()
    except Exception:
        traceback.print_exc()
        return None


class Gateway:
    """Turn coordinator plus shared taskboard lifecycle owner."""

    # Surface wrappers use this explicit contract instead of guessing from the
    # presence of ``run_turn``. Gateway owns selection, consumption, and the
    # publish ordering for every private conversational surface.
    manages_surface_handoff = True

    def __init__(self, agent: "Agent", monitor: BackendMonitor | None = None):
        self.agent = agent
        try:
            setattr(self.agent, "gateway", self)
        except Exception:
            traceback.print_exc()
        self.monitor = monitor or BackendMonitor()
        try:
            from .runtime.backend_monitor import set_monitor
            set_monitor(self.monitor)
        except Exception:
            traceback.print_exc()
        self.last_task_board: TaskBoard | None = None
        self.previous_task_board: TaskBoard | None = None
        # One turn at a time on the shared agent/session/board.
        # Explicit local PRT uses this owner through its normal Gateway scope;
        # post-work automation is owned by the trusted GitHub workflow.
        self._turn_lock = threading.RLock()
        session = getattr(self.agent, "session", None)
        # Resume only this process/session's board. Multiple MO terminals share
        # the private ledger but must never adopt one another's active work.
        self.last_resumable_board: TaskBoard | None = resume_last_board(
            session_id=str(getattr(session, "session_id", "") or "")
        )
        self.monitor.emit("session_event", {
            "kind": "gateway_attached",
            "session_id": str(getattr(session, "session_id", "") or ""),
            "turn_count": int(getattr(session, "turn_count", 0) or 0),
            "messages": len(getattr(session, "messages", []) or []),
            "slot": str(getattr(getattr(self.agent, "_sessions", None), "current_name", "") or ""),
        })

    def should_show_task_board(self, user_input: str) -> bool:
        """Show a board for any real work turn; skip only chat/greetings/commands."""
        return _should_create_board(user_input, for_hint=True)

    def resumable_board(self) -> TaskBoard | None:
        """D5 fix: return the last incomplete board from ledger, or None.

        Callers can offer resume via UI (e.g., TUI hint line, /resume command).
        """
        return self.last_resumable_board

    @contextmanager
    def prt_turn_scope(self, *, timeout_s: float = 120.0):
        """Boundedly exclude foreground turns for one admitted PRT execution."""
        timeout = max(0.0, float(timeout_s or 0.0))
        acquired = (
            self._turn_lock.acquire(blocking=False)
            if timeout <= 0.0
            else self._turn_lock.acquire(timeout=timeout)
        )
        try:
            yield acquired
        finally:
            if acquired:
                self._turn_lock.release()

    def run_turn(
        self,
        user_input: str,
        on_token: object = None,
        on_activity: object = None,
        cancel_event: object = None,
        route_source: str = "user",
        provider_selection: dict | None = None,
        on_assistant_text: object = None,
        on_board_event: object = None,
        on_action: object = None,
        on_operator_visual: object = None,
        on_operator_image: object = None,
        desktop_action_admission: object = None,
        queued_request: bool = False,
        *,
        max_provider_requests: int | None = None,
    ) -> str:
        """Admit one turn at a time on the shared agent, then run it."""
        from .agent.agent_turn import _configured_request_limit, _finish_request_limit
        from .provider.provider import ProviderRequestLimitReached, provider_request_limit

        with self._turn_lock, provider_request_limit(
            _configured_request_limit(self.agent, max_provider_requests)
        ) as limit:
            session = getattr(self.agent, "session", None)
            messages_before = len(getattr(session, "messages", []) or [])
            turns_before = int(getattr(session, "turn_count", 0) or 0)
            limit_stopped = False
            with active_monitor(self.monitor):
                try:
                    override = local_extensions.run_turn_override(self, route_source, user_input, {
                        "on_token": on_token,
                        "on_activity": on_activity,
                        "cancel_event": cancel_event,
                        "on_assistant_text": on_assistant_text,
                        "on_board_event": on_board_event,
                        "on_action": on_action,
                        "on_operator_visual": on_operator_visual,
                        "on_operator_image": on_operator_image,
                    })
                except ProviderRequestLimitReached:
                    override = ""
                    limit_stopped = True
                if limit["exhausted"] and not assistant_result_is_request_limit(override):
                    limit_stopped = True
            if override is not None or limit_stopped:
                # A local override may execute outside Agent.run_turn (for example
                # an isolated maintenance controller). It is still a Gateway turn
                # and therefore participates in the same PRT exclusion boundary.
                recorded = False
                if (
                    session is not None
                    and len(getattr(session, "messages", []) or []) == messages_before
                    and int(getattr(session, "turn_count", 0) or 0) == turns_before
                ):
                    add_user = getattr(session, "add_user", None)
                    add_assistant = getattr(session, "add_assistant", None)
                    if callable(add_user) and callable(add_assistant):
                        add_user(str(user_input or ""))
                        session.turn_count = turns_before + 1
                        if not limit_stopped:
                            add_assistant(str(override or ""))
                        recorded = True
                if limit_stopped:
                    override = _finish_request_limit(
                        self.agent, limit, monitor=self.monitor, discarded_result=override,
                        user_input=user_input,
                    )
                self.monitor.emit("turn_intercept", {
                    "kind": "local_extension_override",
                    "input": "[Mail turn omitted]" if _mail_turn(user_input) else str(user_input or "")[:300],
                    "result_chars": len(str(override or "")),
                    "recorded": recorded,
                    "route_source": route_source,
                    "surface": normalize_runtime_surface(route_source),
                    "session_id": str(getattr(session, "session_id", "") or ""),
                    "instance_id": str(getattr(self.agent, "instance_id", "") or ""),
                })
                return override

            if provider_selection is None and normalize_runtime_surface(route_source) in DESKTOP_SURFACES:
                from .provider.model_catalog import runtime_model_selection

                provider_selection = runtime_model_selection(self.agent, surface="mo_desktop")
            selection_scope = getattr(self.agent, "model_selection_scope", None)
            scoped = (
                selection_scope(provider_selection)
                if provider_selection is not None and callable(selection_scope)
                else nullcontext()
            )
            with scoped:
                with active_monitor(self.monitor):
                    return self._run_turn_impl(
                        user_input,
                        on_token=on_token,
                        on_activity=on_activity,
                        cancel_event=cancel_event,
                        route_source=route_source,
                        on_assistant_text=on_assistant_text,
                        on_board_event=on_board_event,
                        on_action=on_action,
                        on_operator_visual=on_operator_visual,
                        on_operator_image=on_operator_image,
                        desktop_action_admission=desktop_action_admission,
                        queued_request=queued_request,
                    )

    # ── _run_turn_impl (orchestrator) ───────────────────────────────────
    def _run_turn_impl(
        self,
        user_input: str,
        on_token: object = None,
        on_activity: object = None,
        cancel_event: object = None,
        route_source: str = "user",
        on_assistant_text: object = None,
        on_board_event: object = None,
        on_action: object = None,
        on_operator_visual: object = None,
        on_operator_image: object = None,
        desktop_action_admission: object = None,
        queued_request: bool = False,
    ) -> str:
        """Execute a turn; create a taskboard lazily on first tool activity."""
        turn_state = self._prepare_turn(
            user_input,
            route_source,
            desktop_action_admission=desktop_action_admission,
            queued_request=queued_request,
        )
        result_text = ""
        status = "ok"
        handoff_record, handoff_scope, handoff_key = _surface_handoff_scope(self.agent, route_source)
        try:
            with handoff_scope:
                result_text, status = self._plan_and_run(
                    user_input,
                    turn_state,
                    on_token=on_token,
                    on_activity=on_activity,
                    cancel_event=cancel_event,
                    on_assistant_text=on_assistant_text,
                    on_board_event=on_board_event,
                    on_action=on_action,
                    on_operator_visual=on_operator_visual,
                    on_operator_image=on_operator_image,
                )
            if status == "ok" and handoff_record is not None:
                _mark_surface_handoff(self.agent, handoff_record, route_source, handoff_key)
        except Exception:
            status = "error"
            raise
        finally:
            result_text = self._finalize_turn(
                turn_state,
                result_text,
                status,
                route_source,
                on_board_event=on_board_event,
            )
        return result_text
    # ── _prepare_turn ───────────────────────────────────────────────────
    def _prepare_turn(
        self,
        user_input: str,
        route_source: str,
        *,
        desktop_action_admission: object = None,
        queued_request: bool = False,
    ) -> GatewayTurnState:
        """Set up and return the typed state for one admitted outer turn."""
        turn_id = f"turn-{int(time.time() * 1000)}"
        started = time.time()
        session = getattr(self.agent, "session", None)
        session_id = str(getattr(session, "session_id", "") or "")
        surface = normalize_runtime_surface(route_source)
        instance_id = str(getattr(self.agent, "instance_id", "") or "")
        resolve_slot = getattr(self.agent, "_resolve_model_slot", None)
        try:
            resolution = resolve_slot(surface) if callable(resolve_slot) else None
            model_slot = str(getattr(resolution, "slot", "") or "main")
        except Exception:
            traceback.print_exc()
            model_slot = "main"

        secondary = surface in {"api", "mo_desktop", "companion"}
        saved_active_board = getattr(self.agent, "_active_task_board", None)
        if not secondary:
            self.previous_task_board = self.last_task_board
            self.last_task_board = None
        # No new turn may inherit the previous surface's active board. Secondary
        # surfaces restore it in _finalize_turn after their isolated turn ends.
        _safe_setattr(self.agent, "_active_task_board", None)

        # A new session must not inherit a PRIOR session's board.
        if not secondary and session_id:
            prev = self.previous_task_board
            if prev is not None and str(getattr(prev, "session_id", "") or "") not in ("", session_id):
                self.previous_task_board = None
            try:
                clear_current_board_if_foreign_session(session_id)
                if local_extensions.should_skip_task_board(user_input):
                    clear_current_board_if_empty()
            except Exception:
                traceback.print_exc()

        # Secondary companion sessions never adopt or decide terminal work.
        # Their taskboard isolation applies even when their text happens to look
        # like an approval response to a waiting terminal manual gate.
        mail_turn = _mail_turn(user_input)
        manual_resume_board = None if secondary or mail_turn else _manual_gate_resume_board(
            self.previous_task_board,
            self.last_resumable_board,
            user_input,
        )
        resumable_board = None if secondary or mail_turn else _first_open_board(
            self.previous_task_board,
            self.last_resumable_board,
        )
        extension_rows = [] if mail_turn else local_extensions.board_rows(user_input)
        extension_supersedes_resume = bool(
            manual_resume_board is None
            and extension_rows
            and not _board_matches_extension_rows(resumable_board, extension_rows)
        )
        resumable_available = bool(resumable_board) and not extension_supersedes_resume
        pending_boardless_request = (
            ""
            if manual_resume_board is not None or mail_turn
            else _pending_boardless_request_for_resume(
                self.agent,
                user_input,
            )
        )
        pending_resume = bool(
            not mail_turn
            and not pending_boardless_request
            and (
                manual_resume_board is not None
                or (
                    not extension_supersedes_resume
                    and _has_pending_resume_intent(self.agent, user_input)
                )
            )
        )
        intent_input = str(pending_boardless_request or user_input or "")
        classify_resumable_available = bool(
            resumable_available and not pending_boardless_request
        )
        expand_profile_terms = getattr(self.agent, "_operator_text_with_profile_terms", None)
        if callable(expand_profile_terms):
            try:
                intent_input = str(expand_profile_terms(intent_input) or intent_input)
            except Exception:
                traceback.print_exc()
        turn_intent = classify_turn(
            intent_input,
            pending_resume=pending_resume,
            resumable_available=classify_resumable_available,
            queued_request=queued_request,
        )
        if pending_boardless_request:
            # Classification consumed this checkpoint as the report/request to
            # answer. Leaving the marker active would make Agent's generic
            # interrupted-work context contradict the boardless intent and tell
            # the provider to resume execution.
            _safe_setattr(self.agent, "_pending_interrupted_work", {})
        # Computer-use authority is request-local on every surface.  Desktop may
        # supply its already-typed option/receipt admission; Terminal and other
        # surfaces classify the same operator text here rather than letting a
        # provider proposal or later taskboard revision mint authority.
        from mo_desktop.intent import (
            DesktopActionAdmission,
            admission_from_pending_action,
            admit_desktop_action,
        )

        prior_assistant_text = session.latest_visible_assistant_text() if callable(getattr(session, "latest_visible_assistant_text", None)) else ""
        admitted_desktop_action = (
            desktop_action_admission
            if isinstance(desktop_action_admission, DesktopActionAdmission)
            else admit_desktop_action(
                user_input,
                prior_assistant_text=prior_assistant_text,
            )
        )
        resume_intent = turn_intent.board_policy == BOARD_RESUME
        resume_board = (
            _first_open_board(
                manual_resume_board,
                self.previous_task_board,
                self.last_resumable_board,
            )
            if resume_intent else None
        )
        if resume_board is not None and not admitted_desktop_action.permits_action:
            pending_admission = admission_from_pending_action(
                getattr(resume_board, "pending_action", {})
            )
            if pending_admission is not None:
                admitted_desktop_action = pending_admission
        board_objective = _board_objective_text(
            self.agent,
            user_input,
            resume_intent=resume_intent,
            resume_board=resume_board,
        )
        previous_route_source = getattr(self.agent, "_current_route_source", "")
        previous_runtime_surface = getattr(self.agent, "_current_runtime_surface", "")
        previous_model_slot = getattr(self.agent, "_current_model_slot", "")
        previous_turn_intent = getattr(self.agent, "_active_turn_intent", None)
        previous_desktop_action_admission = getattr(
            self.agent, "_current_desktop_action_admission", None
        )
        previous_surface_session_identity = getattr(
            self.agent, "_active_surface_session_identity", None
        )
        _safe_setattr(self.agent, "_current_route_source", route_source)
        _safe_setattr(self.agent, "_current_runtime_surface", surface)
        _safe_setattr(self.agent, "_current_model_slot", model_slot)
        _safe_setattr(self.agent, "_active_turn_intent", turn_intent)
        _safe_setattr(
            self.agent,
            "_current_desktop_action_admission",
            admitted_desktop_action,
        )
        _safe_setattr(
            self.agent,
            "_active_surface_session_identity",
            _surface_session_identity(self.agent, surface),
        )
        _safe_setattr(self.agent, "_pre_vision_provider", None)
        begin_provider_turn = getattr(self.agent, "begin_provider_turn", None)
        if callable(begin_provider_turn):
            _safe_call(begin_provider_turn)

        return GatewayTurnState(
            turn_id=turn_id,
            started_at=started,
            session_id=session_id,
            runtime_surface=surface,
            route_source_raw=route_source,
            model_slot=model_slot,
            instance_id=instance_id,
            secondary=secondary,
            task_board=None,
            saved_active_board=saved_active_board,
            resume_intent=resume_intent,
            resume_board=resume_board,
            manual_resume_board=manual_resume_board,
            turn_intent=turn_intent,
            desktop_action_admission=admitted_desktop_action,
            board_objective=board_objective,
            previous_route_source_raw=previous_route_source,
            previous_runtime_surface=previous_runtime_surface,
            previous_model_slot=previous_model_slot,
            previous_turn_intent=previous_turn_intent,
            previous_desktop_action_admission=previous_desktop_action_admission,
            previous_surface_session_identity=previous_surface_session_identity,
            user_input=str(user_input or ""),
        )

    # ── _plan_and_run ───────────────────────────────────────────────────
    def _plan_and_run(
        self,
        user_input: str,
        ts: GatewayTurnState,
        *,
        on_token: object = None,
        on_activity: object = None,
        cancel_event: object = None,
        on_assistant_text: object = None,
        on_board_event: object = None,
        on_action: object = None,
        on_operator_visual: object = None,
        on_operator_image: object = None,
    ) -> tuple[str, str]:
        """Core turn: lazy board → agent run → continuations."""
        result_text = ""
        status = "ok"
        session = getattr(self.agent, "session", None)
        turn_id = ts.turn_id
        session_id = ts.session_id
        surface = ts.runtime_surface
        instance_id = ts.instance_id
        route_source = ts.route_source_raw
        resume_intent = ts.resume_intent
        turn_intent = ts.turn_intent
        board_objective = ts.board_objective
        admitted_desktop_action = ts.desktop_action_admission

        with monitor_context(
            turn_id=turn_id,
            user_turn_id=turn_id,
            session_id=session_id,
            surface=surface,
            route_source=route_source,
            provider_slot=ts.model_slot,
            instance_id=instance_id,
        ):
            self.monitor.emit("turn_start", {
                "input": "[Mail turn omitted]" if _mail_turn(user_input) else str(user_input or "")[:300],
                "route_source": route_source,
                "surface": surface,
                "instance_id": instance_id,
                "messages": len(getattr(session, "messages", []) or []),
                "desktop_action_kind": str(
                    getattr(ts.desktop_action_admission, "kind", "") or ""
                ),
                "desktop_action_source": str(
                    getattr(ts.desktop_action_admission, "source", "") or ""
                ),
                "desktop_action_reason": str(
                    getattr(ts.desktop_action_admission, "reason", "") or ""
                ),
            })
            _safe_call(lambda: record_heartbeat(self.agent, gateway=self, surface=route_source, event="turn_start"))

            try:
                def _activate_board(board: TaskBoard, *, update: str = "created") -> TaskBoard:
                    if board is self.last_resumable_board:
                        self.last_resumable_board = None
                    if update == "resumed":
                        board.state = "active"
                    if bool(getattr(admitted_desktop_action, "permits_action", False)):
                        if getattr(admitted_desktop_action, "source", "") == "pending_task":
                            board.clear_pending_action()
                        else:
                            board.bind_pending_action({
                                "capability": getattr(admitted_desktop_action, "capability", ""),
                                "action": getattr(admitted_desktop_action, "action", ""),
                                "target": getattr(admitted_desktop_action, "target", ""),
                            })
                    ts.task_board = board
                    if not ts.secondary:
                        self.last_task_board = board
                    _safe_setattr(self.agent, "_active_task_board", board)
                    record_snapshot(board, update, source="gateway")
                    event = board_update_event(board, update=update)
                    if on_board_event:
                        on_board_event(event)
                    self.monitor.emit("taskboard", event)
                    return board

                manual_resume_board = ts.manual_resume_board
                if manual_resume_board is not None and ts.task_board is None:
                    response_kind = task_evidence.manual_gate_response_kind(user_input)
                    if response_kind == "declined":
                        manual_row = task_evidence.active_manual_gate(manual_resume_board)
                        if manual_row is not None:
                            manual_resume_board.append_evidence(
                                str(getattr(manual_row, "id", "") or ""),
                                task_evidence.MANUAL_DECLINE_EVIDENCE,
                            )
                            manual_resume_board.block(
                                str(getattr(manual_row, "id", "") or ""),
                                task_evidence.MANUAL_DECLINE_EVIDENCE,
                            )
                    _activate_board(
                        manual_resume_board,
                        update="declined" if response_kind == "declined" else "resumed",
                    )
                    if response_kind == "declined":
                        return "Declined. No approved action was performed.", status

                # Explicit resume adopts the saved board before provider selection;
                # model plan revisions reconnect it through the lazy callback below.
                resume_board = ts.resume_board
                if resume_board is not None and ts.task_board is None:
                    if (
                        not str(getattr(resume_board, "objective", "") or "").strip()
                        and str(board_objective or "").strip()
                        and not looks_like_interrupted_resume_request(board_objective)
                    ):
                        # Repair older/model-owned boards that were created empty
                        # without carrying their user objective.  The resumed board
                        # must remain the exact task anchor across interruption,
                        # handoff, and restart.
                        resume_board.objective = str(board_objective).strip()[:8_000]
                    _activate_board(resume_board, update="resumed")

                extension_board_driven = bool(local_extensions.board_rows(user_input))
                if (
                    (turn_intent.board_policy == BOARD_PROCEDURE or extension_board_driven)
                    and not resume_intent
                    and ts.task_board is None
                    and _should_create_board(
                        user_input,
                        agent=self.agent,
                        route_source=route_source,
                        tool_name="procedure_seed",
                        resume_intent=False,
                        turn_intent=turn_intent,
                    )
                ):
                    _activate_board(
                        _new_gateway_board(
                            turn_id,
                            session_id,
                            board_objective,
                            rows=None,
                            model_owned=False,
                            procedure_name=turn_intent.procedure_name,
                        )
                    )

                # Runtime-aware lazy board creation.
                def _lazy_create_board(tool_name: str = "", arguments: dict | None = None):
                    # A pre-turn handoff can change the session before its first
                    # tool. Boards belong to that live session, not the turn's
                    # original telemetry identity.
                    session_id = str(getattr(getattr(self.agent, "session", None), "session_id", "") or ts.session_id)
                    if ts.task_board:
                        return ts.task_board
                    if not _should_create_board(
                        user_input,
                        agent=self.agent,
                        route_source=route_source,
                        tool_name=tool_name,
                        arguments=arguments,
                        resume_intent=resume_intent,
                        turn_intent=turn_intent,
                    ):
                        return None

                    if tool_name == "set_plan" and (arguments or {}).get("mode") == "revise" and not ts.secondary:
                        board = _first_open_board(self.previous_task_board, self.last_resumable_board)
                        if board is not None and board.session_id == session_id and board.plan_owner in {"model", "procedure"}:
                            return _activate_board(board, update="resumed")
                    model_owned = _callable_bool(getattr(self.agent, "_model_owned_taskboard_enabled", lambda: False))
                    seed_procedure = (
                        turn_intent.board_policy == BOARD_PROCEDURE
                        and not local_extensions.is_active(user_input)
                    )
                    board = _new_gateway_board(
                        turn_id, session_id, board_objective,
                        rows=None,
                        model_owned=model_owned and not seed_procedure,
                        procedure_name=(turn_intent.procedure_name if seed_procedure else ""),
                    )
                    return _activate_board(board)

                kwargs = _agent_run_kwargs(
                    self.agent,
                    task_board=ts.task_board,
                    on_token=on_token,
                    on_activity=on_activity,
                    on_first_tool=(
                        None
                        if local_extensions.should_skip_task_board(user_input)
                        else _lazy_create_board
                    ),
                    cancel_event=cancel_event,
                    on_assistant_text=on_assistant_text,
                    on_board_event=on_board_event,
                    on_action=on_action,
                    on_operator_visual=on_operator_visual,
                    on_operator_image=on_operator_image,
                )

                result_text = self.agent.run_turn(
                    user_input,
                    monitor=self.monitor,
                    **kwargs,
                )
                if str(result_text or "").startswith("[ABORTED]"):
                    # Cancellation is a real terminal outcome, not a successful
                    # turn with unfinished rows.  Do not run continuation hooks or
                    # report `turn_end: ok` after the operator stopped the work.
                    return result_text, "aborted"
                if assistant_result_is_incomplete(result_text):
                    return result_text, "error"
                result_text = _continue_local_extension_after_runtime_boundary(
                    self.agent,
                    user_input,
                    result_text,
                    monitor=self.monitor,
                    callbacks=kwargs,
                    cancel_event=cancel_event,
                    on_activity=on_activity,
                )
                if assistant_result_is_incomplete(result_text):
                    return result_text, "error"
                result_text = _block_open_extension_board_at_turn_end(
                    self.agent,
                    user_input,
                    result_text,
                    ts.task_board,
                    monitor=self.monitor,
                    route_source=route_source,
                )
                if assistant_result_is_incomplete(result_text):
                    status = "error"

            except Exception as exc:
                status = "error"
                self.monitor.emit("turn_error", {
                    "error_type": type(exc).__name__,
                    "error": "[Mail turn error omitted]" if _mail_turn(user_input) else str(exc)[:300],
                })
                if ts.task_board is not None and not _mail_turn(user_input):
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
            return result_text, status

    # ── _finalize_turn ──────────────────────────────────────────────────
    def _finalize_turn(
        self,
        ts: GatewayTurnState,
        result_text: str,
        status: str,
        route_source: str,
        *,
        on_board_event: object = None,
    ) -> str:
        """Finally-block cleanup: provider restore, terminal event, heartbeat, board restore."""
        restore = getattr(self.agent, "restore_turn_provider", None)
        if callable(restore):
            _safe_call(lambda: restore(
                monitor=self.monitor,
                turn_id=ts.turn_id,
                session_id=ts.session_id,
                surface=ts.runtime_surface,
            ))
        else:
            restore_vision = getattr(self.agent, "restore_vision_provider", None)
            if callable(restore_vision):
                _safe_call(restore_vision)

        elapsed_ms = int((time.time() - ts.started_at) * 1000)
        if ts.runtime_surface in {"mo_desktop", "companion"}:
            from .session.session import PRESENTATION_KEY

            session = getattr(self.agent, "session", None)
            messages = getattr(session, "messages", [])
            if (
                messages and messages[-1].get("role") == "assistant"
                and str(messages[-1].get("content") or "") == str(result_text or "")
                and str(getattr(session, "session_id", "")) == ts.session_id
            ):
                messages[-1][PRESENTATION_KEY] = {
                    "turn_id": ts.turn_id,
                    "session_id": ts.session_id,
                    "instance_id": ts.instance_id,
                    "started_at": ts.started_at,
                    "surface": ts.runtime_surface,
                }
        turn_board = ts.task_board
        if turn_board is not None:
            event, state = terminal_board_event(turn_board, status)
            record_terminal_snapshot(turn_board, event, source="gateway", state=state)
            if not turn_board.tasks:
                clear_current_board_if_empty()
            terminal_update = board_update_event(turn_board, update=event)
            if on_board_event:
                _safe_call(lambda: on_board_event(terminal_update))
            self.monitor.emit("taskboard", {
                **terminal_update,
                "surface": ts.runtime_surface,
                "route_source": ts.route_source_raw,
                "provider_slot": ts.model_slot,
                "instance_id": ts.instance_id,
            })

        self.monitor.emit("turn_end", {
            "status": status,
            "duration_ms": elapsed_ms,
            "result_chars": len(str(result_text or "")),
            "has_task_board": turn_board is not None,
            # _finalize_turn runs after monitor_context exits. Preserve the
            # same correlation keys explicitly so trace readers can join the
            # completed event to its surface/session/turn.
            "turn_id": ts.turn_id,
            "user_turn_id": ts.turn_id,
            "session_id": ts.session_id,
            "surface": ts.runtime_surface,
            "route_source": ts.route_source_raw,
            "provider_slot": ts.model_slot,
            "instance_id": ts.instance_id,
        })
        _safe_call(lambda: record_heartbeat(self.agent, gateway=self, surface=route_source, event="turn_end",
                         extra={"status": status, "duration_ms": elapsed_ms}))
        if str(ts.user_input or "").strip() and not _mail_turn(ts.user_input):
            def _publish_surface_handoff() -> object | None:
                from .state.surface_handoff import publish_completed_turn
                return publish_completed_turn(
                    self.agent,
                    user_input=str(ts.user_input or ""),
                    final_text=result_text,
                    route_source=route_source,
                    status=status,
                )
            _safe_call(_publish_surface_handoff)
        _safe_setattr(self.agent, "_current_route_source", ts.previous_route_source_raw)
        _safe_setattr(self.agent, "_current_runtime_surface", ts.previous_runtime_surface)
        _safe_setattr(self.agent, "_current_model_slot", ts.previous_model_slot)
        _safe_setattr(self.agent, "_active_turn_intent", ts.previous_turn_intent)
        _safe_setattr(
            self.agent,
            "_current_desktop_action_admission",
            ts.previous_desktop_action_admission,
        )
        _safe_setattr(
            self.agent,
            "_active_surface_session_identity",
            ts.previous_surface_session_identity,
        )
        if ts.secondary:
            _safe_setattr(self.agent, "_active_task_board", ts.saved_active_board)

        return result_text


def _surface_session_identity(agent: object, surface: str) -> dict[str, object]:
    """Capture the admitted turn's surface identity for observer threads.

    ``surface_session_scope`` is intentionally thread-local, while Dashboard and
    status readers run on other threads. Gateway already serializes turns, so it
    is the single safe lifecycle owner for publishing and restoring this bounded
    observation-only identity.
    """
    state = getattr(agent, "_thread_state", None)
    slot = str(
        getattr(state, "surface_session_slot", "") if state is not None else ""
    ).strip()
    if not slot:
        slot = str(getattr(getattr(agent, "_sessions", None), "current_name", "") or surface)
    session = getattr(agent, "session", None)
    return {
        "slot": slot,
        "session_id": str(getattr(session, "session_id", "") or ""),
        "surface": str(surface or ""),
        "portable": bool(getattr(session, "_portable_conversation_id", "")),
        "session": session,
    }


def _continue_after_runtime_boundary(
    agent: object,
    result_text: str,
    *,
    monitor: BackendMonitor,
    callbacks: dict[str, object],
    cancel_event: object = None,
    on_activity: object = None,
    namespace: str,
    config_key: str,
    default_max: int,
    guard_fn: object,
    prompt_fn: object,
    activity_msg: str,
    event_kind_prefix: str,
) -> str:
    """Resume an explicitly admitted extension after its declared boundary."""
    if not _callable_bool(guard_fn):
        return result_text

    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    max_continuations = _safe_positive_int(agent_cfg.get(config_key), default_max)
    continuation_count = 0
    current = str(result_text or "")

    while _runtime_boundary_needs_continuation(current, namespace):
        # A continuation can close the board that admitted it.  Re-check the
        # live guard before starting another one; otherwise a closed board is
        # carried into a fresh turn where every tool is guaranteed to block.
        if not _callable_bool(guard_fn):
            return current
        if continuation_count >= max_continuations:
            monitor.emit("session_event", {
                "kind": f"{event_kind_prefix}_auto_continuation_limit",
                "continuations": continuation_count,
                "result_preview": current[:300],
            })
            return current
        if getattr(cancel_event, "is_set", lambda: False)():
            return "[ABORTED] Current turn stopped."

        continuation_count += 1
        if callable(on_activity):
            on_activity(activity_msg)
        monitor.emit("session_event", {
            "kind": f"{event_kind_prefix}_auto_continuation",
            "continuation": continuation_count,
            "boundary_preview": current[:300],
        })
        continuation_callbacks = dict(callbacks)
        live_board = getattr(agent, "_active_task_board", None)
        if live_board is not None:
            continuation_callbacks["task_board"] = live_board
        current = str(agent.run_turn(
            prompt_fn(current, continuation_count),
            monitor=monitor,
            **continuation_callbacks,
        ) or "")

    return current


def _continue_local_extension_after_runtime_boundary(
    agent: object,
    user_input: str,
    result_text: str,
    *,
    monitor: BackendMonitor,
    callbacks: dict[str, object],
    cancel_event: object = None,
    on_activity: object = None,
) -> str:
    """Resume profile extension work after an extension-declared boundary."""
    policy = local_extensions.runtime_boundary_policy(user_input)
    if not policy:
        return result_text
    prompt_fn = policy.get("prompt")
    if not callable(prompt_fn):
        return result_text
    return _continue_after_runtime_boundary(
        agent,
        result_text,
        monitor=monitor,
        callbacks=callbacks,
        cancel_event=cancel_event,
        on_activity=on_activity,
        namespace=str(policy.get("namespace") or "local_extension"),
        config_key=str(policy.get("config_key") or "local_extension_auto_continuation_max_turns"),
        default_max=_safe_positive_int(policy.get("default_max"), 3),
        guard_fn=lambda: True,
        prompt_fn=prompt_fn,
        activity_msg=str(policy.get("activity_msg") or "MO: continuing managed work after runtime boundary..."),
        event_kind_prefix=str(policy.get("event_kind_prefix") or "local_extension"),
    )


# Extension-declared boundary descriptions that an admitted continuation can resume.
_EXTENSION_BOUNDARY_MARKERS = (
    "budget exhaustion",
    "tool budget",
    "tool rounds",
    "max tool",
    "request limit",
    "turn limit",
    "persistently blocked after budget",
    "continuation required in the next fresh turn",
    "no more tools allowed this turn",
    "current runtime instruction explicitly forbids further tool calls",
    "tool-use gate",
)


def _runtime_boundary_needs_continuation(result_text: str, namespace: str) -> bool:
    """True when ``result_text`` ended on a declared extension boundary.

    The namespace makes the boundary opt-in. Ordinary model prose never creates
    fresh provider turns automatically.
    """
    text = _terminal_marker_text(result_text)
    if not text:
        return False
    if text.startswith((f"[{namespace} continuation capsule]", f"[{namespace} continuation]")):
        return True
    if not text.startswith(f"[{namespace} blocked]"):
        return False
    return any(marker in text for marker in _EXTENSION_BOUNDARY_MARKERS)


def _terminal_marker_text(result_text: str) -> str:
    """Normalize harmless Markdown wrapping before runtime boundary markers."""
    text = str(result_text or "").lstrip()
    if not text:
        return ""
    text = re.sub(r"^(?:[-*_]{3,}\s*)+", "", text).lstrip()
    text = re.sub(r"^(?:#{1,6}\s*)+", "", text).lstrip()
    text = re.sub(r"^(?:[*_`~>\s]+)+", "", text).lstrip()
    return " ".join(text.lower().split())
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


def _should_create_board(
    user_input: str,
    *,
    agent: object = None,
    route_source: str = "",
    tool_name: str = "",
    arguments: dict | None = None,
    resume_intent: bool = False,
    for_hint: bool = False,
    turn_intent: TurnIntent | None = None,
) -> bool:
    """Unified board creation decision — single source of truth.

    All board-creation gating runs through this function.  Callers choose the
    mode: *for_hint=True* for the pre-turn TUI hint (``should_show_task_board``);
    *for_hint=False* for the runtime creation decision (``_lazy_create_board``).

    Ordinary model-plan taskboards are materialized only by set_plan; otherwise
    a preliminary read/search can create a visible empty board and overwrite
    current.json before MO has declared any plan rows. Procedure-shaped boards
    are allowed through so Gateway can seed their evidence-gated scaffold before
    the provider turn.
    """
    _ = agent
    text = str(user_input or "")

    # -- common pre-gates (every mode) ---------------------------------------
    if _mail_turn(text):
        return False
    if normalize_runtime_surface(route_source) in DESKTOP_SURFACES:
        return False
    if for_hint and text.startswith("/"):
        return False
    if is_research_method_question(text):
        return False
    if local_extensions.should_skip_task_board(text):
        return False
    extension_decision = local_extensions.should_show_task_board(text)
    if extension_decision is not None:
        return extension_decision
    extension_rows = local_extensions.board_rows(text)
    if extension_rows:
        return True
    extension_active = local_extensions.is_active(text)
    if resume_intent:
        return True
    try:
        from .agent.agent_utils import _is_mapthis
        if _is_mapthis(text):
            return True
    except Exception:
        traceback.print_exc()

    # Runtime callers already classified the profile-expanded operator request.
    # Reuse that admitted truth so a private shorthand cannot flash a generic
    # one-row board before the model replaces it with the real procedure.
    intent = turn_intent or classify_turn(text)

    # -- hint mode: stop here ------------------------------------------------
    if for_hint:
        return extension_active or intent.board_policy != BOARD_NONE

    # -- runtime mode: model-owned gate + tool signals -----------------------
    model_owned = False
    if agent is not None:
        model_owned = _callable_bool(getattr(agent, "_model_owned_taskboard_enabled", lambda: False))
    if model_owned:
        # An active extension with no rows supplies behavior/context, not a
        # controller-selected plan. MO may create its own plan with set_plan.
        if extension_active:
            return str(tool_name) == "set_plan"
        if intent.board_policy == BOARD_PROCEDURE:
            return True
        return str(tool_name) == "set_plan"
    if extension_active:
        return True
    if intent.board_policy in {BOARD_PROCEDURE, BOARD_MODEL_PLAN}:
        return True
    if str(tool_name) == "set_plan":
        return True
    return tool_is_runtime_work_signal(tool_name, arguments or {})


def _board_has_open_work(board: TaskBoard | None) -> bool:
    """Return True only for a concrete board with unresolved rows."""
    if board is None:
        return False
    try:
        return bool(getattr(board, "tasks", None)) and int(board.open_count()) > 0
    except Exception:
        return False


def _first_open_board(*boards: TaskBoard | None) -> TaskBoard | None:
    """Pick the nearest resumable board without reviving completed/empty state."""
    return next((board for board in boards if _board_has_open_work(board)), None)


def _board_matches_extension_rows(board: TaskBoard | None, rows: list[dict]) -> bool:
    """Match plan identity while ignoring runtime progress/evidence state.

    An extension may deliberately replace a failed plan with a repair-specific
    one.  Resuming the abandoned board in that case discards the new rows before
    the provider sees them.  Matching plans still resume with their accumulated
    evidence and completed rows intact.
    """
    if board is None or not rows:
        return False

    current = [
        (
            str(getattr(task, "id", "") or ""),
            str(getattr(task, "title", "") or "").strip(),
            str(getattr(task, "kind", "") or "").strip(),
            str(getattr(task, "completion_gate", "") or "").strip(),
            tuple(str(dep) for dep in (getattr(task, "depends_on", []) or [])),
        )
        for task in list(getattr(board, "tasks", []) or [])
    ]
    expected = [
        (
            str(row.get("id") or index),
            str(row.get("text") or row.get("title") or "").strip(),
            str(row.get("kind") or "").strip(),
            str(row.get("completion_gate") or "").strip(),
            tuple(str(dep) for dep in (row.get("depends_on") or [])),
        )
        for index, row in enumerate(rows, start=1)
        if isinstance(row, dict)
    ]
    return current == expected


def _board_objective_text(
    agent: object,
    user_input: str,
    *,
    resume_intent: bool = False,
    resume_board: TaskBoard | None = None,
) -> str:
    """Use the resumed work objective when the user explicitly resumes parked work."""
    if resume_intent:
        pending = getattr(agent, "_pending_interrupted_work", {})
        pending_prior = ""
        if isinstance(pending, dict):
            pending_prior = str(pending.get("user") or "").strip()
            if pending_prior and not looks_like_interrupted_resume_request(pending_prior):
                return pending_prior
        if resume_board is not None:
            prior = str(getattr(resume_board, "objective", "") or "").strip()
            if prior:
                return prior
        if pending_prior:
            return pending_prior
    return str(user_input or "")


def _has_pending_resume_intent(agent: object, user_input: str) -> bool:
    pending = getattr(agent, "_pending_interrupted_work", {})
    if not isinstance(pending, dict) or not str(pending.get("user") or "").strip():
        return False
    return looks_like_interrupted_resume_request(user_input)


def _pending_boardless_request_for_resume(agent: object, user_input: str) -> str:
    """Prefer the interrupted request over an unrelated old board on bare resume.

    An interrupted status/report or bounded lookup is still a pending response,
    but it is not authority to adopt whichever abandoned taskboard also happens
    to be present.  Reclassify that exact saved request through the normal owner;
    explicit taskboard/row wording still selects the board path.
    """
    if not looks_like_interrupted_resume_request(user_input):
        return ""
    current = str(user_input or "")
    if re.search(r"(?i)\b(?:task\s*board|open\s+board|row\s*#?\s*\d+)\b", current):
        return ""
    pending = getattr(agent, "_pending_interrupted_work", {})
    if not isinstance(pending, dict):
        return ""
    request = str(pending.get("user") or "").strip()
    prior = str(pending.get("prior_user") or "").strip()
    if request and looks_like_interrupted_resume_request(request) and prior:
        request = prior
    if not request:
        return ""
    pending_intent = classify_turn(request)
    return request if pending_intent.board_policy == BOARD_NONE else ""


def _manual_gate_resume_board(
    previous_board: TaskBoard | None,
    resumable_board: TaskBoard | None,
    user_input: str,
) -> TaskBoard | None:
    """Resume a waiting manual row only for a bounded user decision reply."""
    if not task_evidence.manual_gate_response_kind(user_input):
        return None
    for board in (previous_board, resumable_board):
        if task_evidence.active_manual_gate(board) is not None:
            return board
    return None


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
    """Map final turn status to the terminal taskboard ledger event/state.

    The turn is over when this runs, so the board must land on a TERMINAL state —
    it must never be recorded as lingering ``active``. When an ok turn ends with rows
    still open (the model answered in prose without walking every row, or a multi-phase
    per-turn plan was seeded for a conversational turn), that plan did not complete:
    record it ``abandoned`` — a truthful, resumable terminal — instead of ``active``,
    which falsely reads as live work and leaves ~half of all boards stuck open forever.
    Resume still picks these up: resume_last_board/_resume_from_current_json skip only
    completed and cancelled boards, and gateway resume-adopt reactivates abandoned work.
    Rows that all completed retain their verified board outcome even if a later
    response/transport step gives the turn a non-ok status.
    """
    if board.state == "cancelled":
        return "cancelled", "cancelled"
    if board.state == "paused":
        return "paused", "paused"
    if board.tasks and all(task.status == "completed" for task in board.tasks):
        return "completed", "completed"
    if status != "ok":
        return "abandoned", "abandoned"
    if not board.tasks:
        return "updated", "active"
    if any(task.status == "blocked" for task in board.tasks):
        return "blocked", "blocked"
    # ok turn, work rows left open, none blocked → plan not completed this turn.
    return "abandoned", "abandoned"


# The taskboard is the TERMINAL's truth. MO Desktop, Telegram and the headless service run their
# own isolated sessions and own no board rows, so an extension that rejects text with open rows must
# never see their turns — a file dropped on the desktop has nothing to do with the terminal's board.
# The gate used to be called without the surface at all, so an extension could not tell them apart.
_BOARD_BLOCK_SURFACES = frozenset({"user"})


def _block_open_extension_board_at_turn_end(
    agent: object,
    user_input: str,
    result_text: str,
    board: TaskBoard | None,
    *,
    monitor: BackendMonitor,
    route_source: str = "user",
) -> str:
    """Let a local extension reject terminal text with open task rows."""
    if route_source not in _BOARD_BLOCK_SURFACES:
        return result_text
    if not board or not board.tasks or board.open_count() <= 0:
        return result_text
    blocked_text = local_extensions.open_board_block_text(agent, user_input, result_text, board)
    if not blocked_text:
        return result_text
    monitor.emit("local_extension_open_taskboard_blocked", {
        "board_id": board.board_id,
        "open_count": board.open_count(),
    })
    return blocked_text


def record_terminal_snapshot(
    board: TaskBoard,
    event: str,
    *,
    source: str = "gateway",
    state: str | None = None,
) -> None:
    """Land and record terminal board state through Gateway's lifecycle.

    The live board and its persisted snapshot must agree so interfaces do not
    render a finished turn as still active. All terminal writes — whether from
    turn-end, goal-end, or external consumers — route through this owner.
    """
    terminal_state = str(state or "active").strip().lower()
    if terminal_state not in {"active", "completed", "blocked", "paused", "abandoned", "cancelled"}:
        terminal_state = "active"
    board.state = terminal_state
    board.updated_at = time.time()
    record_snapshot(board, event, source=source, state=terminal_state)


def _new_gateway_board(
    turn_id: str,
    session_id: str,
    user_input: str,
    *,
    title: str | None = None,
    rows: list[dict[str, object]] | None = None,
    model_owned: bool = False,
    procedure_name: str = "",
) -> TaskBoard:
    """Create board from explicit extension rows or MO/procedure fallback rows.

    When ``model_owned`` is set, normal work turns get an EMPTY board that MO
    populates with its own plan via ``set_plan``. Local extensions may supply
    explicit rows.
    """
    target = str(title or "").strip() or user_input[:80]
    extension_rows = local_extensions.board_rows(user_input)
    if extension_rows:
        # An extension that supplies rows owns the board.
        rows = extension_rows
        plan_owner = "extension"
    elif model_owned:
        # MO owns the board: start EMPTY and let MO populate it via set_plan.
        return TaskBoard(
            turn_id=turn_id,
            title=target,
            objective=str(user_input or "").strip()[:8_000],
            session_id=session_id,
            source="gateway",
            plan_owner="model",
        )
    elif not rows:
        # Seed the matching build/reasoning procedure instead of one generic row.
        rows = _work_procedure_rows(user_input, procedure_name=procedure_name)
        plan_owner = "procedure"
    else:
        plan_owner = "procedure"
    board = TaskBoard(
        turn_id=turn_id,
        session_id=session_id,
        source="gateway",
        plan_owner=plan_owner,
    )
    if rows:
        board.set_rows(f"{target}", rows, objective=str(user_input or "").strip()[:8_000])
    else:
        # No matching procedure: single-row fallback. Never leave the board empty here — the
        # caller already decided a board should exist.
        board.set_rows(
            f"{target}",
            [{"id": "1", "text": f"Work on {target}", "status": "active", "kind": "edit", "completion_gate": "tool", "depends_on": []}],
            objective=str(user_input or "").strip()[:8_000],
        )
    return board


def _work_procedure_rows(
    user_input: str,
    *,
    procedure_name: str = "",
) -> list[dict[str, object]] | None:
    """Seed rows from the matching build/reasoning work procedure, if any.

    The exact request remains in ``TaskBoard.objective``; the visible rows contain
    only the reusable procedure steps. Fail-open: any error yields None so board
    creation always falls back to the single-row default and never breaks a turn.
    """
    try:
        from .context.work_patterns import procedure_for
        from .tasking.procedure import procedure_rows, work_procedure_for

        procedure = work_procedure_for(procedure_name) if procedure_name else procedure_for(user_input)
        return procedure_rows(procedure) if procedure else None
    except Exception:
        return None


def _surface_handoff_scope(agent: object, route_source: str) -> tuple[object | None, object, str]:
    """Prepare one provider-only handoff for shared private Gateway surfaces."""
    try:
        from .state.continuity_events import validate_continuity_source

        surface = validate_continuity_source(route_source)
    except ValueError:
        return None, nullcontext(), ""
    state = getattr(agent, "_thread_state", None)
    # Telegram group turns enter this scope with publication suppressed. The
    # same privacy boundary must also prevent private continuity injection.
    if state is not None and getattr(state, "surface_handoff_suppressed", False):
        return None, nullcontext(), ""
    slot = str(getattr(state, "surface_session_slot", "") if state is not None else "").strip()
    if not slot:
        slot = str(getattr(getattr(agent, "_sessions", None), "current_name", "") or surface)
    try:
        from .state.surface_handoff import pending_handoff, render_handoff_context

        record = pending_handoff(agent, target_surface=surface, target_key=slot)
        factory = getattr(agent, "continuity_handoff_scope", None)
        if record is not None and callable(factory):
            context = render_handoff_context(record)
            scope = factory(context, record=record)
            return record, scope, slot
    except Exception:
        traceback.print_exc()
    return None, nullcontext(), slot


def _mark_surface_handoff(agent: object, record: object, route_source: str, target_key: str) -> None:
    try:
        from .state.continuity_events import validate_continuity_source
        from .state.surface_handoff import mark_handoff_consumed

        surface = validate_continuity_source(route_source)
        mark_handoff_consumed(
            agent,
            record,
            target_surface=surface,
            target_key=target_key,
        )
    except ValueError:
        return
    except Exception:
        traceback.print_exc()
