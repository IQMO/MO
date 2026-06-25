"""MO agent task-board management mixin."""

from datetime import datetime
from pathlib import Path

from ..atomic_write import atomic_write_text
from .task_board import TaskBoard
from ..backend_monitor import BackendMonitor
from . import task_evidence


class AgentTaskBoard:
    """Task-board management methods for the MO Agent."""

    def _advance_task_board_after_tool(self, task_board: TaskBoard, tool_name: str, arguments: dict | None = None, *, monitor: BackendMonitor | None = None) -> bool:
        """Append tool evidence to the active row, and advance only on explicit complete_task.

        The taskboard is a user-visible progress contract. A successful tool
        execution is recorded as evidence, but the active task is only completed
        when the Agent explicitly calls the complete_task tool.
        """
        # Phase 1 (flag-gated, default off): MO owns the board — set_plan replaces the
        # rows with MO's own plan. Inert unless taskboard.model_owned is enabled, so
        # this is a no-op for the current Ghost/procedure-seeded flow.
        if tool_name == "set_plan":
            return self._apply_model_plan(task_board, arguments or {}, monitor=monitor)
        # Bind the active DEVMODE session dir to whatever dir THIS run actually writes
        # its artifacts into, so the economy writer targets it explicitly (never the
        # newest dir by mtime — see _write_devmode_economy_record).
        if tool_name in ("write_file", "edit_file"):
            self._bind_active_devmode_dir_from_write(arguments or {})

        active = task_board.active_task_id()
        if not active:
            return False

        # 1. Always append evidence to the active task (unless it's complete_task itself)
        if tool_name != "complete_task":
            task_board.append_evidence(active, self._task_evidence_item_for_tool(tool_name, arguments or {}))

        # 2. Only advance the taskboard if the agent explicitly signals completion
        if tool_name != "complete_task":
            return False

        tasks = task_board.tasks
        try:
            idx = next(i for i, t in enumerate(tasks) if t.id == active)
        except StopIteration:
            return False

        # Mark current task complete. A phase row the model completes via complete_task
        # without running any tool of its own would otherwise close with ZERO evidence
        # (observed live mo-1782177115: DEVMODE tasks 5-6 closed empty yet passed the
        # contract gate). Attach the session's already-gathered evidence so no completed
        # row is evidence-empty — UPSTREAM attachment, the C1 principle (a gate-side
        # rejection of empty rows loops; see the contract-gate history). Only backfills
        # when the active row has none of its own AND the session gathered real evidence.
        active_row = tasks[idx]
        row_has_real = any(not str(e).startswith("final:") for e in (active_row.evidence or []))
        if not row_has_real:
            carried = self._session_gathered_evidence(task_board)
            task_board.complete(active, evidence=carried or None)
        else:
            task_board.complete(active)

        # Activate next ready task
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
        return True

    def _model_owned_taskboard_enabled(self) -> bool:
        cfg = getattr(self, "config", {}) or {}
        tb = cfg.get("taskboard", {}) if isinstance(cfg.get("taskboard", {}), dict) else {}
        return bool(tb.get("model_owned", False))

    def _apply_model_plan(self, task_board: TaskBoard, arguments: dict, *, monitor: BackendMonitor | None = None) -> bool:
        """Phase 1: let MO own the board by replacing its rows with MO's own plan.

        Flag-gated (taskboard.model_owned, default off) so the current Ghost/
        procedure-seeded flow is unchanged. Accepts ``tasks`` as a list of strings
        or {text, kind} dicts; builds a linear evidence-gated board. Returns True
        when it set rows, else False (flag off, or no usable tasks).
        """
        if not self._model_owned_taskboard_enabled():
            return False
        raw = arguments.get("tasks") or arguments.get("plan") or []
        if not isinstance(raw, list):
            return False
        rows: list[dict] = []
        for i, item in enumerate(raw, 1):
            if isinstance(item, str):
                text, kind = item.strip(), ""
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("title") or "").strip()
                kind = str(item.get("kind") or "")
            else:
                continue
            if not text:
                continue
            rows.append({
                "id": str(len(rows) + 1),
                "text": text,
                "status": "active" if not rows else "pending",
                "kind": kind,
                "completion_gate": "tool",
                "depends_on": [str(len(rows))] if rows else [],
            })
        if not rows:
            return False
        task_board.set_rows("MO plan", rows, objective=str(getattr(task_board, "objective", "") or ""))
        if monitor:
            monitor.emit("taskboard", {"update": "model_plan_set", "rows": len(rows)})
        return True

    @staticmethod
    def _task_evidence_item_for_tool(tool_name: str, arguments: dict | None = None) -> str:
        return task_evidence.taskboard_tool_evidence_item(tool_name, arguments or {})

    @staticmethod
    def _session_gathered_evidence(task_board: TaskBoard, limit: int = 8) -> list[str]:
        """Non-`final:` evidence the session gathered across all rows — the source used to
        backfill a row completed with none of its own (so no completed row is ever
        evidence-empty). Same set the closeout carry uses."""
        carried: list[str] = []
        for t in task_board.tasks:
            for e in (t.evidence or []):
                es = str(e)
                if not es.startswith("final:") and es not in carried:
                    carried.append(es)
        return carried[:limit]

    def _ensure_devmode_session_dir(self) -> Path | None:
        """Create and bind the runtime-owned DEVMODE session dir before model writes.

        The model used to create ``memory/devmode/<stamp>`` itself, which repeatedly
        produced UTC/past/future stamps and let closeout gates fire too late. Product
        runtime owns the directory now; the model receives the exact path as context
        and writes artifacts there.
        """
        try:
            current = getattr(self, "_active_devmode_session_dir", None)
            if current is not None and Path(current).is_dir():
                self._track_devmode_run_session_id()
                return Path(current)

            from ..path_defaults import mo_home
            stamp = datetime.now().strftime("%Y-%m-%dT%H%M")
            root = mo_home(getattr(self, "config", {}) or {}) / "memory" / "devmode"
            target = root / stamp
            target.mkdir(parents=True, exist_ok=True)
            prev = getattr(self, "_active_devmode_session_dir", None)
            if prev is None or Path(prev) != target:
                self._devmode_run_session_ids = set()
                self._devmode_closeout_frozen_errors = None
            self._active_devmode_session_dir = target
            self._active_devmode_session_dir_runtime_owned = True
            self._track_devmode_run_session_id()
            self._write_devmode_manifest_record(
                status="active",
                warnings=["runtime_owned_session_dir"],
            )
            return target
        except Exception:
            return None

    def _devmode_runtime_output_context(self, user_input: str) -> str:
        """Context block that tells OWNER_MAINTENANCE the one allowed output directory."""
        try:
            from ..owner_protocols import is_owner_maintenance_activation
            if not is_owner_maintenance_activation(user_input):
                return ""
            target = self._ensure_devmode_session_dir()
            if target is None:
                return ""
            return (
                "### OWNER_MAINTENANCE Runtime Output Directory\n"
                "The runtime already created and owns this OWNER_MAINTENANCE session directory. "
                "Write all session artifacts here and do not create another "
                "`memory/devmode/<stamp>` folder.\n"
                f"- active_session_dir: `{target}`\n"
                f"- required closeout files: `{target / 'summary.md'}`, "
                f"`{target / 'economy.md'}`, `{target / 'manifest.json'}`\n"
                "If any older protocol text says to run mkdir or hand-roll a timestamp, "
                "ignore that older text and use active_session_dir exactly."
            )
        except Exception:
            return ""

    @staticmethod
    def _path_is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _devmode_output_path_block_reason(self, user_input: str, name: str, arguments: dict | None) -> str | None:
        """Reject DEVMODE artifact writes to any dir other than the runtime-owned one."""
        try:
            if name not in {"write_file", "edit_file"}:
                return None
            from ..owner_protocols import is_owner_maintenance_activation
            if not is_owner_maintenance_activation(user_input):
                return None
            args = arguments or {}
            raw_path = str(args.get("path") or args.get("file_path") or "").strip()
            if not raw_path:
                return None
            raw_norm = raw_path.replace("\\", "/").lower()
            if "memory/devmode" not in raw_norm:
                return None

            from ..path_defaults import mo_home
            active = self._ensure_devmode_session_dir()
            if active is None:
                return None
            active = Path(active).expanduser().resolve(strict=False)
            state_root = (mo_home(getattr(self, "config", {}) or {}) / "memory" / "devmode").resolve(strict=False)
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            candidate = candidate.resolve(strict=False)

            candidate_under_state = self._path_is_relative_to(candidate, state_root)
            if candidate_under_state:
                rel = candidate.relative_to(state_root)
                parts = rel.parts
                if len(parts) <= 1:
                    return None  # global devmode files, e.g. longitudinal.md
                if parts[0][:1].isdigit() and self._path_is_relative_to(candidate, active):
                    return None
            else:
                # Catches checkout-relative paths such as E:\MO-clean\memory\devmode\<stamp>\...
                after = raw_norm.split("memory/devmode", 1)[1].lstrip("/")
                first = after.split("/", 1)[0] if after else ""
                if not first[:1].isdigit():
                    return None

            return (
                "[OWNER_MAINTENANCE OUTPUT BLOCKED] Runtime owns this run's artifact directory: "
                f"{active}. Do not write DEVMODE session artifacts to {candidate}. "
                "Use the active_session_dir path from context exactly."
            )
        except Exception:
            return None

    def _finalize_task_board_for_answer(self, task_board: TaskBoard) -> bool:
        """Reflect final-answer truth without turning unfinished work red.

        Final/report rows may complete from the final assistant answer.  Other
        active rows are not finished just because the model responded; leave
        them active so the board truth shows MO still owes that step instead of
        inventing a red blocker like "not completed before final answer".
        """
        active = task_board.active_task_id()
        if not active:
            return False
        task = task_board.task(active)
        if not self._final_should_complete_task(task):
            return False
        task_board.complete(active, evidence="final:assistant_response")
        ready_id = task_board.first_ready_pending_id()
        if ready_id and task_board.dependencies_satisfied(ready_id):
            task_board.activate(ready_id)
        return True

    def _finalize_self_protocol_task_board_for_answer(self, user_input: str, final_text: str, task_board: TaskBoard) -> bool:
        """Close self-protocol phase rows only after their terminal report gate passes.

        OWNER_MAINTENANCE and OWNER_COMPARISON own deterministic phase boards. Their phases are
        protocol checkpoints, not ordinary implementation rows, so a valid
        terminal report can close the remaining phase rows before the final
        consistency boundary reads task truth.
        """
        from ..owner_protocols import (
            is_owner_maintenance_activation,
            is_owner_interface_audit_activation,
            is_owner_comparison_activation,
        )
        from ..self_maintenance.devmode_closeout import (
            owner_maintenance_final_allows_stop,
            owner_interface_audit_final_allows_stop,
            owner_comparison_final_allows_stop,
        )

        if is_owner_comparison_activation(user_input):
            if not owner_comparison_final_allows_stop(user_input, final_text):
                return False
            evidence = "final:owner_comparison_protocol_closeout"
        elif is_owner_maintenance_activation(user_input):
            from ..backend_monitor import active_monitor_path
            self._track_devmode_run_session_id()
            monitor_path = active_monitor_path()
            run_ids = set(getattr(self, "_devmode_run_session_ids", None) or set())
            if not owner_maintenance_final_allows_stop(
                user_input,
                final_text,
                monitor_path=monitor_path,
                session_ids=run_ids or None,
                frozen_error_count=getattr(self, "_devmode_closeout_frozen_errors", None),
                session_dir=getattr(self, "_active_devmode_session_dir", None),
            ):
                return False
            evidence = "final:owner_maintenance_protocol_closeout"
        elif is_owner_interface_audit_activation(user_input):
            if not owner_interface_audit_final_allows_stop(user_input, final_text):
                return False
            evidence = "final:owner_interface_audit_protocol_closeout"
        else:
            return False

        # Authoritative economy record at this self-protocol closeout — written by
        # the runtime from the live monitor, so it can never be estimated, stale, or
        # hand-faked (observed: model wrote helper-format numbers without running it).
        self._write_devmode_economy_record()

        if not task_board:
            return False

        # C1: the terminal report (gated above) closes the remaining phase rows.
        # Carry the turn's real gathered evidence onto each closed row so the board
        # reflects what actually happened instead of a hollow identical `final:`
        # token. The rows carry the session's real evidence (not per-phase-specific):
        # precise per-phase attribution would require fragile phase auto-advance for
        # no honesty gain, so it is intentionally not done.
        carried = self._session_gathered_evidence(task_board)

        changed = False
        for task in list(task_board.tasks):
            if task.status != "completed":
                row_evidence = [evidence, *carried] if carried else evidence
                task_board.complete(task.id, evidence=row_evidence)
                changed = changed or task.status == "completed"
            else:
                # Backfill a row the model self-completed via complete_task that carries
                # no real evidence (only a `final:` token, or empty) — typically a
                # diagnostic/reasoning phase row with no tool of its own. Attach the
                # session's gathered evidence so the closeout contract gate sees real
                # per-row truth. Without this the row stays empty and the whole-board
                # contract gate rejects every turn (it has no circuit breaker) — an
                # unbounded CONTRACT GATE loop (observed live mo-1782079519). Backfills
                # only when the session actually gathered evidence, so a zero-work
                # closeout that marked rows done with nothing behind them is still caught.
                real = [str(e) for e in (task.evidence or []) if not str(e).startswith("final:")]
                if not real and carried:
                    task.evidence = [*(task.evidence or []), *carried]
                    changed = True
        # This finalize runs only after the protocol stop gate passed → the run is
        # closing out. For DEVMODE, mark the manifest status="complete" and stamp the
        # runtime-owned workflow closeout (the model never returns to tick phase rows).
        if is_owner_maintenance_activation(user_input):
            self._write_devmode_manifest_record(status="complete")
            self._reconcile_devmode_workflow_closeout(task_board)
        return changed

    def _bind_active_devmode_dir_from_write(self, arguments: dict) -> None:
        """Bind the active DEVMODE session dir to the dir THIS run writes its artifacts
        into. Captured from each write_file/edit_file path under memory/devmode/<stamp>/
        so the economy writer can target the explicit active dir instead of guessing the
        newest dir by mtime (which let an aborted run overwrite a prior session).
        The owner profile protocol root is NOT a session dir and is skipped."""
        try:
            from ..path_defaults import mo_home
            path = str((arguments or {}).get("path") or (arguments or {}).get("file_path") or "")
            norm = path.replace("\\", "/")
            low = norm.lower()
            key = "memory/devmode/"
            i = low.find(key)
            if i == -1:
                return
            stamp = norm[i + len(key):].split("/", 1)[0]  # preserve case (Linux is case-sensitive)
            if not stamp or not stamp[:1].isdigit():
                return
            candidate = mo_home() / "memory" / "devmode" / stamp
            if not candidate.is_dir():
                return
            prev = getattr(self, "_active_devmode_session_dir", None)
            if getattr(self, "_active_devmode_session_dir_runtime_owned", False) and prev is not None:
                if Path(prev) != candidate:
                    self._write_devmode_manifest_record(
                        status="active",
                        warnings=["session_dir_write_rejected"],
                    )
                    return
            if prev is None or Path(prev) != candidate:
                # A different session dir = a new logical run → start a fresh id set so
                # this run's economy can never count a PRIOR Main/user run's events that
                # share the same per-process monitor file. Also unfreeze the closeout error
                # count so a new run freezes its own terminal count.
                self._devmode_run_session_ids = set()
                self._devmode_closeout_frozen_errors = None
            self._active_devmode_session_dir = candidate
            self._track_devmode_run_session_id()
        except Exception:
            pass

    def _track_devmode_run_session_id(self) -> None:
        """Accumulate the current session_id into the active logical-run id set, so the
        economy record groups a DEVMODE run across compaction/handoff (each handoff mints
        a new `mo-handoff-*` id on the same run) while excluding other runs' ids. Called
        from the dir binder (run start), the handoff (old+new ids), and the economy writer
        (current id at closeout)."""
        ids = getattr(self, "_devmode_run_session_ids", None)
        if not isinstance(ids, set):
            ids = set()
            self._devmode_run_session_ids = ids
        sid = str(getattr(getattr(self, "session", None), "session_id", "") or "")
        if sid:
            ids.add(sid)

    def _write_devmode_economy_record(self) -> None:
        """Write the authoritative economy record (provider/tool/error/compression
        counts from the live monitor) into the EXPLICIT active DEVMODE session dir
        bound from this run's own artifact writes — NEVER the newest dir by mtime.

        The mtime heuristic let an aborted later run (which created no dir of its own)
        overwrite a PRIOR session's economy: it corrupted T2121's authentic 29/66 with
        a stray 23/43 sourced from the aborted run's monitor file. If no active dir is
        bound this run, REFUSE to write rather than guess. The model never authors these
        numbers. Best-effort: a failure here must never break closeout.

        Note (acceptable residual): the binding lives on the agent (per-process), so a
        fresh process that stalls at boot without writing any artifact has no binding and
        correctly refuses — fixing the observed cross-process corruption."""
        try:
            from ..backend_monitor import GHOST_SURFACES, active_monitor_path, economy_summary, format_economy_record
            target = getattr(self, "_active_devmode_session_dir", None)
            if target is None or not Path(target).is_dir():
                return  # no explicit binding this run → refuse; never fall back to mtime
            # Logical-run scoping: count ONLY this run's own session segments (the
            # original id + every handoff `mo-handoff-*` id accumulated this run) and
            # exclude interleaved Ghost/desktop turns. This isolates the run both from
            # other Main/user runs sharing the per-process monitor file (via session_ids)
            # and from Ghost/desktop activity (via exclude_surfaces). Falls back to
            # surface-only exclusion if no ids were captured (best-effort, never blocks).
            self._track_devmode_run_session_id()
            run_ids = set(getattr(self, "_devmode_run_session_ids", None) or set())
            monitor_path = active_monitor_path()
            summary = economy_summary(
                monitor_path,
                session_ids=run_ids or None,
                exclude_surfaces=GHOST_SURFACES,
            )
            # Freeze the tool-error count at the FIRST closeout write. Post-freeze artifact
            # edits (e.g. an edit_file old-text-not-found while writing the summary) would
            # otherwise bump the live count N -> N+1, invalidating the just-written ledger
            # and making the closeout gate reject every attempt — an unbounded N->N+1 loop
            # that exhausted the turn budget (observed live mo-1782179985). The frozen value
            # IS the authoritative terminal count; the gate and the artifacts all use it.
            frozen = getattr(self, "_devmode_closeout_frozen_errors", None)
            if frozen is None:
                frozen = int(summary.get("tool_errors", 0) or 0)
                self._devmode_closeout_frozen_errors = frozen
            summary["tool_errors"] = frozen
            atomic_write_text(Path(target) / "economy.md", format_economy_record(summary), encoding="utf-8")
            # Reconcile the model-authored economy line in summary.md. The model
            # writes summary.md BEFORE the closeout completes, so its hand-counted
            # numbers go stale by the closeout delta (observed: summary 26/63 vs
            # authoritative economy.md 29/66). The economy fix made economy.md
            # runtime-owned; this extends the same single-source-of-truth to the
            # one place the model still restates counts, so the two files can never
            # disagree. Surgical: only the numeric counts on the economy line are
            # rewritten — any model narration on that line is preserved.
            AgentTaskBoard._reconcile_summary_economy_counts(Path(target) / "summary.md", summary)
            # Runtime-owned manifest: one authoritative projection of this run's outputs
            # (monitor, economy, taskboard, artifacts, status) so the model never
            # hand-tracks its own counts. Best-effort, never model-authored.
            self._write_devmode_manifest_record(status="active", economy=summary)
        except Exception:
            pass

    def _write_devmode_manifest_record(self, *, status: str = "active", economy: dict | None = None,
                                       warnings: list | None = None,
                                       reconciliations: dict | None = None) -> None:
        """Project this DEVMODE run's runtime truth into manifest.json (see
        core/tasking/devmode_manifest.py). Best-effort; a failure must never break closeout."""
        try:
            from .devmode_manifest import build_devmode_manifest, write_devmode_manifest
            from ..backend_monitor import GHOST_SURFACES, active_monitor_path, economy_summary
            target = getattr(self, "_active_devmode_session_dir", None)
            if target is None or not Path(target).is_dir():
                return
            run_ids = set(getattr(self, "_devmode_run_session_ids", None) or set())
            monitor_path = active_monitor_path()
            eco = dict(economy) if economy is not None else economy_summary(
                monitor_path, session_ids=run_ids or None, exclude_surfaces=GHOST_SURFACES)
            frozen = getattr(self, "_devmode_closeout_frozen_errors", None)
            # The manifest's tool_errors MUST equal economy.md (which uses the frozen
            # count). Otherwise a manifest written at the complete/blocked hook (which
            # recomputes the LIVE count) would disagree with economy.md — exactly the
            # cross-artifact drift the manifest exists to prevent (observed T1047: manifest
            # said 5 live while economy.md said the frozen 4).
            if frozen is not None:
                eco["tool_errors"] = frozen
            board = getattr(getattr(self, "gateway", None), "last_task_board", None)
            surface = str(getattr(self, "_current_route_source", "") or "") or None
            instance_id = getattr(self, "instance_id", None)
            warns = list(warnings or [])
            if int(eco.get("provider_errors", 0) or 0) > 0:
                warns.append("provider_error_retry_present")
            manifest = build_devmode_manifest(
                Path(target),
                economy=eco,
                frozen_tool_errors=frozen,
                run_session_ids=run_ids,
                instance_ids={instance_id} if instance_id else None,
                surface=surface,
                status=status,
                monitor_path=str(monitor_path) if monitor_path else None,
                task_board=board,
                warnings=warns,
                reconciliations=reconciliations,
            )
            # final_row_token_only warning from the projected board.
            if any(t.get("final_token_only") for t in manifest["taskboard"]["tasks"]):
                if "final_row_token_only" not in manifest["warnings"]:
                    manifest["warnings"].append("final_row_token_only")
            write_devmode_manifest(Path(target), manifest)
        except Exception:
            pass

    @staticmethod
    def _reconcile_summary_economy_counts(summary_path, summary: dict) -> None:
        """Overwrite stale provider/tool-call/tool-error/sandbox-blocked/compression
        counts ANYWHERE in a OWNER_MAINTENANCE summary.md with the authoritative monitor figures,
        so the header line, the Economy section, AND the Closeout can never disagree.

        The model restates these counts in several places and phrasings; the old version
        only reconciled the "N provider requests" header form, so the Economy section's
        "Provider requests: N" colon form drifted (live 2026-06-24T0404: header 90/119 vs
        Economy section 74/106). This reconciles BOTH "N <metric>" and "<metric>: N" for
        every named metric. Surgical: only a number directly adjacent to a NAMED metric is
        rewritten — model narration and unnamed numbers (a bare "errors: 0", "responses:
        74", "350 tests") are left untouched."""
        try:
            import re
            if not summary_path.exists():
                return
            text = summary_path.read_text(encoding="utf-8", errors="replace")
            # (authoritative value, metric-name regex). Each metric phrase is specific
            # enough that order does not matter and the metrics never overlap.
            metrics = (
                (summary.get("provider_requests", 0), r"provider\s+requests?"),
                (summary.get("tool_calls", 0),        r"tool\s+calls?"),
                (summary.get("tool_errors", 0),       r"tool\s+errors?"),
                (summary.get("sandbox_blocked", 0),   r"sandbox[-\s]*blocked"),
                (summary.get("compression_events", 0), r"compression\s+events?"),
            )
            new_text = text
            for value, phrase in metrics:
                v = str(int(value or 0))
                # "N <metric>"  e.g. "90 provider requests". Adjacency uses [ \t] (NOT
                # \s) so a number ending the PREVIOUS line is never matched as the count
                # "before" a metric on the next line across a newline.
                new_text = re.sub(rf"\b\d+(?=[ \t]+(?:{phrase})\b)", v, new_text, flags=re.IGNORECASE)
                # "<metric>: N" / "<metric> N"  e.g. "Provider requests: 74"
                new_text = re.sub(rf"((?:{phrase})\b[: \t]+)\d+", rf"\g<1>{v}", new_text, flags=re.IGNORECASE)
            if new_text != text:
                atomic_write_text(summary_path, new_text, encoding="utf-8")
        except Exception:
            pass

    def _reconcile_devmode_workflow_closeout(self, task_board) -> None:
        """At DEVMODE closeout, stamp a runtime-owned authoritative section onto
        workflow.md so a model checklist that left phase rows unticked (it never returns
        to tick them) cannot read as incomplete against a completed board. Tied to the
        live taskboard projection; the model's own checklist rows are never rewritten."""
        try:
            from .devmode_manifest import _taskboard_projection
            target = getattr(self, "_active_devmode_session_dir", None)
            if target is None or not Path(target).is_dir():
                return
            AgentTaskBoard._append_workflow_runtime_closeout(
                Path(target) / "workflow.md", _taskboard_projection(task_board))
        except Exception:
            pass

    @staticmethod
    def _append_workflow_runtime_closeout(workflow_path, taskboard_projection: dict) -> None:
        """Append ONE bounded, runtime-owned closeout section to workflow.md stating the
        authoritative taskboard truth (live 2026-06-24T0404: matrix/rotation/diff/catalog
        left `[ ]` while the board was completed/open 0). Deterministic + idempotent;
        stamps only a genuinely complete board; NEVER edits the model's checklist rows."""
        try:
            if not workflow_path.exists():
                return
            if str(taskboard_projection.get("state") or "") != "completed":
                return
            if int(taskboard_projection.get("open_count") or 0) != 0:
                return
            text = workflow_path.read_text(encoding="utf-8", errors="replace")
            marker = "## Runtime Closeout (authoritative)"
            if marker in text:
                return  # idempotent — never stamp twice
            tasks = taskboard_projection.get("tasks") or []
            done = sum(1 for t in tasks if str(t.get("status")) == "completed")
            section = (
                f"\n\n{marker}\n"
                f"- Taskboard: **completed**, open=0, {done}/{len(tasks)} phase rows done "
                "(runtime-owned truth; manifest.json is authoritative).\n"
                "- Any unticked `[ ]` rows above are the model's working checklist and are "
                "superseded by this section — the run completed all phases.\n"
            )
            atomic_write_text(workflow_path, text.rstrip() + section, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _reconcile_summary_terminal_marker(summary_path, *, blocked: bool) -> bool:
        """When the run ends BLOCKED, a summary.md that still claims [OWNER_MAINTENANCE COMPLETE]
        is a lie (observed T0403: COMPLETE in summary while the run hit the budget and
        emitted a continuation capsule). Deterministically rewrite the marker to
        [OWNER_MAINTENANCE BLOCKED]. Returns True if it changed anything."""
        try:
            if not blocked or not summary_path.exists():
                return False
            text = summary_path.read_text(encoding="utf-8", errors="replace")
            if "[OWNER_MAINTENANCE COMPLETE]" not in text:
                return False
            new_text = text.replace(
                "[OWNER_MAINTENANCE COMPLETE]",
                "[OWNER_MAINTENANCE BLOCKED] (reconciled: run ended blocked, not complete)",
            )
            atomic_write_text(summary_path, new_text, encoding="utf-8")
            return True
        except Exception:
            return False

    def _reconcile_devmode_summary_marker(self, final_text: str) -> None:
        """If the model's terminal answer is [OWNER_MAINTENANCE BLOCKED], make summary.md agree —
        a blocked run must never leave a [OWNER_MAINTENANCE COMPLETE] in its summary."""
        try:
            from ..self_maintenance.devmode_closeout import _owner_maintenance_terminal_prefix_text
            text = _owner_maintenance_terminal_prefix_text(final_text) or ""
            if not text.startswith("[OWNER_MAINTENANCE BLOCKED]"):
                return
            target = getattr(self, "_active_devmode_session_dir", None)
            if target is None:
                return
            changed = AgentTaskBoard._reconcile_summary_terminal_marker(Path(target) / "summary.md", blocked=True)
            # A blocked terminal must leave the manifest status="blocked" — it can never
            # read "complete" (acceptance criterion 7).
            self._write_devmode_manifest_record(
                status="blocked",
                reconciliations={"summary_terminal_marker": "changed" if changed else "ok"},
            )
        except Exception:
            pass

    @staticmethod
    def _final_should_complete_task(task: object) -> bool:
        return task_evidence.final_should_complete_task(task)

    @staticmethod
    def _final_report_task_id(task_board: TaskBoard) -> str:
        return task_evidence.final_report_task_id(task_board)
