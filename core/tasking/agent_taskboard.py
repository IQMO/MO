"""MO agent task-board management mixin."""

import re

from .. import local_extensions
from ..runtime.backend_monitor import BackendMonitor
from . import task_evidence
from .task_board import TaskBoard, record_snapshot
from .results import TaskTransitionResult, taskboard_position

TASKBOARD_CONTROL_TOOLS = frozenset({"set_plan", "complete_task"})

# Finish-discipline surfaced as row guidance (via board context) on the default
# model-owned path. This is NON-ENFORCING: expected_evidence is shown to the
# model but the contract never content-matches it, so no row is blocked by it.
_COVERAGE_DOC_HINT = (
    "new/changed behavior covered by a test or a stated reason",
    "docs/docstrings synced if behavior or an API changed",
)

_EXPLICIT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|/)?"
    r"[A-Za-z0-9_.@~+-]+(?:[\\/][A-Za-z0-9_.@~+()\-]+)+"
)


def _explicit_path_literals(text: str) -> set[str]:
    """Return normalized, unquoted path literals from operator/plan text.

    This intentionally recognizes only unmistakable slash-delimited paths. It
    does not guess natural-language filenames or URLs, keeping the stale-target
    guard below narrow enough for ordinary inferred implementation paths.
    """
    value = re.sub(r"https?://\S+", " ", str(text or ""), flags=re.I)
    found: set[str] = set()
    for match in _EXPLICIT_PATH_RE.finditer(value):
        path = match.group(0).rstrip(".,;:!?)]}").replace("\\", "/").casefold()
        leaf = path.rsplit("/", 1)[-1]
        unmistakable = bool(re.match(r"^(?:[a-z]:/|\.{0,2}/|/)", path)) or "." in leaf
        if path and unmistakable:
            found.add(path)
    return found


def _model_row_finish_hint(kind: str) -> list[str]:
    """Return finish-check evidence hints for a model-authored row's kind."""
    k = str(kind or "").lower()
    if any(w in k for w in ("verify", "test", "check")):
        return ["a passing test/build check", *_COVERAGE_DOC_HINT]
    if any(w in k for w in ("edit", "code", "implement", "build", "fix", "write", "change")):
        return list(_COVERAGE_DOC_HINT)
    return []


def _model_row_gate(kind: str) -> str:
    """Map a model-authored phase to the runtime gate that actually closes it."""
    value = str(kind or "").lower().strip()
    if value == "verify":
        return "verification"
    if value == "ask":
        return "manual"
    return "tool"


def _acceptance_criterion_id(value: object) -> str:
    """Return the stable AC id used by goal-plan coverage gates."""
    match = re.match(r"\s*(AC\d+)\b", str(value or ""), flags=re.I)
    return match.group(1).upper() if match else ""


def _model_row_kind(text: str, kind: str = "") -> str:
    """Normalize an explicit phase or infer one for the supported string form."""
    explicit = str(kind or "").lower().strip().replace("-", "_")
    aliases = {"read": "inspect", "search": "inspect", "write": "edit", "test": "verify"}
    explicit = aliases.get(explicit, explicit)
    if explicit in {"report", "final"}:
        return "response"
    if explicit in {"inspect", "edit", "execute", "verify", "ask"}:
        return explicit

    value = " ".join(str(text or "").lower().split())
    if re.search(r"\b(?:ask|clarify|request approval|wait for approval|confirm with (?:the )?user)\b", value):
        return "ask"
    if re.search(r"\b(?:report|summarize|respond|answer|present findings|hand off)\b", value):
        if re.search(r"\b(?:write|create|edit|update|save)\b", value) and re.search(
            r"\b(?:file|document|artifact|markdown|json|csv|pdf)\b", value
        ):
            return "edit"
        return "response"
    if re.search(r"\b(?:verify|test|pytest|check|validate|confirm behavior|rerun|compare result)\b", value):
        return "verify"
    if re.search(r"\b(?:launch|serve|start server|run server|deploy|restart|publish|commit|push)\b", value):
        return "execute"
    if re.search(r"\b(?:inspect|read|search|find|locate|inventory|review|audit|analy[sz]e|investigate|reproduce|trace|map)\b", value):
        return "inspect"
    if re.search(r"\b(?:implement|edit|fix|update|write|add|remove|refactor|apply|build|create|change|act)\b", value):
        return "edit"
    return ""


