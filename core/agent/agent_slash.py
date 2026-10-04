"""MO agent slash-command mixin."""

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import os
import time
from pathlib import Path
import traceback

from ..runtime.backend_monitor import get_monitor
from ..utils.number_utils import cache_hit_percentage
from ..state.paths import LEARNING_DB_PATH, PROFILE_DB_PATH, PROFILE_PROSE_FILES, resolve_state_path
from ..provider.provider import load_config, clean_provider_error, requested_output_token_limit
from ..profile import Profile, format_profile_time
from ..session.session_closeout import (
    build_session_closeout,
    closeout_meta,
    render_session_closeout,
    write_session_closeout,
)
from ..gates.consistency_boundary import (
    check_consistency_boundary,
    emit_consistency_boundary,
    render_consistency_boundary,
)
from .slash_result import SlashCommandResult, command_control, command_result


def slash_command_surface_block(command: str, rest: str, *, surface: str) -> str:
    """Fail closed when a non-terminal surface requests local administration."""
    if str(surface or "terminal").strip().lower() in {"terminal", "tui", "native_terminal"}:
        return ""
    if str(command or "").strip().lower() == "/game":
        return (
            "Game Collaboration is available only from MO Terminal. "
            "No project record or Desktop setting was changed."
        )
    if str(command or "").strip().lower() == "/mail":
        action = str(rest or "").strip().split(" ", 1)[0].lower() or "status"
        if action in {"connect", "disconnect", "sync"}:
            return f"MO Gmail `{action}` is available only from a trusted MO terminal."
        return ""
    if str(command or "").strip().lower() != "/everywhere":
        return ""
    action, _, tail = str(rest or "").strip().partition(" ")
    action = action.lower() or "status"
    arguments = set(tail.lower().split())
    terminal_only = action in {"pair", "devices", "disable"}
    terminal_only = terminal_only or (action in {"setup", "reconcile"} and "--confirm" in arguments)
    if not terminal_only:
        return ""
    return (
        f"MO Everywhere `{action}` is available only from a trusted MO terminal. "
        "No local setting, registry, device, or pairing grant was changed."
    )


