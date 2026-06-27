"""MO agent slash-command mixin."""

import os
import time
from pathlib import Path
import traceback

from ..backend_monitor import get_monitor
from ..path_defaults import resolve_state_path
from ..ghost.ghost_context import build_ghost_context
from ..ghost.ghost_audit import append_ghost_audit
from ..provider.provider_audit import append_provider_audit
from ..provider.provider import load_config, clean_provider_error
from ..profile import Profile, format_profile_time
from ..session.handoff import context_pressure, seed_session_from_handoff
from ..session.session_closeout import (
    build_session_closeout,
    closeout_meta,
    render_session_closeout,
    stage_session_closeout_feedback,
    write_session_closeout,
)
from ..consistency_boundary import (
    check_consistency_boundary,
    emit_consistency_boundary,
    render_consistency_boundary,
)
from ..workers import ensure_worker_registry
from interface.ghost import GHOST_SIDECHAT_SYSTEM, ghost_safe_messages
from interface.slash_commands import SLASH_COMMAND_HELP


class AgentSlashCommands:
    """Slash-command dispatch and handlers for the MO Agent."""

    def process_slash_command(self, user_input: str) -> str | None:
        """Handle slash commands locally. Returns response string or None if not a command."""
        text = user_input.strip()
        if not text.startswith("/"):
            return None

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/help": self._cmd_help,
            "/h": self._cmd_help,
            "/init": self._cmd_init,
            "/doctor": self._cmd_doctor,
            "/migrate": self._cmd_migrate,
            "/exit": lambda _: "[EXIT]",
            "/quit": lambda _: "[EXIT]",
            "/q": lambda _: "[EXIT]",
            "/clear": self._cmd_clear,
            "/c": self._cmd_clear,
            "/status": self._cmd_status,
            "/usage": self._cmd_usage,
            "/heartbeat": self._cmd_heartbeat,
            "/telegram": self._cmd_telegram,
            "/model": self._cmd_model,
            "/projects": self._cmd_projects,
            "/sessions": self._cmd_projects,
            "/new": self._cmd_new,
            "/profile": self._cmd_profile,
            "/p": self._cmd_profile,
            "/learning": self._cmd_learning,
            "/goal": self._cmd_goal,
            "/g": self._cmd_goal,
            "/undo": self._cmd_undo,
            "/u": self._cmd_undo,
            "/retry": self._cmd_retry,
            "/r": self._cmd_retry,
            "/session": self._cmd_session,
            "/s": self._cmd_session,
            "/resume": self._cmd_resume,
            "/reload": self._cmd_reload,
            "/think": self._cmd_think,
            "/settings": self._cmd_settings,
            "/structural-graph": self._cmd_structural_graph,
            "/sg": self._cmd_structural_graph,
            "/prt": self._cmd_prt,
            "/owner_comparison": self._cmd_owner_comparison,
            "/moon": self._cmd_moon,
            "/hints": self._cmd_hints,
            "/ghost": self._cmd_ghost,
            "/gh": self._cmd_ghost,
            "/companion": self._cmd_companion,
        }

        handler = handlers.get(cmd)
        if handler:
            result = handler(rest)
            # Local commands never reach the provider, so without this event
            # they are invisible to traces/the validator (observed live: failed
            # operator /learning confirms left no evidence). Command root only —
            # rest text can carry paths/urls/ids and never enters the event.
            try:
                monitor = get_monitor()
                if monitor:
                    monitor.emit("slash_command", {"command": cmd, "has_args": bool(rest.strip()), "handled": result is not None})
            except Exception:
                pass
            return result
        return None

    def _cmd_owner_comparison(self, rest: str) -> str:
        """Route OWNER_COMPARISON slash syntax into the normal provider/preflight path."""
        clean = str(rest or "").strip()
        self._slash_pending_input = f"start OWNER_COMPARISON {clean}".strip()
        return "[RUN_TURN]"

    def _cmd_moon(self, rest: str) -> str:
        """Toggle the animated moon visuals for the MO logo."""
        from interface.moon_visuals import start_moon_animation_tick
        current = getattr(self, "_moon_mode_active", False)

        if rest.lower() == "on":
            target = True
        elif rest.lower() == "off":
            target = False
        else:
            target = not current

        self._moon_mode_active = target

        # One tick thread per process: stop the old one before starting a new
        # one, otherwise every toggle leaks another 10-FPS invalidation thread.
        existing_stop = getattr(self, "_moon_tick_stop", None)
        if target:
            if existing_stop is None or existing_stop.is_set():
                if hasattr(self, "tui") and self.tui:
                    self._moon_tick_stop = start_moon_animation_tick(self, getattr(self.tui, "_app", None))
        elif existing_stop is not None:
            existing_stop.set()
            self._moon_tick_stop = None

        status = "ON" if target else "OFF"
        return f"[MOON MODE {status}] MO logo glow toggled."

    def _cmd_hints(self, rest: str) -> str:
        """Toggle rotating hint tips on the idle line."""
        current = getattr(self, "_hints_enabled", True)

        if rest.lower() == "on":
            target = True
        elif rest.lower() == "off":
            target = False
        else:
            target = not current

        self._hints_enabled = target
        status = "ON" if target else "OFF"
        return f"[HINTS {status}] Idle line will {('show rotating hints' if target else 'show normal idle')}."

    def _cmd_learning(self, rest: str) -> str:
        """Show deterministic learning/system health status."""
        raw = (rest or "status").strip()
        parts = raw.split(maxsplit=1)
        sub = (parts[0].lower() if parts else "status") or "status"
        arg = parts[1].strip() if len(parts) > 1 else ""
        cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
        suggestions_path = resolve_state_path("memory/learning_suggestions.jsonl", cfg)
        learning_db_path = resolve_state_path("memory/learning.sqlite", cfg)
        if sub.startswith("suggest"):
            from core.learning.proactive_learning import mine_learning_suggestions, render_learning_suggestions, write_learning_suggestions
            suggestions = mine_learning_suggestions(memory_path=learning_db_path)
            path = write_learning_suggestions(suggestions, path=suggestions_path) if suggestions else ""
            return render_learning_suggestions(suggestions, path=str(path) if path else "")
        if sub in {"pending", "list", "review"}:
            from core.learning.proactive_learning import (
                cluster_suggestions,
                expire_stale_suggestions,
                read_learning_suggestions,
                render_learning_clusters,
            )
            expired = expire_stale_suggestions(path=suggestions_path)
            active = read_learning_suggestions(path=suggestions_path)
            clusters = cluster_suggestions(active)
            return render_learning_clusters(clusters, raw_count=len(active), expired_count=expired, path=suggestions_path)
        if sub in {"confirm", "dismiss"}:
            return self._cmd_learning_review(sub, arg)
        from core.learning.proactive_learning import cluster_suggestions, read_learning_suggestions
        from core.skills import default_skill_roots, load_generated_learning_skills, load_skills
        from core.system_health import build_health_report
        report = build_health_report(self.runtime_home)
        profile = report.learning.get("profile_learning", {})
        behavior = report.learning.get("behavior_rules", {})
        workflow = report.learning.get("workflow", {})
        memory = report.learning.get("memory", {})
        graph = report.graph.get("structural", {})
        active = read_learning_suggestions(path=suggestions_path)
        pending_clusters = cluster_suggestions(active)
        confirmed = [s for s in read_learning_suggestions(path=suggestions_path, include_inactive=True) if str(s.status).lower() == "confirmed"]
        confirmed_clusters = cluster_suggestions(confirmed)
        roots = default_skill_roots(
            getattr(self, "project_cwd", None),
            getattr(self, "runtime_home", None),
            profile=getattr(self, "profile", None),
            config=cfg,
        )
        skill_count = len(load_skills(roots)) + len(load_generated_learning_skills(getattr(self, "profile", None)))
        categories = profile.get("categories", {}) if isinstance(profile.get("categories", {}), dict) else {}
        category_text = ", ".join(f"{name} {count}" for name, count in sorted(categories.items())) if categories else "none"
        return "\n".join([
            "Learning status:",
            f"  profile entries: {profile.get('entries', 0)} · categories: {category_text}",
            f"  behavior rules:   {behavior.get('count', 0)}",
            f"  candidates:       {workflow.get('candidates', 0)} staged / {workflow.get('promoted', 0)} promoted",
            f"  skills:           {skill_count} local/generated pack(s); {len(confirmed_clusters)} confirmed cluster(s)",
            f"  suggestions:      {len(pending_clusters)} cluster(s) pending review ({len(active)} raw)",
            f"  memory:           {memory.get('turns', 0)} turns · FTS5 {'yes' if memory.get('fts5') else 'no'} · misses {memory.get('miss_terms', 0)}",
            f"  graph:            {graph.get('nodes', 0)} nodes / {graph.get('edges', 0)} edges / {graph.get('communities', 0)} communities",
            "  proof:            deterministic local reads only; no provider call",
        ])

    def _cmd_learning_review(self, action: str, suggestion_id: str) -> str:
        """Confirm/dismiss learning suggestions — cluster-wide, not item-by-item."""
        clean_id = str(suggestion_id or "").strip()
        if not clean_id:
            return f"Usage: /learning {action} <suggestion-id>"
        from core.learning.proactive_learning import (
            read_learning_suggestions,
            resolve_cluster_ids,
            update_learning_suggestion_status,
        )

        cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
        suggestions_path = resolve_state_path("memory/learning_suggestions.jsonl", cfg)
        member_ids = resolve_cluster_ids(clean_id, path=suggestions_path)
        if not member_ids:
            return f"No active learning suggestion found: {clean_id}"
        if action == "dismiss":
            updated = sum(1 for mid in member_ids if update_learning_suggestion_status(mid, "dismissed", path=suggestions_path))
            return f"Dismissed cluster: {updated} suggestion(s) ({clean_id})"
        from core.learning.trace_learning import apply_trace_learning_suggestion

        suggestions = {item.id: item for item in read_learning_suggestions(path=suggestions_path)}
        representative = suggestions.get(clean_id) or next((suggestions[mid] for mid in member_ids if mid in suggestions), None)
        result = apply_trace_learning_suggestion(getattr(self, "profile", None), representative) if representative else ""
        updated = sum(1 for mid in member_ids if update_learning_suggestion_status(mid, "confirmed", path=suggestions_path))
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
        return f"Confirmed cluster: {updated} suggestion(s) - now part of MO's skills ({clean_id}){skill_line}\n{result}".rstrip()

    def _cmd_structural_graph(self, rest: str) -> str:
        """Show or build MO's optional structural code graph."""
        from core.graph.structural_graph import build_structural_graph, graph_status

        sub = (rest or "").strip().split(maxsplit=1)[0].lower() if (rest or "").strip() else "status"
        graph_root = self.project_cwd
        if sub in {"status", ""}:
            status = graph_status(graph_root)
            lines = ["Structural graph:"]
            if status.get("available"):
                lines.append(f"  available: yes ({status.get('source_kind')})")
                lines.append(f"  nodes:     {status.get('nodes', 0)}")
                lines.append(f"  edges:     {status.get('edges', 0)}")
                lines.append(f"  communities: {status.get('communities', 0)}")
                if status.get("stale"):
                    lines.append("  freshness: stale vs current git HEAD; run /structural-graph refresh")
            else:
                lines.append("  available: no")
                lines.append("  run:       /structural-graph build")
            lines.append(f"  path:      {status.get('path')}")
            lines.append("  proof:     orientation only; file reads/tests still decide truth")
            return "\n".join(lines)
        if sub in {"build", "refresh", "rebuild", "setup"}:
            result = build_structural_graph(graph_root)
            if result.get("built"):
                return (
                    "Structural graph built:\n"
                    f"  path:  {result.get('path')}\n"
                    f"  files: {result.get('files', 0)}\n"
                    f"  nodes: {result.get('nodes', 0)}\n"
                    f"  edges: {result.get('edges', 0)}"
                )
            return f"Structural graph not built: {result.get('reason') or 'unknown'}"
        return "Use: /structural-graph status | build | refresh"

    def _cmd_prt(self, rest: str) -> str:
        """Trigger PRT review on last commit."""
        from core.review.diff_review import review_diff
        from core.workers import ensure_worker_registry
        import time
        
        parts = str(rest or "").strip().split()
        diff_ref = "HEAD"
        fix_mode = False
        
        for p in parts:
            if p == "--fix":
                fix_mode = True
            elif p and not p.startswith("--"):
                diff_ref = p
                
        worker_id = f"prt-{int(time.time()*1000)}"
        registry = ensure_worker_registry(self)
        registry.create(
            kind="prt",
            source="user",
            route="background",
            objective=f"Reviewing {diff_ref}",
            state="offered",
            worker_id=worker_id,
            claimed_paths=self._prt_claimed_paths(diff_ref),
        )
        
        # Clear suggestion flag now that PRT is running
        if hasattr(self, "_prt_ghost_suggestion"):
            delattr(self, "_prt_ghost_suggestion")
        
        def custom_prt_run(w_id: str, _obj: str, on_fin):
            try:
                report = review_diff(self, diff_ref)
                unresolved = int(getattr(report, "unresolved_count", 0) or 0)
                summary = f"PRT finished: {report.score}/5.0" + (f" · {unresolved} unresolved" if unresolved else "")
                boundary = self._run_consistency_boundary("prt", prt_report=report, final_text=summary)
                if boundary is not None and not getattr(boundary, "clean", True):
                    summary += f" · consistency {len(getattr(boundary, 'findings', []) or [])}"
                evidence = [
                    f"review:{diff_ref}",
                    f"files_changed:{int(getattr(report, 'files_changed', 0) or 0)}",
                    f"findings:{len(getattr(report, 'findings', []) or [])}",
                ]
                affected = list(getattr(report, "affected_tests", []) or [])[:3]
                evidence.extend(f"affected_test:{item}" for item in affected)
                registry.update(w_id, "completed", summary, result_summary=summary, evidence=evidence)
                
                # If fix mode, start iteration
                if fix_mode and not report.is_target_met:
                    from core.review.review_iteration import run_fix_loop
                    run_fix_loop(self, report)
                    
                # Route the report
                from core.review.prt_report import route_prt_report
                route_prt_report(self, report)
                
                if on_fin:
                    on_fin(registry.get(w_id), "completed")
            except Exception as e:
                registry.update(w_id, "blocked", f"PRT Error: {e}")
                if on_fin:
                    on_fin(registry.get(w_id), f"Error: {e}")
            finally:
                from core.worker_runtime import ensure_worker_runtime
                rt = ensure_worker_runtime(self)
                with rt._lock:
                    rt._threads.pop(w_id, None)
        
        from core.worker_runtime import ensure_worker_runtime, notify_native_async
        runtime = ensure_worker_runtime(self)
        record = runtime.start(
            objective=f"Reviewing {diff_ref}",
            source="user",
            worker_id=worker_id,
            on_finish=lambda rec, _result: notify_native_async(self, rec),
            custom_target=custom_prt_run
        )
        if getattr(record, "state", "") == "blocked":
            return f"[PRT BLOCKED] {getattr(record, 'note', '') or 'worker coordination blocked review'}"
        return f"[PRT STARTED] Reviewing {diff_ref} in background..."

    def _cmd_help(self, _rest: str) -> str:
        return SLASH_COMMAND_HELP

    def _cmd_init(self, _rest: str) -> str:
        from ..initializer import initialize_mo, render_init_report

        report = initialize_mo(home=getattr(self, "runtime_home", None), project_path=getattr(self, "project_cwd", None))
        return render_init_report(report)

    def _cmd_doctor(self, rest: str) -> str:
        """One-shot, offline-safe health check; `/doctor --json` for scripting."""
        from ..doctor import build_doctor_report, render_doctor_json, render_doctor_report

        args = str(rest or "").lower().split()
        report = build_doctor_report(
            home=getattr(self, "runtime_home", None),
            config_path=getattr(self, "config_path", None),
            project_path=getattr(self, "project_cwd", None),
            config=getattr(self, "config", None),
        )
        if "--json" in args or "json" in args:
            return render_doctor_json(report)
        return render_doctor_report(report)

    def _cmd_migrate(self, rest: str) -> str:
        from ..state_migration import (
            apply_state_migration,
            parse_migration_request,
            plan_state_migration,
            render_state_migration_report,
        )

        action, confirm = parse_migration_request(rest)
        plan = plan_state_migration(source_root=getattr(self, "agent_root", None), home=getattr(self, "runtime_home", None))
        if action == "dry-run":
            return render_state_migration_report(plan)
        if not confirm:
            return render_state_migration_report(plan) + "\n\nApply not run: add `--confirm` to copy/move legacy state."
        result = apply_state_migration(plan, confirm=True, remove_source=(action == "move"))
        return render_state_migration_report(plan, result)

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

        if sub in {"mine", "suggestions", "suggest"}:
            from ..learning.proactive_learning import mine_learning_suggestions, render_learning_suggestions, write_learning_suggestions
            memory_path = getattr(getattr(self, "memory", None), "path", "memory/learning.sqlite")
            suggestions = mine_learning_suggestions(memory_path)
            out_path = Path(getattr(self.profile, "_path", "memory/mo.db")).parent / "learning_suggestions.jsonl"
            if suggestions:
                write_learning_suggestions(suggestions, path=out_path)
            return render_learning_suggestions(suggestions, path=str(out_path) if suggestions else "")

        if sub == "export":
            from ..learning.learning_bundle import export_learning_bundle
            result = export_learning_bundle(self.profile, path=value.strip() or None)
            if not result.get("exported"):
                return f"Export refused: {result.get('reason', 'unknown')}"
            counts = result.get("counts", {})
            return (
                f"Learning bundle exported: {result['path']}\n"
                f"  profile files {counts.get('profile_files', 0)} · confirmed clusters {counts.get('confirmed_suggestions', 0)} · promoted workflows {counts.get('promoted_workflows', 0)}\n"
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
                f"  new promoted workflow(s):    {result.get('new_promoted_workflows', 0)}",
            ]
            files = result.get("profile_files_for_review") or []
            if files:
                lines.append(f"  profile files staged for manual review: {', '.join(files)}")
                if result.get("review_dir"):
                    lines.append(f"  staged at: {result['review_dir']}")
            return "\n".join(lines)

        return f"Unknown profile subcommand: {sub}\nUse: /profile name, /profile tools, /profile provider, /profile mine, /profile export, /profile import <path> [--confirm]"

    def _cmd_clear(self, _rest: str) -> str:
        self.session.clear()
        return "Conversation cleared."

    def _cmd_heartbeat(self, rest: str) -> str:
        """Show or refresh MO's local heartbeat ledger."""
        sub = (rest or "").strip().split(maxsplit=1)[0].lower() if (rest or "").strip() else "status"
        try:
            from ..heartbeat import build_surface_continuity_context, record_heartbeat, render_heartbeat_status
            if sub in {"status", ""}:
                return render_heartbeat_status(self, gateway=getattr(self, "gateway", None))
            if sub in {"now", "record", "ping"}:
                record_heartbeat(self, gateway=getattr(self, "gateway", None), surface=getattr(self, "_current_route_source", "terminal"), event="manual")
                return render_heartbeat_status(self, gateway=getattr(self, "gateway", None))
            if sub in {"context", "surfaces"}:
                return build_surface_continuity_context(self, current_surface=getattr(self, "_current_route_source", "terminal")) or "No recent heartbeat continuity."
        except Exception as exc:
            detail = clean_provider_error(str(exc))
            return "\n".join([
                "MO heartbeat error: unavailable",
                "  where: /heartbeat command",
                "Fix: try /status or check the heartbeat ledger if this repeats.",
                f"  detail: {detail}",
            ])
        return "Use: /heartbeat [status|now|context]"

    def _cmd_telegram(self, rest: str) -> str:
        """Local Telegram gateway control. Never prints or stores token values."""
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
        parts = (rest or "").strip().split()
        sub = parts[0].lower() if parts else "status"
        if sub in {"status", ""}:
            st = gateway.status()
            token_state = "present" if st.get("token_present") else f"missing {st.get('token_env')}"
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
        return "Use: /telegram [status|queue|chats|approve <code>|start|disable]"

    def _cmd_usage(self, _rest: str) -> str:
        input_tokens = sum(e.get("input_tokens", 0) for e in self.session.token_log)
        output_tokens = sum(e.get("output_tokens", 0) for e in self.session.token_log)
        total_tokens = sum(e.get("total_tokens", 0) for e in self.session.token_log)
        lines = [
            "Token usage:",
            f"  total:   {total_tokens:,}",
            f"  input:   {input_tokens:,}",
            f"  output:  {output_tokens:,}",
        ]
        if self._tool_context_saving_ops() > 0:
            lines.append(f"  saved:   ~{self._compression_saved_tokens_estimate():,} tokens ({self._tool_context_saved_chars():,} chars) via tool compression/truncation")
            carry_text = f" · carried {self._carried_tool_context_saving_ops()}" if self._carried_tool_context_saving_ops() else ""
            lines.append(
                f"  context-save:{self._tool_context_saving_ops():>4} ops · "
                f"current compressed {getattr(self, 'compression_total_ops', 0)} / truncated {getattr(self, 'truncation_total_ops', 0)}{carry_text}"
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

    def _compression_saved_tokens_estimate(self) -> int:
        """Approximate saved context tokens from compressed tool-output chars."""
        try:
            saved_chars = self._tool_context_saved_chars()
        except (TypeError, ValueError):
            saved_chars = 0
        return max(0, round(saved_chars / 4))

    def _cmd_model(self, rest: str) -> str:
        rest = rest.strip()
        if not rest:
            lines = [
                f"model:        {self.provider_name} / {self.model}",
                "",
                "Available models:",
            ]
            for i, p in enumerate(self.providers):
                marker = "  * " if i == self.provider_index else "    "
                lines.append(f"{marker}[{i+1}] {p.name} / {p.model}")
            lines.append("")
            lines.append("To switch model, use: /model <number> or /model <provider_name>")
            return "\n".join(lines)

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
            old_provider = self.provider_name
            old_model = self.model
            self.provider_index = target_idx
            p = self.providers[target_idx]
            self.model = p.model
            self.provider_name = p.name
            self.api_mode = p.api_mode
            self._refresh_context_budget()
            append_provider_audit(
                "model_switch",
                surface="slash_command",
                session_id=getattr(self.session, "session_id", ""),
                from_provider=old_provider,
                from_model=old_model,
                to_provider=self.provider_name,
                to_model=self.model,
                provider=self.provider_name,
                model=self.model,
                reason="/model command",
            )
            return f"Switched to model: {p.name} / {p.model}"

        return f"Model '{rest}' not found in available models."

    def _session_save_extra_meta(self, *, closeout: dict | None = None) -> dict | None:
        extra: dict = {}
        if self._tool_context_saving_ops() > 0 or self._safe_int(getattr(self, "session_compaction_total_ops", 0)) > 0:
            extra["compression"] = {
                "total_ops": self.compression_total_ops,
                "total_saved": self.compression_total_saved,
                "last_pct": self.compression_last_pct,
                "truncation_ops": getattr(self, "truncation_total_ops", 0),
                "truncation_saved": getattr(self, "truncation_total_saved", 0),
                "truncation_last_pct": getattr(self, "truncation_last_pct", 0),
                "momentum_ops": getattr(self, "context_momentum_compression_ops", 0),
                "momentum_saved": getattr(self, "context_momentum_compression_saved", 0),
                "momentum_truncation_ops": getattr(self, "context_momentum_truncation_ops", 0),
                "momentum_truncation_saved": getattr(self, "context_momentum_truncation_saved", 0),
                "session_compaction_ops": getattr(self, "session_compaction_total_ops", 0),
                "session_compaction_saved": getattr(self, "session_compaction_total_saved", 0),
                "context_saved_chars": self._tool_context_saved_chars(),
                "current_context_saved_chars": self._current_tool_context_saved_chars(),
                "momentum_saved_chars": self._carried_tool_context_saved_chars(),
                "saved_tokens_est": self._compression_saved_tokens_estimate(),
            }
        pending = getattr(self, "_pending_interrupted_work", {})
        if isinstance(pending, dict) and str(pending.get("user") or "").strip():
            extra["pending_interrupted_work"] = {
                "changed": True,
                "reason": str(pending.get("reason") or "paused_work")[:120],
                "user": str(pending.get("user") or "")[:500],
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
        if not hasattr(self, "config") or not getattr(self.session, "messages", None):
            return None
        try:
            report = build_session_closeout(self, reason=reason)
            path = write_session_closeout(report)
            staged = stage_session_closeout_feedback(getattr(self, "profile", None), report, closeout_path=path)
            boundary = self._run_consistency_boundary("session_closeout", session_closeout=report)
            self._last_session_closeout_report = report
            monitor = get_monitor()
            if monitor:
                monitor.emit("session_event", {
                    "kind": "closeout_write",
                    "reason": reason,
                    "path": str(path),
                    "clean": bool(getattr(report, "clean", False)),
                    "unresolved": len(getattr(report, "unresolved", []) or []),
                    "learning_staged": bool(staged.get("staged")) if isinstance(staged, dict) else False,
                })
            meta = closeout_meta(report, path)
            if isinstance(staged, dict):
                meta["closeout_learning"] = staged
            if boundary is not None and hasattr(boundary, "as_dict"):
                meta["consistency_boundary"] = boundary.as_dict()
            return meta
        except Exception:
            return None

    def autosave_session(self, *, closeout: dict | None = None) -> None:
        """Persist the current foreground session when it has real conversation history."""
        try:
            if self._sessions and getattr(self.session, "messages", None):
                self._sessions.save(self._sessions.current_name, self.session, extra_meta=self._session_save_extra_meta(closeout=closeout))
                self._emit_session_event(None, "autosave", slot=str(getattr(self._sessions, "current_name", "") or ""), closeout=bool(closeout))
        except Exception:
            traceback.print_exc()

    def _cmd_ghost(self, rest: str) -> str:
        """Quick side-question to Ghost, toggle on/off, or show status."""
        rest = rest.strip()
        if rest.lower() == "on":
            return "[GHOST_ON]"
        if rest.lower() == "off":
            return "[GHOST_OFF]"
        low = rest.lower()
        if low in {"window", "desktop", "launch", "start"} or low.startswith(("window ", "desktop ", "launch ", "start ")):
            parts = rest.split(None, 1)
            return self._ghost_desktop_control(parts[1] if len(parts) > 1 else "toggle")
        if not rest or rest.lower() == "help":
            return (
                "Ghost:\n"
                "  Alt+G opens/hides the current Ghost/PRT panel in the TUI.\n"
                "  Ctrl+O expands/collapses; Esc hides the panel.\n"
                "  /ghost launch   start Ghost Desktop as its own process (Win+Alt+M to summon).\n"
                "  /ghost window   show/hide it, or launch it if not running (same as /companion).\n"
                "  Ghost Desktop runs separately and survives this terminal closing.\n"
                "  Ghost is side-check/planning only; send real work to MO when ready.\n"
            )
        # Quick slash Ghost side-question: provider-side tools stay disabled.
        live_context = build_ghost_context(self, getattr(self, "gateway", None), question=rest)
        side_context = GHOST_SIDECHAT_SYSTEM
        if live_context:
            side_context = f"{side_context}\n\n{live_context}"
        # Ghost side-chat gets compact context, not full session history
        recent = self._ghost_context_messages(rest)
        messages = [{"role": "system", "content": side_context}] + recent + [
            {"role": "user", "content": f"Operator side-question for Ghost (brief answer; provider-side tools disabled):\n{rest}"}
        ]
        registry = ensure_worker_registry(self)
        record = registry.create(kind="ghost", source="user", route="main", objective=rest, state="running", note="ghost slash side-chat")
        try:
            with self.provider_scope("ghost_slash", worker_id=record.id):
                response, _provider = self.complete_ghost_no_tools(
                    surface="ghost_slash",
                    request="ghost-slash",
                    messages=messages,
                    max_tokens=min(int(self.max_tokens or 800), 800),
                )
            result = str(getattr(response, "content", "") or "").strip()
            registry.update(record.id, "completed", "ghost slash replied", result_summary=result[:240])
            append_ghost_audit("side_chat_command", user_text=rest, response_text=result)
            return result
        except Exception as exc:
            result = f"Ghost error: {clean_provider_error(str(exc))}"
            registry.update(record.id, "blocked", result[:240])
            append_ghost_audit("side_chat_command_error", user_text=rest, response_text=result)
            return result

    def _ghost_desktop_control(self, action: str) -> str:
        """Control the desktop Ghost. If it's co-hosted in this process
        (run_in_terminal: true) show/hide/toggle it; otherwise launch it as its own
        detached process so it survives this terminal."""
        action = (action or "").strip().lower()
        window = getattr(self, "_companion", None)
        if window is not None:
            if action == "show":
                window.show()
                return "[GHOST WINDOW SHOWN]"
            if action == "hide":
                window.hide()
                return "[GHOST WINDOW HIDDEN]"
            window.toggle()
            return "[GHOST WINDOW TOGGLED]"
        # Decoupled default: no in-thread companion → launch the separate process.
        if action == "hide":
            return "Ghost Desktop runs as its own process — hide it with Esc or from its tray icon."
        from core.ghost.desktop_launch import launch_ghost_desktop_detached
        return launch_ghost_desktop_detached(getattr(self, "config", None))

    def _cmd_companion(self, rest: str) -> str:
        """Back-compat alias for `/ghost window` — toggle the desktop Ghost window."""
        return self._ghost_desktop_control(rest)

    @staticmethod
    def _ghost_safe_messages(raw_messages: list[dict], prompt: str) -> list[dict]:
        return ghost_safe_messages(raw_messages, prompt)

    def _cmd_compact(self, rest: str) -> str:
        """Legacy command: use Handsoff instead of destructive trimming."""
        return self._cmd_handoff("start " + (rest or "manual compact replacement").strip())

    def _cmd_handoff(self, rest: str) -> str:
        """Manage MO context handoff."""
        rest = str(rest or "").strip()
        lower = rest.lower()
        if not rest or lower in {"status", "check"}:
            pressure = context_pressure(self)
            return (
                "Context handoff status:\n"
                f"  used:     {pressure['pressure']:.0%}\n"
                f"  chars:    {pressure['chars']:,} / {pressure['budget_chars']:,}\n"
                f"  messages: {pressure['message_count']} / {pressure['max_history']}\n"
                f"  compacted:{pressure.get('trimmed_messages_count', 0):>3}\n"
                f"  mode:     {'automatic' if self.context_handoff_enabled else 'off'}"
            )
        if lower.startswith("import "):
            path = Path(rest.split(None, 1)[1]).expanduser()
            if not path.exists():
                return f"Handoff file not found: {path}"
            document = path.read_text(encoding="utf-8")
            seed_session_from_handoff(self.session, document)
            self.last_handoff_notice = f"Context handoff imported into clean session: {path}"
            return self.last_handoff_notice
        focus = rest
        if lower.startswith("start"):
            focus = rest[5:].strip()
        return self._perform_context_handoff(focus=focus or "manual handoff", reason="manual handoff", latest_user="", expose_notice=True)

    def _cmd_undo(self, _rest: str) -> str:
        """Remove the last user+assistant exchange."""
        msgs = self.session.messages
        if not msgs:
            return "Nothing to undo."
        # Remove from tail: last assistant, then last user
        removed = 0
        while msgs and msgs[-1].get("role") in ("assistant", "tool"):
            msgs.pop()
            removed += 1
        if msgs and msgs[-1].get("role") == "user":
            self._last_undone_input = msgs[-1].get("content", "")
            msgs.pop()
            removed += 1
        else:
            self._last_undone_input = ""
        if self.session.turn_count > 0:
            self.session.turn_count -= 1
        return f"Undone ({removed} messages removed). Use /retry to re-run."

    def _cmd_retry(self, _rest: str) -> str:
        """Re-run the last user prompt. Returns special marker for TUI dispatch."""
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
        return "[RETRY]"

    def _cmd_session(self, rest: str) -> str:
        """Manage sessions: list, save, remove, switch."""
        if not self._sessions:
            return "Session manager not available."
        rest = rest.strip()
        if not rest or rest.lower() == "list":
            return self._sessions.render_list()
        parts = rest.split(maxsplit=1)
        sub = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if sub == "save":
            name = arg or self._sessions.current_name
            closeout = self.save_session_closeout(reason=f"session save:{name}")
            result = self._sessions.save(name, self.session, extra_meta=self._session_save_extra_meta(closeout=closeout))
            if closeout:
                report = getattr(self, "_last_session_closeout_report", None)
                if report:
                    result += "\n" + render_session_closeout(report, path=str(closeout.get("path", "")))
                boundary = getattr(self, "_last_consistency_boundary_report", None)
                if boundary:
                    result += "\n" + render_consistency_boundary(boundary)
            return result
        if sub == "remove":
            if not arg:
                return "Use: /session remove <name>"
            return self._sessions.remove(arg)
        # Treat as session name to switch to
        name = rest
        if name.isdigit():
            sessions = self._sessions.list_sessions()
            idx = int(name) - 1
            if 0 <= idx < len(sessions):
                name = sessions[idx]["name"]
            else:
                return f"Invalid session number. Use 1-{len(sessions)}."
        closeout = self.save_session_closeout(reason=f"session switch:{name}")
        result = self._sessions.switch(name, self.session, extra_meta=self._session_save_extra_meta(closeout=closeout))
        self._restore_context_saving_meta(getattr(self.session, "_loaded_meta", {}))
        return result

    def _cmd_resume(self, _rest: str) -> str:
        """Resume the most recently saved session."""
        if not self._sessions:
            return "Session manager not available."
        latest = self._sessions.latest()
        if not latest:
            return "No saved sessions to resume."
        closeout = self.save_session_closeout(reason=f"session resume:{latest}")
        result = self._sessions.switch(latest, self.session, extra_meta=self._session_save_extra_meta(closeout=closeout))
        self._restore_context_saving_meta(getattr(self.session, "_loaded_meta", {}))
        return result

    def _cmd_reload(self, _rest: str) -> str:
        """Reload model instructions, config, and profile without restart."""
        self.config = load_config(self.config_path)
        system_path = self.config.get("paths", {}).get("system_prompt", "")
        self.system_message, self.system_prompt_source = self._load_system_message(system_path)
        self.session.system_message = self.system_message
        agent_cfg = self.config.get("agent", {}) if isinstance(self.config.get("agent", {}), dict) else {}
        self.reasoning = str(agent_cfg.get("reasoning", getattr(self, "reasoning", "high")) or "high")
        self.profile = Profile.load(
            resolve_state_path(self.config.get("paths", {}).get("memory_file", "memory/mo.db"), self.config)
        )
        return (
            "Reloaded:\n"
            f"  model instructions: {self.system_prompt_source} · {len(self.system_message)} chars\n"
            f"  config: {self.config_path}\n"
            f"  profile: {self.profile.user_name or 'Operator'}"
        )

    def _cmd_think(self, rest: str) -> str:
        """Set reasoning effort level."""
        level = rest.strip().lower()
        valid = {"high", "medium", "low"}
        if level not in valid:
            current = self.config.get("agent", {}).get("reasoning", "high")
            return f"Reasoning: {current}\nUse: /think high | medium | low"
        self.config.setdefault("agent", {})["reasoning"] = level
        self.reasoning = level
        return f"Reasoning set to: {level}"

    def _cmd_settings(self, _rest: str) -> str:
        """Show current settings."""
        reasoning = getattr(self, "reasoning", self.config.get("agent", {}).get("reasoning", "high"))
        return (
            f"MO settings:\n"
            f"  model:        {self.provider_name} / {self.model}\n"
            f"  reasoning: {reasoning}\n"
            f"  temperature: {self.temperature}\n"
            f"  max_tokens: {self.max_tokens}\n"
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
            return "No projects tracked yet."
        lines = ["Projects:"]
        for entry in sorted(self.profile.projects.values(), key=lambda e: e.last_opened, reverse=True):
            lines.append(f"  {entry.name} ({entry.path})")
            lines.append(f"    sessions: {entry.session_count} | last: {format_profile_time(entry.last_opened)}")
        return "\n".join(lines)

    def _cmd_new(self, _rest: str) -> str:
        # Record current session stats before clearing
        closeout = self.save_session_closeout(reason="new session")
        if self._sessions and getattr(self.session, "messages", None):
            self.autosave_session(closeout=closeout)
        input_tokens = sum(e.get("input_tokens", 0) for e in self.session.token_log)
        output_tokens = sum(e.get("output_tokens", 0) for e in self.session.token_log)
        self.profile.record_session(
            turns=self.session.turn_count,
            tokens_in=input_tokens,
            tokens_out=output_tokens,
        )
        self.session.clear()
        self.session.session_id = f"mo-{int(time.time())}"
        return f"New session: {self.session.session_id}"

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
            return runner.stop()

        if command in ("status", "info", ""):
            if self._goal_active:
                if command == "":
                    # /goal with no args while active → show/foreground active goal in UI
                    return "[GOAL_CONTINUE]"
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

        # Start new goal or continue
        if command in ("continue", "resume"):
            if self._goal_active:
                return "[GOAL_CONTINUE]"
            plan = getattr(self, "_goal_plan", None)
            if plan and getattr(plan, "state", "") == "paused":
                plan.state = "running"
                plan.stop_reason = ""
                self._goal_active = True
                return "[GOAL_CONTINUE]"
            return "No active goal to continue."

        # New goal with objective
        objective_tokens = rest.split()
        objective, budget = parse_goal_budget(objective_tokens)
        if not objective:
            return "Usage: /goal <task>"
        if self._goal_objective_too_generic(objective):
            return "Goal needs a specific objective. Example: /goal review interface visuals for inconsistencies and report findings"

        # Return special marker so TUI knows to start goal in background thread
        self._goal_pending_objective = objective
        self._goal_pending_budget = budget
        return "[GOAL_START]"

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
