"""MO agent /status dashboard mixin — extracted from agent_slash.py.

A cohesive, read-only status surface (the `/status` command and its per-area
summaries). Lives on its own so agent_slash.py stays focused on command
dispatch. All methods are bound to the Agent via mixin inheritance.
"""

import os
import time
from pathlib import Path
import traceback

from .agent_utils import visible_worker_state
from ..utils.number_utils import cache_hit_percentage
from ..state.paths import LEARNING_SUGGESTIONS_PATH, PROFILE_DB_PATH, resolve_state_path


class AgentStatusCommands:
    """`/status` dashboard and its per-area summary helpers."""

    def health_status(self) -> tuple[tuple[str, str], ...]:
        """Bounded process-local observations for Health; never start a service."""
        rows: list[tuple[str, str]] = []

        def observe(label, reader):
            try:
                value = reader()
                text = " ".join(str(value or "").split())[:240]
            except Exception:
                text = "Observation unavailable"
            if text:
                rows.append((label, text))

        def provider_status():
            from ..provider.provider_capacity import get_capacity
            state = get_capacity().snapshot(self.provider_name, self.model)
            condition = {"unobserved": "No response evidence", "no_known_block": "No known block",
                         "blocked": state["kind"] or "Blocked"}[state["state"]]
            retry = state["retry_after_seconds"]
            if retry is not None:
                condition += f" · retry in {retry}s"
            return f"{self.provider_name} / {self.model} · {condition} · /model"

        def usage():
            from ..provider.codex_usage import usage_text
            from ..provider.deepseek_balance import balance_amount
            provider = getattr(self, "provider", None)
            text = usage_text(provider)
            balance = balance_amount(provider)
            return text or (f"Cached balance ${balance:.2f}" if balance is not None else "")

        observe("Selected provider", provider_status)
        observe("Usage", usage)
        observe("Provider notice", self._status_provider_attention_summary)
        observe("Recent work", self._status_taskboard_summary)
        observe("Workers", self._status_workers_summary)
        observe("Goal", self._status_goal_summary)
        observe("Context", self._status_context_summary)
        observe("Interrupted work", self._status_paused_work_summary)
        observe("Scheduler", self._status_scheduler_summary)
        observe("MCP", self._status_mcp_summary)
        gateway = getattr(self, "telegram_gateway", None) or getattr(self, "_telegram_gateway", None)
        if gateway is not None:
            def telegram():
                state = gateway.status()
                condition = "running" if state.get("running") else "stopped" if state.get("enabled") else "disabled"
                return f"{condition} · {int(state.get('pending_jobs') or 0)} pending · /telegram status"
            observe("Telegram", telegram)

        def systemcare():
            from ..systemcare.dashboard import read_dashboard_status
            state = read_dashboard_status(getattr(self, "config", {}) or {})
            when = float(state.get("last_scan_at") or 0)
            if state.get("active") or state.get("state") == "unavailable":
                return state.get("label")
            if when:
                age = max(0, int((time.time() - when) / 60))
                return f"{state['label']} · last scan {age}m ago"
            return ""

        observe("SystemCare", systemcare)
        memory = getattr(self, "memory", None)
        if memory is not None and callable(getattr(memory, "retrieval_status", None)):
            def recall():
                state = memory.retrieval_status()
                return str(state.get("mode") or "not observed") + (
                    f" · {state['reason']}" if state.get("reason") else ""
                )
            observe("Recall", recall)

        def sync_status():
            from ..state.everywhere_coordinator import read_coordinator_status
            state = read_coordinator_status(getattr(self, "config", {}) or {})
            parts = []
            for name in ("continuity", "profile", "transfer"):
                item = state.get(name)
                if not isinstance(item, dict):
                    continue
                when = item.get("checked_at") if name == "profile" else state.get("created_at")
                age = f" · {max(0, int(time.time() - float(when)))}s ago" if when else " · age unknown"
                parts.append(f"{name}: {item.get('state') or 'unknown'}{age}")
            return "; ".join(parts)

        observe("Sync record", sync_status)
        return tuple(rows)

    def _cmd_status(self, _rest: str) -> str:
        name_part = ""
        if self.profile.user_name:
            name_part = f" ({self.profile.user_alias})" if self.profile.user_alias else ""
            name_part = f" — {self.profile.user_name}{name_part}"
        lines = [
            f"MO status{name_part}:",
            f"  model:      {self.provider_name} / {self.model}",
            f"  instance:   {getattr(self, 'instance_id', 'unknown')}",
            f"  session id: {self.session.session_id}",
            f"  turns:      {self.session.turn_count}",
            f"  workspace:  {self._active_lane or 'default (read/write)'}",
            f"  project:    {getattr(self, 'project_cwd', os.getcwd())}",
            f"  home:       {getattr(self, 'runtime_home', '')}",
            f"  invoked:    {getattr(self, 'invoked_as', 'mo')}",
            f"  safeguards: {'on' if self.sandbox_config['enabled'] else 'off'}",
            "",
            "Runtime:",
            f"  heartbeat:  {self._status_heartbeat_summary()}",
            f"  telegram:   {self._status_telegram_summary()}",
            f"  workers:    {self._status_workers_summary()}",
            f"  goal:       {self._status_goal_summary()}",
            f"  taskboard:  {self._status_taskboard_summary()}",
            f"  work state: {self._status_work_learning_summary()}",
            f"  context:    {self._status_context_summary()}",
            f"  graph:      {self._status_graph_summary()}",
            f"  mcp:        {self._status_mcp_summary()}",
        ]
        from ..profile.server_aliases import configured_server_aliases
        try:
            aliases = configured_server_aliases()
            if aliases:
                lines.append("Configured SSH aliases (not health observations):")
                lines.extend(f"  {alias}: Not checked" for alias in aliases)
        except (OSError, ValueError, UnicodeError):
            lines.append("  SSH aliases: configuration unavailable")
        lines.extend(self._status_hidden_attention_rows())
        if self._tool_context_saving_ops() > 0:
            saved_tokens = self._context_saved_tokens_estimate()
            carried_ops = self._carried_tool_context_saving_ops()
            carry_text = f"; carried {carried_ops} ops" if carried_ops else ""
            lines.append(
                f"  context-save: {self._tool_context_saving_ops()} ops · ~{saved_tokens:,} tokens / "
                f"{self._tool_context_saved_chars():,} chars saved "
                f"(current {getattr(self, 'result_cap_total_ops', 0)} result caps{carry_text}; "
                f"last {getattr(self, 'result_cap_last_pct', 0)}% capped)"
            )
        if self._safe_int(getattr(self, "session_compaction_total_ops", 0)) > 0:
            lines.append(
                f"  session-compact: {getattr(self, 'session_compaction_total_ops', 0)} ops · "
                f"{getattr(self, 'session_compaction_total_saved', 0):,} serialized chars removed (not token savings)"
            )
        sess = getattr(self, "session", None)
        input_toks = self._safe_int(getattr(sess, "input_tokens", 0))
        if input_toks > 0:
            hit = self._safe_int(getattr(sess, "cache_hit_tokens", 0))
            miss = self._safe_int(getattr(sess, "cache_miss_tokens", 0))
            write = self._safe_int(getattr(sess, "cache_write_tokens", 0))
            if hit or miss or write:
                ratio = cache_hit_percentage(input_toks, hit)
                ratio_text = "ratio unavailable" if ratio is None else f"{ratio:.0f}% prefix-cache hit"
                lines.append(
                    f"  cache:    {ratio_text} "
                    f"({hit:,}/{input_toks:,} input tokens"
                    + (f"; {write:,} cache-write tokens" if write else "")
                    + "; provider-reported)"
                )
            else:
                lines.append(
                    f"  cache:    provider reports no prefix-cache breakdown ({input_toks:,} input tokens)"
                )
        lines.append(f"  profile:  {self.profile.total_sessions} sessions · {self.profile.total_turns} turns lifetime")
        return "\n".join(lines)

    def _status_mcp_summary(self) -> str:
        mgr = getattr(self, "mcp_manager", None)
        if not mgr:
            return "off (no servers configured)"
        try:
            clients = getattr(mgr, "_clients", {}) or {}
            parts = [f"{name} ({len(getattr(c, 'tools', []) or [])} tools)" for name, c in clients.items()]
            text = ", ".join(parts) if parts else "enabled, no tools"
            degraded = list(getattr(mgr, "degraded", []) or [])
            if degraded:
                text += f"; degraded: {', '.join(degraded)}"
            return text
        except Exception:
            return "unavailable"

    def _status_heartbeat_summary(self) -> str:
        try:
            from ..runtime.heartbeat import build_heartbeat_snapshot, read_recent_heartbeats
            item = (read_recent_heartbeats(limit=1) or [None])[-1]
            if not item:
                item = build_heartbeat_snapshot(
                    self,
                    gateway=getattr(self, "gateway", None),
                    surface=self._provider_surface(),
                    event="status",
                )
            age = time.time() - float(item.get("created_at") or time.time())
            age_text = "now" if age < 1 else f"{int(age)}s ago" if age < 60 else f"{int(age // 60)}m ago"
            surface = str(item.get("surface") or "terminal")
            return f"clear · {surface} {age_text} · detail /heartbeat status"
        except Exception:
            return "needs attention · detail /heartbeat status"

    def _status_telegram_summary(self) -> str:
        try:
            from ..telegram.gateway import TelegramGateway
            gateway = getattr(self, "telegram_gateway", None) or getattr(self, "_telegram_gateway", None)
            if gateway is None:
                gateway = TelegramGateway.from_agent(self, gateway=getattr(self, "gateway", None))
            st = gateway.status()
            enabled = bool(st.get("enabled"))
            running = bool(st.get("running"))
            token = (
                "canonical token present"
                if st.get("token_present")
                else f"canonical token missing {st.get('token_env')}"
            )
            pending = int(st.get("pending_jobs") or 0) + int(st.get("unfinished_jobs") or 0)
            active = len(st.get("active_chats") or [])
            state = "running" if running else "disabled" if not enabled else "blocked"
            extra = f" · queue {pending} open" if pending else ""
            if active:
                extra += f" · {active} active"
            return f"{state} · {token}{extra} · detail /telegram status"
        except Exception:
            return "needs attention · detail /telegram status"

    def _status_workers_summary(self) -> str:
        try:
            registry = getattr(self, "workers", None)
            active = registry.active() if registry and hasattr(registry, "active") else []
            if not active:
                return "clear"
            counts: dict[str, int] = {}
            for record in active:
                state = self._visible_worker_state(str(getattr(record, "state", "") or "running"))
                counts[state] = counts.get(state, 0) + 1
            return " · ".join(f"{count} {state}" for state, count in sorted(counts.items())) + " · detail monitor/TUI"
        except Exception:
            return "needs attention"

    def _visible_worker_state(self, state: str) -> str:
        return visible_worker_state(state)

    def _status_goal_summary(self) -> str:
        try:
            plan = getattr(self, "_goal_plan", None)
            if not plan or not getattr(self, "_goal_active", False):
                return "clear · detail /goal status"
            steps = list(getattr(plan, "steps", []) or [])
            total = len(steps)
            completed = sum(1 for step in steps if str(getattr(step, "status", "") or "") == "completed")
            state = str(getattr(plan, "state", "") or "running")
            visible = self._visible_worker_state(state)
            return f"{visible} · {completed}/{total} completed · detail /goal status"
        except Exception:
            return "needs attention · detail /goal status"

    def _status_taskboard_summary(self) -> str:
        try:
            gateway = getattr(self, "gateway", None)
            board = getattr(gateway, "last_task_board", None) or getattr(self, "_active_task_board", None)
            if not board:
                resumable = getattr(gateway, "last_resumable_board", None)
                if resumable and int(getattr(resumable, "open_count", lambda: 0)() or 0) > 0:
                    total = len(getattr(resumable, "tasks", []) or [])
                    completed = int(getattr(resumable, "done_count", lambda: 0)() or 0)
                    open_count = int(getattr(resumable, "open_count", lambda: 0)() or 0)
                    return f"resumable · {completed}/{total} completed · {open_count} open · type resume"
                return "clear"
            total = len(getattr(board, "tasks", []) or [])
            if not total:
                return "clear"
            completed = 0
            blocked = 0
            open_count = 0
            for task in getattr(board, "tasks", []) or []:
                status = str(getattr(task, "status", "") or "").lower()
                if status == "completed":
                    completed += 1
                elif status == "blocked":
                    blocked += 1
                    open_count += 1
                elif status in {"pending", "active", "open", "running", "queued"}:
                    open_count += 1
            if blocked:
                return f"blocked · {completed}/{total} completed · {open_count} open"
            if open_count:
                if str(getattr(board, "state", "") or "").lower() == "abandoned":
                    return f"resumable · {completed}/{total} completed · {open_count} open · type resume"
                return f"open · {completed}/{total} completed"
            return f"completed · {completed}/{total} completed"
        except Exception:
            return "needs attention"

    def _status_context_summary(self) -> str:
        try:
            compact_ops = self._safe_int(getattr(self, "session_compaction_total_ops", 0))
            save_ops = self._tool_context_saving_ops()
            trimmed = self._safe_int(getattr(getattr(self, "session", None), "trimmed_messages_count", 0))
            pressure = 0.0
            threshold = 0.70
            try:
                from ..session.handoff import context_pressure
                metrics = context_pressure(self)
                pressure = float(metrics.get("pressure") or 0.0)
                cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
                agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
                threshold = float(agent_cfg.get("context_handoff_threshold", getattr(self, "context_handoff_threshold", 0.70)) or 0.70)
            except Exception:
                pressure = 0.0
            if trimmed:
                return f"needs attention · {trimmed} trimmed messages · detail /usage"
            if pressure >= max(0.25, min(0.95, threshold)):
                return f"needs attention · {pressure:.0%} pressure · detail /usage"
            if compact_ops:
                return f"clear · {compact_ops} compact ops · detail /usage"
            if save_ops:
                return f"clear · {save_ops} context-save ops · detail /usage"
            return "clear · detail /usage"
        except Exception:
            return "needs attention · detail /usage"

    def _status_work_learning_summary(self) -> str:
        try:
            from ..runtime.work_learning_status import build_work_learning_status, render_work_learning_status

            return render_work_learning_status(build_work_learning_status(self))
        except Exception:
            return "unavailable"

    def _status_graph_summary(self) -> str:
        try:
            from pathlib import Path
            from ..graph.structural_graph import graph_exists, graph_path, graph_status
            root = Path(getattr(self, "project_cwd", os.getcwd()))
            external = ""
            try:
                from ..graph.mcp_backend import status as graph_mcp_status
                mcp_status = graph_mcp_status()
                if mcp_status.get("available"):
                    servers = mcp_status.get("servers") or []
                    names = ", ".join(str(s.get("server") or "") for s in servers if s.get("server"))
                    external = f" · codebase-memory {names or 'active'}"
            except Exception:
                external = ""
            if graph_exists(root):
                gpath = graph_path(root)
                status = graph_status(root)
                age = time.time() - gpath.stat().st_mtime
                if age < 60:
                    age_text = "just now"
                elif age < 3600:
                    age_text = f"{int(age/60)}m ago"
                elif age < 86400:
                    age_text = f"{int(age/3600)}h ago"
                else:
                    age_text = f"{int(age/86400)}d ago"
                size_kb = gpath.stat().st_size // 1024
                freshness = "stale" if status.get("stale") else "fresh"
                quality = status.get("quality") if isinstance(status.get("quality"), dict) else {}
                return (
                    f"active · {freshness} · {size_kb}KB · built {age_text} · "
                    f"{quality.get('qualified_symbol_nodes', 0)} qualified symbols · "
                    f"{quality.get('ambiguous_calls', 0)} ambiguous calls{external}"
                )
            return f"not built · /sg build{external}"
        except Exception:
            return "unknown"

    def _status_hidden_attention_rows(self) -> list[str]:
        rows: list[str] = []
        paused = self._status_paused_work_summary()
        if paused:
            rows.append(f"  paused work: {paused}")
        provider = self._status_provider_attention_summary()
        if provider:
            rows.append(f"  provider:    {provider}")
        learning = self._status_learning_attention_summary()
        if learning:
            rows.append(f"  learning:    {learning}")
        scheduler = self._status_scheduler_summary()
        if scheduler:
            rows.append(f"  scheduler:   {scheduler}")
        return rows

    def _status_paused_work_summary(self) -> str:
        try:
            pending = getattr(self, "_pending_interrupted_work", {})
            if isinstance(pending, dict) and str(pending.get("user") or "").strip():
                return "available · detail /resume"
        except Exception:
            traceback.print_exc()
        return ""

    def _status_provider_attention_summary(self) -> str:
        try:
            notice = str(getattr(self, "last_fallback_notice", "") or "").strip()
            change_kind = str(getattr(self, "last_model_change_kind", "") or "")
            if notice and change_kind in {"", "provider_fallback"}:
                return "fallback active · detail /model"
        except Exception:
            traceback.print_exc()
        return ""

    def _status_learning_attention_summary(self) -> str:
        try:
            from ..learning.proactive_learning import read_learning_suggestions
            cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
            profile_db = str(
                getattr(getattr(self, "profile", None), "_path", "")
                or resolve_state_path(PROFILE_DB_PATH, cfg)
            )
            suggestions = Path(profile_db).parent / Path(LEARNING_SUGGESTIONS_PATH).relative_to("memory")
            count = len(read_learning_suggestions(path=suggestions))
            if count:
                label = "suggestion" if count == 1 else "suggestions"
                return f"{count} {label} available · detail /learning pending"
        except Exception:
            traceback.print_exc()
        return ""

    def _status_scheduler_summary(self) -> str:
        try:
            cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
            scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler", {}), dict) else {}
            service = getattr(self, "scheduler_service", None)
            enabled = scheduler_cfg.get("enabled", False) is True or service is not None
            if not enabled:
                return ""
            thread = getattr(service, "_thread", None) if service is not None else None
            if thread is not None and getattr(thread, "is_alive", lambda: False)():
                return "running · detail monitor"
            if service is not None:
                return "paused · detail monitor"
            return "needs attention · detail monitor"
        except Exception:
            return "needs attention · detail monitor"
