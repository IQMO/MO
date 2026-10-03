"""MO Desktop adapter for trusted Studio send routes."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.design.board import load_board_working
from core.design.context import DESIGN_REQUEST_KINDS, build_design_prompt, build_handoff_prompt
from core.design.service import load_design, load_design_revision
from core.design.session import (
    complete_design_request,
    complete_design_response,
    fail_design_request,
    load_design_session,
    update_design_activity,
)
from core.design.terminal_handoff import queue_terminal_turn
from core.provider.model_catalog import activate_model_selection, runtime_model_selection
from core.state.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_TURN,
    find_attachment,
)
from core.state.paths import MO_DESIGN_HANDOFF_DIR, resolve_state_path
from core.tooling.sandbox import redact_sensitive_text
from core.tooling.tool_constants import DESIGN_ONLY_LANE

from .routing import (
    COMMAND_DIR,
    exact_terminal_target,
    launch_background_goal,
    launch_prompt_terminal,
    validate_command,
)
from .delivery import brief_signature, delivery_activity

BROKER_POLL_SECONDS = 0.25


def _completion_title(intent: str, state: str) -> str:
    completed = state == "completed"
    if intent == "design":
        return "Design updated" if completed else "Design request needs attention"
    return "Design handoff complete" if completed else "Design handoff needs attention"


@dataclass(frozen=True)
class _PendingCompletion:
    intent: str
    path: Path
    design_id: str
    title: str
    command_id: str
    base_revision: int
    base_visual_signature: tuple[str, str, str, bool, str]
    base_brief_signature: tuple[Any, ...]
    request_kind: str
    started_at: float


def _visual_qa_unavailable(result: str) -> str:
    """Read the explicit truthful escape hatch for an unavailable preview."""
    marker = "visual qa: unavailable:"
    for raw in str(result or "").splitlines():
        line = " ".join(raw.strip().strip("-• ").split())
        lowered = line.casefold()
        offset = lowered.find(marker)
        if offset >= 0:
            return line[offset + len(marker):].strip()[:4000]
    return ""


def _visual_qa_observed(evidence: dict[str, Any] | None) -> bool:
    """Require actual discovery plus an observation event, not activity prose."""
    if not isinstance(evidence, dict):
        return False
    return bool(evidence.get("targets") and evidence.get("observation"))


class DesignCommandBroker:
    """Consume authenticated local Studio commands in the resident Desktop process."""

    def __init__(self, companion: Any) -> None:
        self.companion = companion
        self.secret = secrets.token_hex(32)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._background_completions: dict[str, _PendingCompletion] = {}
        self._design_cancellations: dict[str, tuple[Path, threading.Event]] = {}
        self._resource_started_at = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._resource_started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="mo-design-broker", daemon=True)
        self._thread.start()
        self._emit_resource("start")

    def stop(self) -> None:
        self._stop.set()
        for _path, event in list(self._design_cancellations.values()):
            event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._emit_resource("stop", "desktop_shutdown")
        self._thread = None

    def _run(self) -> None:
        directory = Path(resolve_state_path(COMMAND_DIR, self.companion._config()))
        directory.mkdir(parents=True, exist_ok=True)
        self._emit_resource("ready")
        while not self._stop.wait(BROKER_POLL_SECONDS):
            for path in sorted(directory.glob("*.json"), key=lambda item: item.name)[:12]:
                self._consume(path)
            self._consume_background_completions()

    def _emit_resource(self, transition: str, reason: str = "") -> None:
        from core.runtime.resource_events import emit_component_resource_event

        emit_component_resource_event(
            "desktop_design_broker",
            transition,
            owned_thread_count=int(bool(self._thread and self._thread.is_alive())),
            elapsed_seconds=(
                time.monotonic() - self._resource_started_at
                if self._resource_started_at else 0.0
            ),
            reason=reason,
        )

    def _consume(self, path: Path) -> None:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            payload = validate_command(row, self.secret)
        except (OSError, json.JSONDecodeError):
            payload = None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if payload is None:
            return
        try:
            self._dispatch(payload)
        except Exception as exc:
            if str(payload.get("intent") or "handoff").strip().lower() in {"handoff", "open"}:
                title = "Design could not open" if payload.get("intent") == "open" else "Design handoff failed"
                self._notice(title, str(exc)[:120], event="notify")
            else:
                try:
                    target = Path(str(payload.get("path") or ""))
                    document = load_design(target)
                    detail = redact_sensitive_text(str(exc))[:500]
                    if payload.get("intent") == "cancel":
                        update_design_activity(
                            target, design_id=document.meta.id, title=document.meta.title,
                            command_id=str(payload.get("request_id") or ""), phase="stop_failed",
                            label="Stop failed", detail=detail, message="Could not stop this request: " + detail,
                        )
                    else:
                        fail_design_request(
                            target, design_id=document.meta.id, title=document.meta.title,
                            command_id=str(payload.get("command_id") or ""), message="Design request failed: " + detail,
                        )
                except (OSError, ValueError):
                    pass

    def _dispatch(self, payload: dict[str, Any]) -> None:
        intent = str(payload.get("intent") or "handoff").strip().lower()
        if intent == "cancel":
            self._cancel_design(payload)
            return
        if intent == "open":
            path = Path(str(payload.get("path") or "")).expanduser().resolve(strict=False)
            load_design(path)
            self.companion.open_design_studio(str(path))
            return
        if intent not in {"design", "handoff"}:
            raise ValueError("unknown MO Design intent")
        route = str(payload.get("route") or "").strip().lower()
        if intent == "design" and route != "studio":
            raise ValueError("MO Design chat must stay inside Studio")
        if intent == "handoff" and route not in {"current", "background", "terminal"}:
            raise ValueError("unknown MO Design handoff route")
        request_kind = str(payload.get("request_kind") or "auto").strip().lower()
        if intent == "design" and request_kind not in DESIGN_REQUEST_KINDS:
            raise ValueError("unknown MO Design request kind")
        path = Path(str(payload.get("path") or "")).expanduser().resolve(strict=False)
        head_document = load_design(path)
        selected_revision = int(payload.get("revision") or head_document.meta.revision)
        source_path, document = load_design_revision(
            head_document.meta.id,
            selected_revision,
            config=self.companion._config(),
        )
        command_id = str(payload.get("command_id") or "")[:128]
        completion = _PendingCompletion(
            intent=intent,
            path=path,
            design_id=document.meta.id,
            title=document.meta.title,
            command_id=command_id,
            base_revision=head_document.meta.revision,
            base_visual_signature=_visual_signature(document),
            base_brief_signature=brief_signature(document),
            request_kind=request_kind,
            started_at=time.time(),
        )
        feedback = str(payload.get("feedback") or "").strip()
        attachments: list[dict[str, Any]] = []
        if intent == "design":
            try:
                attachments = _resolve_design_attachments(
                    self.companion._config(),
                    payload.get("attachment_ids"),
                )
            except ValueError as exc:
                self._fail_design_request(completion, str(exc))
                raise
        profile = getattr(self.companion._agent, "profile", None)
        conversation = load_design_session(
            path,
            design_id=document.meta.id,
            title=document.meta.title,
        )
        prompt_conversation = (
            conversation if selected_revision == head_document.meta.revision else None
        )
        if intent == "design":
            selected_model = runtime_model_selection(self.companion._agent)
            if selected_model is not None:
                try:
                    activate_model_selection(
                        self.companion._agent,
                        selected_model,
                        surface="mo_design",
                        reason="current saved operator model preference",
                    )
                except ValueError:
                    self._fail_design_request(
                        completion,
                        "The current /model selection is no longer available. Choose another model in Terminal and retry.",
                    )
                    raise
            prompt = build_design_prompt(
                document,
                path=source_path,
                feedback=feedback[:4000],
                request_kind=request_kind,
                profile=profile,
                conversation=prompt_conversation,
                attachments=attachments,
            )
        else:
            prompt = build_handoff_prompt(
                document,
                path=source_path,
                profile=profile,
                conversation=prompt_conversation,
            )
        project_root = str(document.handoff.project_root or "").strip()
        if route == "studio":
            from core.worker import ensure_worker_runtime

            runtime = ensure_worker_runtime(self.companion._agent)
            visual_evidence: dict[str, Any] = {
                "targets": False,
                "observation": False,
            }
            fallback_events: list[str] = []
            reported_errors: set[str] = set()
            cancel_event = threading.Event()
            self._design_cancellations[command_id] = (path, cancel_event)

            def activity(raw: str) -> None:
                if cancel_event.is_set():
                    return
                phase, label, detail = _design_activity(raw)
                if phase == "model_fallback":
                    if detail not in fallback_events:
                        fallback_events.append(detail)
                        del fallback_events[:-3]
                elif fallback_events:
                    detail = f"{detail} {fallback_events[-1]}"[:600]
                try:
                    update_design_activity(
                        completion.path,
                        design_id=completion.design_id,
                        title=completion.title,
                        command_id=completion.command_id,
                        phase=phase,
                        label=label,
                        detail=detail,
                    )
                except (OSError, ValueError):
                    pass

            def action(payload: dict[str, Any]) -> None:
                if not isinstance(payload, dict) or cancel_event.is_set():
                    return
                tool = str(payload.get("tool") or "")
                if not payload.get("successful"):
                    detail = redact_sensitive_text(str(payload.get("detail") or "The operation did not succeed."))[:240]
                    if detail not in reported_errors and len(reported_errors) < 3:
                        reported_errors.add(detail)
                        update_design_activity(
                            path, design_id=completion.design_id, title=completion.title,
                            command_id=command_id, phase="tool_error", label="A tool needs attention",
                            detail=detail, message=f"MO encountered a problem while working: {detail}",
                        )
                    return
                if tool == "computer_targets":
                    visual_evidence["targets"] = True
                elif tool == "computer_observe":
                    events = payload.get("computer_events")
                    if isinstance(events, (list, tuple)) and any(
                        isinstance(event, dict)
                        and event.get("event") == "observation"
                        and event.get("status") == "observed"
                        for event in events
                    ):
                        visual_evidence["observation"] = True

            def finished(record: Any, result: str) -> None:
                try:
                    self._finish_completion(
                        completion,
                        "cancelled" if cancel_event.is_set() else str(getattr(record, "state", "") or ""),
                        result=result,
                        notify=False,
                        visual_evidence=visual_evidence,
                        fallback_events=tuple(fallback_events),
                    )
                finally:
                    self._design_cancellations.pop(command_id, None)

            try:
                roots = [str(path.parent)]
                if project_root:
                    roots.insert(0, project_root)
                roots.extend(str(row["saved_path"]) for row in attachments)
                record = runtime.start(
                    prompt,
                    source="mo_design",
                    on_finish=finished,
                    on_activity=activity,
                    on_action=action,
                    lane=DESIGN_ONLY_LANE,
                    project_cwd=project_root or str(path.parent),
                    allowed_roots=roots,
                    notify_completion=False,
                    provider_surface="mo_design",
                    cancel_event=cancel_event,
                )
            except Exception:
                self._design_cancellations.pop(command_id, None)
                self._fail_design_request(
                    completion,
                    "MO Design could not start this request. Retry from Design chat.",
                )
                raise
            if str(getattr(record, "state", "")) == "blocked":
                self._design_cancellations.pop(command_id, None)
                self._fail_design_request(
                    completion,
                    "MO Design could not start this request. Retry from Design chat.",
                )
                raise RuntimeError(str(getattr(record, "note", "") or "Design request is unavailable"))
            return
        if route == "current":
            target = exact_terminal_target(
                payload.get("terminal_target"),
                self.companion._config(),
            )
            if target is None:
                raise RuntimeError("The connected MO terminal is no longer available")
            prompt += (
                "\nDelivery mode: continue this exact existing MO terminal conversation "
                "as a normal interactive turn. Do not enter or simulate /goal."
            )
            queue_terminal_turn(prompt, target, config=self.companion._config())
            return
        if route == "background":
            launched = launch_background_goal(
                prompt,
                project_root=project_root,
                config=self.companion._config(),
            )
            prompt_id = str(launched.get("prompt_id") or "")
            if len(prompt_id) != 24 or not all(char in "0123456789abcdef" for char in prompt_id):
                raise RuntimeError("MO Design background launch did not return a valid completion id")
            self._background_completions[prompt_id] = completion
            return
        if route == "terminal":
            target = exact_terminal_target(
                payload.get("terminal_target"),
                self.companion._config(),
            )
            launch_prompt_terminal(
                prompt,
                config=self.companion._config(),
                project_root=project_root,
                fallback_workspace=str((target or {}).get("cwd") or ""),
            )
            return
        raise ValueError("unknown MO Design route")

    def _consume_background_completions(self) -> None:
        """Consume bounded headless-goal markers on the existing broker thread."""
        if not self._background_completions:
            return
        directory = Path(resolve_state_path(
            f"{MO_DESIGN_HANDOFF_DIR}/completions",
            self.companion._config(),
        ))
        now = time.time()
        for prompt_id, completion in tuple(self._background_completions.items()):
            marker = directory / f"{prompt_id}.json"
            if not marker.is_file():
                if now - completion.started_at > 24 * 60 * 60:
                    self._background_completions.pop(prompt_id, None)
                continue
            try:
                row = json.loads(marker.read_text(encoding="utf-8"))
                state = str(row.get("state") or "needs_attention") if isinstance(row, dict) else "needs_attention"
            except (OSError, json.JSONDecodeError):
                state = "needs_attention"
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            self._background_completions.pop(prompt_id, None)
            self._finish_completion(completion, state, notify=True)

    def _cancel_design(self, payload: dict[str, Any]) -> None:
        path = Path(str(payload.get("path") or "")).expanduser().resolve(strict=False)
        document = load_design(path)
        command_id = str(payload.get("request_id") or "")
        session = load_design_session(path, design_id=document.meta.id, title=document.meta.title)
        pending = session.get("pending") or {}
        if not command_id or pending.get("command_id") != command_id:
            return
        active = self._design_cancellations.get(command_id)
        if active is not None:
            if active[0] != path:
                return
            active[1].set()
            update_design_activity(
                path, design_id=document.meta.id, title=document.meta.title, command_id=command_id,
                phase="stopping", label="Stopping…", detail="Waiting for the current operation to stop. Saved work is retained.",
            )
            return
        # The broker consumes commands sequentially: remove a queued request before
        # it starts, or resolve an orphan whose Desktop owner has already exited.
        if re.fullmatch(r"[a-f0-9]{32}", command_id):
            queued = Path(resolve_state_path(COMMAND_DIR, self.companion._config())) / f"{command_id}.json"
            queued.unlink(missing_ok=True)
        complete_design_response(
            path, design_id=document.meta.id, title=document.meta.title, command_id=command_id,
            message="Stopped. Your saved Design and manual edits were retained.", stopped=True,
        )

    def _finish_completion(
        self,
        completion: _PendingCompletion,
        state: str,
        *,
        result: str = "",
        notify: bool,
        visual_evidence: dict[str, Any] | None = None,
        fallback_events: tuple[str, ...] = (),
    ) -> None:
        final_state = str(state or "needs_attention")
        if completion.intent == "design":
            try:
                document = load_design(completion.path)
                session = load_design_session(
                    completion.path,
                    design_id=completion.design_id,
                    title=completion.title,
                )
                pending = session.get("pending") if isinstance(session.get("pending"), dict) else None
                if (
                    pending
                    and completion.command_id
                    and str(pending.get("command_id") or "") != completion.command_id
                ):
                    return
                if final_state == "cancelled":
                    complete_design_response(
                        completion.path, design_id=completion.design_id, title=document.meta.title,
                        command_id=completion.command_id, stopped=True,
                        message="Stopped. Your saved Design and manual edits were retained; unfinished changes were not accepted.",
                    )
                    return
                base_revision = (
                    int(pending.get("base_revision") or completion.base_revision)
                    if pending and str(pending.get("command_id") or "") == completion.command_id
                    else completion.base_revision
                )
                revision_advanced = document.meta.revision > base_revision
                visual_changed = (
                    revision_advanced
                    and _visual_signature(document) != completion.base_visual_signature
                )
                qa_unavailable = _visual_qa_unavailable(result)
                visual_updated = final_state == "completed" and visual_changed and (
                    _visual_qa_observed(visual_evidence) or bool(qa_unavailable)
                )
                brief_result = _design_result_message(result, "Design brief updated:")
                brief_updated = (
                    final_state == "completed"
                    and not visual_changed
                    and bool(brief_result)
                    and document.meta.revision > base_revision
                    and brief_signature(document) != completion.base_brief_signature
                )
                working = load_board_working(
                    completion.path,
                    design_id=completion.design_id,
                    design_revision=document.meta.revision,
                    committed=document.board,
                )
                board_draft = (
                    _design_result_message(result, "Board draft:")
                    if final_state == "completed" and working.get("draft") else ""
                )
                if visual_updated:
                    delivered = _design_result_message(result, "Design delivered:")
                    activity_label, activity_detail = delivery_activity(
                        document,
                        result_message=delivered,
                    )
                    complete_design_request(
                        completion.path,
                        design_id=completion.design_id,
                        title=document.meta.title,
                        revision=document.meta.revision,
                        command_id=completion.command_id,
                        message=(
                            f"r{document.meta.revision} · {delivered}"
                            if delivered else ""
                        ),
                        activity_label=activity_label,
                        activity_detail=activity_detail,
                    )
                    final_state = "completed"
                elif brief_updated:
                    activity_label, activity_detail = delivery_activity(
                        document,
                        result_message=brief_result,
                        brief_only=True,
                    )
                    complete_design_request(
                        completion.path,
                        design_id=completion.design_id,
                        title=document.meta.title,
                        revision=document.meta.revision,
                        command_id=completion.command_id,
                        message=f"r{document.meta.revision} · {brief_result}",
                        activity_label=activity_label,
                        activity_detail=activity_detail,
                    )
                    final_state = "completed"
                elif board_draft:
                    complete_design_response(
                        completion.path,
                        design_id=completion.design_id,
                        title=document.meta.title,
                        command_id=completion.command_id,
                        message="Board draft ready for review · " + board_draft,
                    )
                    final_state = "completed"
                elif final_state == "completed" and (
                    response := _design_result_message(result, "Design response:")
                ):
                    complete_design_response(
                        completion.path,
                        design_id=completion.design_id,
                        title=document.meta.title,
                        command_id=completion.command_id,
                        message=response,
                    )
                    final_state = "completed"
                else:
                    final_state = "needs_attention"
                    worker_failed = str(state or "needs_attention") != "completed"
                    if worker_failed:
                        reason = _design_failure_detail(result) or "The Design worker stopped before it produced an accepted result."
                    elif visual_changed:
                        reason = (
                            "MO changed the Design but did not produce a real visual QA observation. "
                            "Reopen the Design preview and retry, or explicitly report "
                            "`Visual QA: unavailable: <reason>`."
                        )
                    else:
                        reason = (
                            "MO finished without a usable Design answer, Board draft, complete visual update, "
                            "or explicit implementation-brief update."
                        )
                    if fallback_events:
                        reason += " Model recovery: " + " ".join(fallback_events[-3:])
                    preservation = _design_preservation_message(
                        session,
                        document.meta.revision,
                        revision_advanced=revision_advanced,
                        visual_changed=visual_changed,
                    )
                    self._fail_design_request(
                        completion,
                        f"{reason} {preservation} Retry this request in Design.",
                    )
            except (OSError, ValueError):
                final_state = "needs_attention"
        if notify:
            self._notice(
                _completion_title(completion.intent, final_state),
                completion.title,
                event="notify_worker",
                design_path=completion.path,
            )

    def _fail_design_request(self, completion: _PendingCompletion, message: str) -> None:
        if completion.intent != "design":
            return
        try:
            fail_design_request(
                completion.path,
                design_id=completion.design_id,
                title=completion.title,
                command_id=completion.command_id,
                message=message,
            )
        except (OSError, ValueError):
            pass

    def _notice(self, title: str, detail: str, *, event: str, design_path: Path | None = None) -> None:
        visible_detail = f"{detail} · click MO to open" if design_path is not None else detail
        from mo_desktop.notify import Notice

        activate = (
            (lambda target=str(design_path): self.companion.open_design_studio(target))
            if design_path is not None else None
        )
        self.companion._emit_notice(Notice(
            f"mo-design:{time.time_ns()}", title, visible_detail, event, 5.0, activate,
        ))


def _visual_signature(document: Any) -> tuple[str, str, str, bool, str]:
    return (
        str(document.design.html or ""),
        str(document.design.css or ""),
        str(document.design.script or ""),
        bool(document.runtime.allow_scripts),
        json.dumps(document.design.edits, sort_keys=True),
    )


def _design_result_message(result: str, marker: str) -> str:
    """Extract only the explicit Design chat contract from a worker result."""
    expected = marker.casefold()
    for raw in str(result or "").splitlines():
        line = raw.strip().strip("-• ")
        if line.casefold().startswith("result:"):
            line = line.split(":", 1)[1].strip()
        if line.casefold().startswith(expected):
            return line[len(marker):].strip()[:4000]
    return ""


def _design_activity(raw: str) -> tuple[str, str, str]:
    """Map internal worker activity to safe, truthful Studio phases."""
    clean = " ".join(str(raw or "").split())
    value = clean.casefold()
    fallback = re.search(
        r"fallback from ([^\s]+) to ([^\s]+) after ([a-z0-9_-]+)",
        clean,
        flags=re.IGNORECASE,
    )
    if fallback:
        failed, target, reason = fallback.groups()
        readable_reason = reason.replace("_", " ")
        return (
            "model_fallback",
            f"Model fallback · {target}"[:80],
            f"Primary {failed} failed ({readable_reason}); retrying with {target}."[:240],
        )
    if "computer_observe" in value:
        return "observing", "Checking pixels…", "Comparing the visible preview with the request and available reference evidence."
    if "computer_targets" in value:
        return "observing", "Finding visual target…", "Looking for the requested surface and the Design preview."
    if "mo_design" in value and "tooling" in value:
        return "design", "Working with the saved Design…", "Reading saved content or applying a requested Design change."
    if "gathering work context" in value or "context supplied" in value:
        return "context", "Preparing context…", "Loading the context needed for this request before contacting the model."
    if any(name in value for name in ("read_file", "grep", "glob", "search", "graph")) and "tooling" in value:
        return "inspecting", "Checking project evidence…", "Verifying source owners, shared theme, and existing behavior read-only."
    if "finalizing" in value or "evidence" in value:
        return "finalizing", "Checking result…", "Confirming the visual, brief, and evidence limits before delivery."
    if "waiting on model" in value or "thinking" in value:
        return "thinking", "Considering your request…", "Deciding whether to answer, suggest options, or change the visual."
    return "preparing", "Preparing request…", "Reading your message and the selected visual context."


def _resolve_design_attachments(
    config: dict[str, Any],
    attachment_ids: Any,
) -> list[dict[str, Any]]:
    if attachment_ids is not None and not isinstance(attachment_ids, (list, tuple)):
        raise ValueError("A Design attachment reference is invalid; attach the file again.")
    values = list(attachment_ids or ())
    if len(values) > MAX_ATTACHMENTS_PER_TURN:
        raise ValueError("MO Design accepts at most 8 attachments per message.")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        attachment_id = str(value or "").strip()
        if not attachment_id or attachment_id in seen:
            raise ValueError("A Design attachment reference is invalid; attach the file again.")
        record = find_attachment(config, attachment_id)
        if record is None:
            raise ValueError("A Design attachment is unavailable or changed; attach the file again.")
        size = int(record.get("bytes") or 0)
        if size < 1 or size > MAX_ATTACHMENT_BYTES:
            raise ValueError("A Design attachment is outside the 20 MiB request limit.")
        seen.add(attachment_id)
        rows.append(record)
    return rows


def _design_failure_detail(result: str) -> str:
    lines = [" ".join(line.split()) for line in str(result or "").splitlines() if line.strip()]
    if not lines:
        return ""
    preferred = [
        line for line in lines
        if line.casefold().startswith(("mo provider error:", "detail:", "provider error:"))
    ]
    selected = preferred[-1] if preferred else lines[0]
    safe = " ".join(redact_sensitive_text(selected).split())
    return f"Provider/runtime failure: {safe[:300]}" if safe else ""


def _design_preservation_message(
    session: dict[str, Any],
    revision: int,
    *,
    revision_advanced: bool,
    visual_changed: bool,
) -> str:
    parts: list[str] = []
    if revision_advanced:
        label = "Partial visual draft" if visual_changed else "Unaccepted draft"
        parts.append(f"{label} r{int(revision)} was retained but not accepted.")
    completed = int(session.get("last_completed_revision") or 0)
    if completed > 0:
        parts.append(f"The last accepted preview is r{completed}.")
    else:
        parts.append("No completed preview exists yet.")
    return " ".join(parts)