class AgentSlashCommands:
    """Slash-command dispatch and handlers for the MO Agent."""

    def process_slash_command(self, user_input: str, *, surface: str = "terminal") -> SlashCommandResult | None:
        """Handle one registered command and return explicit presentation semantics."""
        from interface.command_registry import resolve_slash_command, slash_command_spec

        text = user_input.strip()
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        requested = parts[0].lower()
        cmd = resolve_slash_command(requested)
        rest = parts[1] if len(parts) > 1 else ""
        spec = slash_command_spec(cmd)
        surface_block = slash_command_surface_block(cmd, rest, surface=surface)
        if surface_block:
            return command_result(surface_block, kind="error")

        # The registry is the sole built-in inventory. Handler names are derived
        # from registered roots, so execution cannot drift behind a second table.
        handler = getattr(self, spec.handler_name, None) if spec else None
        if handler:
            result = command_result(handler(rest), kind=spec.presentation if spec else "report")
            # Command root only: arguments can contain private paths, URLs, or ids.
            try:
                monitor = get_monitor()
                if monitor:
                    monitor.emit("slash_command", {"command": cmd, "has_args": bool(rest.strip()), "handled": True})
            except Exception:
                pass
            return result
        try:
            from ..local_extensions import dispatch_slash

            extension_result = dispatch_slash(self, cmd, rest)
        except Exception:
            extension_result = None
        if extension_result is not None:
            try:
                monitor = get_monitor()
                if monitor:
                    monitor.emit("slash_command", {"command": cmd, "has_args": bool(rest.strip()), "handled": True})
            except Exception:
                pass
            return command_result(extension_result, kind=spec.presentation if spec else "report")
        return None

    def _cmd_exit(self, _rest: str) -> SlashCommandResult:
        return command_control("exit")

    def _file_transfer_targets(self) -> tuple[list[dict[str, str]], bool]:
        from ..transfer.addressing import transfer_targets

        return transfer_targets(getattr(self, "config", {}) or {})

    def _cmd_send(self, rest: str) -> str:
        """Start one resumable transfer. Syntax: /send <path> :: <target>."""
        try:
            targets, hub_local = self._file_transfer_targets()
        except Exception as exc:
            from core.transfer import safe_transfer_error

            return f"[TRANSFER] unavailable: {safe_transfer_error(exc)}"
        if not targets:
            return (
                "[TRANSFER] disabled or unavailable. Enable `file_transfer.enabled` "
                "and pair a device with the exact `file_transfer` scope."
            )
        raw = str(rest or "").strip()
        if "::" not in raw:
            choices = "\n".join(
                f"- {item['label']} [{item['device_id']}]"
                for item in targets
            )
            return (
                "[TRANSFER] use `/send <path> :: <device label-or-id>`.\n"
                + choices
            )
        path_text, target_text = (part.strip() for part in raw.rsplit("::", 1))
        if not path_text or not target_text:
            return "[TRANSFER] use `/send <path> :: <device label-or-id>`."
        from ..transfer.addressing import resolve_transfer_target

        try:
            target = resolve_transfer_target(target_text, targets)
        except Exception as exc:
            from core.transfer import safe_transfer_error

            return f"[TRANSFER] {safe_transfer_error(exc)}"
        source = Path(path_text).expanduser()
        if not source.is_absolute():
            source = Path(getattr(self, "project_cwd", os.getcwd())) / source
        try:
            source = source.resolve(strict=True)
        except OSError:
            return "[TRANSFER] source file was not found."
        from ..tooling.sandbox import path_allowed

        roots = (
            self._effective_allowed_roots()
            if hasattr(self, "_effective_allowed_roots")
            else getattr(self, "allowed_roots", None)
        )
        if not source.is_file() or not path_allowed(str(source), roots):
            return "[TRANSFER] source must be a readable file inside an allowed root."
        thread = getattr(self, "_file_transfer_thread", None)
        if thread is not None and thread.is_alive():
            return "[TRANSFER] another file transfer is already active in this terminal."

        def invalidate() -> None:
            surface = getattr(self, "tui", None)
            app = getattr(surface, "_app", None)
            if app is not None:
                try:
                    app.invalidate()
                except Exception:
                    pass

        def progress(sent: int, total: int) -> None:
            percent = int((max(0, sent) * 100) / max(1, total))
            self._file_transfer_status = (
                f"Sending {source.name} to {target['label']} · {percent}%"
            )
            self._file_transfer_status_until = time.time() + 30.0
            invalidate()

        def run() -> None:
            try:
                from core.transfer import TransferOutbox

                result = TransferOutbox(
                    getattr(self, "config", {}) or {}
                ).send_now(
                    source,
                    target_device_id=target["device_id"],
                    target_label=target["label"],
                    source_surface="terminal",
                    hub_local=hub_local,
                    on_progress=progress,
                )
                state = (
                    "hub accepted"
                    if result.state == "done"
                    else f"queued for retry · {result.error}"
                )
                self._file_transfer_status = (
                    f"{source.name} → {target['label']} · {state}"
                )
            except Exception as exc:
                from core.transfer import safe_transfer_error

                self._file_transfer_status = (
                    "Transfer blocked · " + safe_transfer_error(exc)[:120]
                )
            self._file_transfer_status_until = time.time() + 15.0
            invalidate()

        import threading

        self._file_transfer_status = (
            f"Preparing {source.name} → {target['label']}"
        )
        self._file_transfer_status_until = time.time() + 30.0
        self._file_transfer_thread = threading.Thread(
            target=run,
            name="mo-file-transfer",
            daemon=True,
        )
        self._file_transfer_thread.start()
        return f"[TRANSFER] started {source.name} → {target['label']}."

    def _cmd_transfers(self, rest: str) -> str:
        try:
            _targets, hub_local = self._file_transfer_targets()
            from core.transfer import TransferOutbox

            outbox = TransferOutbox(getattr(self, "config", {}) or {})
            parts = str(rest or "").strip().split(maxsplit=2)
            if parts and parts[0].lower() in {"accept", "cancel", "retry"}:
                if len(parts) < 2 or (
                    parts[0].lower() != "accept" and len(parts) != 2
                ):
                    return (
                        "[TRANSFER] use /transfers <accept|cancel|retry> "
                        "<transfer-id> [destination-path]."
                    )
                action, transfer_id = parts[0].lower(), parts[1]
                destination_path = parts[2] if len(parts) > 2 else None
                roots = (
                    self._effective_allowed_roots()
                    if hasattr(self, "_effective_allowed_roots")
                    else getattr(self, "allowed_roots", None)
                )
                if action == "accept" and destination_path:
                    requested = Path(destination_path).expanduser()
                    if not requested.is_absolute():
                        requested = Path(
                            getattr(self, "project_cwd", os.getcwd())
                        ) / requested
                    destination_path = str(requested.resolve(strict=False))
                    from ..tooling.sandbox import path_allowed

                    if not path_allowed(destination_path, roots):
                        return (
                            "[TRANSFER] destination must be inside an allowed root."
                        )
                try:
                    local = outbox.get(transfer_id)
                except Exception:
                    local = None
                if action == "retry":
                    if local is None:
                        return "[TRANSFER] only sender-custody outbox IDs can be retried."
                    result = outbox.retry(transfer_id)
                elif action == "accept":
                    if local is not None:
                        return "[TRANSFER] sender-custody outbox IDs cannot be accepted."
                    if hub_local:
                        from core.transfer import TransferService

                        result = TransferService(
                            getattr(self, "config", {}) or {}
                        ).receive_local(
                            transfer_id,
                            "hub",
                            destination_path=destination_path,
                            allowed_roots=roots,
                        )
                    else:
                        from mo_everywhere.client import TransferClient

                        result = TransferClient(
                            getattr(self, "config", {}) or {}
                        ).receive_one(
                            transfer_id,
                            destination_path=destination_path,
                            allowed_roots=roots,
                        )
                elif local is not None:
                    result = outbox.cancel(transfer_id)
                elif hub_local:
                    from core.transfer import TransferService

                    result = TransferService(
                        getattr(self, "config", {}) or {}
                    ).cancel(transfer_id, "hub")
                else:
                    from mo_everywhere.client import TransferClient

                    result = TransferClient(
                        getattr(self, "config", {}) or {}
                    ).cancel(transfer_id)
                state = (
                    result.state
                    if hasattr(result, "state")
                    else str(result.get("state") or action)
                )
                return f"[TRANSFER] {transfer_id} · {state}."

            pending = [
                item.public()
                for item in outbox.list_pending(limit=20)
            ]
            actor_device_id = "hub"
            if hub_local:
                from core.transfer import TransferService

                records = [
                    item.public()
                    for item in TransferService(
                        getattr(self, "config", {}) or {}
                    ).list_for("hub", purposes=("cargo",), limit=20)
                ]
            else:
                from mo_everywhere.client import TransferClient

                transfer_client = TransferClient(
                    getattr(self, "config", {}) or {}
                )
                actor_device_id = transfer_client.device_id
                records = transfer_client.transfers(limit=20)
            records = pending + records
        except Exception as exc:
            from core.transfer import safe_transfer_error

            return f"[TRANSFER] unavailable: {safe_transfer_error(exc)}"
        if not records:
            return "[TRANSFER] no recent incoming or outgoing files."
        lines = ["[TRANSFER] recent files:"]
        for item in records:
            transfer_id = str(
                item.get("outbox_id") or item.get("transfer_id") or "?"
            )
            direction = str(item.get("direction") or "").strip() or (
                "out"
                if str(item.get("sender_device_id")) == actor_device_id
                else "in"
            )
            lines.append(
                f"- {transfer_id} · {direction} · {item.get('name') or 'file'} · "
                f"{item.get('state') or '?'} · "
                f"{int(float(item.get('progress') or 0) * 100)}%"
                + (
                    " · named path needs an explicit destination"
                    if item.get("destination") == "named_path"
                    else ""
                )
            )
        return "\n".join(lines)

    def _save_terminal_preferences(self, **changes: object) -> str:
        """Persist one interactive Terminal choice and return an honest UI suffix."""
        from ..state.preferences import RuntimePreferenceError, persist_terminal_preferences

        try:
            saved = persist_terminal_preferences(getattr(self, "config", {}) or {}, **changes)
        except (RuntimePreferenceError, OSError) as exc:
            return f" · current process only; preference not saved ({exc})"
        self.runtime_preferences = saved
        return " · saved for future terminals"

    def _cmd_show(self, rest: str) -> str:
        """Toggle and save transcript streams. /show [reasoning|tools|all] [on|off]."""
        flags = {"reasoning": "_show_reasoning", "tools": "_show_tool_activity"}
        surface = getattr(self, "tui", None)

        def current_value(attr: str) -> bool:
            if surface is not None and hasattr(surface, attr):
                return bool(getattr(surface, attr))
            return bool(getattr(self, attr, True))

        parts = rest.lower().split()
        if not parts:
            state = ", ".join(f"{k}={'on' if current_value(attr) else 'off'}" for k, attr in flags.items())
            return f"[SHOW] {state}. Usage: /show <reasoning|tools|all> [on|off]"
        target = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        keys = list(flags) if target == "all" else ([target] if target in flags else [])
        if not keys:
            return command_result(f"[SHOW] unknown: {target!r}. Use reasoning, tools, or all.", kind="error")
        if len(parts) > 2 or arg not in {"", "on", "off"}:
            return command_result("Usage: /show <reasoning|tools|all> [on|off]. Nothing changed.", kind="error")
        results = []
        for key in keys:
            attr = flags[key]
            current = current_value(attr)
            value = True if arg == "on" else False if arg == "off" else not current
            setattr(self, attr, value)
            if surface is not None:
                setattr(surface, attr, value)
            results.append(f"{key} {'ON' if value else 'OFF'}")
        app = getattr(surface, "_app", None) if surface is not None else getattr(self, "_app", None)
        if app:
            app.invalidate()
        suffix = self._save_terminal_preferences(
            show_reasoning=current_value(flags["reasoning"]),
            show_tools=current_value(flags["tools"]),
        )
        return command_result("[SHOW] " + ", ".join(results) + suffix, kind="notice")

    def _cmd_visualize(self, rest: str) -> str:
        """Render a file or directory as a diagram. Usage: /visualize <file-or-dir> [mermaid|ascii]."""
        parts = rest.split()
        if not parts:
            return "[VISUALIZE] usage: /visualize <file-or-dir> [mermaid|ascii]"
        fmt = "mermaid"
        if len(parts) > 1 and parts[-1].lower() in ("mermaid", "ascii"):
            fmt = parts[-1].lower()
            target = " ".join(parts[:-1])
        else:
            target = rest.strip()
        from pathlib import Path
        from core.visualize import visualize
        path = Path(target).expanduser()
        if path.is_dir():
            return visualize(str(path), kind="tree", format=fmt, allow_fs=True)
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return f"[VISUALIZE] cannot read {target}: {exc}"
            kind = {
                "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
                "md": "markdown", "markdown": "markdown",
            }.get(path.suffix.lower().lstrip("."), "auto")
            return visualize(text, kind=kind, format=fmt, allow_fs=True)
        return f"[VISUALIZE] not found: {target}"

    def _cmd_role(self, rest: str) -> str:
        """Activate a conversational role or dispatch a background role worker."""
        text = str(rest or "").strip()
        action, _, argument = text.partition(" ")
        action = action.casefold()
        argument = argument.strip()
        if action in {"activate", "use"}:
            if not argument:
                return "[ROLE] usage: /role activate <role-name>"
            return command_result(
                self._execute_role_work({"action": "activate", "role": argument}),
                kind="notice",
            )
        if action == "show" and not argument:
            return command_result(self._execute_role_work({"action": "show"}), kind="notice")
        if action == "status" and not argument:
            role = self._active_role()
            if role is None:
                return "[ROLE] no conversational role is active."
            label = str(getattr(role, "name", "") or getattr(role, "role", "role"))
            summary = f"[ROLE ACTIVE] {label} governs this conversation."
            if str(getattr(role, "role", "") or "").casefold() == "project-architect":
                team = self._execute_role_work({"action": "list"})
                work = self._execute_role_work({"action": "status"})
                return "\n\n".join((summary, team, work))
            return summary
        if action in {"off", "stop"} and not argument:
            return command_result(
                self._execute_role_work({"action": "off"}), kind="notice",
            )

        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            try:
                from core.skills import default_skill_roots, list_roles
                roots = default_skill_roots(
                    getattr(self, "project_cwd", None),
                    getattr(self, "runtime_home", None),
                    profile=getattr(self, "profile", None),
                    config=getattr(self, "config", None),
                )
                names = sorted({
                    skill.role for skill in list_roles(
                        roots,
                        profile=getattr(self, "profile", None),
                        project_cwd=getattr(self, "project_cwd", None),
                    )
                })
            except Exception:
                names = []
            available = (" Available roles: " + ", ".join(names)) if names else " No roles defined yet."
            return "[ROLE] usage: /role activate <role-name> | /role show | /role off | /role status | /role <role-name> <objective>." + available
        role_name, objective = parts[0].strip(), parts[1].strip()
        from core.worker import ensure_worker_runtime
        runtime = ensure_worker_runtime(self)
        record = runtime.start(objective=objective, source="user", role=role_name)
        if getattr(record, "state", "") == "blocked":
            return f"[ROLE BLOCKED] {getattr(record, 'note', '') or 'could not start role worker'}"
        return f"[ROLE STARTED] '{role_name}' worker (id {getattr(record, 'id', '?')}) on: {objective[:120]} · /status to follow"

    def _cmd_schedule(self, rest: str) -> str:
        """Manage persistent tasks locally. Natural requests use schedule_job."""
        from core.runtime.scheduler import format_scheduler_jobs, manage_scheduler_jobs

        text = str(rest or "").strip()
        verb, _, reference = text.partition(" ")
        if verb.lower() == "show" or (verb.lower() in {"remove", "delete"} and "--confirm" not in reference.split()):
            try:
                result = manage_scheduler_jobs(self, "list", {})
                job = next((job for job in result.get("jobs", []) if str(job.get("id")) == reference.strip()), None)
                if job is None:
                    return SlashCommandResult("Scheduled task not found. Select it again.", choices=(("/schedule list", "Back to scheduled tasks"),))
                job_id = str(job["id"])
                if verb.lower() in {"remove", "delete"}:
                    return SlashCommandResult(
                        f"Remove scheduled task?\n\n{job.get('name') or job_id}\n{job.get('prompt') or job.get('script') or ''}",
                        choices=((f"/schedule remove {job_id} --confirm", "Remove task"), (f"/schedule show {job_id}", "Cancel")),
                    )
                toggle = "pause" if job.get("enabled", True) else "resume"
                return SlashCommandResult(
                    format_scheduler_jobs({**result, "jobs": [job]})
                    + f"\n\nTask\n{job.get('prompt') or job.get('script') or ''}\n\nSchedule\n"
                    + " · ".join(f"{key}: {value}" for key, value in (job.get("schedule") or {}).items()),
                    title="Scheduled task",
                    choices=((f"/schedule edit {job_id} ", "Edit schedule/task…"),
                             (f"/schedule {toggle} {job_id}", toggle.title()),
                             (f"/schedule run {job_id}", "Run on next scheduler tick"),
                             (f"/schedule remove {job_id}", "Remove task…"),
                             ("/schedule list", "Back to scheduled tasks")),
                )
            except (TypeError, ValueError, OSError) as exc:
                return command_result(f"[SCHEDULE] {exc}", kind="error")
        if not text or text.lower() in {"list", "status"}:
            action, args = "list", {}
        else:
            action, _, tail = text.partition(" ")
            action = {"add": "create", "edit": "update", "delete": "remove"}.get(action.lower(), action.lower())
            tail = tail.strip()
            if action == "create":
                schedule, separator, prompt = tail.partition("::")
                if not separator:
                    return command_result("[SCHEDULE] usage: /schedule add <when> :: <task>", kind="error")
                args = {"schedule": schedule.strip(), "prompt": prompt.strip()}
            elif action == "update":
                reference, _, changes = tail.partition(" ")
                schedule, separator, prompt = changes.partition("::")
                args = {"job_id": reference}
                if schedule.strip():
                    args["schedule"] = schedule.strip()
                if separator:
                    args["prompt"] = prompt.strip()
            else:
                args = {"job_id": tail.removesuffix(" --confirm") if action == "remove" else tail}
        try:
            result = manage_scheduler_jobs(self, action, args)
            choices = tuple((f"/schedule show {job['id']}", str(job.get("name") or job.get("prompt") or job["id"])) for job in result.get("jobs", [])) if action == "list" else (("/schedule list", "Back to scheduled tasks"),)
            return SlashCommandResult(format_scheduler_jobs(result), choices=choices + (("/schedule add ", "Add a scheduled task…"),))
        except (TypeError, ValueError, OSError) as exc:
            return command_result(f"[SCHEDULE] {exc}", kind="error")

    def _cmd_hints(self, rest: str) -> str:
        """Toggle rotating hint tips on the idle line."""
        rest = rest.strip().lower()
        if rest not in {"", "on", "off"}:
            return command_result("Usage: /hints [on|off]. Nothing changed.", kind="error")
        current = getattr(self, "_hints_enabled", True)

        if rest == "on":
            target = True
        elif rest == "off":
            target = False
        else:
            target = not current

        self._hints_enabled = target
        status = "ON" if target else "OFF"
        suffix = self._save_terminal_preferences(hints=target)
        return command_result(f"[HINTS {status}] Idle line will {('show rotating hints' if target else 'show normal idle')}.{suffix}", kind="notice")

    def _cmd_skin(self, rest: str) -> str:
        """Switch to one registered UI skin."""
        from interface.theming import available_skins, get_skin_name, set_skin
        name = (rest or "").strip().lower()
        available = available_skins()
        if not name:
            return command_result(f"Skin: {get_skin_name()} (available: {', '.join(available)})", kind="report")
        if name not in available:
            return command_result(f"Unknown skin: {name!r}. Available: {', '.join(available)}", kind="error")
        set_skin(name)
        # Apply to the live TUI surface immediately.
        if hasattr(self, "tui") and self.tui is not None:
            app = getattr(self.tui, "_app", None)
            if app is not None:
                from interface.theme import build_tui_style
                from interface.terminal_host import sync_terminal_background_to_skin
                app.style = build_tui_style()
                sync_terminal_background_to_skin(output=getattr(app, "output", None))
                app.invalidate()
        return f"[SKIN] Switched to {name}."

    def _cmd_learning(self, rest: str) -> str:
        """Show deterministic learning/system health status."""
        raw = (rest or "status").strip()
        parts = raw.split(maxsplit=1)
        sub = (parts[0].lower() if parts else "status") or "status"
        arg = parts[1].strip() if len(parts) > 1 else ""
        cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
        from core.learning.proactive_learning import _resolve_suggestions_path

        suggestions_path = _resolve_suggestions_path(profile=getattr(self, "profile", None), config=cfg)
        learning_db_path = getattr(getattr(self, "memory", None), "path", None) or resolve_state_path(LEARNING_DB_PATH, cfg)

        from core.learning.review import describe_review_item, learning_review_items
        from core.agent.slash_result import SlashCommandResult

        def collect_pending_review_items() -> list[tuple[str, object]]:
            return learning_review_items(getattr(self, "profile", None), config=cfg)

        def selected_review_item(reference: str):
            source_id, _, revision = reference.partition(" ")
            items = collect_pending_review_items() + learning_review_items(
                getattr(self, "profile", None), config=cfg, active=True,
            )
            return next((item for item in items if (
                describe_review_item(item)["ref"] == reference if revision else review_item_id(item) == source_id
            )), None)

        def pending_review_items(*, refresh: bool = False) -> list[tuple[str, object]]:
            cached = getattr(self, "_learning_review_items", None)
            if not refresh and isinstance(cached, list):
                return cached
            items = collect_pending_review_items()
            self._learning_review_items = items
            return items

        def review_item_id(item: tuple[str, object]) -> str:
            item_kind, value = item
            if item_kind == "suggestion":
                return str(value.representative.id)
            if item_kind == "workflow_skill":
                return value.candidate_id
            return str(value.get("id") or "")

        def learning_overview(*, expire: bool = True) -> str:
            from core.learning.proactive_learning import (
                expire_stale_suggestions,
                render_learning_overview,
                suggestion_review_clusters,
            )
            from core.learning.workflow_learning import active_workflow_skills, pending_workflow_candidates

            expired = expire_stale_suggestions(path=suggestions_path) if expire else 0
            pending_clusters, active_clusters = suggestion_review_clusters(path=suggestions_path)
            workflows = pending_workflow_candidates(getattr(self, "profile", None))
            self._learning_review_items = (
                [("suggestion", item) for item in pending_clusters]
                + [("workflow", item) for item in workflows]
            )
            return render_learning_overview(
                pending_clusters,
                active_clusters,
                workflow_candidates=workflows,
                active_workflows=[describe_review_item(("workflow_skill", skill)) for skill in active_workflow_skills(getattr(self, "profile", None), config=cfg)],
                auto_enabled=bool((cfg.get("learning") or {}).get("auto_promote", DEFAULT_PREFERENCES["learning.auto_promote"])),
                expired_count=expired,
            )

        if sub == "more":
            from interface.command_registry import dynamic_argument_items

            return SlashCommandResult(
                "Learning runs after eligible turns. These actions are optional maintenance and source imports.",
                title="More learning actions",
                choices=tuple((f"/learning {value}", label) for value, label, _description, _kind in dynamic_argument_items("/learning more", agent=self)),
            )
        if sub.startswith("suggest"):
            from core.learning.proactive_learning import mine_learning_suggestions, write_learning_suggestions

            suggestions = mine_learning_suggestions(memory_path=learning_db_path)
            if suggestions:
                write_learning_suggestions(suggestions, path=suggestions_path)
            return learning_overview(expire=False)
        if sub in {"status", "pending", "list", "review"}:
            return learning_overview()
        back = (("/learning review", "Back"),)
        if sub in {"details", "detail", "show"}:
            items = pending_review_items() if arg.isdigit() else []
            target = int(arg) - 1 if arg.isdigit() else -1
            selected = items[target] if 0 <= target < len(items) else selected_review_item(arg)
            if selected is None:
                return SlashCommandResult("This item changed or is no longer available. Open learning to review the current list.", title="Learning", choices=back)
            detail = describe_review_item(selected)
            choices = [] if detail["state"] == "active" else [(f"/learning confirm {detail['ref']}", "Approve")]
            choices += [(f"/learning dismiss {detail['ref']}", "Undo learning" if detail["state"] == "active" else "Dismiss"), *back]
            return SlashCommandResult(detail["details"], title="Learning review", choices=tuple(choices))
        if sub in {"confirm", "dismiss"}:
            selected_number = arg if arg.isdigit() else ""
            selected_kind = ""
            reviewed = " " in arg.strip()
            if reviewed:
                selected = selected_review_item(arg)
                if selected is None or (sub == "confirm" and describe_review_item(selected)["state"] != "pending"):
                    return SlashCommandResult("This item changed or was already reviewed. Nothing changed; review the current item again.", title="Learning", choices=back)
                selected_kind = selected[0]
                arg = review_item_id(selected)
            if selected_number:
                items = pending_review_items()
                index = int(selected_number) - 1
                if not 0 <= index < len(items):
                    return f"No pending learning item {selected_number}. Run /learning to see the current list."
                selected_kind, selected_item = items[index]
                arg = (
                    selected_item.representative.id
                    if selected_kind == "suggestion"
                    else str(selected_item.get("id") or "")
                )
            if arg.strip().lower().startswith("workflow-candidate:"):
                from core.learning.workflow_learning import dismiss_workflow_candidate, promote_workflow_candidate

                if sub == "dismiss":
                    changed = dismiss_workflow_candidate(getattr(self, "profile", None), arg, config=cfg)
                    verb = "Dismissed" if changed else "Could not dismiss"
                else:
                    result = promote_workflow_candidate(
                        getattr(self, "profile", None),
                        f"approve workflow candidate {arg}",
                        "approved from /learning review",
                    )
                    changed = bool(result.get("promoted"))
                    verb = "Approved" if changed else "Could not approve"
                label = f"learning item {selected_number}" if selected_number else "workflow suggestion"
                self._learning_review_items = None
                message = f"{verb} {label}." + (
                    " It is now active." if changed and sub == "confirm" else
                    " It is no longer active." if changed and sub == "dismiss" and selected_kind == "workflow_skill" else ""
                )
                return SlashCommandResult(message, title="Learning", choices=back) if reviewed else message
            response = self._cmd_learning_review(sub, arg)
            self._learning_review_items = None
            if reviewed:
                from core.learning.proactive_learning import read_learning_suggestions

                expected = "confirmed" if sub == "confirm" else "dismissed"
                changed = any(row.id == arg and row.status == expected for row in read_learning_suggestions(path=suggestions_path, include_inactive=True))
                changed = changed and "could not be retired" not in response
                message = ("Approved. It is now active." if sub == "confirm" else "Dismissed. It is no longer active.") if changed else "Could not finish this change. Review the current learning state before trying again."
                return SlashCommandResult(message, kind="report" if changed else "error", title="Learning", choices=back)
            if (
                selected_number
                and "could not be retired" not in response
                and (response.startswith("Confirmed cluster:") or response.startswith("Dismissed cluster:"))
            ):
                verb = "Approved" if sub == "confirm" else "Dismissed"
                suffix = " It is now active." if sub == "confirm" else ""
                return f"{verb} learning item {selected_number}.{suffix}"
            return response
        if sub in {"reconcile", "consolidate"}:
            if arg.strip().lower() in {"deep", "dialectic", "model"}:
                return self._cmd_learning_reconcile_deep(cfg, suggestions_path)
            from core.learning.proactive_learning import materialize_confirmed_learning_clusters, reconcile_confirmed_learnings
            from core.learning.workflow_learning import reconcile_workflow_candidates

            profile = getattr(self, "profile", None)
            res = reconcile_confirmed_learnings(
                path=suggestions_path,
                profile=profile,
                config=cfg,
            )
            workflow_res = reconcile_workflow_candidates(profile)
            profile_res = (
                profile.reconcile_profile_learning()
                if profile is not None and hasattr(profile, "reconcile_profile_learning")
                else {
                    "entries_before": 0,
                    "entries_after": 0,
                    "duplicates_removed": 0,
                    "sections_removed": 0,
                }
            )
            packs = materialize_confirmed_learning_clusters(
                profile,
                path=suggestions_path,
                runtime_home=getattr(self, "runtime_home", None),
                config=cfg,
            )
            retirement_line = (
                f"  blocked retirements: {res.get('retirement_blocked', 0)} pack(s) remain active; "
                "their ledger rows stayed confirmed.\n"
                if res.get("retirement_blocked") else ""
            )
            return (
                f"Reconciled confirmed learnings: {res['confirmed_before']} confirmed -> "
                f"{res['clusters']} distinct cluster(s); {res['superseded']} confirmed and "
                f"{res.get('pending_superseded', 0)} pending duplicate(s) superseded.\n"
                f"{retirement_line}"
                f"  workflow queue: {workflow_res['structural_retired']} mechanical graph hint(s) retired; "
                f"{workflow_res['duplicates_retired']} duplicate row(s) retired.\n"
                f"  profile ledger: {profile_res['duplicates_removed']} duplicate entry(s) removed "
                f"across {profile_res['sections_removed']} section(s) "
                f"({profile_res['entries_before']} -> {profile_res['entries_after']}).\n"
                f"  skill coverage: {packs['covered']}/{packs['clusters']} cluster(s); {packs['created']} backfilled.\n"
                "  (deterministic local consolidation; no provider call)\n"
                "  /learning reconcile deep — run the dialectic operator-model pass (one cheap call)"
            )
        if sub in {"inspect", "use", "promote", "candidates", "sources", "imports"}:
            return self._cmd_learning_import(sub, arg, cfg)
        return (
            f"Unknown learning option: {sub}.\n"
            "Use /learning, /learning details <number>, /learning confirm <number>, "
            "or /learning dismiss <number>."
        )

    def _cmd_skills(self, _rest: str = "") -> str:
        """List exact loader-visible profile skills without a provider call."""
        from core.skills import default_skill_roots, render_skill_inventory

        cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
        roots = default_skill_roots(
            getattr(self, "project_cwd", None),
            getattr(self, "runtime_home", None),
            profile=getattr(self, "profile", None),
            config=cfg,
            maintain=False,
        )
        return render_skill_inventory(
            roots,
            profile=getattr(self, "profile", None),
            config=cfg,
            selected=_rest,
        )

    def _cmd_learning_import(self, sub: str, arg: str, cfg: dict) -> str:
        """Skill-import subcommands: inspect/use/promote/candidates.

        Network is OFF by default; enable per source via config
        `skills.import.network_allowed`. Imported material is untrusted until
        explicitly promoted through the existing skill writer.
        """
        from core.skills.importing import pipeline as _imp
        profile = getattr(self, "profile", None)
        net = bool(((cfg.get("skills") or {}).get("import") or {}).get("network_allowed", False))

        if sub in {"candidates", "sources", "imports"}:
            st = _imp.status(profile=profile, config=cfg)
            cands, imps = st["candidates"], st["imported_skills"]
            lines = ["Skill imports:", f"  candidates: {len(cands)}"]
            for c in cands[:10]:
                state = "approved" if c.get("approved") else "pending"
                lines.append(f"    - {c['candidate_id']} {c.get('label', '')} ({c.get('kind', '')}) [{state}]")
            lines.append(f"  imported skills: {len(imps)}")
            for s in imps[:10]:
                upd = " [update available]" if s.get("update_available") else ""
                lines.append(f"    - {s['skill']} <- {s.get('source_url') or s.get('source_kind')}{upd}")
            return "\n".join(lines)

        if not arg:
            return f"Usage: /learning {sub} <github-repo | docs-url | llms.txt | local-path | candidate-id>"

        if sub == "inspect":
            res = _imp.inspect(arg, profile=profile, config=cfg, network_allowed=net)
            if not res.get("ok"):
                warns = f" ({'; '.join(res.get('warnings', []))})" if res.get("warnings") else ""
                return f"inspect failed: {res.get('error', '')}{warns}"
            warn = f"\n  warnings: {'; '.join(res['warnings'])}" if res.get("warnings") else ""
            block = " [BLOCK findings — resolve before promotion]" if res.get("has_block") else ""
            return (
                f"Inspected {res['name']} ({res['kind']}): candidate {res['candidate_id']}, "
                f"{res['files']} file(s){block}.{warn}\n"
                f"  bundle: {res['bundle_dir']}\n"
                f"  conflicts: {len(res.get('conflicts', []))} structural\n"
                f"  -> review, then: /learning promote {res['candidate_id']}"
            )

        if sub == "use":
            res = _imp.use(arg, profile=profile, config=cfg, network_allowed=net)
            if not res.get("ok"):
                return f"use failed: {res.get('error', '')}"
            holder = getattr(self, "session", None) or self
            setattr(holder, "_pending_skill_import_context", str(res["context"] or "")[:2400])
            return (
                f"Temporary source staged ({res['files']} file(s)). "
                "It will be used once on your next provider turn and is not installed."
            )

        if sub == "promote":
            res = _imp.promote(arg, profile=profile, config=cfg)
            if not res.get("ok"):
                return f"promote failed: {res.get('error', '')}"
            return f"Promoted candidate {arg} -> {res['skill_path']}\n  manifest: {res['skill_manifest']}"

        return f"unknown import subcommand: {sub}"

    def _cmd_learning_review(self, action: str, suggestion_id: str) -> str:
        """Confirm/dismiss learning suggestions — cluster-wide, not item-by-item."""
        clean_id = str(suggestion_id or "").strip()
        if not clean_id:
            return f"Usage: /learning {action} <suggestion-id>"
        from core.learning.proactive_learning import (
            read_learning_suggestions,
            reconcile_confirmed_learnings,
            resolve_cluster_ids,
            update_learning_suggestion_status,
        )

        cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
        from core.learning.proactive_learning import _resolve_suggestions_path

        suggestions_path = _resolve_suggestions_path(profile=getattr(self, "profile", None), config=cfg)
        member_ids = resolve_cluster_ids(
            clean_id,
            path=suggestions_path,
            include_confirmed=action == "dismiss",
        )
        if not member_ids:
            return f"No active learning suggestion found: {clean_id}"
        if action == "dismiss":
            from core.skills import materialized_learning_ids, retire_skill_packs_by_candidate_ids

            retired = retire_skill_packs_by_candidate_ids(
                getattr(self, "profile", None),
                member_ids,
                config=cfg,
            )
            still_active = set(member_ids).intersection(
                materialized_learning_ids(getattr(self, "profile", None), config=cfg)
            )
            updated = sum(
                1 for mid in member_ids
                if mid not in still_active
                and update_learning_suggestion_status(mid, "dismissed", path=suggestions_path)
            )
            blocked = (
                f", {len(still_active)} pack(s) could not be retired and stayed confirmed"
                if still_active else ""
            )
            return (
                f"Dismissed cluster: {updated} suggestion(s), retired {len(retired)} "
                f"derived pack(s){blocked} ({clean_id})"
            )
        suggestions = {item.id: item for item in read_learning_suggestions(path=suggestions_path)}
        representative = suggestions.get(clean_id) or next((suggestions[mid] for mid in member_ids if mid in suggestions), None)
        representative_id = str(getattr(representative, "id", "") or "")
        updated = int(bool(
            representative_id
            and update_learning_suggestion_status(representative_id, "confirmed", path=suggestions_path)
        ))
        if updated:
            reconcile_confirmed_learnings(
                path=suggestions_path,
                profile=getattr(self, "profile", None),
                config=cfg,
            )
        skill_path = ""
        if representative:
            try:
                from core.skills import write_skill_pack_from_suggestion

                row = representative.as_dict()
                row["status"] = "confirmed"
                skill_path = str(write_skill_pack_from_suggestion(
                    row,
                    profile=getattr(self, "profile", None),
                    runtime_home=getattr(self, "runtime_home", None),
                    config=cfg,
                ))
            except Exception:
                traceback.print_exc()
        skill_line = f"\nSkill pack: {skill_path}" if skill_path else ""
        cluster_count = len(member_ids) if updated else 0
        return f"Confirmed cluster: {cluster_count} suggestion(s) - now part of MO's skills ({clean_id}){skill_line}"

    def _cmd_learning_reconcile_deep(self, cfg: dict, suggestions_path) -> str:
        """Dialectic operator-model reconciliation - one no-tools provider call.

        Gathers confirmed learnings + profile prose and asks the no-tools model to
        assess -> self-audit -> reconcile into a single clean operator model. The
        result is written as a REVIEW PROPOSAL under profile state; MO never edits
        learning.md/behavior.md itself."""
        from core.learning.profile_reconcile import (
            build_dialectic_prompt,
            gather_reconcile_inputs,
            has_reconcile_inputs,
            write_reconcile_proposal,
        )
        inputs = gather_reconcile_inputs(getattr(self, "profile", None), suggestions_path=suggestions_path, config=cfg)
        if not has_reconcile_inputs(inputs):
            return "Nothing to reconcile yet: no confirmed learnings or profile prose."
        system, user = build_dialectic_prompt(inputs)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            with self.provider_scope("learning_reconcile"):
                response, _provider = self.complete_no_tools(
                    surface="learning_reconcile",
                    request="learning-reconcile",
                    messages=messages,
                    max_tokens=min(int(self.max_tokens or 1400), 1400),
                )
            text = str(getattr(response, "content", "") or "").strip()
        except Exception as exc:
            return f"Dialectic reconcile failed: {clean_provider_error(str(exc))}"
        if not text:
            return "Dialectic reconcile produced no output."
        path = write_reconcile_proposal(text, config=cfg)
        if not path:
            return "Reconcile output tripped the secret detector; nothing written."
        preview = "\n".join(text.splitlines()[:12])
        return (
            "Dialectic operator-model reconciliation (PROPOSAL - review, then apply by hand; "
            "MO did not edit your profile):\n"
            f"  {path}\n\n{preview}\n  ...(full proposal at the path above)"
        )

    def _cmd_structural_graph(self, rest: str) -> str:
        """Show or build MO's optional structural code graph."""
        from core.graph.structural_graph import build_structural_graph_isolated, graph_status

        raw = (rest or "").strip()
        sub = raw.split(maxsplit=1)[0].lower() if raw else "status"
        arg = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) > 1 else ""
        graph_root = self.project_cwd
        if sub in {"status", ""}:
            status = graph_status(graph_root)
            lines = ["Structural graph:"]
            if status.get("available"):
                lines.append(f"  available: yes ({status.get('source_kind')})")
                lines.append(f"  nodes:     {status.get('nodes', 0)}")
                lines.append(f"  edges:     {status.get('edges', 0)}")
                lines.append(f"  path groups: {status.get('groups', status.get('communities', 0))}")
                lines.append(f"  trust:     {status.get('trust') or 'unknown'}")
                lines.append(f"  quality:   {status.get('quality') or {}}")
                if status.get("confidence_breakdown"):
                    lines.append(f"  confidence: {status.get('confidence_breakdown')}")
                if status.get("refreshing"):
                    lines.append(
                        "  freshness: refresh in progress; the existing snapshot remains available"
                    )
                elif status.get("stale"):
                    reasons = ", ".join(str(item) for item in list(status.get("stale_reasons") or [])[:3]) or "unknown"
                    lines.append(
                        "  freshness: stale "
                        f"({reasons}; {int(status.get('stale_files') or 0)} changed/new/removed file(s)); "
                        "run /structural-graph refresh"
                    )
            else:
                lines.append("  available: no")
                lines.append("  run:       /structural-graph build")
            lines.append(f"  path:      {status.get('path')}")
            lines.append("  proof:     orientation only; file reads/tests still decide truth")
            return "\n".join(lines)
        if sub in {"build", "refresh", "rebuild", "setup"}:
            result = build_structural_graph_isolated(graph_root, wait_for_existing=True)
            if result.get("built"):
                return (
                    "Structural graph built:\n"
                    f"  path:  {result.get('path')}\n"
                    f"  files: {result.get('files', 0)}\n"
                    f"  nodes: {result.get('nodes', 0)}\n"
                    f"  edges: {result.get('edges', 0)}"
                )
            if result.get("status") == "busy":
                return (
                    "Structural graph refresh is already in progress; "
                    "the existing snapshot was kept."
                )
            return f"Structural graph not built: {result.get('reason') or 'unknown'}"
        if sub == "export":
            import shlex
            from core.graph.structural_graph import export_sanitized_graph

            try:
                tokens = shlex.split(arg)
            except ValueError as exc:
                return f"Use: /structural-graph export [path] [--compat]\n  parse error: {exc}"
            compatibility = any(token in {"--compat", "--compatibility"} for token in tokens)
            paths = [token for token in tokens if token not in {"--compat", "--compatibility"}]
            result = export_sanitized_graph(graph_root, output=paths[0] if paths else None, compatibility=compatibility)
            if not result.get("exported"):
                return f"Structural graph export failed: {result.get('reason') or 'unknown'}"
            return (
                "Structural graph sanitized export written:\n"
                f"  path:  {result.get('path')}\n"
                f"  nodes: {result.get('nodes', 0)}\n"
                f"  links: {result.get('links', 0)}\n"
                f"  compatibility: {result.get('compatibility')}\n"
                "  proof: orientation only; exported by explicit command"
            )
        if sub in {"explain", "node"}:
            if not arg:
                return "Use: /structural-graph explain <node|symbol|file>"
            from core.graph.query import explain

            result = explain(arg, cwd=graph_root)
            if not result.get("available"):
                return f"Structural graph explain unavailable: {result.get('reason') or 'graph unavailable'}"
            if not result.get("matched"):
                if result.get("ambiguous"):
                    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
                    names = ", ".join(str(item.get("qualified_name") or item.get("id")) for item in candidates if isinstance(item, dict))
                    return f"Structural graph: {arg!r} is ambiguous; use a qualified name. Candidates: {names}"
                return f"Structural graph: no node matched {arg!r}."
            node = result.get("node") if isinstance(result.get("node"), dict) else {}
            lines = [
                "Structural graph explain (orientation only):",
                f"  node: {node.get('label') or node.get('id')}",
                f"  file: {node.get('source_file') or '-'} {node.get('source_location') or ''}".rstrip(),
                f"  path group: {node.get('community_label') or node.get('community')}",
                f"  match: {result.get('match_type')}",
                f"  degree: {result.get('degree', 0)}",
                f"  confidence: {result.get('confidence_breakdown') or {}}",
            ]
            for edge in (result.get("neighbours") or [])[:8]:
                if isinstance(edge, dict):
                    lines.append(
                        f"  - {edge.get('source_label')} --{edge.get('relation')} "
                        f"[{edge.get('confidence')}]--> {edge.get('target_label')}"
                    )
            return "\n".join(lines)
        if sub in {"neighbors", "neighbours", "near"}:
            if not arg:
                return "Use: /structural-graph neighbors <node|symbol|file>"
            from core.graph.query import neighbors

            result = neighbors(arg, cwd=graph_root, depth=1, limit=12)
            if not result.get("available"):
                return f"Structural graph neighbors unavailable: {result.get('reason') or 'graph unavailable'}"
            if not result.get("matched"):
                return f"Structural graph: no node matched {arg!r}."
            lines = [
                "Structural graph neighbors (orientation only):",
                f"  query: {arg}",
                f"  depth: {result.get('depth')}",
            ]
            for edge in (result.get("edges") or [])[:12]:
                if isinstance(edge, dict):
                    lines.append(
                        f"  - {edge.get('source_label')} --{edge.get('relation')} "
                        f"[{edge.get('confidence')}]--> {edge.get('target_label')}"
                    )
            if result.get("truncated"):
                lines.append("  truncated: true")
            return "\n".join(lines)
        if sub == "path":
            if not arg:
                return "Use: /structural-graph path <source> -> <target>"
            source = target = ""
            if "->" in arg:
                source, target = [part.strip() for part in arg.split("->", 1)]
            else:
                marker = " to "
                idx = arg.lower().find(marker)
                if idx >= 0:
                    source = arg[:idx].strip()
                    target = arg[idx + len(marker):].strip()
                else:
                    parts = arg.split(maxsplit=1)
                    if len(parts) == 2:
                        source, target = parts[0].strip(), parts[1].strip()
            if not source or not target:
                return "Use: /structural-graph path <source> -> <target>"
            from core.graph.query import shortest_path

            result = shortest_path(source, target, cwd=graph_root)
            if not result.get("available"):
                return f"Structural graph path unavailable: {result.get('reason') or 'graph unavailable'}"
            if not result.get("found"):
                return f"Structural graph: no path found from {source!r} to {target!r}."
            labels = " -> ".join(
                str(node.get("label") or node.get("id") or "?")
                for node in (result.get("nodes") or [])
                if isinstance(node, dict)
            )
            lines = ["Structural graph path (orientation only):", f"  hops: {result.get('hops')}", f"  path: {labels}"]
            for edge in (result.get("edges") or [])[:8]:
                if isinstance(edge, dict):
                    lines.append(
                        f"  - {edge.get('source_label')} --{edge.get('relation')} "
                        f"[{edge.get('confidence')}]--> {edge.get('target_label')}"
                    )
            return "\n".join(lines)
        if sub in {"stats", "audit"}:
            from core.graph.query import stats

            result = stats(cwd=graph_root)
            status = result.get("status") if isinstance(result.get("status"), dict) else {}
            if not result.get("available"):
                return "Structural graph stats unavailable."
            lines = [
                "Structural graph stats (orientation only):",
                f"  nodes: {status.get('nodes', 0)}",
                f"  edges: {status.get('edges', 0)}",
                f"  path groups: {status.get('groups', status.get('communities', 0))}",
                f"  stale: {status.get('stale')}",
                f"  trust: {status.get('trust')}",
                f"  quality: {status.get('quality') or {}}",
                f"  stale_reasons: {status.get('stale_reasons') or []}",
                f"  confidence: {status.get('confidence_breakdown') or {}}",
                f"  provenance: {status.get('provenance_breakdown') or {}}",
            ]
            gods = result.get("god_nodes") if isinstance(result.get("god_nodes"), list) else []
            if gods:
                lines.append("  god nodes:")
                for item in gods[:5]:
                    if isinstance(item, dict):
                        lines.append(f"  - {item.get('label') or item.get('id')} degree={item.get('degree')}")
            return "\n".join(lines)
        return "Use: /structural-graph status | build | refresh | export [path] [--compat] | explain <query> | neighbors <query> | path <source> -> <target> | stats"

    def _cmd_knowledge(self, rest: str) -> str:
        """Inspect or query the automatically maintained private project knowledge index."""
        from core.knowledge import knowledge_status, query_manifest, render_knowledge

        raw = str(rest or "").strip()
        parts = raw.split(maxsplit=1)
        sub = parts[0].lower() if parts else "status"
        arg = parts[1].strip() if len(parts) > 1 else ""
        root = self._effective_project_cwd() if hasattr(self, "_effective_project_cwd") else self.project_cwd
        if sub == "status":
            status = knowledge_status(root)
            if not status.get("available"):
                return "Knowledge index unavailable: automatic maintenance has not produced a manifest yet."
            coverage = status.get("coverage") or {}
            return (
                "Knowledge index status (automatic, private, source-linked):\n"
                f"  path: {status.get('path')}\n"
                f"  files: {coverage.get('indexed_files', 0)} · docs: {coverage.get('markdown_files', 0)} · tests: {coverage.get('test_files', 0)}\n"
                f"  manifest current: {status.get('manifest_current')}\n"
                f"  graph current: {status.get('graph_current')}"
            )
        if sub == "query":
            if not arg:
                return "Use: /knowledge query <text>"
            return render_knowledge(query_manifest(arg, root))
        return "Use: /knowledge status | query <text>"

    def _cmd_prt(self, rest: str) -> str:
        """Review worktree targets or review and correct committed targets."""
        _r = str(rest or "").strip()
        _rl = _r.lower()
        if _rl == "report":
            from core.review.maintainer import maintainer_report
            return maintainer_report(self)
        legacy_mode = next(
            (
                token
                for token in _r.split()
                if token.lower() in {"fix", "--fix", "loop", "ok", "accept"}
            ),
            "",
        )
        if legacy_mode:
            return (
                f"PRT {legacy_mode!r} is retired: committed-target correction is automatic after "
                "confirmed findings, while worktree targets remain report-only. Use /prt [ref]."
            )
        parts = str(rest or "").strip().split()
        unsupported_option = next((token for token in parts if token.startswith("--")), "")
        if unsupported_option:
            return f"PRT: unsupported option {unsupported_option!r}. Use /prt [ref] or /prt report."
        diff_ref = parts[-1] if parts else ""
        raw_workspace = getattr(self, "workspace", None) or getattr(self, "project_cwd", None) or "."
        from core.review.diff_review import review_diff, resolve_review_target
        try:
            selected = resolve_review_target(raw_workspace, diff_ref)
        except Exception as exc:
            return f"PRT could not resolve the target: {exc}"
        diff_ref = selected.requested_ref
        phase = "pre-commit" if selected.is_path_review else "post-commit"

        from core.review.prt_report import (
            build_prt_correction_objective, committed_correction_findings,
            render_prt_report, review_is_incomplete,
            route_prt_report,
        )
        from core.worker import ensure_worker_registry, ensure_worker_runtime, notify_native_async
        from core.review.lease import acquire_prt_execution
        import time

        registry = ensure_worker_registry(self)
        runtime = ensure_worker_runtime(self)
        progress = None

        def review(correction_of=None):
            with acquire_prt_execution(self, diff_ref) as admission:
                if not admission.acquired:
                    raise RuntimeError(admission.detail or admission.reason or "review admission unavailable")
                report = review_diff(self, diff_ref, on_progress=progress, correction_of=correction_of)
                report.structural_impact.setdefault("execution", {})["phase"] = phase
                return report

        def run_prt():
            report = review()
            baseline = dict((report.structural_impact or {}).get("review_target") or {})
            correction = bool(committed_correction_findings(report))
            seen = set()
            rounds = 0
            reason = ""
            while correction:
                # Record the immutable commit verdict before edits. Subsequent
                # reports describe worktree corrections, never rewrite that verdict.
                try:
                    before = resolve_review_target(raw_workspace, diff_ref, correction_of=baseline)
                except ValueError as exc:
                    reason = str(exc)
                    break
                route_prt_report(self, report, queue_provider=False, correction_pending=True)
                seen.add(before.evidence_digest)
                outcome = {}
                record = runtime.start(
                    objective=build_prt_correction_objective(report), source="prt",
                    on_finish=lambda rec, result: outcome.update(result=result),
                    on_activity=progress, project_cwd=str(raw_workspace),
                    notify_completion=False, run_inline=True,
                    claimed_paths=[str(finding.file) for finding in committed_correction_findings(report)],
                )
                rounds += 1
                try:
                    resolve_review_target(raw_workspace, diff_ref, correction_of=baseline)
                except ValueError as exc:
                    reason = str(exc)
                    break
                # The worker's prose is not a score or proof of correction.
                # Even an unchanged candidate gets one fresh assessment so a
                # disproved finding need not force a cosmetic edit.
                report = review(correction_of=baseline)
                state = str(getattr(record, "state", "blocked") or "blocked")
                if state != "completed":
                    reason = f"Correction {state}: " + str(outcome.get("result") or record.note or "worker could not finish")
                    break
                if review_is_incomplete(report):
                    reason = "Reassessment is incomplete; corrections remain unverified."
                    break
                if report.is_target_met:
                    break
                assessed_digest = (report.structural_impact.get("review_target") or {}).get("evidence_digest")
                if assessed_digest in seen:
                    reason = "No further source progress: the candidate is unchanged or repeats an earlier correction. Remaining findings require investigation."
                    break
                correction = bool(committed_correction_findings(report))
                if not correction:
                    reason = "The target score was not reached; no confirmed actionable finding remains to justify another edit."

            incomplete = review_is_incomplete(report)
            if incomplete:
                reason = reason or "Review incomplete: evidence could not be completed."
            if reason:
                summary = f"PRT {phase} stopped: {reason}"
            elif rounds:
                summary = f"PRT post-commit corrections verified: {report.score}/5.0 after {rounds} correction pass(es); changes remain uncommitted."
            else:
                summary = f"PRT {phase} finished: {report.score}/5.0 · {report.unresolved_count} unresolved"
            if not incomplete:
                boundary = self._run_consistency_boundary("prt", prt_report=report, final_text=summary)
                if boundary is not None and not getattr(boundary, "clean", True):
                    summary += f" · consistency {len(getattr(boundary, 'findings', []) or [])}"
            report.structural_impact["correction_outcome"] = summary if rounds or reason else ""
            route_prt_report(self, report)
            rendered = render_prt_report(report)
            evidence = [f"review:{diff_ref}", f"score:{report.score}", f"unresolved:{report.unresolved_count}"]
            return rendered, summary, evidence, bool(reason)

        if getattr(self, "_noninteractive", False):
            try:
                return run_prt()[0]
            except Exception as exc:
                return f"PRT {phase} blocked: {exc}"

        worker_id = f"prt-{time.time_ns()}"
        registry.create(
            kind="prt", source="user", route="background",
            objective=f"PRT {phase} {diff_ref}", state="offered", worker_id=worker_id,
            claimed_paths=[],  # Correction workers claim their own edit paths.
        )
        progress = lambda note: registry.update(worker_id, "running", note)

        def custom_prt_run(w_id: str, _obj: str, on_fin):
            try:
                _rendered, summary, evidence, blocked = run_prt()
                registry.update(w_id, "blocked" if blocked else "completed", summary,
                                result_summary=summary, evidence=evidence)
            except Exception as exc:
                registry.update(w_id, "blocked", f"PRT {phase} error: {exc}")
                if on_fin:
                    on_fin(registry.get(w_id), "")
            finally:
                with runtime._lock:
                    runtime._threads.pop(w_id, None)

        record = runtime.start(
            objective=f"PRT {phase} {diff_ref}", source="user", worker_id=worker_id,
            on_finish=lambda rec, _result: notify_native_async(self, rec),
            custom_target=custom_prt_run,
        )
        if getattr(record, "state", "") == "blocked":
            return command_result(
                f"PRT review blocked: {record.note or 'worker coordination blocked review'}", kind="error",
            )
        return SlashCommandResult(
            f"PRT review started · {phase} · {diff_ref}" + (
                f" · MO {self.instance_id}" if getattr(self, "instance_id", "") else ""
            ), kind="notice", action="prt_started",
        )

    def _cmd_help(self, _rest: str) -> str:
        from interface.command_registry import build_help_text

        return build_help_text()

    def _cmd_update(self, _rest: str) -> str:
        from core.update.apply import apply_update

        return apply_update()

    def _cmd_init(self, _rest: str) -> str:
        from ..state.initializer import initialize_mo, render_init_report

        report = initialize_mo(home=getattr(self, "runtime_home", None), project_path=getattr(self, "project_cwd", None))
        return render_init_report(report)

    def _cmd_doctor(self, rest: str) -> str:
        """One-shot, offline-safe diagnostics; `/doctor --json` for scripting."""
        from ..diagnostics.doctor import build_doctor_report, render_doctor_json, render_doctor_report

        args = str(rest or "").lower().split()
        if args == ["layout"]:
            from ..state.layout import check_state_layout, render_layout_report

            return render_layout_report(check_state_layout(
                home=getattr(self, "runtime_home", None), config=getattr(self, "config", None),
            ))
        if args and args[0] == "personalization":
            if args not in (["personalization"], ["personalization", "--json"], ["personalization", "json"]):
                return command_result("Usage: /doctor personalization [--json].", kind="error")
            from ..diagnostics.personalization import (
                build_personalization_report,
                render_personalization_json,
                render_personalization_report,
            )

            report = build_personalization_report(
                state_root=getattr(self, "runtime_home", None),
                project_root=getattr(self, "project_cwd", None),
            )
            return render_personalization_json(report) if len(args) > 1 else render_personalization_report(report)
        if args not in ([], ["--json"], ["json"]):
            return command_result("Usage: /doctor [layout|personalization [--json]|--json].", kind="error")
        report = build_doctor_report(
            home=getattr(self, "runtime_home", None),
            config_path=getattr(self, "config_path", None),
            project_path=getattr(self, "project_cwd", None),
            config=getattr(self, "config", None),
        )
        if "--json" in args or "json" in args:
            return render_doctor_json(report)
        return render_doctor_report(report)

    def _cmd_credentials(self, rest: str) -> str:
        """Show value-free credential readiness by service."""
        from tools import execute_credential_status

        return execute_credential_status({
            "service": (rest or "all").strip().lower() or "all",
            "_mo_config": getattr(self, "config", {}) or {},
        })

    def _cmd_mail(self, rest: str) -> str:
        """Local Gmail connection and bounded status; content uses the Agent tool."""
        from core.mail.service import MailService
        from core.mail.gmail import GmailError
        from core.mail.secure_store import SecureStoreUnavailable

        action = str(rest or "status").strip().lower() or "status"
        service = MailService(getattr(self, "config", {}) or {})
        try:
            if action == "status":
                value = service.status()
            elif action == "connect":
                value = service.connect()
            elif action == "disconnect":
                value = service.disconnect()
            elif action == "sync":
                value = service.sync()
            else:
                return "Usage: /mail [status|connect|sync|disconnect]. Ask MO to read or draft Gmail messages."
        except (GmailError, SecureStoreUnavailable, ValueError) as exc:
            return f"Gmail: {exc}"
        state = str(value.get("state") or "unknown")
        detail = str(value.get("next_action") or value.get("note") or "").strip()
        if value.get("unread") is not None:
            detail = f"{int(value['unread'])} unread" + (f" · {detail}" if detail else "")
        return f"Gmail: {state}" + (f" · {detail}" if detail else "")

    def _cmd_profile(self, rest: str) -> str:
        """Show or edit profile. Subcommands: name, tools, provider, default (show)."""
        rest = rest.strip()
        if not rest:
            return self.profile.render()

        parts = rest.split(maxsplit=1)
        sub = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ""

        if sub == "name" and value:
            # Format: /profile name John/Doe or /profile name John
            name_parts = value.split("/", 1)
            self.profile.user_name = name_parts[0].strip()
            self.profile.user_alias = name_parts[1].strip() if len(name_parts) > 1 else ""
            if hasattr(self.profile, "sync_operator_profile_files"):
                self.profile.sync_operator_profile_files()
            self.profile.save()
            return f"Profile name set: {self.profile.user_name}" + (
                f" ({self.profile.user_alias})" if self.profile.user_alias else ""
            )

        if sub == "tools" and value:
            tools = [t.strip() for t in value.split(",") if t.strip()]
            self.profile.preferred_tools = tools
            self.profile.save()
            self.tool_definitions = self._ordered_tool_definitions(self.tool_definitions)
            return f"Preferred tools: {', '.join(tools)}"

        if sub == "provider" and value:
            prov_parts = value.split("/", 1)
            self.profile.favorite_provider = prov_parts[0].strip()
            self.profile.favorite_model = prov_parts[1].strip() if len(prov_parts) > 1 else ""
            self.profile.save()
            label = f"Favorite provider: {self.profile.favorite_provider}" + (
                f" / {self.profile.favorite_model}" if self.profile.favorite_model else ""
            )
            return (
                f"{label} (saved as profile metadata only; active lane remains "
                f"{self.provider_name} / {self.model}. Edit config to change runtime providers.)"
            )

        if sub in {"status", "facts"}:
            from ..profile.facts import list_profile_facts

            facts = list_profile_facts(profile=self.profile, config=getattr(self, "config", None))
            if sub == "facts":
                if value:
                    item = next((item for item in facts if item.id == value.strip()), None)
                    if item is None:
                        return SlashCommandResult("This saved note has changed or was removed. Choose it again.", choices=(("/profile facts", "Back to saved notes"),))
                    return SlashCommandResult(
                        f"{item.category.title()}\n\n{item.fact}\n\nMO recalls relevant saved notes as context. They are not proof of the current state.",
                        title="Saved profile note",
                        choices=(
                            (f"/profile fact update {item.id} {item.category} :: {item.fact} ", "Edit note…"),
                            (f"/profile fact forget {item.id}", "Forget note…"),
                            ("/profile facts", "Back to saved notes"),
                        ),
                    )
                return SlashCommandResult(
                    f"Saved profile notes · {len(facts)}\nMO recalls relevant notes about your projects and preferences.\nSelect a note to inspect, edit, or forget it.",
                    title="Saved profile notes",
                    choices=tuple((f"/profile facts {item.id}", f"[{item.category}] {item.fact}") for item in facts)
                    + (("/profile fact add", "Add a note…"),),
                )
            cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
            profile_db = str(getattr(self.profile, "_path", "") or resolve_state_path(PROFILE_DB_PATH, cfg))
            pdir = Path(profile_db).parent / "profile"
            files = [name for name in PROFILE_PROSE_FILES if (pdir / name).is_file()]
            return (
                "Profile status:\n"
                f"  operator: {self.profile.user_name or 'not set'}\n"
                f"  profile files: {len(files)}/{len(PROFILE_PROSE_FILES)}\n"
                f"  durable facts: {len(facts)}\n"
                "  context: compact chat capsule; balanced work/profile capsule\n"
                "  secrets: excluded from profile context"
            )

        if sub == "fact":
            from ..profile.facts import ALLOWED_CATEGORIES, forget_profile_fact, list_profile_facts, record_profile_fact, update_profile_fact

            action, _, tail = value.strip().partition(" ")
            action = action.lower()
            tail = tail.strip()
            if action == "add":
                if not tail:
                    return SlashCommandResult(
                        "Choose a category, then type a short note after ::. Never include secret values.",
                        title="Add a profile note",
                        choices=tuple((f"/profile fact add {category} :: ", category.title()) for category in sorted(ALLOWED_CATEGORIES)),
                    )
                category, separator, fact = tail.partition("::")
                if not separator:
                    return "Usage: /profile fact add <category> :: <fact>"
                state, entry = record_profile_fact(
                    category.strip(), fact.strip(), profile=self.profile, config=getattr(self, "config", None)
                )
                return SlashCommandResult(f"Profile fact {state}" + (f": [{entry.category}] {entry.fact}" if entry else ""), choices=(("/profile facts", "Back to saved notes"),))
            if action == "update":
                reference, _, change = tail.partition(" ")
                category, separator, fact = change.partition("::")
                if not reference or not separator:
                    return "Usage: /profile fact update <id> <category> :: <fact>"
                state, entry = update_profile_fact(
                    reference, category=category.strip(), fact=fact.strip(),
                    profile=self.profile, config=getattr(self, "config", None),
                )
                return SlashCommandResult(f"Profile fact {state}" + (f": [{entry.category}] {entry.fact}" if entry else ""), choices=(("/profile facts", "Back to saved notes"),))
            if action == "forget":
                confirm = "--confirm" in tail
                reference = tail.replace("--confirm", "").strip()
                if not reference:
                    return "Usage: /profile fact forget <id> --confirm"
                if not confirm:
                    item = next((item for item in list_profile_facts(profile=self.profile, config=getattr(self, "config", None)) if item.id == reference), None)
                    if item is None:
                        return SlashCommandResult("Saved note not found or changed.", choices=(("/profile facts", "Back to saved notes"),))
                    return SlashCommandResult(
                        f"Forget this saved note?\n\n[{item.category}] {item.fact}\n\nThis removes the saved note, not past chat messages.",
                        title="Confirm forgetting",
                        choices=((f"/profile fact forget {item.id} --confirm", "Forget this note"), (f"/profile facts {item.id}", "Cancel")),
                    )
                return SlashCommandResult(f"Profile fact {'forgotten' if forget_profile_fact(reference, profile=self.profile, config=getattr(self, 'config', None)) else 'not found'}", choices=(("/profile facts", "Back to saved notes"),))
            return "Use: /profile fact add | update | forget, or /profile facts to list exact ids."

        if sub == "archive-section":
            from ..profile.curation import archive_operator_profile_section

            confirm = "--confirm" in value
            heading = value.replace("--confirm", "").strip()
            if not heading:
                return "Usage: /profile archive-section <exact heading> [--confirm]"
            try:
                result = archive_operator_profile_section(
                    heading,
                    profile=self.profile,
                    config=getattr(self, "config", None),
                    confirm=confirm,
                )
            except ValueError as exc:
                return f"Profile section archive refused: {exc}"
            if not result["archived"]:
                return (
                    f"Archive preview: {result['heading']} ({result['line_count']} lines).\n"
                    f"Confirm with: /profile archive-section {result['heading']} --confirm"
                )
            return (
                f"Profile section archived: {result['heading']} ({result['line_count']} lines)\n"
                f"  inert archive: {result['archive_path']}"
            )

        if sub in {"mine", "suggestions", "suggest"}:
            return self._cmd_learning("suggestions")

        if sub == "export":
            from ..learning.learning_bundle import export_learning_bundle
            result = export_learning_bundle(self.profile, path=value.strip() or None)
            if not result.get("exported"):
                return f"Export refused: {result.get('reason', 'unknown')}"
            counts = result.get("counts", {})
            return (
                f"Learning bundle exported: {result['path']}\n"
                f"  profile files {counts.get('profile_files', 0)} · confirmed clusters {counts.get('confirmed_suggestions', 0)} · skill files {counts.get('skills', 0)}\n"
                f"  import on the other MO with: /profile import <path> --confirm"
            )

        if sub == "import" and value:
            from ..learning.learning_bundle import import_learning_bundle
            confirm = "--confirm" in value
            bundle_path = value.replace("--confirm", "").strip()
            result = import_learning_bundle(self.profile, bundle_path, confirm=confirm)
            if result.get("reason"):
                return f"Import failed: {result['reason']}"
            lines = [
                ("Imported:" if result.get("imported") else "Import dry-run (add --confirm to apply):"),
                f"  new confirmed suggestion(s): {result.get('new_confirmed_suggestions', 0)}",
                f"  new skill file(s):           {result.get('new_skill_files', 0)}",
            ]
            files = result.get("profile_files_for_review") or []
            if files:
                lines.append(f"  profile files staged for manual review: {', '.join(files)}")
                if result.get("review_dir"):
                    lines.append(f"  staged at: {result['review_dir']}")
            return "\n".join(lines)

        return f"Unknown profile subcommand: {sub}\nUse: /profile status, /profile facts, /profile fact add|update|forget, /profile archive-section <heading> [--confirm], /profile name, /profile tools, /profile provider, /profile mine, /profile export, /profile import <path> [--confirm]"

    def _cmd_clear(self, _rest: str) -> SlashCommandResult:
        self.session.clear()
        self.set_conversation_role(None)
        return command_control("clear_transcript", "Conversation cleared")

    def _cmd_heartbeat(self, rest: str) -> str:
        """Show or refresh MO's local heartbeat ledger."""
        sub = (rest or "").strip().split(maxsplit=1)[0].lower() if (rest or "").strip() else "status"
        try:
            from ..runtime.heartbeat import build_surface_continuity_context, record_heartbeat, render_heartbeat_status
            if sub in {"status", ""}:
                return render_heartbeat_status(self, gateway=getattr(self, "gateway", None))
            if sub in {"now", "record", "ping"}:
                record_heartbeat(self, gateway=getattr(self, "gateway", None), surface=self._provider_surface(), event="manual")
                return render_heartbeat_status(self, gateway=getattr(self, "gateway", None))
            if sub in {"context", "surfaces"}:
                return build_surface_continuity_context(self, current_surface=self._provider_surface()) or "No recent heartbeat continuity."
            if sub in {"instances", "terminals"}:
                from ..runtime.instance import render_existing_instances_notice

                notice = render_existing_instances_notice(
                    getattr(self, "config", None),
                    current_pid=os.getpid(),
                )
                return notice or "MO instances: no other recent terminal instances."
        except Exception as exc:
            detail = clean_provider_error(str(exc))
            return "\n".join([
                "MO heartbeat error: unavailable",
                "  where: /heartbeat command",
                "Fix: try /status or check the heartbeat ledger if this repeats.",
                f"  detail: {detail}",
            ])
        return "Use: /heartbeat [status|now|context|instances]"

    def _cmd_activity(self, rest: str) -> str:
        """Control the canonical Goal/Background/PRT worker activity panel."""
        raw = (rest or "").strip()
        sub = raw.lower()
        if sub in {"on", "enable"}:
            self._activity_enabled_override = True
            self._refresh_tui_surface()
            suffix = self._save_terminal_preferences(activity=True)
            return command_result("Activity panel on — Goal, Background, and PRT work is shown." + suffix, kind="notice")
        if sub in {"off", "disable"}:
            self._activity_enabled_override = False
            self._refresh_tui_surface()
            suffix = self._save_terminal_preferences(activity=False)
            return command_result("Activity panel off." + suffix, kind="notice")
        if sub not in {"", "status"}:
            return command_result("Usage: /activity [on|off|status]", kind="error")
        from interface.workspace import active_sources, activity_enabled

        override = getattr(self, "_activity_enabled_override", None)
        panel_on = bool(override) if override is not None else activity_enabled(
            getattr(self, "config", None)
        )
        sources = active_sources(self)
        lines = [f"Activity panel: {'on' if panel_on else 'off'}  (/activity on|off)"]
        if sources:
            lines.append(f"  active workers ({len(sources)}):")
            for source in sources:
                detail = f" — {source.detail}" if source.detail else ""
                lines.append(f"    {source.label}: {source.status}{detail}")
        else:
            lines.append("  no true workers active (Goal/Background/PRT).")
        return "\n".join(lines)

    def _cmd_workspace(self, rest: str) -> str:
        """Manage the local split-terminal workspace owned by the full TUI."""
        raw = (rest or "").strip()
        tui = getattr(self, "tui", None)
        controller = getattr(tui, "_workspace", None) if tui is not None else None
        if controller is None:
            return "Split-terminal workspace requires the full terminal TUI."
        return controller.command(raw)

    def _refresh_tui_surface(self) -> None:
        """Best-effort immediate repaint so a toggle is visible without a keystroke."""
        tui = getattr(self, "tui", None)
        app = getattr(tui, "_app", None) if tui is not None else None
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _cmd_terminal(self, rest: str) -> str:
        """Open a real terminal inside MO (codex/claude/shell/any command).

        Suspends MO, runs the command on the actual console at full fidelity, and
        returns to MO on exit. Light: one foreground terminal, no background
        processes, no new deps. Also bound to Ctrl+T (no command = a shell).
        """
        self._terminal_pending_command = str(rest or "").strip()
        return command_control("terminal")

    def _cmd_everywhere(self, rest: str) -> str:
        """Manage optional cross-device continuity without overloading heartbeat."""
        from ..state.everywhere_setup import dispatch_everywhere_command

        return dispatch_everywhere_command(getattr(self, "config", None), rest)

    def _cmd_now(self, _rest: str) -> str:
        """Show MO's deterministic current-work continuity snapshot."""
        try:
            from ..runtime.continuity import render_current_work_status

            return render_current_work_status(self)
        except Exception as exc:
            detail = clean_provider_error(str(exc))
            return "\n".join([
                "MO continuity snapshot error: unavailable",
                "  where: /now command",
                "Fix: try /heartbeat status and /status if this repeats.",
                f"  detail: {detail}",
            ])

    def _cmd_dashboard(self, rest: str) -> str:
        """Show or build the bounded MO dashboard."""
        sub = (rest or "").strip().split(maxsplit=1)[0].lower() if (rest or "").strip() else "status"
        try:
            from ..dashboard import build_dashboard_snapshot, render_dashboard_text, write_dashboard_html

            if sub == "show":
                from ..dashboard.server import open_dashboard

                open_dashboard(self)
                return "MO Dashboard opened in your browser · connected to this terminal."
            if sub == "lsp":
                return "Language-server preferences are in Settings → Projects & checks. /dashboard checks shows recorded evidence."
            if sub == "checks":
                from ..diagnostics.surface_trace import build_check_evidence
                from ..graph.structural_graph import graph_status

                root = self._effective_project_cwd()
                mgr = getattr(self, "lsp_manager", None)
                status = mgr.status(root) if mgr else {"state": "unavailable", "selection": "default", "running": 0}
                graph = graph_status(root)
                evidence = build_check_evidence(self.config, session_id=str(getattr(self.session, "session_id", "")))
                lines = [
                    f"Project checks · {Path(root).name}",
                    f"  LSP: {status['state']} · selection {status['selection']} · {status['running']} server(s) started",
                    "  Preferences: Settings → Projects & checks — saved for this project only",
                    "  Server commands: lsp.servers in normal MO config; /reload applies changes. No auto-install.",
                    f"  Structural graph: {'stale' if graph.get('stale') else 'available' if graph.get('available') else 'not built'} (orientation, not verification)",
                    f"Latest recorded model-turn checks: {evidence['state']}",
                ]
                lines.extend(f"  {row['label']}: {row['value']}" for row in evidence["checks"])
                lines.extend([
                    "  Sampled historical evidence, not a current all-clear; absent results mean not observed.",
                    "  Syntax/edit checks, affected tests, LSP, security scan and final-claim gates have different scopes.",
                    "  Gates visited are not tests passed. LSP does not replace tests, review, or task evidence.",
                ])
                return "\n".join(lines)
            snapshot = build_dashboard_snapshot(self)
            if sub in {"html", "build", "export"}:
                result = write_dashboard_html(snapshot)
                return (
                    "MO dashboard HTML written:\n"
                    f"  path: {result['path']}\n"
                    f"  bytes: {result['bytes']}\n"
                    "  trust: read-only private-state artifact; raw profile prose and memory text are not embedded"
                )
            if sub in {"map", "open", "graph"}:
                # Surface MO's REAL interactive map (generate_code_map's code_map.html:
                # skin-themed 3D code + brain map with taskboard/commit overlays), not
                # the dashboard's summary. Auto-generated on graph build; open it via the
                # canonical computer action.
                code_map = (snapshot.get("artifacts") or {}).get("code_map") \
                    or (snapshot.get("graph") or {}).get("code_map_path") or ""
                if not (code_map and Path(code_map).is_file()):
                    return (
                        "MO interactive map isn't built yet.\n"
                        "  Run /structural-graph build, then /dashboard map."
                    )
                from tools.computer import execute_computer_act
                opened = execute_computer_act({"kind": "desktop", "action": "open", "url": Path(code_map).as_uri()})
                if str(opened).startswith("Error"):
                    return f"MO interactive map is built but the browser didn't open:\n  path: {code_map}\n  {opened}"
                return (
                    "MO interactive map opened in your browser:\n"
                    f"  path: {code_map}\n"
                    "  skin-themed 3D code + brain map · drag rotate · wheel zoom · "
                    "click a node/cluster · toggle drift/grid · Esc clears"
                )
            if sub in {"status", "summary", ""}:
                return render_dashboard_text(snapshot)
        except Exception as exc:
            detail = clean_provider_error(str(exc))
            return "\n".join([
                "MO dashboard error: unavailable",
                "  where: /dashboard command",
                "Fix: try /status, /now, and /structural-graph status if this repeats.",
                f"  detail: {detail}",
            ])
        return "Use: /dashboard [show|status|checks|lsp on|lsp off|lsp default|html|map] (also /brain)"

    def _cmd_telegram(self, rest: str) -> str:
        """Local Telegram gateway control. Never prints or stores token values."""
        parts = (rest or "").strip().split()
        sub = parts[0].lower() if parts else "status"
        if sub == "trace":
            from ..diagnostics.surface_trace import build_telegram_trace_report

            state = getattr(self, "_thread_state", None)
            slot = str(getattr(state, "surface_session_slot", "") if state is not None else "")
            live_session = getattr(self, "session", None) if slot.startswith("telegram-") else None
            return build_telegram_trace_report(
                getattr(self, "config", None),
                session_slot=slot,
                live_session=live_session,
            )
        try:
            from ..telegram.gateway import TelegramGateway, start_telegram_gateway_if_enabled
        except Exception as exc:
            detail = clean_provider_error(str(exc))
            return "\n".join([
                "MO telegram error: unavailable",
                "  where: /telegram command",
                "Fix: check Telegram config/dependencies, then retry.",
                f"  detail: {detail}",
            ])
        gateway = getattr(self, "telegram_gateway", None) or getattr(self, "_telegram_gateway", None)
        if gateway is None:
            gateway = TelegramGateway.from_agent(self, gateway=getattr(self, "gateway", None))
            try:
                setattr(self, "telegram_gateway", gateway)
                setattr(self, "_telegram_gateway", gateway)
            except Exception:
                traceback.print_exc()
        if sub in {"status", ""}:
            st = gateway.status()
            token_state = (
                "canonical present"
                if st.get("token_present")
                else f"canonical missing {st.get('token_env')}"
            )
            source = f" ({st.get('token_source')})" if st.get("token_source") else ""
            return (
                f"telegram {'enabled' if st.get('enabled') else 'disabled'} | "
                f"running={'yes' if st.get('running') else 'no'} | token={token_state}{source}\n"
                f"  auth: paired={st.get('paired')} pending={st.get('pending')} policy={st.get('dm_policy')}\n"
                f"  queue: pending={st.get('pending_jobs')} unfinished={st.get('unfinished_jobs')} active={len(st.get('active_chats') or [])} steer={st.get('queued_steer')}"
            )
        if sub == "queue":
            return gateway.queue_report() if hasattr(gateway, "queue_report") else "telegram queue unavailable."
        if sub in {"sessions", "session", "chats", "chat"}:
            return gateway.session_report() if hasattr(gateway, "session_report") else "telegram chat sessions unavailable."
        if sub == "approve" and len(parts) >= 2:
            return "telegram approved." if gateway.approve(parts[1]) else "telegram approval failed."
        if sub == "start":
            self.config.setdefault("telegram", {})["enabled"] = True
            started = start_telegram_gateway_if_enabled(self, getattr(self, "gateway", None))
            if started is not None:
                try:
                    setattr(self, "telegram_gateway", started)
                    setattr(self, "_telegram_gateway", started)
                except Exception:
                    traceback.print_exc()
                return self._cmd_telegram("status")
            return "telegram not started."
        if sub == "disable":
            self.config.setdefault("telegram", {})["enabled"] = False
            try:
                gateway.stop()
            except Exception:
                traceback.print_exc()
            return "telegram disabled for current process."
        return "Use: /telegram [status|queue|chats|approve <code>|start|trace|disable]"

    def _cmd_usage(self, _rest: str) -> str:
        input_tokens = sum(e.get("input_tokens", 0) for e in self.session.token_log)
        output_tokens = sum(e.get("output_tokens", 0) for e in self.session.token_log)
        total_tokens = sum(e.get("total_tokens", 0) for e in self.session.token_log)
        cache_hit_tokens = sum(e.get("cache_hit_tokens", 0) for e in self.session.token_log)
        cache_miss_tokens = sum(e.get("cache_miss_tokens", 0) for e in self.session.token_log)
        cache_write_tokens = sum(e.get("cache_write_tokens", 0) for e in self.session.token_log)
        if not cache_hit_tokens and not cache_miss_tokens:
            cache_hit_tokens = self._safe_int(getattr(self.session, "cache_hit_tokens", 0))
            cache_miss_tokens = self._safe_int(getattr(self.session, "cache_miss_tokens", 0))
        if not cache_write_tokens:
            cache_write_tokens = self._safe_int(getattr(self.session, "cache_write_tokens", 0))
        lines = [
            "Token usage:",
            f"  total:   {total_tokens:,}",
            f"  input:   {input_tokens:,}",
            f"  output:  {output_tokens:,}",
        ]
        if input_tokens > 0:
            if cache_hit_tokens or cache_miss_tokens or cache_write_tokens:
                cache_ratio = cache_hit_percentage(input_tokens, cache_hit_tokens)
                ratio_text = "ratio unavailable" if cache_ratio is None else f"{cache_ratio:.0f}% prefix-cache hit"
                lines.append(
                    f"  cache:   {ratio_text} "
                    f"({cache_hit_tokens:,} hit / {cache_miss_tokens:,} miss"
                    + (f" / {cache_write_tokens:,} write" if cache_write_tokens else "")
                    + "; provider-reported)"
                )
            else:
                lines.append("  cache:   provider reports no prefix-cache breakdown")
        if self._tool_context_saving_ops() > 0:
            lines.append(f"  saved:   ~{self._context_saved_tokens_estimate():,} tokens ({self._tool_context_saved_chars():,} chars) via explicit result caps")
            carry_text = f" · carried {self._carried_tool_context_saving_ops()}" if self._carried_tool_context_saving_ops() else ""
            lines.append(
                f"  context-save:{self._tool_context_saving_ops():>4} ops · "
                f"current result caps {getattr(self, 'result_cap_total_ops', 0)}{carry_text}"
            )
        if self._safe_int(getattr(self, "session_compaction_total_ops", 0)) > 0:
            lines.append(
                f"  session-compact:{getattr(self, 'session_compaction_total_ops', 0):>3} ops · "
                f"{getattr(self, 'session_compaction_total_saved', 0):,} chars saved"
            )
        lines.extend([
            f"  turns:   {self.session.turn_count}",
            f"  session id: {self.session.session_id}",
        ])
        return "\n".join(lines)

    def _context_saved_tokens_estimate(self) -> int:
        """Approximate saved context tokens from capped tool-output chars."""
        try:
            saved_chars = self._tool_context_saved_chars()
        except (TypeError, ValueError):
            saved_chars = 0
        return max(0, round(saved_chars / 4))

    def _cmd_model(self, rest: str) -> str:
        rest = rest.strip()
        from ..provider.model_catalog import (
            activate_model_selection,
            ensure_model_choice_provider,
            model_menu_items,
            provider_source_key,
            source_label,
            source_menu_items,
            split_model_command_rest,
            thinking_levels_for,
            thinking_menu_items,
        )

        def _render_sources() -> str:
            lines = [
                f"model:        {self.provider_name} / {self.model}",
                f"thinking:     {getattr(self, 'reasoning', self.config.get('agent', {}).get('reasoning', 'high'))}",
                "",
                "Provider sources:",
            ]
            rows = source_menu_items(self)
            if not rows:
                lines.append("  no provider sources initialized")
                return "\n".join(lines)
            for item in rows:
                lines.append(f"  {item.label:<18} {item.desc}")
            lines.extend([
                "",
                "Use: /model <source> <model> <high|medium|low>",
                "Or open the command palette and drill into /model.",
            ])
            return "\n".join(lines)

        def _render_models(source: str) -> str:
            rows = model_menu_items(self, source)
            if not rows:
                return f"No available models for source: {source}"
            lines = [f"{source_label(source)} models:"]
            for item in rows:
                lines.append(f"  {item.label:<26} {item.desc}")
            lines.append("")
            lines.append(f"Use: /model {source} <model> <high|medium|low>")
            return "\n".join(lines)

        def _render_thinking(source: str, model: str) -> str:
            rows = thinking_menu_items(self, source, model)
            if not rows:
                return f"No thinking levels available for: {source} / {model}"
            lines = [f"Choose thinking for {source_label(source)} / {model}:"]
            for item in rows:
                lines.append(f"  {item.label:<8} {item.desc}")
            lines.append("")
            levels = "|".join(level for level, _desc in thinking_levels_for(source, model))
            lines.append(f"Use: /model {source} {model} <{levels}>")
            return "\n".join(lines)

        if not rest:
            return command_result(_render_sources(), kind="report")

        source, model, thinking = split_model_command_rest(rest)
        if source and not model:
            return command_result(_render_models(source), kind="report")
        if source and model and not thinking:
            return command_result(_render_thinking(source, model), kind="report")
        if source and model and thinking:
            valid_thinking = {level for level, _desc in thinking_levels_for(source, model)}
            if thinking not in valid_thinking:
                return command_result(f"Unknown thinking level: {thinking}\n" + _render_thinking(source, model), kind="error")
            available = {item.label.lower() for item in model_menu_items(self, source)}
            if available and model.lower() not in available:
                return command_result(f"Model '{model}' not found under {source_label(source)}.", kind="error")
            selected = ensure_model_choice_provider(self, source, model)
            if selected is None:
                setup_errors = [
                    clean_provider_error(err)
                    for err in getattr(self, "provider_setup_errors", [])
                    if str(err).lower().startswith((f"{source}/", f"{source}-"))
                ]
                detail = f" Setup error: {setup_errors[0]}" if setup_errors else ""
                return command_result(
                    f"Model source '{source_label(source)}' is configured but not initialized."
                    f"{detail}\nAdd the provider key to ~/.mo/credentials/providers.env, then run /reload.", kind="error",
                )
            selection = {"source": source, "model": model, "thinking": thinking}
            activate_model_selection(
                self,
                selection,
                surface="slash_command",
                reason="/model command",
            )
            suffix = self._save_terminal_preferences(model=selection)
            return f"Switched to model: {source_label(source)} / {self.model} · thinking {thinking}{suffix}"

        target_idx = None
        if rest.isdigit():
            idx = int(rest) - 1
            if 0 <= idx < len(self.providers):
                target_idx = idx
        else:
            rest_lower = rest.lower()
            for i, p in enumerate(self.providers):
                if p.name.lower() == rest_lower or p.model.lower() == rest_lower:
                    target_idx = i
                    break

        if target_idx is not None:
            p = self.providers[target_idx]
            source = provider_source_key(p)
            levels = [level for level, _desc in thinking_levels_for(source, p.model)]
            current = str(getattr(self, "reasoning", "") or "").strip().lower()
            thinking = current if current in levels else ("medium" if "medium" in levels else levels[0])
            selection = {"source": source, "model": p.model, "thinking": thinking}
            activate_model_selection(
                self,
                selection,
                surface="slash_command",
                reason="/model command",
            )
            suffix = self._save_terminal_preferences(model=selection)
            return f"Switched to model: {p.name} / {p.model} · thinking {thinking}{suffix}"

        return command_result(f"Model '{rest}' not found in available models.", kind="error")

    def _session_save_extra_meta(
        self,
        *,
        closeout: dict | None = None,
        active_turn_user: str = "",
    ) -> dict | None:
        extra: dict = {}
        role = getattr(self, "_conversation_role", None)
        role_id = str(getattr(role, "role", "") or getattr(role, "name", "") or "").strip()
        role_project = str(getattr(role, "project_root", "") or "")
        if not role_id:
            try:
                session_meta = getattr(self.session, "_loaded_meta", {})
                role_id = str(session_meta.get("active_role") or "").strip() if isinstance(session_meta, dict) else ""
                role_project = str(session_meta.get("active_role_project") or "") if isinstance(session_meta, dict) else ""
            except Exception:
                role_id = ""
        if role_id:
            extra["active_role"] = role_id[:200]
            extra["active_role_project"] = role_project
        binding = getattr(self, "_game_collaboration_binding", {})
        if isinstance(binding, dict) and str(binding.get("mode") or "") in {"active", "paused"}:
            extra["game_collaboration"] = {
                "mode": str(binding.get("mode") or "paused"),
                "surface": "terminal",
                "project_key": str(binding.get("project_key") or "")[:64],
                "project_name": str(binding.get("project_name") or "Game project")[:120],
                "record_revision": int(binding.get("record_revision", 0) or 0),
            }
        if self._tool_context_saving_ops() > 0 or self._safe_int(getattr(self, "session_compaction_total_ops", 0)) > 0:
            extra["context_savings"] = {
                "result_cap_ops": getattr(self, "result_cap_total_ops", 0),
                "result_cap_saved": getattr(self, "result_cap_total_saved", 0),
                "result_cap_last_pct": getattr(self, "result_cap_last_pct", 0),
                "carried_result_cap_ops": getattr(self, "carried_result_cap_ops", 0),
                "carried_result_cap_saved": getattr(self, "carried_result_cap_saved", 0),
                "session_compaction_ops": getattr(self, "session_compaction_total_ops", 0),
                "session_compaction_saved": getattr(self, "session_compaction_total_saved", 0),
                "context_saved_chars": self._tool_context_saved_chars(),
                "current_context_saved_chars": self._current_tool_context_saved_chars(),
                "momentum_saved_chars": self._carried_tool_context_saved_chars(),
                "saved_tokens_est": self._context_saved_tokens_estimate(),
            }
        pending = getattr(self, "_pending_interrupted_work", {})
        pending_user = (
            str(pending.get("user") or "").strip()
            if isinstance(pending, dict)
            else ""
        )
        active_user = str(active_turn_user or "").strip()
        if active_user and bool(getattr(getattr(self, "session", None), "_mail_sensitive_turn", False)):
            active_user = "[Gmail turn omitted from saved conversation]"
        if active_user:
            checkpoint = {
                "changed": True,
                "reason": "provider_turn_checkpoint",
                "user": active_user[:500],
                "dropped_messages": 0,
                "saved_at": time.time(),
            }
            # A vague resume can itself be the interrupted turn. Retain the
            # older parked anchor as bounded orientation without letting it
            # replace the request that was actually active at the crash.
            if pending_user and pending_user != active_user:
                checkpoint["prior_user"] = pending_user[:500]
            extra["pending_interrupted_work"] = checkpoint
        elif pending_user:
            extra["pending_interrupted_work"] = {
                "changed": True,
                "reason": str(pending.get("reason") or "paused_work")[:120],
                "user": pending_user[:500],
                "dropped_messages": int(pending.get("dropped_messages") or 0),
                "saved_at": time.time(),
            }
        if closeout:
            extra["closeout"] = closeout
        return extra or None

    def _run_consistency_boundary(self, boundary: str, **kwargs) -> object | None:
        """Run and emit a deterministic consistency boundary check."""
        try:
            report = check_consistency_boundary(boundary, agent=self, **kwargs)
            self._last_consistency_boundary_report = report
            emit_consistency_boundary(report, get_monitor())
            return report
        except Exception:
            return None

    def save_session_closeout(self, *, reason: str = "session boundary") -> dict | None:
        """Write deterministic closeout for real session boundaries."""
        self._last_session_closeout_report = None
        self._last_consistency_boundary_report = None
        if not hasattr(self, "config") or not getattr(self.session, "messages", None):
            self._last_session_closeout_status = "not_applicable"
            return None
        try:
            report = build_session_closeout(self, reason=reason)
            path = write_session_closeout(report)
            meta = closeout_meta(report, path)
        except Exception:
            self._last_session_closeout_status = "failed"
            try:
                self._emit_session_event(None, "closeout_failed")
            except Exception:
                pass
            return None

        self._last_session_closeout_status = "saved"
        self._last_session_closeout_report = report
        boundary = self._run_consistency_boundary("session_closeout", session_closeout=report)
        try:
            monitor = get_monitor()
            if monitor:
                monitor.emit("session_event", {
                    "kind": "closeout_write",
                    "reason": reason,
                    "path": str(path),
                    "clean": bool(getattr(report, "clean", False)),
                    "unresolved": len(getattr(report, "unresolved", []) or []),
                })
        except Exception:
            pass
        if boundary is not None and hasattr(boundary, "as_dict"):
            meta["consistency_boundary"] = boundary.as_dict()
        return meta

    def session_closeout_notice(self, *, snapshot_saved: bool) -> str:
        """Return a safe user notice for the most recent closeout failure."""
        if getattr(self, "_last_session_closeout_status", "") != "failed":
            return ""
        if snapshot_saved:
            return "Session closeout did not complete. The conversation snapshot was saved."
        return "Session closeout did not complete. The conversation snapshot could not be confirmed."

    def autosave_session(
        self,
        *,
        closeout: dict | None = None,
        active_turn_user: str = "",
        allow_empty: bool = False,
    ) -> bool | None:
        """Persist the foreground session, retrying one transient OS write failure."""
        sessions = getattr(self, "_sessions", None)
        session = getattr(self, "session", None)
        if not sessions or session is None or (not allow_empty and not getattr(session, "messages", None)):
            self._last_session_autosave_status = "not_applicable"
            return None
        save_error: Exception | None = None
        for attempt in (1, 2):
            try:
                sessions.save(
                    sessions.current_name,
                    session,
                    extra_meta=self._session_save_extra_meta(
                        closeout=closeout,
                        active_turn_user=active_turn_user,
                    ),
                )
                save_error = None
                break
            except Exception as exc:
                save_error = exc
                if attempt == 1 and isinstance(exc, OSError):
                    try:
                        self._emit_session_event(
                            None,
                            "autosave_retry",
                            error_type=type(exc).__name__,
                        )
                    except Exception:
                        pass
                    time.sleep(0.05)
                    continue
                break
        if save_error is not None:
            self._last_session_autosave_status = "failed"
            try:
                self._emit_session_event(
                    None,
                    "autosave_failed",
                    error_type=type(save_error).__name__,
                    attempts=attempt,
                )
            except Exception:
                pass
            return False
        self._last_session_autosave_status = "saved"
        self._pristine_new_session = None
        event_kind = "active_turn_checkpoint" if str(active_turn_user or "").strip() else "autosave"
        try:
            self._emit_session_event(
                None,
                event_kind,
                slot=str(getattr(sessions, "current_name", "") or ""),
                closeout=bool(closeout),
            )
        except Exception:
            pass
        return True

    def _cmd_desktop(self, rest: str) -> str:
        """Summon the MO Desktop companion window (also Win+Alt+M)."""
        command = rest.strip().lower()
        if command == "help":
            return (
                "MO Desktop is MO's separate desktop companion window (Win+Alt+M or /desktop).\n"
                "It runs on its own isolated session and never owns the terminal taskboard.\n"
                "Use `/desktop trace` to inspect the joined private desktop diagnostics.\n"
            )
        if command in {"trace", "diagnostics", "diag", "log", "logs"}:
            from mo_desktop.diagnostics import build_mo_desktop_trace_report
            return build_mo_desktop_trace_report(getattr(self, "config", None))
        # MO Desktop is a separate MO-branded process (Win+Alt+M + tray).
        # It is NOT a router/planner and never owns the taskboard — passive by design.
        from mo_desktop.desktop_launch import launch_mo_desktop_detached
        return launch_mo_desktop_detached(getattr(self, "config", None))

    def _cmd_undo(self, _rest: str) -> str:
        """Remove the last user+assistant exchange."""
        msgs = self.session.messages
        if not msgs:
            return "Nothing to undo."
        # Remove from tail: last assistant, then last user
        while msgs and msgs[-1].get("role") in ("assistant", "tool"):
            msgs.pop()
        if msgs and msgs[-1].get("role") == "user":
            self._last_undone_input = msgs[-1].get("content", "")
            msgs.pop()
        else:
            self._last_undone_input = ""
        if self.session.turn_count > 0:
            self.session.turn_count -= 1
        return "Exchange undone; scrollback stays. /retry"

    def _cmd_retry(self, _rest: str) -> str:
        """Prepare the last user prompt and return an explicit retry action."""
        last_input = getattr(self, "_last_undone_input", "")
        if not last_input:
            # Find last user message in history
            for msg in reversed(self.session.messages):
                if msg.get("role") == "user":
                    last_input = msg.get("content", "")
                    break
        if not last_input:
            return "Nothing to retry."
        self._retry_pending_input = last_input
        return command_control("retry")

    def _game_service(self):
        from ..game_collaboration import GameCollaborationService

        return GameCollaborationService(getattr(self, "config", {}) or {})

    def _game_project_cwd(self) -> str:
        effective = getattr(self, "_effective_project_cwd", None)
        if callable(effective):
            return str(effective() or "")
        return str(getattr(self, "project_cwd", "") or os.getcwd())

    def _cmd_game(self, rest: str) -> str:
        """Manage Game Collaboration; state-changing actions return Terminal notices."""
        from ..game_collaboration.context import render_status
        from ..game_collaboration.store import GameCollaborationConflict, GameCollaborationError

        service = self._game_service()
        parts = str(rest or "").strip().split(maxsplit=2)
        sub = parts[0].lower() if parts else "status"
        arg = parts[1].strip() if len(parts) > 1 else ""
        tail = parts[2].strip() if len(parts) > 2 else ""
        binding = getattr(self, "_game_collaboration_binding", {})
        if not isinstance(binding, dict):
            binding = {"mode": "off", "surface": "terminal"}
        cwd = self._game_project_cwd()

        try:
            if sub == "start":
                record, _changed = service.start(arg, cwd)
                self._game_collaboration_binding = service.binding(record, "active")
                return SlashCommandResult(
                    "Game Collaboration is active in this Terminal.",
                    kind="notice",
                )
            if sub in {"status", "review"}:
                selector = arg
                record = service.status(selector, cwd)
                if record is None:
                    if binding.get("mode") in {"active", "paused"}:
                        record = service.store.load(str(binding.get("project_key") or ""))
                    if record is None:
                        return "No Game Collaboration project is active. Use `/game start`."
                if binding.get("mode") == "active" and str(binding.get("project_key")) == str(record.get("project_key")):
                    binding["record_revision"] = int(record.get("record_revision", 0) or 0)
                    self._game_collaboration_binding = binding
                return render_status(record, review=sub == "review")
            if sub == "pause":
                record = service.pause(binding)
                self._game_collaboration_binding = service.binding(record, "paused")
                return SlashCommandResult(
                    "Game Collaboration is paused; normal MO behavior continues.",
                    kind="notice",
                )
            if sub == "resume":
                selector = arg or str(binding.get("project_key") or "")
                record = service.resume(selector, cwd)
                self._game_collaboration_binding = service.binding(record, "active")
                return SlashCommandResult(
                    "Game Collaboration is active in this Terminal.",
                    kind="notice",
                )
            if sub in {"ask", "decide", "propose"}:
                if not arg or not tail:
                    return f"Use: /game {sub} <id> <text>"
                if sub == "ask":
                    record = service.ask(binding, arg, tail)
                elif sub == "decide":
                    record = service.decide(binding, arg, tail)
                else:
                    record = service.propose(binding, arg, tail)
                self._game_collaboration_binding = service.binding(record, "active")
                return SlashCommandResult(
                    f"Game {sub} recorded: {arg.upper()}.",
                    kind="notice",
                )
            if sub in {"approve", "reject"}:
                if not arg or tail:
                    return f"Use: /game {sub} <proposal-id>"
                record = service.approve(binding, arg) if sub == "approve" else service.reject(binding, arg)
                self._game_collaboration_binding = service.binding(record, "active")
                return SlashCommandResult(
                    f"Game proposal {arg.upper()} {sub}ed.",
                    kind="notice",
                )
            if sub == "stop":
                self._game_collaboration_binding = {"mode": "off", "surface": "terminal"}
                return SlashCommandResult(
                    "Game Collaboration stopped for this Terminal session; its project record was preserved.",
                    kind="notice",
                )
            return (
                "Use: /game start|status|review|pause|resume|ask|decide|propose|approve|reject|stop"
            )
        except GameCollaborationConflict:
            return "Game project changed in another Terminal session; nothing was overwritten. Use `/game status` to inspect it."
        except GameCollaborationError as exc:
            return str(exc)

    def _cmd_session(self, rest: str) -> SlashCommandResult | str:
        """Manage local sessions and explicit portable-conversation sharing."""
        if not self._sessions:
            return "Session manager not available."
        rest = rest.strip()
        if not rest or rest.lower() == "list":
            return self._sessions.render_list(surface="terminal")
        parts = rest.split(maxsplit=1)
        sub = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if sub == "save":
            name = arg or self._sessions.current_name
            closeout = self.save_session_closeout(reason=f"session save:{name}")
            result = self._sessions.save(name, self.session, extra_meta=self._session_save_extra_meta(closeout=closeout))
            self._pristine_new_session = None
            if closeout:
                report = getattr(self, "_last_session_closeout_report", None)
                if report:
                    result += "\n" + render_session_closeout(report, path=str(closeout.get("path", "")))
                boundary = getattr(self, "_last_consistency_boundary_report", None)
                if boundary:
                    result += "\n" + render_consistency_boundary(boundary)
            notice = self.session_closeout_notice(snapshot_saved=True)
            if notice:
                result += "\n" + notice
            return result
        if sub == "share":
            name = arg or self._sessions.current_name
            try:
                if name == self._sessions.current_name:
                    self._sessions.save(
                        name,
                        self.session,
                        extra_meta=self._session_save_extra_meta(),
                    )
                info = self._sessions.share_portable(name)
                if name == self._sessions.current_name:
                    self._sessions.bind_portable_session(self.session, name, info)
                return (
                    f"Portable conversation enabled: {info['name']}\n"
                    "Only devices with explicit conversation access can list or continue it. "
                    "Use /session unshare to keep it local again."
                )
            except Exception as exc:
                from ..session.sessions import PortableConversationError

                if isinstance(exc, PortableConversationError):
                    return f"Portable conversation not changed: {exc}"
                raise
        if sub == "unshare":
            name = arg or self._sessions.current_name
            info = next(
                (item for item in self._sessions.list_portable() if item["name"] == name),
                None,
            )
            if info is None:
                return f"Portable conversation not found: {name}"
            try:
                kept = self._sessions.unshare_portable(
                    info["conversation_id"],
                    expected_revision=info["revision"],
                )
                if name == self._sessions.current_name:
                    self._sessions.unbind_portable_session(self.session, name=name)
                return f"Portable access removed; local session kept: {kept}"
            except Exception as exc:
                from ..session.sessions import PortableConversationError

                if isinstance(exc, PortableConversationError):
                    return f"Portable conversation not changed: {exc}"
                raise
        if sub == "remove":
            if not arg:
                return "Use: /session remove <name>"
            return self._sessions.remove(arg)
        # Treat as session name to switch to
        name = rest
        if name.isdigit():
            sessions = self._sessions.list_sessions(surface="terminal")
            idx = int(name) - 1
            if 0 <= idx < len(sessions):
                name = sessions[idx]["name"]
            else:
                return f"Invalid session number. Use 1-{len(sessions)}."
        closeout = self.save_session_closeout(reason=f"session switch:{name}")
        result = self._sessions.switch(
            name,
            self.session,
            extra_meta=self._session_save_extra_meta(closeout=closeout),
            save_current=self._consume_pristine_new_session(),
        )
        self._restore_loaded_session_meta()
        notice = self.session_closeout_notice(snapshot_saved=True)
        if notice:
            result += "\n" + notice
        return command_control("clear_transcript", result)

    def _restore_loaded_session_meta(self) -> None:
        """Restore all runtime metadata owned by a switched-in session.

        SessionManager owns serialization, while the agent owns the live fields.
        Keeping this at the switch boundary prevents `/resume` from loading a
        transcript but silently losing its exact parked work objective.
        """
        meta = getattr(self.session, "_loaded_meta", {})
        if not isinstance(meta, dict):
            meta = {}
        self._conversation_role = None
        self._active_role()
        binding = meta.get("game_collaboration")
        if isinstance(binding, dict) and str(binding.get("mode") or "") in {"active", "paused"}:
            self._game_collaboration_binding = {
                "mode": str(binding.get("mode") or "paused"),
                "surface": "terminal",
                "project_key": str(binding.get("project_key") or "")[:64],
                "project_name": str(binding.get("project_name") or "Game project")[:120],
                "record_revision": int(binding.get("record_revision", 0) or 0),
            }
        else:
            self._game_collaboration_binding = {"mode": "off", "surface": "terminal"}
        self._restore_context_saving_meta(meta)
        self._pending_interrupted_work = {}
        saved_pending = meta.get("pending_interrupted_work")
        if (
            isinstance(saved_pending, dict)
            and str(saved_pending.get("user") or "").strip()
        ):
            self._pending_interrupted_work = dict(saved_pending)
        self._reset_session_taskboard(restore=True)

    def _reset_session_taskboard(self, *, restore: bool) -> None:
        """Align in-memory taskboard truth with a new or switched session."""
        self._active_task_board = None
        gateway = getattr(self, "gateway", None)
        if gateway is None:
            return
        gateway.last_task_board = None
        gateway.previous_task_board = None
        if not restore:
            gateway.last_resumable_board = None
            return
        try:
            from ..tasking.task_board import resume_last_board

            gateway.last_task_board = gateway.last_resumable_board = resume_last_board(
                session_id=str(getattr(self.session, "session_id", "") or ""),
                max_age_hours=None,
            )
        except Exception:
            traceback.print_exc()
            gateway.last_resumable_board = None

    def _cmd_resume(self, _rest: str) -> SlashCommandResult | str:
        """Resume this Terminal's saved session, or the latest Terminal session."""
        if not self._sessions:
            return "Session manager not available."
        from ..runtime.continuity import TERMINAL_SLOT_FAMILY, _surface_family

        current = self._sessions.current_name
        terminal_sessions = [
            row
            for row in self._sessions.list_sessions()
            if _surface_family(
                str(row.get("name") or ""),
                portable=bool(row.get("portable")),
                session_id=str(row.get("session_id") or ""),
                surface=str(row.get("surface") or ""),
            ) == TERMINAL_SLOT_FAMILY
            or (bool(row.get("portable")) and row.get("name") == current)
        ]
        current_session = next(
            (row for row in terminal_sessions if row.get("name") == current),
            None,
        )
        selected = current_session or (terminal_sessions[0] if terminal_sessions else None)
        latest = str(selected.get("name") or "") if selected else ""
        if not latest:
            return "No saved Terminal sessions to resume."
        closeout = self.save_session_closeout(reason=f"session resume:{latest}")
        result = self._sessions.switch(
            latest,
            self.session,
            extra_meta=self._session_save_extra_meta(closeout=closeout),
            save_current=self._consume_pristine_new_session(),
        )
        self._restore_loaded_session_meta()
        notice = self.session_closeout_notice(snapshot_saved=True)
        if notice:
            result += "\n" + notice
        return command_control("clear_transcript", result)

    def _cmd_reload(self, _rest: str) -> str:
        """Reload model instructions, config, and profile without restart."""
        self.config = load_config(self.config_path)
        self._load_runtime_preferences()
        from .. import local_extensions

        local_extensions.configure(self.config)
        self._init_providers()
        self._init_agent_config()
        self._restore_runtime_preferences()
        from ..lsp import LspManager

        if getattr(self, "lsp_manager", None) is not None:
            self.lsp_manager.stop_all()
        self.lsp_manager = LspManager.from_config(self.config, root_path=self._effective_project_cwd())
        system_path = self.config.get("paths", {}).get("system_prompt", "")
        self.system_message, self.system_prompt_source = self._load_system_message(system_path)
        self.session.system_message = self.system_message
        self.profile = Profile.load(
            resolve_state_path(self.config.get("paths", {}).get("memory_file", "memory/mo.db"), self.config)
        )
        return command_result(
            "Reloaded:\n"
            f"  model instructions: {self.system_prompt_source} · {len(self.system_message)} chars\n"
            f"  config: {self.config_path}\n"
            f"  profile: {self.profile.user_name or 'Operator'}\n"
            f"  provider: {self.provider_name} / {self.model}", kind="report",
        )

    def _cmd_settings(self, _rest: str) -> str:
        """Show the effective runtime and persisted Terminal display settings."""
        reasoning = getattr(self, "reasoning", self.config.get("agent", {}).get("reasoning", "high"))
        interface = self.config.get("interface") if isinstance(self.config.get("interface"), dict) else {}
        show = interface.get("show") if isinstance(interface.get("show"), dict) else {}
        surface = getattr(self, "tui", None)
        show_reasoning = bool(getattr(surface, "_show_reasoning", show.get("reasoning", False)))
        show_tools = bool(getattr(surface, "_show_tool_activity", show.get("tools", True)))
        hints = bool(getattr(self, "_hints_enabled", True))
        activity_override = getattr(self, "_activity_enabled_override", None)
        if activity_override is None:
            from interface.workspace import activity_enabled

            activity = activity_enabled(self.config)
        else:
            activity = bool(activity_override)
        display = (
            f"reasoning={'on' if show_reasoning else 'off'}, "
            f"tools={'on' if show_tools else 'off'}, "
            f"hints={'on' if hints else 'off'}, "
            f"activity={'on' if activity else 'off'}"
        )
        requested_limit, limit_field = requested_output_token_limit(
            self.active_provider,
            self.max_tokens,
        )
        limit_detail = (
            f"{requested_limit} (sent as {limit_field})"
            if requested_limit is not None
            else f"{self.max_tokens} (configured; not sent by {self.api_mode})"
        )
        provider_request_limit = self.config.get("agent", {}).get("max_provider_requests_per_turn", DEFAULT_PREFERENCES["agent.max_provider_requests_per_turn"])
        return (
            f"MO settings:\n"
            f"  model:        {self.provider_name} / {self.model}\n"
            f"  reasoning: {reasoning}\n"
            f"  display:   {display}\n"
            f"  temperature: {self.temperature}\n"
            f"  max_tokens: {limit_detail}\n"
            f"  provider requests per turn: {provider_request_limit} (configured; 0=unlimited)\n"
            f"  context budget: {self.context_budget_tokens:,} tokens ({self.context_budget_source})\n"
            f"  project: {getattr(self, 'project_cwd', os.getcwd())}\n"
            f"  home: {getattr(self, 'runtime_home', '')}\n"
            f"  invoked: {getattr(self, 'invoked_as', 'mo')}\n"
            f"  safeguards: {'on' if self.sandbox_config['enabled'] else 'off'}\n"
            f"  profile: {self.profile.user_name or 'not set'}\n"
            f"  session slot: {self._sessions.current_name if self._sessions else 'main'}"
        )

    def _cmd_projects(self, _rest: str) -> str:
        if not self.profile.projects:
            return "No project history yet. Projects appear here after MO opens in them."
        current = os.path.normcase(os.path.abspath(getattr(self, "project_cwd", None) or os.getcwd()))
        entries = sorted(self.profile.projects.values(), key=lambda e: e.last_opened, reverse=True)
        lines = [f"Project history · {len(entries)}", "Recently opened folders, not a project switcher.", ""]
        for entry in entries:
            is_current = os.path.normcase(os.path.abspath(entry.path)) == current
            lines.append(f"{entry.name}" + (" · current" if is_current else ""))
            lines.append(f"  {entry.path}")
            lines.append(f"  {entry.session_count} sessions · last {format_profile_time(entry.last_opened)}")
            lines.append("")
        return "\n".join(lines)

    def _cmd_new(self, _rest: str) -> SlashCommandResult:
        # Record current session stats before clearing
        closeout = self.save_session_closeout(reason="new session")
        snapshot_saved = None
        if getattr(self.session, "messages", None):
            snapshot_saved = self.autosave_session(closeout=closeout)
            if snapshot_saved is not True:
                result = (
                    "New session not started because the conversation snapshot could not be saved. "
                    "The current session is unchanged."
                )
                notice = self.session_closeout_notice(snapshot_saved=False)
                if notice:
                    result += "\n" + notice
                return SlashCommandResult(
                    result,
                    kind="error",
                )
        input_tokens = sum(e.get("input_tokens", 0) for e in self.session.token_log)
        output_tokens = sum(e.get("output_tokens", 0) for e in self.session.token_log)
        self.profile.record_session(
            turns=self.session.turn_count,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
        )
        self.session.clear()
        self.set_conversation_role(None)
        self.session.session_id = f"mo-{int(time.time())}"
        self._pending_interrupted_work = {}
        self._reset_session_taskboard(restore=False)
        # `/new` leaves a pristine live object detached from the saved current
        # slot so `/resume` or `/session <same-slot>` can recover the conversation
        # that was just autosaved.  Track that exact state instead of treating
        # every empty transcript as disposable.
        self._mark_pristine_new_session()
        result = f"New session: {self.session.session_id}"
        notice = self.session_closeout_notice(snapshot_saved=snapshot_saved is True)
        if notice:
            result += "\n" + notice
        return command_control("clear_transcript", result)

    def _mark_pristine_new_session(self) -> None:
        """Protect a fresh empty live session from overwriting its durable slot."""
        self._pristine_new_session = (
            id(self._sessions),
            str(getattr(self._sessions, "current_name", "") or ""),
            str(getattr(self.session, "session_id", "") or ""),
        )

    def _consume_pristine_new_session(self) -> bool:
        """Return whether a session switch must save the live session first."""
        marker = getattr(self, "_pristine_new_session", None)
        current = (
            id(getattr(self, "_sessions", None)),
            str(getattr(getattr(self, "_sessions", None), "current_name", "") or ""),
            str(getattr(getattr(self, "session", None), "session_id", "") or ""),
        )
        pristine = bool(
            marker == current
            and not getattr(getattr(self, "session", None), "messages", None)
            and int(getattr(getattr(self, "session", None), "turn_count", 0) or 0) == 0
        )
        self._pristine_new_session = None
        return not pristine

    def _cmd_goal(self, rest: str) -> str:
        """Handle /goal command: start, continue, stop, or status."""
        from ..goal import GoalRunner, parse_goal_budget

        rest = rest.strip()
        command = rest.lower()

        # Lazy init runner
        if not self._goal_runner:
            self._goal_runner = GoalRunner(self)

        runner = self._goal_runner

        if command in ("stop", "cancel", "abort"):
            text = runner.stop().replace("[GOAL STOPPED] ", "Goal stopped · ", 1)
            return SlashCommandResult(text, kind="report", action="goal_stopped")

        if command in ("status", "info", ""):
            if self._goal_active:
                if command == "":
                    return command_control("goal_continue")
                return runner.status()
            if command == "":
                plan = getattr(self, "_goal_plan", None)
                if plan and getattr(plan, "state", "") == "paused":
                    return (
                        "Paused goal available.\n"
                        "Use: /goal resume to continue, /goal status to inspect, or /goal <task> to start a new goal."
                    )
                return (
                    "No active goal.\n"
                    "Use: /goal <task> to start an autonomous goal.\n"
                    "  /goal stop     — stop active goal\n"
                    "  /goal status   — show progress\n"
                    "  Ctrl+G         — toggle background/foreground"
                )
            return runner.status()

        if command in ("continue", "resume"):
            if self._goal_active:
                return command_control("goal_continue")
            plan = getattr(self, "_goal_plan", None)
            if plan and getattr(plan, "state", "") == "paused":
                plan.state = "running"
                plan.stop_reason = ""
                plan.stop_kind = ""
                self._goal_active = True
                return command_control("goal_continue")
            return "No active goal to continue."

        objective_tokens = rest.split()
        objective, budget = parse_goal_budget(objective_tokens)
        if not objective:
            return "Usage: /goal <task>"
        if self._goal_objective_too_generic(objective):
            return "Goal needs a specific objective. Example: /goal review interface visuals for inconsistencies and report findings"

        self._goal_pending_objective = objective
        self._goal_pending_budget = budget
        return command_control("goal_start")

    @staticmethod
    def _goal_objective_too_generic(objective: str) -> bool:
        text = " ".join(str(objective or "").lower().split())
        generic = {
            "build something",
            "do something",
            "give yourself a goal",
            "make something",
            "work on something",
            "start a goal",
        }
        return text in generic or len(text.split()) < 3
