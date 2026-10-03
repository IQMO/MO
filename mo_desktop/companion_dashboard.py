"""CompanionSurface's Dashboard lane: source registration, section providers,
snapshot/notice collection, and the compact card data/actions composition."""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from typing import Any

from core.dashboard.registry import DashboardRow, DashboardSection, register as register_dashboard
from mo_desktop.desktop_log import write_stderr as _write_stderr


def redact_sensitive_text(text: str) -> str:
    from core.tooling.sandbox import redact_sensitive_text as redact

    return redact(text)


class CompanionDashboardMixin:
    """Verbatim extraction from companion.py; state and composition stay
    with the host class."""

    def _register_dashboard_sources(self) -> None:
        from mo_desktop import notify
        from core.dashboard.providers import register_core_dashboard_defaults
        from core.runtime.scheduler import scheduler_paths

        register_core_dashboard_defaults(self._dashboard_snapshot)
        register_dashboard(self._dashboard_profile_section, order=10, key="you")
        register_dashboard(self._dashboard_mo_section, order=20, key="mo")
        register_dashboard(self._dashboard_artifacts_section, order=55, key="desktop_artifacts")
        register_dashboard(self._dashboard_communication_section, order=60, key="communication")
        register_dashboard(self._dashboard_automation_section, order=70, key="automations")
        notify.register_source(self._scheduler_notice_source, order=40, key="scheduler")
        notify.register_source(self._mail_notice_source, order=45, key="gmail")
        try:
            runs = scheduler_paths(self._agent).runs
            self._scheduler_run_offset = runs.stat().st_size if runs.exists() else 0
        except OSError:
            self._scheduler_run_offset = 0

    def _dashboard_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._dashboard_snapshot_cache and now - self._dashboard_snapshot_at < 2.0:
            return self._dashboard_snapshot_cache
        from core.dashboard.snapshot import build_dashboard_snapshot

        snapshot = build_dashboard_snapshot(self._agent)
        snapshot["desktop_apps"] = list(self.private_desktop_app_specs())
        try:
            from core.systemcare.dashboard import read_dashboard_status

            snapshot["systemcare"] = read_dashboard_status(
                getattr(self._agent, "config", {}) or {}
            )
        except Exception:
            _write_stderr(traceback.format_exc())
        try:
            from core.state.paths import resolve_state_path
            from mo_desktop.everywhere import terminal_binding_status

            config = getattr(self._agent, "config", {}) or {}
            snapshot["everywhere"] = terminal_binding_status(
                config,
                resolve_state_path("memory/sessions", config),
            )
        except Exception:
            _write_stderr(traceback.format_exc())
        self._dashboard_snapshot_cache = snapshot
        self._dashboard_snapshot_at = now
        return self._dashboard_snapshot_cache

    def _dashboard_profile_section(self) -> Any:
        profile = getattr(self._agent, "profile", None)
        name = str(getattr(profile, "user_alias", "") or getattr(profile, "user_name", "") or "Operator")
        return DashboardSection("You", (DashboardRow(name, icon="MO", sub="local profile"),), order=10)

    def _dashboard_mo_section(self) -> Any:
        focus = redact_sensitive_text(self._terminal_focus()).strip()
        sub = (focus[:64] + "...") if len(focus) > 67 else (focus or "No active terminal focus")
        return DashboardSection("MO", (
            DashboardRow("MO terminal", icon=">_", sub=sub, meta="sync",
                         action=self._dashboard_sync_terminal),
        ), order=20)

    def _dashboard_communication_section(self) -> Any:
        telegram_cfg = (getattr(self._agent, "config", {}) or {}).get("telegram", {}) or {}
        telegram = getattr(self._agent, "telegram_gateway", None) or getattr(self._agent, "_telegram_gateway", None)
        running = bool(getattr(telegram, "_poll_thread", None) and telegram._poll_thread.is_alive())
        telegram_meta = "live" if running else ("configured" if telegram_cfg.get("enabled") else "open")
        from core.mail.service import MailService

        mail = MailService(getattr(self._agent, "config", {}) or {}).status()
        gmail_state = str(mail.get("state") or "unknown")
        gmail_count = mail.get("unread")
        gmail_sub = f"{int(gmail_count)} unread" if gmail_count is not None else ("Ask MO about Gmail" if gmail_state == "disabled" else gmail_state.replace("_", " "))
        gmail_meta = "setup" if gmail_state == "disabled" else gmail_state.replace("_", " ")
        return DashboardSection("Communication", (
            DashboardRow("Outlook.com", icon="O", sub="Ask MO about Outlook", meta="chat",
                         action=self.summon),
            DashboardRow("Gmail", icon="G", sub=gmail_sub, meta=gmail_meta,
                         action=self.summon),
            DashboardRow("Telegram", icon="T", sub="Open messages", meta=telegram_meta,
                         action=lambda: self._open_dashboard_url("https://web.telegram.org/", "Telegram")),
        ), order=30)

    def _dashboard_automation_section(self) -> Any:
        from core.runtime.scheduler import manage_scheduler_jobs

        jobs = manage_scheduler_jobs(self._agent, "list").get("jobs", [])
        agent_jobs = [job for job in jobs if str(job.get("kind") or "turn") != "script"]
        compute_jobs = [job for job in jobs if str(job.get("kind") or "") == "script"]

        def row(label: str, icon: str, items: list[dict[str, Any]]) -> DashboardRow:
            active = sum(1 for item in items if item.get("enabled", True))
            next_times = [float(item["next_run_at"]) for item in items if item.get("enabled", True) and item.get("next_run_at")]
            sub = "No scheduled tasks" if not items else (f"Next run {time.strftime('%b %d %H:%M', time.localtime(min(next_times)))}" if next_times else "No upcoming run")
            return DashboardRow(label, icon=icon, sub=sub, meta=f"{active}/{len(items)}", action=self._dashboard_schedule_detail)

        return DashboardSection("Automations", (
            row("Agent tasks", "A", agent_jobs),
            row("Compute jobs", ">_", compute_jobs),
        ), order=40)

    def _dashboard_artifacts_section(self) -> Any:
        from core.state.attachments import attachment_summary

        summary = attachment_summary(self._config())
        categories = summary.get("categories") or {}
        total = int(summary.get("total") or 0)
        indexed = int(summary.get("indexed") or 0)
        rows = [DashboardRow(
            f"{total} attachment{'' if total == 1 else 's'}",
            icon="+",
            sub="private Desktop attachment home",
            meta=f"{indexed} indexed",
        )]
        for key, label in (("gallery", "Gallery"), ("audio-video", "Audio/video"), ("documents", "Documents"), ("files", "Files")):
            count = int(categories.get(key) or 0)
            if count:
                rows.append(DashboardRow(f"{label}: {count}", icon="·"))
        return DashboardSection("Desktop artifacts", tuple(rows), order=55)

    def _dashboard_schedule_detail(self) -> None:
        from core.runtime.scheduler import format_scheduler_jobs, manage_scheduler_jobs

        self._show_reply_dialog(format_scheduler_jobs(manage_scheduler_jobs(self._agent, "list")))

    def _scheduler_notice_source(self) -> list[Any]:
        from mo_desktop.notify import Notice
        from core.runtime.scheduler import scheduler_paths

        path = scheduler_paths(self._agent).runs
        if not path.exists():
            return []
        try:
            size = path.stat().st_size
            if size < self._scheduler_run_offset:
                self._scheduler_run_offset = 0
            with path.open("rb") as handle:
                handle.seek(self._scheduler_run_offset)
                lines = handle.readlines()
                self._scheduler_run_offset = handle.tell()
        except OSError:
            return []
        notices = []
        for raw in lines[-8:]:
            try:
                run = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if run.get("kind") == "systemcare":
                from core.systemcare.config import normalized_systemcare_preferences
                if not normalized_systemcare_preferences(self._config())["notifications"]:
                    continue
            ok = str(run.get("status") or "") == "ok"
            detail = str(run.get("output_preview") or run.get("error") or run.get("job_id") or "")[:150]
            notices.append(Notice(
                f"scheduler:{run.get('job_id')}:{run.get('finished_at')}",
                ("Reminder" if ok else "Reminder failed") if run.get("kind") == "reminder"
                else ("automation done" if ok else "automation failed"),
                detail,
                "notify",
                3.0,
            ))
        return notices

    def _mail_notice_source(self) -> list[Any]:
        """Collect encrypted local mail notices; schedule network sync separately."""
        from core.mail.service import MailService
        from mo_desktop.notify import Notice

        service = MailService(self._config())
        status = service.status()
        if status.get("state") == "reconnect_required":
            if not bool(getattr(self, "_mail_reauth_notified", False)):
                self._mail_reauth_notified = True
                return [Notice("gmail:reauth", "Gmail reconnect", "Run /mail connect in MO Terminal", "notify_email", 3.0)]
            return []
        if status.get("state") not in {"connected", "sync_unknown"}:
            return []
        self._mail_reauth_notified = False
        outcomes = getattr(self, "_mail_sync_outcomes", None)
        if outcomes is None:
            outcomes = queue.SimpleQueue()
            self._mail_sync_outcomes = outcomes
        now = time.monotonic()
        interval = max(60, int(service.settings.get("poll_seconds") or 120))
        last = float(getattr(self, "_mail_last_attempt", 0) or 0)
        if now - last >= interval and not bool(getattr(self, "_mail_sync_in_flight", False)):
            self._mail_last_attempt = now
            self._mail_sync_in_flight = True

            def _sync() -> None:
                try:
                    result = service.sync()
                    outcomes.put("resynced" if result.get("resynced") else "ok")
                    self._mail_sync_error_notified = False
                except Exception:
                    outcomes.put("error")
                finally:
                    self._mail_sync_in_flight = False

            try:
                threading.Thread(target=_sync, name="mo-desktop-mail-sync", daemon=True).start()
            except Exception:
                self._mail_sync_in_flight = False
                outcomes.put("error")
        try:
            event = outcomes.get_nowait()
        except queue.Empty:
            event = ""
        if event == "error":
            if not bool(getattr(self, "_mail_sync_error_notified", False)):
                self._mail_sync_error_notified = True
                return [Notice("gmail:sync", "Gmail needs attention", "Open /mail status in MO Terminal", "notify_email", 3.0)]
        elif event == "resynced":
            self._mail_sync_error_notified = False
            return [Notice("gmail:resync", "Gmail refreshed", "Open Gmail to review recent mail", "notify_email", 3.0)]
        count = service.claim_notices()
        if not count:
            return []
        return [Notice("gmail:new", "New Gmail", f"{count} new unread message{'s' if count != 1 else ''}", "notify_email", 3.0,
                       activate=self.summon)]

    def _collect_notices_async(self) -> None:
        """Collect filesystem/SQLite-backed notice sources off the Tk frame lane."""
        if bool(getattr(self, "_notice_poll_in_flight", False)):
            return
        self._notice_poll_in_flight = True

        def _collect() -> None:
            try:
                from core.runtime.instance import get_instance_id
                from core.transfer.presence import claim_transfer_notices
                from mo_desktop.notify import Notice, collect

                notices = list(collect())
                for item in claim_transfer_notices(
                    self._config(),
                    surface="mo_desktop",
                    instance_id=get_instance_id(),
                    limit=3,
                ):
                    size = int(item.get("size_bytes") or 0)
                    notices.append(
                        Notice(
                            f"transfer:{item.get('transfer_id')}",
                            "Incoming complete",
                            f"{item.get('name')} · {size:,} bytes",
                            "notify",
                            3.0,
                        )
                    )
            except Exception:
                notices = []

            def _apply() -> None:
                self._notice_poll_in_flight = False
                if not self._running:
                    return
                for notice in notices:
                    self._emit_notice(notice)

            self._post_gui_call(_apply)

        try:
            threading.Thread(target=_collect, name="mo-desktop-notices", daemon=True).start()
        except Exception:
            self._notice_poll_in_flight = False

    def _dashboard_sync_terminal(self) -> None:
        bubble = getattr(self, "_bubble", None)
        if bubble and bubble is not False:
            bubble.hide()
        self._sync_with_terminal()

    def _dashboard_switch_terminal(self) -> None:
        from core.state.paths import resolve_state_path
        from mo_desktop.everywhere import (
            desktop_binding,
            select_desktop_thread,
            terminal_session_candidates,
        )

        config = getattr(self._agent, "config", {}) or {}
        choices = terminal_session_candidates(
            config,
            resolve_state_path("memory/sessions", config),
        )
        if len(choices) > 1:
            self._show_terminal_choices(choices)
            return
        if len(choices) == 1:
            current = str((desktop_binding(config) or {}).get("thread_id") or "")
            selected = str(choices[0].get("thread_id") or "")
            if selected and selected != current:
                select_desktop_thread(config, selected)
        self._dashboard_sync_terminal()

    def _open_dashboard_url(self, url: str, label: str) -> bool:
        from tools.computer import execute_computer_act

        result = execute_computer_act({"kind": "desktop", "action": "open", "url": url})
        if result.startswith("Opened "):
            self._log_action("dashboard", f"Opened {label}")
            return True
        self._set_status(f"Could not open {label}", self._visual_palette.warn)
        return False

    def _display_dashboard(self) -> None:
        """Right-click / 2nd Ctrl+Ctrl: show the compact dashboard
        on the cube-attached panel. The panel is the ONLY surface: if it cannot render, this records
        why and shows nothing. Disk/profile/graph synthesis never runs on Tk's GUI thread: the
        current snapshot (or an honest empty state on first open) paints immediately. The daemon
        refresh starts only after the reveal's final frame, then updates this same panel in place."""
        data, actions = self._dashboard_compact_data(self._dashboard_snapshot_cache)
        if self._show_on_reply_surface(
            "dashboard",
            lambda bubble: bool(bubble.show_dashboard(data, actions)),
        ):
            self._visible = True
            # Snapshot gathering and its GUI-side projection must not compete with
            # the short reveal. ReplyBubble owns the transition boundary, so there
            # is no duplicated duration or timer in this adapter.
            bubble = self._bubble
            bubble.after_panel_transition(self._refresh_dashboard_snapshot_async)

    def _refresh_dashboard_snapshot_async(self) -> None:
        """Refresh dashboard evidence without ever making a gesture wait on filesystem work."""
        if self._dashboard_snapshot_refreshing:
            return
        self._dashboard_snapshot_refreshing = True

        def _refresh() -> None:
            try:
                snapshot = dict(self._dashboard_snapshot())
                agent = getattr(self, "_agent", None)
                if agent is not None:
                    from core.mail.service import dashboard_glance

                    glances = {}
                    try:
                        glances["gmail"] = dashboard_glance(
                            "gmail", config=getattr(agent, "config", {}), agent=agent)
                    except Exception:
                        pass
                    snapshot["_mail_glance"] = glances
            except Exception:
                snapshot = {}
            finally:
                self._dashboard_snapshot_refreshing = False

            def _apply() -> None:
                bubble = getattr(self, "_bubble", None)
                if not bubble or bubble is False:
                    return
                try:
                    visible = bool(bubble.visible())
                    state = str(getattr(getattr(bubble, "_panel_state", ""), "value", ""))
                except Exception:
                    return
                if not visible or state != "dashboard":
                    return
                data, actions = self._dashboard_compact_data(snapshot)
                self._show_on_reply_surface(
                    "dashboard refresh",
                    lambda current: bool(current.show_dashboard(data, actions)),
                )

            self._post_gui_call(_apply)

        threading.Thread(target=_refresh, name="mo-desktop-dashboard", daemon=True).start()

    def _dashboard_compact_data(self, snapshot: dict[str, Any] | None = None) -> tuple[dict, dict]:
        """Build the compact dashboard (data + hit actions) from inline metrics,
        mail shortcuts, and delegated owner controls."""
        from core.dashboard.projection import build_dashboard_projection

        snap = snapshot if isinstance(snapshot, dict) else {}
        everywhere = snap.get("everywhere") or {}
        systemcare = snap.get("systemcare") if isinstance(snap.get("systemcare"), dict) else {}
        user_projection = build_dashboard_projection(
            snap,
            perspective="user",
            surface="desktop",
        )
        operations_projection = build_dashboard_projection(
            snap,
            perspective="operations",
            surface="desktop",
        )

        def _metric_tile(metric: Any) -> dict[str, Any]:
            item = metric if isinstance(metric, dict) else {}
            tile = {
                "value": str(item.get("value") if item.get("value") is not None else "—"),
                "label": str(item.get("label") or "Not reported"),
                "tone": str(item.get("tone") or "neutral"),
            }
            return tile

        def _section_rows(
            projection: dict[str, Any],
            section_ids: tuple[str, ...] = (),
            *,
            one_per_section: bool = False,
        ) -> list[dict[str, str]]:
            sections = projection.get("sections") if isinstance(projection.get("sections"), list) else []
            selected = []
            if section_ids:
                selected = [
                    section
                    for identifier in section_ids
                    for section in sections
                    if isinstance(section, dict) and section.get("id") == identifier
                ]
            if not selected and sections:
                selected = [section for section in sections if isinstance(section, dict)]
            rows: list[dict[str, str]] = []
            used: set[tuple[str, str]] = set()

            def _append(section: dict[str, Any], raw_item: Any) -> None:
                if not isinstance(raw_item, dict):
                    return
                key = (str(section.get("id") or ""), str(raw_item.get("id") or ""))
                if key in used:
                    return
                used.add(key)
                value = str(raw_item.get("value") or raw_item.get("detail") or "Not reported")
                if key == ("communication", "gmail") and value == "Connected":
                    value = str(raw_item.get("detail") or value)
                rows.append({
                    "label": str(raw_item.get("label") or section.get("title") or "Status"),
                    "value": value,
                    "tone": str(raw_item.get("tone") or "neutral"),
                })

            if one_per_section:
                for section in selected:
                    items = section.get("items") if isinstance(section.get("items"), list) else []
                    if items:
                        _append(section, items[0])
                    if len(rows) == 3:
                        return rows
            for section in selected:
                for raw_item in section.get("items") or []:
                    _append(section, raw_item)
                    if len(rows) == 3:
                        return rows
            return rows

        action_by_id = {
            str(action.get("id") or ""): dict(action)
            for action in list(user_projection.get("actions") or [])
            if isinstance(action, dict) and action.get("id")
        }

        def _view(
            projection: dict[str, Any],
            *,
            section_ids: tuple[str, ...] = (),
            metric_ids: tuple[str, ...] = (),
            action_ids: tuple[str, ...] = (),
            one_row_per_section: bool = False,
        ) -> dict[str, Any]:
            metrics = projection.get("metrics") if isinstance(projection.get("metrics"), list) else []
            selected_metrics = [
                metric for identifier in metric_ids
                for metric in metrics
                if isinstance(metric, dict) and metric.get("id") == identifier
            ] if metric_ids else [metric for metric in metrics if isinstance(metric, dict)]
            # A view is not a four-slot grid. Never fill it with unrelated counts.
            selected_metrics = [metric for metric in selected_metrics
                                if str(metric.get("value", "")).lower()
                                not in {"", "0", "0/0", "idle", "none"}]
            status_payload = projection.get("status") if isinstance(projection.get("status"), dict) else {}
            return {
                "status": str(status_payload.get("label") or "Dashboard"),
                "tone": str(status_payload.get("tone") or "neutral"),
                "tiles": [_metric_tile(metric) for metric in selected_metrics[:4]],
                "rows": _section_rows(
                    projection,
                    section_ids,
                    one_per_section=one_row_per_section,
                ),
                "actions": [
                    action_by_id[action_id]
                    for action_id in action_ids
                    if action_id in action_by_id
                ][:4],
            }

        apps = list(snap.get("desktop_apps") or [])[:4]
        data = {"comms": [{"label": "Outlook"}, {"label": "Gmail"}, {"label": "Telegram"}],
                "apps": apps,
                "apps_total": len(snap.get("desktop_apps") or []),
                "systemcare": systemcare,
                "views": {
                    "overview": _view(
                        user_projection,
                        section_ids=("work", "personal", "surfaces"),
                        metric_ids=("open_work", "learning"),
                        action_ids=("dashboard.open.work", "dashboard.open.projects", "dashboard.open.learning", "dashboard.open.checks"),
                        one_row_per_section=True,
                    ),
                    "work": _view(
                        user_projection,
                        section_ids=("work",),
                        metric_ids=("open_work", "work_state"),
                        action_ids=("dashboard.open.work", "dashboard.open.goals", "dashboard.open.schedules", "dashboard.open.checks"),
                    ),
                    "personal": _view(
                        user_projection,
                        section_ids=("communication", "personal"),
                        metric_ids=("profile", "learned_behavior", "learning"),
                        action_ids=("dashboard.open.profile", "dashboard.open.learning", "dashboard.open.skills"),
                    ),
                    "systems": _view(
                        operations_projection,
                        section_ids=(
                            "runtime",
                            "graph",
                            "systemcare" if systemcare.get("available") else "evidence",
                        ),
                        action_ids=(
                            "dashboard.open.files",
                            "dashboard.open.connections",
                            "dashboard.open.servers",
                            "dashboard.open.map",
                        ),
                        one_row_per_section=True,
                    ),
                },
                "terminal": {
                    "state": str(everywhere.get("state") or "loading"),
                    "detail": str(everywhere.get("detail") or "Checking live terminals"),
                }}
        glance = snap.get("_mail_glance") if isinstance(snap.get("_mail_glance"), dict) else {}
        recent = []
        gmail = glance.get("gmail") if isinstance(glance.get("gmail"), dict) else {}
        for message in list(gmail.get("messages") or [])[:2]:
            if isinstance(message, dict):
                recent.append({"label": "Gmail · Recent",
                               "value": str(message.get("title") or "(no subject)")[:140],
                               "tone": "neutral"})
        if recent:
            overview = data["views"]["overview"]
            existing = overview["rows"]
            overview["rows"] = (existing[:1] + recent + existing[1:])[:3]
        actions = {
            "comm:0": lambda: self._submit_text_request("List my Outlook inbox", source="dashboard", hide_input=True),
            "comm:1": lambda: self._submit_text_request("List my Gmail inbox", source="dashboard", hide_input=True),
            "comm:2": lambda: self._open_dashboard_url("https://web.telegram.org/", "Telegram"),
            "terminal": self._dashboard_switch_terminal,
            "systemcare:scan": self._dashboard_systemcare_scan,
            "systemcare:open": self._display_systemcare_panel,
            "systemcare:cancel": self._dashboard_systemcare_cancel,
        }
        for index in range(4):
            actions[f"action:{index}"] = (
                lambda selected=index: self._dashboard_dispatch_owner_action(selected)
            )
        for app in apps:
            actions["app:" + app["id"]] = lambda app_id=app["id"]: self.open_private_desktop_app(app_id)
        return data, actions

    def _dashboard_dispatch_owner_action(self, index: int) -> None:
        """Delegate one visible Dashboard control without owning its mutation."""
        bubble = getattr(self, "_bubble", None)
        if not bubble or bubble is False:
            return
        data = getattr(bubble, "_dashboard_data", None)
        view = str(getattr(bubble, "_dashboard_view", "overview") or "overview")
        views = data.get("views") if isinstance(data, dict) and isinstance(data.get("views"), dict) else {}
        payload = views.get(view) if isinstance(views.get(view), dict) else {}
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        if not 0 <= int(index) < len(actions):
            return
        action = actions[int(index)] if isinstance(actions[int(index)], dict) else {}
        kind = str(action.get("kind") or "")
        target = str(action.get("target") or "").strip()
        submits_text = (kind == "command" and target.startswith("/")) or (kind == "request" and bool(target))
        if submits_text:
            self._submit_text_request(target, source="dashboard", hide_input=True)
        elif kind == "surface" and target == "files":
            self._show_files_panel()

    def _dashboard_systemcare_scan(self) -> None:
        self._display_systemcare_panel(start_scan=True)

    def _dashboard_systemcare_cancel(self) -> None:
        window = getattr(self, "_systemcare_window", None)
        if window is not None and window.is_running():
            window.cancel()
            return

        def cancel() -> None:
            try:
                from core.systemcare.service import SystemCareService

                requested = SystemCareService(self._config()).cancel()
            except Exception:
                requested = False

            def apply() -> None:
                self._cube_notice(
                    "SystemCare",
                    "Stopping at the next safe boundary" if requested else "No matching active operation",
                )
                self._systemcare_state_changed()

            self._post_gui_call(apply)

        threading.Thread(target=cancel, name="mo-systemcare-cancel", daemon=True).start()