def _task_row_dict(task: object) -> dict:
    """Serialize an existing row without losing completed evidence during revision."""
    return {
        "id": str(getattr(task, "id", "") or ""),
        "text": str(getattr(task, "title", "") or ""),
        "status": str(getattr(task, "status", "pending") or "pending"),
        "evidence": list(getattr(task, "evidence", []) or []),
        "blocker": str(getattr(task, "blocker", "") or ""),
        "kind": str(getattr(task, "kind", "") or ""),
        "completion_gate": str(getattr(task, "completion_gate", "") or ""),
        "depends_on": list(getattr(task, "depends_on", []) or []),
        "parent_id": str(getattr(task, "parent_id", "") or ""),
        "acceptance_criteria": list(getattr(task, "acceptance_criteria", []) or []),
        "expected_evidence": list(getattr(task, "expected_evidence", []) or []),
        "test_strategy": str(getattr(task, "test_strategy", "") or ""),
    }


class AgentTaskBoard:
    """Task-board management methods for the MO Agent."""

    def _advance_task_board_after_tool(
        self,
        task_board: TaskBoard,
        tool_name: str,
        arguments: dict | None = None,
        *,
        monitor: BackendMonitor | None = None,
        user_input: str = "",
        verification_results: dict[str, str] | None = None,
    ) -> bool:
        """Append tool evidence and advance only on explicit complete_task."""
        active_before, phase_before = taskboard_position(task_board)

        def record_complete(
            *,
            ok: bool,
            reason: str,
            task_id: str,
            row: object | None = None,
            requested_task_id: str = "",
            activated: str | None = None,
        ) -> None:
            self._record_complete_task_result(
                ok=ok,
                reason=reason,
                task_id=task_id,
                row=row,
                requested_task_id=requested_task_id,
                activated=activated,
                task_board=task_board,
                active_task_before=active_before,
                phase_before=phase_before,
            )

        if tool_name == "set_plan":
            return self._apply_model_plan(
                task_board,
                arguments or {},
                monitor=monitor,
                user_input=user_input,
            )
        if task_evidence.taskboard_is_closed(task_board):
            if tool_name == "complete_task":
                record_complete(
                    ok=False,
                    reason="board_closed",
                    task_id=active_before,
                )
            return False
        local_extensions.on_tool_arguments(self, tool_name, arguments or {})

        active = task_board.active_task_id()
        if not active:
            if tool_name == "complete_task":
                record_complete(ok=False, reason="no_active_task", task_id="")
            return False

        tasks = task_board.tasks
        try:
            idx = next(i for i, t in enumerate(tasks) if t.id == active)
        except StopIteration:
            return False

        active_row = tasks[idx]
        if tool_name != "complete_task":
            if task_evidence.should_record_taskboard_tool_evidence(
                active_row,
                tool_name,
                arguments or {},
                idx=idx,
                total=len(tasks),
            ):
                task_board.append_evidence(active, self._task_evidence_item_for_tool(tool_name, arguments or {}))
                record_snapshot(task_board, "updated")
            return False

        requested_task = str((arguments or {}).get("task_id") or "").strip()
        if requested_task and requested_task != active:
            record_complete(
                ok=False,
                reason="task_id_mismatch",
                task_id=active,
                row=active_row,
                requested_task_id=requested_task,
            )
            if monitor:
                monitor.emit("board_complete_rejected", {
                    "task": active,
                    "tool": tool_name,
                    "reason": "task_id_mismatch",
                    "requested_task": requested_task,
                })
            return False
        manual_evidence = ""
        if task_evidence.task_is_manual_gate(active_row):
            manual_evidence = task_evidence.manual_approval_evidence(user_input)
            if not manual_evidence:
                record_complete(
                    ok=False,
                    reason="manual_approval_required",
                    task_id=active,
                    row=active_row,
                )
                if monitor:
                    monitor.emit("board_complete_rejected", {
                        "task": active,
                        "tool": tool_name,
                        "reason": "manual_approval_required",
                    })
                return False

        verification_state = ""
        if (
            str(getattr(active_row, "kind", "") or "").strip().lower() == "verify"
            or str(getattr(active_row, "completion_gate", "") or "").strip().lower()
            == "verification"
        ):
            verification_state = task_evidence.verification_result_set_state(
                verification_results
            )
        if verification_state in {"pending", "failed", "inconclusive"}:
            reason = f"verification_{verification_state}"
            record_complete(
                ok=False,
                reason=reason,
                task_id=active,
                row=active_row,
            )
            if monitor:
                monitor.emit(
                    "board_complete_rejected",
                    {"task": active, "tool": tool_name, "reason": reason},
                )
            return False
        if verification_state == "passed":
            task_board.append_evidence(active, "verification_result:passed")

        if manual_evidence:
            completed = task_board.complete(active, evidence=manual_evidence)
        elif str(getattr(task_board, "source", "") or "") != "goal":
            # Ordinary turn rows are phase labels over one board lifecycle. Merge
            # every matching item already gathered on that board, even when the
            # active row has partial evidence of its own. Otherwise a combined
            # delivery row can see push/deploy yet reject the earlier commit.
            carried = self._session_gathered_evidence(task_board, task=active_row)
            completed = task_board.complete(active, evidence=carried or None)
        else:
            # Goal rows are long-lived acceptance gates. Evidence from an older
            # row or iteration must never be borrowed to close the active row.
            completed = task_board.complete(active)
        if not completed:
            record_complete(
                ok=False,
                reason=getattr(completed, "reason", "completion_rejected"),
                task_id=active,
                row=active_row,
            )
            if monitor:
                monitor.emit("board_complete_rejected", {
                    "task": active,
                    "tool": tool_name,
                    "reason": getattr(completed, "reason", "completion_rejected"),
                })
            return False

        next_id = None
        for row in tasks:
            if row.status == "pending" and task_board.dependencies_satisfied(row.id):
                if task_board.activate(row.id):
                    next_id = row.id
                break

        if monitor:
            monitor.emit("board_advance", {
                "completed": active,
                "activated": next_id,
                "tool": tool_name,
                "idx": idx,
                "total": len(tasks),
            })
        record_complete(ok=True, reason="", task_id=active, row=active_row, activated=next_id)
        record_snapshot(task_board, "updated")
        return True

    @staticmethod
    def _board_is_extension_owned(task_board: TaskBoard) -> bool:
        """True when model plan creation/revision must fail closed for this board."""
        owner = str(getattr(task_board, "plan_owner", "") or "").strip().lower()
        if owner:
            return owner in {"extension", "goal"}
        return False

    def _model_owned_taskboard_enabled(self) -> bool:
        cfg = getattr(self, "config", {}) or {}
        tb = cfg.get("taskboard", {}) if isinstance(cfg.get("taskboard", {}), dict) else {}
        return bool(tb.get("model_owned", True))

    def _apply_model_plan(
        self,
        task_board: TaskBoard,
        arguments: dict,
        *,
        monitor: BackendMonitor | None = None,
        user_input: str = "",
    ) -> bool:
        """Own model/procedure planning, waiting and cancellation transitions."""
        mode = str(arguments.get("mode") or "start").strip().lower()
        active_before, phase_before = taskboard_position(task_board)

        def record(*, ok: bool, reason: str, rows: int = 0, preserved: int = 0) -> None:
            self._record_set_plan_result(
                ok=ok,
                reason=reason,
                mode=mode,
                rows=rows,
                preserved=preserved,
                task_board=task_board,
                active_task_before=active_before,
                phase_before=phase_before,
            )

        if mode not in {"start", "revise", "pause", "cancel"}:
            record(ok=False, reason="invalid_mode")
            return False
        if not self._model_owned_taskboard_enabled():
            record(ok=False, reason="disabled")
            return False
        if self._board_is_extension_owned(task_board):
            record(ok=False, reason="board_locked")
            if monitor:
                monitor.emit("taskboard", {"update": "set_plan_skipped_locked_board", "mode": mode})
            return False
        owner = str(getattr(task_board, "plan_owner", "") or "").strip().lower()
        if mode in {"pause", "cancel"}:
            reason = str(arguments.get("reason") or "").strip()
            if owner not in {"model", "procedure"} or not task_board.tasks:
                record(ok=False, reason="no_owned_plan")
                return False
            if not reason:
                record(ok=False, reason="lifecycle_reason_required")
                return False
            if arguments.get("tasks") or arguments.get("plan"):
                record(ok=False, reason="lifecycle_tasks_not_allowed")
                return False
            if not task_board.open_count() and task_board.state != "cancelled":
                record(ok=False, reason="board_closed")
                return False
            if mode == "cancel":
                task_board.cancel(reason)
            else:
                row = next((task for task in task_board.tasks if task.is_open), None)
                if row is None:
                    record(ok=False, reason="board_closed")
                    return False
                task_board.block(row.id, reason)
            record_snapshot(task_board, "cancelled" if mode == "cancel" else "blocked")
            record(ok=True, reason=reason)
            if monitor:
                monitor.emit("taskboard", {
                    "update": "model_plan_cancelled" if mode == "cancel" else "model_plan_paused",
                    "state": task_board.state,
                    "open_count": task_board.open_count(),
                    "reason": reason[:240],
                })
            return True
        if task_board.state == "cancelled":
            record(ok=False, reason="board_closed")
            return False
        if mode == "start" and task_board.tasks:
            record(ok=False, reason="plan_already_exists")
            return False
        if mode == "revise":
            if owner not in {"model", "procedure"}:
                record(ok=False, reason="revision_not_owned")
                return False
            if not task_board.tasks:
                record(ok=False, reason="no_plan_to_revise")
                return False
            if not str(arguments.get("reason") or "").strip():
                record(ok=False, reason="revision_reason_required")
                return False
        raw = arguments.get("tasks") or arguments.get("plan") or []
        if not isinstance(raw, list):
            record(ok=False, reason="tasks_required")
            return False
        new_rows: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                text, kind = item.strip(), ""
                acceptance_criteria: list[str] = []
                expected_evidence: list[str] = []
                test_strategy = ""
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("title") or "").strip()
                kind = str(item.get("kind") or "")
                acceptance_criteria = [
                    criterion
                    for raw_criterion in list(item.get("acceptance_criteria") or [])
                    if (criterion := _acceptance_criterion_id(raw_criterion))
                ]
                expected_evidence = [
                    str(value or "").strip()
                    for value in list(item.get("expected_evidence") or [])
                    if str(value or "").strip()
                ]
                test_strategy = str(item.get("test_strategy") or "").strip()
            else:
                continue
            if not text:
                continue
            normalized_kind = _model_row_kind(text, kind)
            if normalized_kind == "response":
                continue
            new_rows.append({
                "text": text,
                "kind": normalized_kind,
                "acceptance_criteria": list(dict.fromkeys(acceptance_criteria)),
                "expected_evidence": list(dict.fromkeys(expected_evidence)),
                "test_strategy": test_strategy,
            })
        preserved = [task for task in task_board.tasks if getattr(task, "status", "") == "completed"] if mode == "revise" else []
        # An explicit empty tail removes unnecessary work; it never completes
        # that work or clears approval/goal obligations. Keep the normal
        # preserved-row coverage checks and revision snapshots below.
        empty_tail_revision = (
            mode == "revise" and arguments.get("tasks") == [] and not raw
            and bool(preserved) and str(getattr(task_board, "source", "")) != "goal"
            and all(task_evidence.completion_evidence_set_matches_task(task, task.evidence) for task in preserved)
            and not any(task_evidence.task_is_manual_gate(task) for task in task_board.tasks if task.is_open)
        )
        if not new_rows and not empty_tail_revision:
            record(ok=False, reason="tasks_required")
            return False
        if self._model_plan_reuses_prior_targets(
            [(row["text"], row["kind"]) for row in new_rows],
            user_input=user_input,
        ):
            record(ok=False, reason="stale_plan_target")
            if monitor:
                monitor.emit("taskboard", {
                    "update": "model_plan_rejected_stale_target",
                    "mode": mode,
                })
            return False

        # A revision may include the completed prefix of the full plan. Those
        # rows already own their evidence; do not append them as new work.
        if preserved and len(new_rows) >= len(preserved) and all(
            row["text"] == task.title and row["kind"] == task.kind
            and row["acceptance_criteria"] == task.acceptance_criteria
            and list(dict.fromkeys([*row["expected_evidence"], *_model_row_finish_hint(row["kind"])])) == task.expected_evidence
            and row["test_strategy"] == task.test_strategy
            for row, task in zip(new_rows, preserved)
        ):
            new_rows = new_rows[len(preserved):]
        required_delivery = {
            criterion
            for value in list(getattr(task_board, "required_acceptance_criteria", []) or [])
            if (criterion := _acceptance_criterion_id(value))
        }
        required_verification = {
            criterion
            for value in list(getattr(task_board, "required_verification_criteria", []) or [])
            if (criterion := _acceptance_criterion_id(value))
        }
        if required_delivery or required_verification:
            candidate_rows = [
                {
                    "kind": str(getattr(task, "kind", "") or ""),
                    "acceptance_criteria": list(getattr(task, "acceptance_criteria", []) or []),
                }
                for task in preserved
            ] + new_rows
            delivery_coverage = {
                criterion
                for row in candidate_rows
                if str(row.get("kind") or "") in {"inspect", "edit", "execute"}
                for value in list(row.get("acceptance_criteria") or [])
                if (criterion := _acceptance_criterion_id(value))
            }
            verification_coverage = {
                criterion
                for row in candidate_rows
                if str(row.get("kind") or "") == "verify"
                for value in list(row.get("acceptance_criteria") or [])
                if (criterion := _acceptance_criterion_id(value))
            }
            if not required_delivery.issubset(delivery_coverage):
                record(ok=False, reason="acceptance_delivery_missing")
                return False
            if not required_verification.issubset(verification_coverage):
                record(ok=False, reason="acceptance_verification_missing")
                return False
        rows = [_task_row_dict(task) for task in preserved]
        unfinished = [task for task in task_board.tasks if task.is_open] if mode == "revise" else []
        numeric_ids = [int(str(task.id)) for task in task_board.tasks if str(task.id).isdigit()]
        next_id = (max(numeric_ids) + 1) if numeric_ids else (len(task_board.tasks) + 1)
        previous_id = str(preserved[-1].id) if preserved else ""
        for item in new_rows:
            text = str(item["text"])
            kind = str(item["kind"])
            expected = list(dict.fromkeys([
                *list(item["expected_evidence"]),
                *_model_row_finish_hint(kind),
            ]))
            # Preserve only the same work contract, never transfer evidence to
            # a renamed target or a newly introduced row that reuses its number.
            retained = next((task for task in unfinished if (
                task.title == text and task.kind == kind
                and task.completion_gate == _model_row_gate(kind)
                and task.acceptance_criteria == item["acceptance_criteria"]
                and task.expected_evidence == expected
                and task.test_strategy == item["test_strategy"]
            )), None)
            row_id = retained.id if retained else str(next_id)
            row = {
                "id": row_id,
                "text": text,
                "status": "active" if len(rows) == len(preserved) else "pending",
                "kind": kind,
                "completion_gate": _model_row_gate(kind),
                "depends_on": [previous_id] if previous_id else [],
                "acceptance_criteria": list(item["acceptance_criteria"]),
                "expected_evidence": expected,
                "test_strategy": str(item["test_strategy"]),
            }
            if retained is not None:
                row["evidence"] = list(retained.evidence)
                unfinished.remove(retained)
            else:
                next_id += 1
            rows.append(row)
            previous_id = row_id

        if mode == "revise" and len(rows) == len(task_board.tasks) and all(
            all(row.get(key, [] if isinstance(value, list) else "") == value
                for key, value in _task_row_dict(task).items())
            for row, task in zip(rows, task_board.tasks)
        ):
            record(ok=True, reason="unchanged", rows=len(new_rows), preserved=len(preserved))
            return True
        if mode == "revise":
            record_snapshot(task_board, "plan_superseded")
        else:
            task_board.plan_owner = "model"
        title = str(getattr(task_board, "title", "") or "MO plan") if mode == "revise" else "MO plan"
        task_board.set_rows(title, rows, objective=str(getattr(task_board, "objective", "") or ""))
        event = "plan_revised" if mode == "revise" else "plan_set"
        record_snapshot(task_board, event)
        record(
            ok=True,
            reason="",
            rows=len(new_rows),
            preserved=len(preserved),
        )
        if monitor:
            monitor.emit("taskboard", {
                "update": "model_plan_revised" if mode == "revise" else "model_plan_set",
                "rows": len(new_rows),
                "preserved_rows": len(preserved),
                "reason": str(arguments.get("reason") or "")[:240],
            })
        return True

    def _model_plan_reuses_prior_targets(
        self,
        new_rows: list[tuple[str, str]],
        *,
        user_input: str,
    ) -> bool:
        """Reject an unmistakable prior-turn target substituted for this turn.

        The guard fires only when the latest request names path literals, the
        proposed plan names none of them, and instead names a path from one of
        the last three earlier user turns. Generic plans and plans that mention
        both an earlier source and the current target remain valid.
        """
        conversation_input = getattr(self, "_conversation_user_input", None)
        current_text = (
            conversation_input(user_input)
            if callable(conversation_input)
            else str(user_input or "")
        )
        current_paths = _explicit_path_literals(current_text)
        if not current_paths:
            return False

        plan_paths = _explicit_path_literals("\n".join(text for text, _kind in new_rows))
        if not plan_paths or plan_paths.intersection(current_paths):
            return False

        session = getattr(self, "session", None)
        user_messages = [
            str(message.get("content") or "")
            for message in list(getattr(session, "messages", []) or [])
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        if user_messages and user_messages[-1].strip() == str(user_input or "").strip():
            user_messages = user_messages[:-1]
        prior_paths: set[str] = set()
        for message in user_messages[-3:]:
            prior_paths.update(_explicit_path_literals(message))
        return bool((plan_paths - current_paths).intersection(prior_paths))

    def _record_set_plan_result(
        self,
        *,
        ok: bool,
        reason: str,
        mode: str,
        rows: int = 0,
        preserved: int = 0,
        task_board: TaskBoard | None = None,
        active_task_before: str = "",
        phase_before: str = "",
    ) -> None:
        active_after, phase_after = taskboard_position(task_board)
        result = TaskTransitionResult(
            operation="set_plan",
            ok=bool(ok),
            reason=str(reason or ""),
            mode=str(mode or "start"),
            rows=int(rows or 0),
            preserved=int(preserved or 0),
            active_task_before=str(active_task_before or ""),
            active_task_after=active_after,
            phase_before=str(phase_before or ""),
            phase_after=phase_after,
        )
        self._last_set_plan_result = result
        self._append_task_transition(result)

    def _set_plan_result_message(self) -> str:
        result = getattr(self, "_last_set_plan_result", None)
        return result.render() if isinstance(result, TaskTransitionResult) else "set_plan rejected: not_applied."

    def _append_task_transition(self, result: TaskTransitionResult) -> None:
        state = getattr(self, "_thread_state", None)
        transitions = getattr(state, "task_transitions", None) if state is not None else None
        if isinstance(transitions, list):
            transitions.append(result)

    @staticmethod
    def _task_evidence_item_for_tool(tool_name: str, arguments: dict | None = None) -> str:
        return task_evidence.taskboard_tool_evidence_item(tool_name, arguments or {})

    @staticmethod
    def _session_gathered_evidence(
        task_board: TaskBoard,
        limit: int = 8,
        *,
        task: object | None = None,
    ) -> list[str]:
        """Evidence gathered across rows, optionally matching *task*.

        Historical zero-evidence backfill copied every earlier label onto the
        active row.  Once gates became phase-specific that polluted verification
        rows with unrelated inspect/edit evidence while still failing them.  A
        completion attempt may carry only evidence that could actually close the
        active row.
        """
        carried: list[str] = []
        evidence = [
            *list(getattr(task_board, "gathered_evidence", []) or []),
            *(item for row in task_board.tasks for item in (row.evidence or [])),
        ]
        for item in evidence:
            value = str(item)
            if (
                value not in carried
                and (task is None or task_evidence.completion_evidence_matches_task(task, value))
            ):
                carried.append(value)
        return carried[:limit]

    def _record_complete_task_result(
        self,
        *,
        ok: bool,
        reason: str,
        task_id: str,
        row: object | None = None,
        requested_task_id: str = "",
        activated: str | None = None,
        task_board: TaskBoard | None = None,
        active_task_before: str = "",
        phase_before: str = "",
        operation: str = "complete_task",
    ) -> None:
        active_after, phase_after = taskboard_position(task_board)
        result = TaskTransitionResult(
            operation=str(operation or "complete_task"),
            ok=bool(ok),
            reason=str(reason or ""),
            task_id=str(task_id or ""),
            requested_task_id=str(requested_task_id or ""),
            activated=str(activated or ""),
            title=str(getattr(row, "title", "") or ""),
            kind=str(getattr(row, "kind", "") or ""),
            completion_gate=str(getattr(row, "completion_gate", "") or ""),
            expected_evidence=tuple(str(item) for item in (getattr(row, "expected_evidence", []) or [])),
            evidence=tuple(str(item) for item in (getattr(row, "evidence", []) or [])),
            active_task_before=str(active_task_before or ""),
            active_task_after=active_after,
            phase_before=str(phase_before or ""),
            phase_after=phase_after,
        )
        self._last_complete_task_result = result
        self._append_task_transition(result)

    def _complete_task_result_message(self) -> str:
        result = getattr(self, "_last_complete_task_result", None)
        return result.render() if isinstance(result, TaskTransitionResult) else "complete_task rejected for task active task: completion_rejected."
