"""Local Dashboard adapter for a running MO instance; no second Agent or state store.

The random connection capability stays in the browser fragment/header, never
in logs or disk. Only loopback, exact-host, same-origin requests are accepted.
Business operations remain with the terminal, graph, profile and MO Files.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


_MAX_REQUEST_BYTES = 1_100_000


# A first state built at launch serves the page only while this fresh (the window takes 2-3 s).
_PREFETCH_FRESH_SECONDS = 15.0


class DashboardServer:
    def __init__(self, agent: Any):
        self.agent = agent
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._graph_lock = threading.Lock()
        self._mail_setup_lock = threading.Lock()
        self._graph_cache: dict[str, tuple[float, str]] = {}
        self._snapshot_graph = None
        self._prefetched = None
        self._renderer = None
        from ..runtime.resources import ResourceSampler
        self._resources = ResourceSampler()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # URLs and local state never enter HTTP logs.

            def do_GET(self):
                self._serve(False)

            def do_POST(self):
                self._serve(True)

            def _serve(self, post):
                if self.headers.get("Host") != owner.address:
                    self._discard_rejected_body(post)
                    self._reply(403, {"error": "Wrong host"})
                    return
                if not post and self.path == "/":
                    from .render import render_dashboard_html
                    self._reply(200, render_dashboard_html({}, connected=True), "text/html")
                    return
                auth = self.headers.get("Authorization", "")
                origin = self.headers.get("Origin")
                if (not hmac.compare_digest(auth, "Bearer " + owner.token)
                        or origin not in (None, owner.origin)
                        or (post and origin != owner.origin)):
                    self._discard_rejected_body(post)
                    self._reply(403, {"error": "Dashboard connection unavailable; reopen /dashboard show"})
                    return
                try:
                    if post:
                        size = int(self.headers.get("Content-Length", "0"))
                        if size < 1 or size > _MAX_REQUEST_BYTES:
                            raise ValueError("Request is empty or too large")
                        body = json.loads(self.rfile.read(size))
                        if not isinstance(body, dict):
                            raise ValueError("Expected an object")
                    else:
                        body = {}
                    lock = (owner._graph_lock if self.path == "/api/graph" else
                            owner._mail_setup_lock if self.path == "/api/mail/setup" else owner._lock)
                    with lock:
                        result = owner.handle(self.path, body, post=post)
                    self._reply(200, result)
                except (ValueError, RuntimeError) as exc:
                    from ..files.service import FileBoundaryConflict
                    from ..tooling.sandbox import redact_sensitive_text
                    self._reply(409 if isinstance(exc, FileBoundaryConflict) else 400,
                                {"error": redact_sensitive_text(str(exc))[:240]})
                except Exception:
                    self._reply(500, {"error": "Source unavailable; existing terminal remains usable"})

            def _discard_rejected_body(self, post):
                """Drain a bounded rejected POST so Windows can deliver the response."""
                if not post:
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except (TypeError, ValueError):
                    return
                if not 0 < size <= _MAX_REQUEST_BYTES:
                    return
                previous_timeout = self.connection.gettimeout()
                try:
                    self.connection.settimeout(1)
                    self.rfile.read(size)
                except OSError:
                    pass
                finally:
                    self.connection.settimeout(previous_timeout)

            def _reply(self, status, result, kind="application/json"):
                data = (result if kind == "text/html" else json.dumps(result, ensure_ascii=False)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", kind + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src 'self' about:; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(data)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.address = f"127.0.0.1:{self.http.server_port}"
        self.origin = "http://" + self.address
        self.thread = threading.Thread(target=self.http.serve_forever, name="mo-dashboard", daemon=True)
        self.thread.start()

    def open(self, *, on_source=None, on_started=None, on_ready=None):
        from mo_desktop.mo_renderer import focus_renderer, launch_dashboard
        from ..state.paths import runtime_config_path
        if self._renderer is not None and self._renderer.poll() is None:
            focus_renderer(self._renderer)
            if on_ready:
                on_ready(True)
            return
        options = {"on_source": on_source, "on_started": on_started, "on_ready": on_ready} if on_source else {}
        self._prefetch_state()
        self._renderer = launch_dashboard(self.origin + "/#" + self.token,
                                          config_path=runtime_config_path(self.agent.config), **options)

    def close(self):
        if self._renderer is not None and self._renderer.poll() is None:
            self._renderer.terminate()
            self._renderer.wait(timeout=5)
        self._renderer = None
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)

    def projects(self):
        """Curated declarations plus the actual current folder, without a catalog."""
        from ..files.service import FileBoundaryError, FileManagerService
        from ..state.paths import mo_home
        current = Path(self.agent._effective_project_cwd()).resolve()
        profile = getattr(self.agent, "profile", None)
        locations = getattr(profile, "project_locations", None)
        declared = list(locations()) if callable(locations) else []
        entries = [(str(current), current.name, "current folder")]
        entries.extend((item.path, item.name, "declared project") for item in declared)
        opaque = (mo_home(self.agent.config) / "personal").resolve()
        files = FileManagerService(self.agent.config)
        roots = []
        try:
            file_locations = files.locations()
        except FileBoundaryError:
            file_locations = []
        for location in file_locations:
            if location["kind"] in {"project", "drive"}:
                roots.append((location, files._locations[location["location_id"]].root))
        result = {}
        for path, name, source in entries[:64]:
            # Personal declarations remain names only; do not resolve/stat them.
            raw = Path(path) if path else None
            private = raw is not None and (raw == opaque or opaque in raw.parents)
            root = raw.resolve() if raw is not None and not private else None
            if root is not None and (root == opaque or opaque in root.parents):
                root = None
            if root and any(row["root"] == root for row in result.values()):
                continue
            key = "0" if not result else hashlib.sha256((str(path) + "\0" + str(name)).encode()).hexdigest()[:20]
            binding = next(((loc, base) for loc, base in roots if root and (root == base or base in root.parents)), None)
            result[key] = {"id": key, "name": str(name)[:120], "source": source, "root": root,
                           "available": bool(root and root.is_dir()), "binding": binding}
        return result

    def handle(self, path, body, *, post=False):
        if path == "/api/usage" and not post:
            sessions = getattr(self.agent, "_sessions", None)
            return sessions.usage_activity() if sessions is not None else {
                "available": False, "days": [], "scope": "Saved usage is unavailable in this host"}
        if path == "/api/state" and not post:
            return self._first_or_current_state()
        if not post:
            raise ValueError("Unknown dashboard route")
        return self._handle_post(path, body)

    def _prefetch_state(self):
        """Build the first state while the window starts (1.9-2.7 s), so the page that waits on it
        reveals as soon as it loads instead of waiting again for this build."""
        holder = {"started": time.monotonic()}

        def build():
            try:
                holder["state"] = self._state_payload()
            except Exception as exc:
                holder["error"] = exc

        thread = threading.Thread(target=build, name="mo-dashboard-first-state", daemon=True)
        holder["thread"] = thread
        with self._lock:
            self._prefetched = holder
        thread.start()

    def _first_or_current_state(self):
        with self._lock:
            holder, self._prefetched = self._prefetched, None
        if holder is not None and time.monotonic() - holder["started"] < _PREFETCH_FRESH_SECONDS:
            holder["thread"].join(timeout=30)
            if "state" in holder:
                return holder["state"]
        return self._state_payload()

    def _state_payload(self):
        from .projection import build_dashboard_projection
        snap = self.snapshot()
        projection = build_dashboard_projection(snap, surface="html")
        projects = self.projects()
        from dataclasses import asdict
        from .render import _collect_user_sections
        resources = asdict(self._resources.sample({"terminal": os.getpid()}))
        from interface.command_registry import slash_command_spec, slash_command_with_desc
        controls = [{"id": "command:" + spec.name, "label": spec.name.lstrip("/"),
                     "detail": spec.description} for spec in (slash_command_spec(name) for name, _ in slash_command_with_desc())
                    if spec.palette and spec.name not in {"/dashboard", "/exit", "/quit"}]
        health = getattr(self.agent, "health_status", None)
        from ..tooling.sandbox import redact_sensitive_text
        observations = [{"label": str(label), "detail": redact_sensitive_text(str(detail))}
                        for label, detail in health()] if callable(health) else []
        return {"snapshot": snap, "projection": projection,
                "projects": [{k: row[k] for k in ("id", "name", "source", "available")} for row in projects.values()],
                "controls": controls, "resources": resources,
                "terminals": self.terminals(projects, snap), "health": observations,
                "terminal_host": callable(getattr(self.agent, "_dashboard_dispatch", None)),
                "evidence": [{"title": section.title, "rows": [{"text": row.text, "sub": row.sub, "meta": row.meta} for row in section.rows]} for section in _collect_user_sections(snap)]}

    def _handle_post(self, path, body):
        if path == "/api/life/items":
            from ..life.items import add_case_update, create_item, forget_item, list_items, update_item

            action = str(body.get("action") or "list")
            config = getattr(self.agent, "config", {})
            if action == "list":
                from ..life.money import linked_counts
                from ..runtime.scheduler import manage_scheduler_jobs

                items = list_items(config=config)
                payment_counts = linked_counts(config=config)
                try:
                    scheduled = manage_scheduler_jobs(self.agent, "list")
                except (OSError, ValueError):
                    return {"items": items, "linked_counts": payment_counts,
                            "scheduled": None, "scheduled_total": None}
                return {"items": items, "linked_counts": payment_counts,
                        "scheduler_enabled": scheduled.get("scheduler_enabled") is True,
                        "scheduled": [
                    {"id": str(job.get("id") or ""),
                     "name": str(job.get("name") or "")[:80],
                     "kind": str(job.get("kind") or "turn"),
                     "enabled": job.get("enabled") is not False,
                     "next_run_at": job.get("next_run_at"),
                     "last_status": job.get("last_status")}
                    for job in scheduled.get("jobs", [])[:50]
                ], "scheduled_total": len(scheduled.get("jobs", []))}
            if action == "create":
                item = create_item(
                    title=body.get("title"), category=body.get("category"),
                    due_date=body.get("due_date"), notes=body.get("notes"),
                    case_area=body.get("case_area"), reference=body.get("reference"),
                    expected_amount=body.get("expected_amount"), currency=body.get("currency"),
                    frequency=body.get("frequency"), installments_total=body.get("installments_total"),
                    source_provider=body.get("source_provider"),
                    source_id=body.get("source_id"), config=config,
                )
                return {"item": item}
            if action == "update":
                item = update_item(str(body.get("id") or ""),
                    expected_revision=int(body.get("revision") or 0),
                    changes=body.get("changes"), config=config)
                return {"item": item}
            if action == "add_update":
                return {"item": add_case_update(str(body.get("id") or ""),
                    expected_revision=int(body.get("revision") or 0),
                    date_value=body.get("date"), kind=body.get("kind"),
                    summary_text=body.get("summary"), reference=body.get("reference"),
                    config=config)}
            if action == "forget":
                forget_item(str(body.get("id") or ""),
                    expected_revision=int(body.get("revision") or 0), config=config)
                return {"forgotten": True}
            raise ValueError("Unknown life-item action")
        if path == "/api/life/schedule":
            from ..runtime.scheduler import manage_scheduler_jobs

            action = str(body.get("action") or "").strip().lower()
            if action == "create":
                kind = str(body.get("kind") or "reminder").strip().lower()
                if kind not in {"reminder", "turn"}:
                    raise ValueError("Dashboard can add reminders and Agent tasks")
                result = manage_scheduler_jobs(self.agent, "create", {
                    "kind": kind,
                    "name": str(body.get("name") or "").strip()[:80],
                    "schedule": str(body.get("schedule") or "").strip()[:80],
                    "prompt": str(body.get("prompt") or "").strip()[:1000],
                    "deliver": "desktop",
                })
            elif action in {"pause", "resume", "run", "remove"}:
                result = manage_scheduler_jobs(self.agent, action, {
                    "job_id": str(body.get("job_id") or "").strip(),
                })
            else:
                raise ValueError("Unknown scheduled-task action")
            job = result.get("job") or {}
            return {"action": action, "id": job.get("id"),
                    "scheduler_enabled": result.get("scheduler_enabled") is True}
        if path == "/api/life/money":
            from ..life.money import create_entry, forget_entry, month_view, update_entry

            action = str(body.get("action") or "list")
            config = getattr(self.agent, "config", {})
            if action == "list":
                return month_view(month=str(body.get("month") or ""), config=config)
            if action == "create":
                return {"entry": create_entry(
                    title=body.get("title"), kind=body.get("kind"), amount=body.get("amount"),
                    currency=body.get("currency"), date=body.get("date"),
                    category=body.get("category"), notes=body.get("notes"),
                    life_item_id=body.get("life_item_id"), config=config,
                )}
            if action == "update":
                return {"entry": update_entry(str(body.get("id") or ""),
                    expected_revision=int(body.get("revision") or 0),
                    changes=body.get("changes"), config=config)}
            if action == "forget":
                forget_entry(str(body.get("id") or ""),
                    expected_revision=int(body.get("revision") or 0), config=config)
                return {"forgotten": True}
            raise ValueError("Unknown money-entry action")
        if path == "/api/mail/glance":
            from ..mail.service import dashboard_glance

            return dashboard_glance(str(body.get("provider") or ""),
                                    config=self.agent.config, agent=self.agent,
                                    limit=10,
                                      query=str(body.get("query") or "")[:300])
        if path == "/api/mail/setup":
            provider = str(body.get("provider") or "").strip().lower()
            action = str(body.get("action") or "status").strip().lower()
            if provider == "gmail":
                from ..mail.service import MailService

                service = MailService(self.agent.config)
                if action == "status":
                    return service.status()
                if action == "enable":
                    from ..state.preferences import persist_mail_enabled

                    persist_mail_enabled(self.agent.config, True)
                    return service.status()
                if action == "save_client":
                    return service.save_client_credentials(body.get("client_id"), body.get("client_secret"))
                if action == "connect":
                    service.connect()
                    service.sync()  # Establish the unread baseline before showing notices.
                    return service.status()
            if provider == "outlook":
                from .. import browser_bridge

                if action == "prepare":
                    browser_bridge.install()
                elif action != "status":
                    raise ValueError("Unknown Outlook setup action")
                current = browser_bridge.status()
                return {key: current.get(key) for key in (
                    "installed", "bridge_live", "extension_connected", "extension_dir", "error")}
            raise ValueError("Unknown mail setup action")
        if path == "/api/mail/folders":
            provider = str(body.get("provider") or "")
            if provider == "gmail":
                from ..mail.service import MailService

                return {"folders": MailService(self.agent.config).move_destinations()}
            if provider == "outlook":
                from ..mail import outlook_browser

                return outlook_browser.execute("folders", {}, agent=self.agent)
            raise ValueError("Unknown mail provider")
        if path == "/api/mail/read":
            provider = str(body.get("provider") or "")
            ident = str(body.get("id") or "")
            if provider == "gmail":
                from ..mail.service import MailService

                return MailService(self.agent.config).read_message(ident)
            if provider == "outlook":
                from ..mail import outlook_browser

                identity = str(body.get("identity") or "")
                if len(identity) != 64 or any(ch not in "0123456789abcdef" for ch in identity):
                    raise ValueError("Refresh Outlook messages before opening one")
                return outlook_browser.execute("read", {"id": ident,
                    "expected_preview_hash": identity}, agent=self.agent)
            raise ValueError("Unknown mail provider")
        if path == "/api/mail/action":
            provider = str(body.get("provider") or "")
            action = str(body.get("action") or "")
            ident = str(body.get("id") or "")
            if provider not in {"gmail", "outlook"} or action not in {"archive", "move", "delete", "compose"}:
                raise ValueError("Unknown mail action")
            if action in {"archive", "move"}:
                folder = str(body.get("folder") or "")
                if provider == "gmail":
                    from ..mail.service import MailService

                    service = MailService(self.agent.config)
                    result = service.move(ident, folder) if action == "move" else service.modify(ident, "archive")
                else:
                    from ..mail import outlook_browser

                    identity = str(body.get("identity") or "")
                    if len(identity) != 64 or any(ch not in "0123456789abcdef" for ch in identity):
                        raise ValueError("Refresh Outlook messages before acting")
                    result = outlook_browser.execute(action, {"id": ident, "folder": folder,
                        "expected_preview_hash": identity}, agent=self.agent)
                return {"message": "Move requested" if action == "move" else "Archive requested", "result": result}
            if action == "delete":
                prompt = f"I want to delete a {provider.title()} message. Show me the current message list and ask me which one before requesting delete approval."
            else:
                prompt = f"I want to compose and send a {provider.title()} email. Ask me for the recipient, subject, and body, then use the normal send approval."
            dispatch = getattr(self.agent, "_dashboard_dispatch", None)
            if callable(dispatch):
                return dispatch("request", prompt)
            submit = getattr(self.agent, "_dashboard_mail_request", None)
            if callable(submit) and submit(prompt):
                return {"message": "Opened this request in MO Desktop chat"}
            raise ValueError("The Dashboard's MO conversation is unavailable")
        if path == "/api/mail/notifications":
            from ..mail.service import MailService

            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("Choose on or off for Gmail notifications")
            return {"enabled": MailService(self.agent.config).set_notifications_enabled(enabled)}
        if path == "/api/action":
            action = str(body.get("id") or "")
            project_id = str(body.get("project", "0"))
            instance = str(body.get("instance") or "")
            host_instance = str(getattr(self.agent, "instance_id", ""))
            projects = self.projects()
            selected = projects.get(project_id)
            if not selected or not selected["available"]:
                raise ValueError("This project has no available local folder")
            kind, value = (action, "") if action in {"terminal", "steer", "stop"} else self.destination(action, selected["root"])
            dispatch = getattr(self.agent, "_dashboard_dispatch", None)
            if project_id == "0" and callable(dispatch) and (not instance or instance == host_instance):
                return {**dispatch(kind, value), "instance": host_instance}
            candidates = [row for row in self.terminals(projects, self.snapshot())
                          if row["project"] == project_id and row["live"]]
            target = next((row for row in candidates if row["instance"] == instance), None) if instance else candidates[0] if len(candidates) == 1 else None
            if instance and target is None:
                raise ValueError("Selected terminal has ended or changed project; refresh and choose again")
            if len(candidates) > 1 and target is None:
                return {"message": "Choose the existing project terminal", "terminals": [
                    {"instance": row["instance"], "label": row["title"]} for row in candidates]}
            if target is None:
                if action == "stop":
                    return {"message": "No live terminal to stop in this project"}
                from mo_desktop.design_studio.routing import launch_normal_terminal
                launched = launch_normal_terminal(project_root=str(selected["root"]), config=self.agent.config)
                return {"message": "Normal MO terminal launched; waiting for its controls",
                        "instance": launched, "pending": True}
            from interface.terminal_host import focus_terminal
            focused = focus_terminal(target["instance"])
            if action in {"terminal", "steer"}:
                return {"message": "MO terminal opened" if focused else "Terminal is live; switch to its window",
                        "instance": target["instance"]}
            from ..design.terminal_handoff import queue_terminal_control
            queue_terminal_control(kind, value, {"instance_id": target["instance"], "pid": target["pid"]},
                                   project_root=str(selected["root"]), config=self.agent.config)
            return {"message": "Control sent to the selected terminal; its result appears there",
                    "instance": target["instance"]}
        if path == "/api/learning":
            from ..learning.review import describe_review_item, learning_review_items
            profile = getattr(self.agent, "profile", None)
            result = {}
            for state, active in (("pending", False), ("active", True)):
                items = learning_review_items(profile, config=self.agent.config, active=active)
                result[state + "_total"] = len(items)
                result[state] = [{key: info.get(key, "") for key in ("ref", "summary", "label", "state", "details")}
                                 for info in (describe_review_item(item) for item in items[:60])]
            return result
        if path == "/api/learning/review":
            # The existing command owns revision validation, promotion and Undo.
            action = str(body.get("action") or "")
            ref = str(body.get("ref") or "")
            if action not in {"confirm", "dismiss"} or len(ref.split()) != 2 or len(ref) > 240:
                raise ValueError("Review the current learning item first")
            owner = getattr(self.agent, "_cmd_learning", None)
            if not callable(owner):
                raise ValueError("Learning review owner unavailable in this host")
            return {"message": str(owner(action + " " + ref))}
        project = self.projects().get(str(body.get("project", "0")))
        if not project or not project["available"]:
            raise ValueError("This project has no available local folder")
        root = project["root"]
        if path in {"/api/skills", "/api/skill/read"}:
            from ..skills import default_skill_roots, visible_skill_packs
            from ..skills.model import skill_matches_project
            from ..skills._util import _as_int
            from ..tooling.sandbox import redact_sensitive_text
            profile = getattr(self.agent, "profile", None)
            roots = default_skill_roots(str(root), getattr(self.agent, "runtime_home", None),
                                        profile=profile, config=self.agent.config, maintain=False)
            rows = visible_skill_packs(roots, profile=profile, config=self.agent.config)
            query = str(body.get("query") or "").strip().casefold()[:200]
            items = []
            for row in rows:
                skill = row.skill
                if skill is not None and not skill_matches_project(skill, str(root)):
                    continue
                source = skill.source if skill else row.name
                identity = hashlib.sha256(source.encode()).hexdigest()[:24]
                ownership = "This project" if skill and skill.project_root else (
                    "Unbound convention · applies across projects" if skill and skill.provenance == "learned-convention"
                    else "All projects")
                if path == "/api/skill/read":
                    if identity != str(body.get("id") or ""):
                        continue
                    details = [row.name, ownership]
                    if skill:
                        details.extend([f"Source: {skill.source}", f"Origin: {skill.provenance}",
                                        f"Activation: {', '.join(skill.triggers)}", f"Files / scope: {skill.scope or 'task match'}",
                                        f"Approval: {skill.approval or 'authored'}"])
                        if skill.mastery:
                            details.append("Recorded outcomes: " + " · ".join(
                                f"{label} {_as_int(skill.mastery.get(key))}" for key, label in (
                                    ("mastery_uses", "uses"), ("mastery_successes", "positive"),
                                    ("mastery_corrections", "corrections"))))
                        details.extend(["Counts are recorded feedback, not proof of effectiveness.", "", skill.body])
                    else:
                        details.extend(row.issues)
                    return {"details": redact_sensitive_text("\n".join(details))}
                searchable = " ".join((row.name, row.description, skill.scope if skill else "", ownership)).casefold()
                if query and query not in searchable:
                    continue
                items.append({"id": identity, "name": redact_sensitive_text(row.name),
                              "description": redact_sensitive_text(row.description), "ownership": ownership,
                              "kind": "Convention" if skill and skill.provenance == "learned-convention" else "Skill",
                              "state": "available" if row.active else "unavailable"})
            if path == "/api/skill/read":
                raise ValueError("Skill is no longer available in this project; reload its sources")
            return {"items": items[:60], "total": len(items)}
        if path == "/api/lsp":
            manager = getattr(self.agent, "lsp_manager", None)
            if manager is None:
                return {"state": "unavailable", "selection": "default", "configured": False}
            return manager.status(str(root))
        if path == "/api/checks":
            from ..diagnostics.surface_trace import build_check_evidence
            from ..graph.structural_graph import graph_status
            selected = str(body.get("instance") or "")
            candidates = [row for row in self.terminals(self.projects(), self.snapshot())
                          if row["project"] == project["id"] and row["live"]]
            terminal = next((row for row in candidates if row["instance"] == selected), None) if selected else candidates[0] if len(candidates) == 1 else None
            session_id = str((terminal or {}).get("session") or "")
            evidence = build_check_evidence(self.agent.config, session_id=session_id)
            graph = graph_status(root)
            return {"evidence": evidence, "session": session_id, "instance": (terminal or {}).get("instance", ""),
                    "graph": {key: graph.get(key) for key in ("available", "stale", "stale_reasons", "nodes", "edges")},
                    "scope": "Latest recorded model turn of the selected terminal" if session_id else
                    "No single live conversation selected; no other conversation substituted"}
        if path in {"/api/knowledge", "/api/knowledge/query"}:
            from ..knowledge import knowledge_status, query_manifest, render_knowledge
            if path.endswith("/query"):
                query = str(body.get("query") or "").strip()
                if not query or len(query) > 500:
                    raise ValueError("Enter a query of 1–500 characters")
                result = query_manifest(query, root)
                return {"result": render_knowledge(result), "available": result.get("available", False)}
            return knowledge_status(root)
        if path == "/api/graph":
            from ..graph.structural_graph import graph_status
            from ..graph.generate_code_map import generate_code_map
            status = graph_status(root)
            graph = Path(status["path"]) if status.get("available") and status.get("path") else None
            if not graph or not graph.is_file():
                return {"html": "", "state": "Not built — use /structural-graph build in this project's terminal"}
            stamp = graph.stat().st_mtime
            cached = self._graph_cache.get(str(graph))
            if not cached or cached[0] != stamp:
                artifact = generate_code_map(graph)
                self._graph_cache.clear()  # Keep only the current rendered project, not every visited HTML.
                self._graph_cache[str(graph)] = (stamp, Path(artifact["path"]).read_text(encoding="utf-8"))
            return {"html": self._graph_cache[str(graph)][1], "state": "Stale orientation" if status.get("stale") else "Structural orientation · not verification"}
        if path in {"/api/rules", "/api/rule/read", "/api/rule/save"}:
            from ..context.project_context import discover_project_context_files
            from ..files.service import FileManagerService
            files = FileManagerService(self.agent.config)
            scope = str(body.get("scope") or "")
            target = (root / scope).resolve()
            if target != root and root not in target.parents:
                raise ValueError("Rule scope is outside this project")
            rules = []
            bindings = {}
            for index, source in enumerate(discover_project_context_files(target)):
                key = hashlib.sha256(str(source).encode()).hexdigest()[:24]
                binding = project["binding"]
                editable = False
                if binding:
                    loc, base = binding
                    if source == base or base in source.parents:
                        relative = source.relative_to(base).as_posix()
                        try:
                            files.read_text(loc["location_id"], relative)
                            bindings[key] = (loc["location_id"], relative)
                            editable = "edit_text" in loc["operations"]
                        except RuntimeError:
                            pass
                rules.append({"id": key, "name": source.name, "source": str(source),
                              "scope": "inherited" if root not in source.parents else "project",
                              "readable": key in bindings, "editable": editable})
            if path == "/api/rules":
                return {"rules": rules, "scope": scope}
            binding = bindings.get(str(body.get("rule")))
            if not binding:
                raise ValueError("This rule is outside the authorized Files locations; open it in its owning project")
            if path.endswith("/save"):
                return files.write_text(*binding, body.get("text"), expected_sha256=body.get("sha256"))
            return files.read_text(*binding)
        raise ValueError("Unknown dashboard route")

    def snapshot(self):
        """Poll live work without rediscovering the entire project every ten seconds."""
        from .snapshot import _graph_summary, build_dashboard_snapshot
        root = Path(self.agent._effective_project_cwd()).resolve()
        cached = self._snapshot_graph
        if cached is None or cached[0] != root or time.monotonic() - cached[1] >= 60:
            graph = _graph_summary(root)
            graph["observed_at"] = time.time()
            self._snapshot_graph = (root, time.monotonic(), graph)
        return build_dashboard_snapshot(self.agent, graph_summary=self._snapshot_graph[2])

    def destination(self, action, project_root):
        """Allowlisted navigation, never arbitrary command text from the page."""
        if action.startswith("learning:"):
            from ..learning.review import describe_review_item, learning_review_items
            ref = action.removeprefix("learning:")
            profile = getattr(self.agent, "profile", None)
            for active in (False, True):
                for item in learning_review_items(profile, config=self.agent.config, active=active):
                    if describe_review_item(item)["ref"] == ref:
                        return "command", "/learning details " + ref
            raise ValueError("Learning changed; refresh the review list")
        if action.startswith("command:"):
            from interface.command_registry import slash_command_names, slash_command_spec
            value = action.removeprefix("command:")
            if value not in slash_command_names() or not slash_command_spec(value).palette or value in {"/dashboard", "/exit", "/quit"}:
                raise ValueError("Unknown terminal destination")
            return "command", value
        from .projection import build_dashboard_projection
        snapshot = self.snapshot()
        actions = build_dashboard_projection(snapshot, surface="html")["actions"]
        selected = next((row for row in actions if row["id"] == action and row["kind"] in {"command", "request"}), None)
        if selected is None:
            raise ValueError("Unknown dashboard action")
        return selected["kind"], selected["target"]

    def terminals(self, projects, snapshot):
        """Join existing heartbeat identity and task evidence; never infer execution from a count."""
        from ..runtime.instance import recent_instance_snapshots
        from ..tooling.sandbox import redact_sensitive_text
        host = str(getattr(self.agent, "instance_id", ""))
        work = snapshot.get("work", {})
        board = work.get("live_taskboard") or work.get("latest_taskboard") or {}
        result = [{"instance": host, "pid": os.getpid(), "project": "0", "live": True,
                   "session": str(getattr(getattr(self.agent, "session", None), "session_id", "")),
                   "title": board.get("title") or "Ready for a task", "state": board.get("state") or "idle",
                   "tasks": board.get("open_tasks", []), "age": 0, "host": True}] if callable(getattr(self.agent, "_dashboard_dispatch", None)) else []
        for row in recent_instance_snapshots(self.agent.config, max_age_seconds=86400, limit=32):
            if row.get("surface") != "terminal" or row.get("instance_id") == host or not row.get("cwd"):
                continue
            root = Path(row["cwd"]).resolve()
            project = next((key for key, value in projects.items() if value["root"] == root), None)
            if project is None:
                continue
            board = row.get("taskboard") or {}
            live = bool(row.get("pid_alive")) and row.get("age_seconds", 0) < 300
            result.append({"instance": row.get("instance_id", ""), "pid": row["pid"], "project": project,
                           "live": live, "session": row.get("session_id", ""),
                           "title": redact_sensitive_text(str(board.get("title") or "No task reported")),
                           "state": str(board.get("state") or "idle") if live else "ended / stale",
                           "tasks": [{"id": board.get("active_task_id") or board.get("ready_task_id") or "",
                                      "title": board.get("active_task_title") or board.get("next_task_title") or "",
                                      "status": "last observed", "blocker": ""}],
                           "age": int(row.get("age_seconds", 0)), "host": False})
        return result


def open_dashboard(agent: Any, *, on_source=None, on_started=None, on_ready=None) -> None:
    server = getattr(agent, "_dashboard_server", None)
    if server is None:
        server = DashboardServer(agent)
        agent._dashboard_server = server
    if on_source:
        server.open(on_source=on_source, on_started=on_started, on_ready=on_ready)
    else:
        server.open()
