"""MO agent /status dashboard mixin — extracted from agent_slash.py.

A cohesive, read-only status surface (the `/status` command and its per-area
summaries). Lives on its own so agent_slash.py stays focused on command
dispatch. All methods are bound to the Agent via mixin inheritance.
"""

import os
import time
from pathlib import Path
import traceback


class AgentStatusCommands:
    """`/status` dashboard and its per-area summary helpers."""

    def _cmd_status(self, _rest: str) -> str:
        name_part = ""
        if self.profile.user_name:
            name_part = f" ({self.profile.user_alias})" if self.profile.user_alias else ""
            name_part = f" — {self.profile.user_name}{name_part}"
        lines = [
            f"MO status{name_part}:",
            f"  model:      {self.provider_name} / {self.model}",
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
            f"  context:    {self._status_context_summary()}",
            f"  graph:      {self._status_graph_summary()}",
        ]
        lines.extend(self._status_hidden_attention_rows())
        if self._tool_context_saving_ops() > 0:
            saved_tokens = self._compression_saved_tokens_estimate()
            carried_ops = self._carried_tool_context_saving_ops()
            carry_text = f"; carried {carried_ops} ops" if carried_ops else ""
            lines.append(
                f"  context-save: {self._tool_context_saving_ops()} ops · ~{saved_tokens:,} tokens / "
                f"{self._tool_context_saved_chars():,} chars saved "
                f"(current {getattr(self, 'compression_total_ops', 0)} compressed/{getattr(self, 'truncation_total_ops', 0)} truncated{carry_text}; "
                f"last {getattr(self, 'compression_last_pct', 0)}% compressed / {getattr(self, 'truncation_last_pct', 0)}% truncated)"
            )
        if self._safe_int(getattr(self, "session_compaction_total_ops", 0)) > 0:
            lines.append(
                f"  session-compact: {getattr(self, 'session_compaction_total_ops', 0)} ops · "
                f"{getattr(self, 'session_compaction_total_saved', 0):,} chars saved before handoff"
            )
        lines.append(f"  profile:  {self.profile.total_sessions} sessions · {self.profile.total_turns} turns lifetime")
        return "\n".join(lines)

    def _status_heartbeat_summary(self) -> str:
        try:
            from ..heartbeat import build_heartbeat_snapshot, read_recent_heartbeats
            item = (read_recent_heartbeats(limit=1) or [None])[-1]
            if not item:
                item = build_heartbeat_snapshot(
                    self,
                    gateway=getattr(self, "gateway", None),
                    surface=getattr(self, "_current_route_source", "terminal"),
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
            from .telegram.gateway import TelegramGateway
            gateway = getattr(self, "telegram_gateway", None) or getattr(self, "_telegram_gateway", None)
            if gateway is None:
                gateway = TelegramGateway.from_agent(self, gateway=getattr(self, "gateway", None))
            st = gateway.status()
            enabled = bool(st.get("enabled"))
            running = bool(st.get("running"))
            token = "token present" if st.get("token_present") else f"token missing {st.get('token_env')}"
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
        value = str(state or "").strip().lower()
        if value in {"offered", "accepted", "pending"}:
            return "queued"
        if value in {"active"}:
            return "running"
        if value in {"done"}:
            return "completed"
        if value in {"cancelled", "canceled"}:
            return "paused"
        return value if value in {"running", "queued", "blocked", "completed", "paused", "failed", "open"} else "running"

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
            board = getattr(getattr(self, "gateway", None), "last_task_board", None) or getattr(self, "_active_task_board", None)
            if not board:
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

    def _status_graph_summary(self) -> str:
        try:
            from pathlib import Path
            from ..graph.structural_graph import graph_exists, graph_path
            root = Path(getattr(self, "project_cwd", os.getcwd()))
            if graph_exists(root):
                gpath = graph_path(root)
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
                return f"active · {size_kb}KB · built {age_text}"
            return "not built · /sg build"
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
            if str(getattr(self, "last_fallback_notice", "") or "").strip():
                return "fallback active · detail /model"
        except Exception:
            traceback.print_exc()
        return ""

    def _status_learning_attention_summary(self) -> str:
        try:
            from ..learning.proactive_learning import read_learning_suggestions
            base = Path(getattr(getattr(self, "profile", None), "_path", "memory/mo.db")).parent
            count = len(read_learning_suggestions(path=base / "learning_suggestions.jsonl"))
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
