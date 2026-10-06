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


# While Gmail access is ended, remind at most this often (a missed glance must not hide it for good).
_MAIL_REAUTH_REPEAT_SECONDS = 1800.0


def _sender_name(header: str) -> str:
    """"Name <a@b>" -> "Name"; a bare address stays as it is."""
    text = " ".join(str(header or "").split())
    name = text.split("<", 1)[0].strip().strip('"') if "<" in text else text
    return name[:40]

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
            # Without access MO cannot see new mail at all: say so, and let one click fix it.
            now = time.monotonic()
            if now - float(getattr(self, "_mail_reauth_notified_at", -1e9) or -1e9) >= _MAIL_REAUTH_REPEAT_SECONDS:
                self._mail_reauth_notified_at = now
                return [Notice("gmail:reauth", "Gmail disconnected", "Google ended MO's access · click to reconnect",
                               "notify_email", 6.0, activate=self._reconnect_gmail)]
            return []
        if status.get("state") not in {"connected", "sync_unknown"}:
            return []
        self._mail_reauth_notified_at = -1e9
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
        idents = service.claim_notices()
        if not idents:
            return []
        newest = idents[-1]
        try:
            summary = service.message_summary(newest)
        except Exception:
            summary = {}
        sender = _sender_name(str(summary.get("from") or ""))
        subject = " ".join(str(summary.get("subject") or "").split())[:70] or "(no subject)"
        more = f" · +{len(idents) - 1} more" if len(idents) > 1 else ""
        account = service.account()
        return [Notice("gmail:new", f"Gmail · {sender}" if sender else "New Gmail", subject + more, "notify_email", 6.0,
                       activate=lambda: self._open_gmail_message(newest, account))]

    def _open_gmail_message(self, ident: str, account: str = "") -> None:
        """A click on a new-mail notice opens that message in Gmail in the default browser."""
        from urllib.parse import quote

        user = f"?authuser={quote(account)}" if account else ""
        self._open_dashboard_url(f"https://mail.google.com/mail/u/{user}#all/{quote(ident)}", "the new Gmail message")

    def _reconnect_gmail(self) -> None:
        """A click on the disconnected notice: Google consent in the browser, then a first sync."""
        if bool(getattr(self, "_mail_reconnecting", False)):
            return
        self._mail_reconnecting = True
        from mo_desktop.notify import Notice

        def _run() -> None:
            from core.mail.service import MailService

            try:
                service = MailService(self._config())
                service.connect()
                service.sync()
                unread = service.status().get("unread")
                notice = Notice("gmail:connected", "Gmail connected",
                                f"{unread} unread" if unread is not None else "Inbox synced", "notify_email", 3.0)
                self._mail_reauth_notified_at = -1e9
            except Exception as exc:
                notice = Notice("gmail:reauth", "Gmail not reconnected", str(exc)[:80] or "Click to try again",
                                "notify_email", 6.0, activate=self._reconnect_gmail)
            finally:
                self._mail_reconnecting = False
            self._emit_notice(notice)

        try:
            threading.Thread(target=_run, name="mo-desktop-gmail-reconnect", daemon=True).start()
        except Exception:
            self._mail_reconnecting = False

    def _collect_notices_async(self) -> None:
        """Collect filesystem/SQLite-backed notice sources off the resident GUI lane."""
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
        bubble = self._dashboard_face()
        if bubble is not None:
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
        why and shows nothing. Disk/profile/graph synthesis never runs on the resident GUI thread: the
        current snapshot (or an honest empty state on first open) paints immediately. The daemon
        refresh starts only after the reveal's final frame, then updates this same panel in place."""
        data, actions = self._dashboard_compact_data(self._dashboard_snapshot_cache)
        if self._show_on_reply_surface(
            "dashboard",
            lambda bubble: bool(bubble.show_dashboard(data, actions)),
            face="dashboard",
        ):
            # Snapshot gathering and its GUI-side projection must not compete with
            # the short reveal. ReplyBubble owns the transition boundary, so there
            # is no duplicated duration or timer in this adapter.
            self._dashboard_face().after_panel_transition(self._refresh_dashboard_snapshot_async)

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
                bubble = self._dashboard_face()
                if bubble is None:
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
                    face="dashboard",
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

        data, actions = self._dashboard_mini(snap, user_projection, operations_projection, everywhere, systemcare)
        return data, actions

    def _dashboard_mini(self, snap: dict[str, Any], user_projection: dict[str, Any],
                        operations_projection: dict[str, Any], everywhere: dict[str, Any],
                        systemcare: dict[str, Any]) -> tuple[dict, dict]:
        """The mini Dashboard (row 19, approved v2): a glance that jumps into MO's main Dashboard
        app, never a copy of it. Three views - Now, You, System - each with three figures and two
        short lists from data the snapshot already holds; one fixed size, '+N' instead of growing."""
        import os

        def metric(projection: dict[str, Any], identifier: str) -> dict[str, Any]:
            for item in projection.get("metrics") or []:
                if isinstance(item, dict) and item.get("id") == identifier:
                    return item
            return {}

        def item(projection: dict[str, Any], section_id: str, item_id: str) -> dict[str, Any]:
            for section in projection.get("sections") or []:
                if isinstance(section, dict) and section.get("id") == section_id:
                    for entry in section.get("items") or []:
                        if isinstance(entry, dict) and entry.get("id") == item_id:
                            return entry
            return {}

        def tile(value: Any, label: str, tone: str = "neutral") -> dict[str, str]:
            text = str(value if value not in (None, "") else "—")
            return {"value": text[:10], "label": label, "tone": tone}

        def tone_of(entry: dict[str, Any]) -> str:
            return str(entry.get("tone") or "neutral")

        actions: dict[str, Any] = {
            "open": self.open_dashboard,
            "terminal": self._dashboard_switch_terminal,
            "comm:0": lambda: self._submit_text_request("List my Outlook inbox", source="dashboard", hide_input=True),
            "comm:1": lambda: self._submit_text_request("List my Gmail inbox", source="dashboard", hide_input=True),
            "systemcare:scan": self._dashboard_systemcare_scan,
            "systemcare:open": self._display_systemcare_panel,
            "systemcare:cancel": self._dashboard_systemcare_cancel,
            "learning": lambda: self._dashboard_run_action({"kind": "command", "target": "/learning pending"}),
            "work": lambda: self._dashboard_run_action({"kind": "command", "target": "/now"}),
            "gmail:reconnect": getattr(self, "_reconnect_gmail", lambda: None),
        }

        # Now: open work, live terminals, mail; what is running; what needs the operator.
        terminals: list[dict[str, str]] = []
        try:
            from core.runtime.instance import recent_instance_snapshots

            for entry in recent_instance_snapshots(self._config(), current_pid=os.getpid(), max_age_seconds=180.0, limit=16):
                if not entry.get("pid_alive") or str(entry.get("surface") or "").lower() != "terminal":
                    continue
                turn = entry.get("turn") if isinstance(entry.get("turn"), dict) else {}
                busy = bool(turn.get("busy"))
                request = " ".join(str(turn.get("request") or "").split())
                terminals.append({"name": os.path.basename(str(entry.get("cwd") or "").rstrip("\\/")) or "MO",
                                  "detail": ("working · " + request) if busy and request else ("working" if busy else "idle"),
                                  "tone": "accent" if busy else "muted", "hit": "terminal"})
        except Exception:
            terminals = []
        mail_status: dict[str, Any] = {}
        try:
            from core.mail.service import MailService

            mail_status = MailService(self._config()).status()
        except Exception:
            mail_status = {}
        mail_state = str(mail_status.get("state") or "")
        unread = mail_status.get("unread")
        needs: list[dict[str, str]] = []
        if mail_state == "reconnect_required":
            needs.append({"name": "Gmail", "detail": "signed out · click to reconnect", "tone": "attention",
                          "hit": "gmail:reconnect"})
        learning = metric(user_projection, "learning")
        pending = str(learning.get("detail") or learning.get("value") or "")
        if "review" in pending.lower() or tone_of(learning) == "attention":
            needs.append({"name": "Learning", "detail": pending or "reviews waiting", "tone": "attention", "hit": "learning"})
        open_work = metric(user_projection, "open_work")
        now = {
            "tiles": [tile(open_work.get("value"), "open tasks", tone_of(open_work)),
                      tile(len(terminals) or "0", "terminals", "accent" if terminals else "neutral"),
                      tile(unread if isinstance(unread, int) else "—", "unread mail",
                           "attention" if mail_state == "reconnect_required" else "neutral")],
            "sections": [{"title": "Running now", "rows": terminals[:2],
                          "more": f"+{len(terminals) - 2}" if len(terminals) > 2 else ""},
                         {"title": "Needs you", "rows": needs[:2] or [
                             {"name": "Work", "detail": str(item(user_projection, "work", "current").get("value") or "nothing waiting"),
                              "tone": "muted", "hit": "work"}]}],
        }

        # You: what MO knows and has learned about the operator; mail; their own apps.
        apps = list(snap.get("desktop_apps") or [])
        for app in apps:
            actions["app:" + str(app.get("id"))] = lambda app_id=app.get("id"): self.open_private_desktop_app(app_id)
        profile = metric(user_projection, "profile")
        behavior = metric(user_projection, "learned_behavior")
        mail_rows = [{"name": "Outlook", "detail": "open inbox", "tone": "accent", "hit": "comm:0"},
                     {"name": "Gmail",
                      "detail": ("signed out" if mail_state == "reconnect_required"
                                 else f"{unread} unread" if isinstance(unread, int) else (mail_state or "not connected")),
                      "tone": "attention" if mail_state == "reconnect_required" else "accent",
                      "hit": "gmail:reconnect" if mail_state == "reconnect_required" else "comm:1"}]
        you = {
            "tiles": [tile(profile.get("value"), "profile files", tone_of(profile)),
                      tile(behavior.get("value"), "learned skills", tone_of(behavior)),
                      tile(learning.get("value"), "learning", tone_of(learning))],
            "sections": [{"title": "Mail", "rows": mail_rows},
                         {"title": "Your apps",
                          "chips": [{"label": str(app.get("label") or app.get("id")), "hit": "app:" + str(app.get("id"))}
                                    for app in apps[:3]],
                          "more": f"+{len(apps) - 3}" if len(apps) > 3 else ""}],
        }

        # System: where MO runs, the machine, the project map.
        host_state = str(everywhere.get("state") or "loading")
        host = {"following": "Online", "available": "Online", "choose": "Online", "offline": "Offline",
                "none": "None"}.get(host_state, "…")
        surfaces = metric(operations_projection, "surfaces")
        provider = item(operations_projection, "runtime", "provider")
        session = item(operations_projection, "runtime", "session")
        graph = item(operations_projection, "graph", "structure")
        care = str(systemcare.get("summary") or systemcare.get("state") or ("ready" if systemcare.get("available") else "—"))
        system = {
            "tiles": [tile(host, "MO host", "good" if host == "Online" else "attention" if host == "Offline" else "neutral"),
                      tile(surfaces.get("value"), "surfaces", tone_of(surfaces)),
                      tile(care.capitalize(), "PC health", "good" if systemcare.get("available") else "neutral")],
            "sections": [{"title": "Running on",
                          "rows": [{"name": "Model", "detail": str(provider.get("value") or "—"), "tone": tone_of(provider)},
                                   {"name": "Session", "detail": str(session.get("value") or "—"), "tone": tone_of(session)}]},
                         {"title": "Project map",
                          "rows": [{"name": "Map", "detail": str(graph.get("value") or "not built"), "tone": tone_of(graph)}]}],
            "chip": ({"label": "Cancel scan" if systemcare.get("active") else "Scan PC",
                      "hit": "systemcare:cancel" if systemcare.get("active") else "systemcare:scan"}
                     if systemcare.get("available") else None),
        }
        data = {"views": {"overview": now, "personal": you, "systems": system},
                "status": str((user_projection.get("status") or {}).get("label") or "Dashboard")}
        return data, actions

    def _dashboard_run_action(self, action: dict[str, Any]) -> None:
        """Run one Dashboard action descriptor through its existing owner (a command or request
        submitted as a turn, or the files surface) without owning its mutation."""
        kind = str(action.get("kind") or "")
        target = str(action.get("target") or "").strip()
        if (kind == "command" and target.startswith("/")) or (kind == "request" and target):
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
