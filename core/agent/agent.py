"""MO — Core agent. Provider-first, sandbox-gated.

The pipeline:
  User -> LaneGuard(read-only?) -> Provider with FULL tools -> Sandbox gate -> Critic(secrets) -> Display

No protocol routing, no task rating, no intent classification, no tool profiles.
"""

import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import subprocess
import traceback

from ..provider.provider import (
    BaseProvider,
    init_provider,
    load_config,
    fallback_reason,
    clean_provider_error,
    is_context_overflow_error,
    is_rate_limit_error,
    prt_review_provider_chain,
    first_vision_provider_index,
)
from ..provider.model_slots import (
    main_model_selectors,
    provider_matches_selector,
    resolve_model_slot,
)
from ..provider.provider_capacity import get_capacity
from ..session.session import Session, _session_ended_clean
from ..prompts.system_prompt import load_system_prompt
from ..review.critic import AnswerCritic
from ..tooling.sandbox import redact_sensitive_text
from ..state.paths import ENV_MO_STATE_HOME, default_config_path, default_project_roots, mo_home, private_state_enabled, project_cwd, repo_root, resolve_state_path
from ..runtime.backend_monitor import BackendMonitor, get_monitor, preview_provider_messages, preview_provider_response
from ..runtime.instance import get_instance_id, instance_session_slot, shared_session_enabled
from ..provider.provider_audit import append_provider_audit
from ..context.work_patterns import build_ghost_work_guidance
from ..runtime.work_signals import looks_like_interrupted_resume_request
from ..learning.feedback_learning import extract_feedback_learning, record_feedback_learning
from ..learning.terms_learning import record_terms_learning
from ..learning.workflow_learning import WORKFLOW_CANDIDATE_NOTICE, promote_workflow_candidate, record_workflow_candidate_result
from ..worker import BackgroundWorkerRuntime, WorkerRegistry
from ..provider.model_limits import resolve_context_budget_tokens, context_budget_source
from ..session.handoff import build_compact_summary, build_handoff_document, context_pressure, recent_visible_report_messages, seed_session_from_handoff, should_auto_handoff, write_handoff_document
from ..session.session_momentum import maybe_compact_session
from ..profile import Profile
from ..learning.memory import EpisodicMemory
from ..tasking.agent_taskboard import AgentTaskBoard
from .agent_prt import AgentPRT
from .agent_slash import AgentSlashCommands
from .agent_status import AgentStatusCommands
from .agent_turn import AgentTurn



# Re-exports from core/agent/agent_utils.py.
from .agent_utils import (
    GHOST_PROPOSAL_SYSTEM,
    _usage_tokens,
    _usage_cache_tokens,
)


class Agent(AgentTaskBoard, AgentPRT, AgentSlashCommands, AgentStatusCommands, AgentTurn):
    """Provider-first MO agent. Model decides, sandbox enforces."""

    def __init__(self, config_path: str | None = None):
        self._init_config(config_path)
        self._init_providers()
        self._init_agent_config()
        self._init_system_message()
        self._init_session()
        self._init_safety_and_profile()
        self._init_state()
        self._init_tools()

    def _init_config(self, config_path: str | None) -> None:
        self.config_path = config_path or default_config_path()
        self.config = load_config(self.config_path)
        self.agent_root = repo_root()
        self.project_cwd = str(project_cwd())
        self.runtime_home = str(mo_home(self.config))
        self.instance_id = get_instance_id()
        self.invoked_as = os.environ.get("MO_INVOKED_AS", "mo")
        if private_state_enabled(self.config) and not os.environ.get("PYTEST_CURRENT_TEST"):
            os.environ.setdefault(ENV_MO_STATE_HOME, self.runtime_home)

    def _init_providers(self) -> None:
        prov = init_provider(self.config)
        self.providers: list[BaseProvider] = prov["providers"]
        self.provider_index: int = prov["provider_index"]
        self.model: str = prov["model"]
        self.fallback_model: str | None = prov["fallback_model"]
        self.provider_name: str = prov["provider_name"]
        self.api_mode: str = prov["api_mode"]
        self.temperature: float = prov["temperature"]
        self.max_tokens: int = prov["max_tokens"]
        self.reasoning: str = str(prov.get("reasoning") or "high")

    def _init_agent_config(self) -> None:
        agent_cfg = self.config.get("agent", {})
        self.max_tool_rounds: int = agent_cfg.get("max_tool_rounds", 80)
        self.max_provider_requests: int = agent_cfg.get("max_provider_requests", 300)
        self.doom_loop_tool_batch_threshold: int = max(2, int(agent_cfg.get("doom_loop_tool_batch_threshold", 3) or 3))
        self.tool_result_max_chars: int = agent_cfg.get("tool_result_max_chars", 6000)
        self.tool_compress_enabled: bool = bool(agent_cfg.get("tool_compress_enabled", True))
        self.tool_compress_min_bytes: int = int(agent_cfg.get("tool_compress_min_bytes", 500) or 500)
        self.context_summary_enabled: bool = agent_cfg.get("context_summary_enabled", True)
        self.context_budget_config = agent_cfg.get("context_budget_tokens", "auto")
        self.context_reserve_tokens: int = int(agent_cfg.get("context_reserve_tokens", 16384) or 16384)
        self.context_budget_tokens: int = self._context_budget_tokens_for(self.provider_name, self.model)
        self.context_budget_source: str = context_budget_source(self.context_budget_config, provider=self.provider_name, model=self.model)
        self.context_handoff_enabled: bool = bool(agent_cfg.get("context_handoff_enabled", True))
        self.context_handoff_threshold: float = float(agent_cfg.get("context_handoff_threshold", 0.70) or 0.70)
        self.deferred_tool_registry_enabled: bool = bool(agent_cfg.get("deferred_tool_registry_enabled", True))

        # Sandbox config
        sandbox_cfg = self.config.get("sandbox", {})
        self.sandbox_config = {
            "enabled": sandbox_cfg.get("enabled", True),
            "clean_env": sandbox_cfg.get("clean_env", True),
            "block_shell_escape": sandbox_cfg.get("block_shell_escape", True),
            "shell_network_enabled": sandbox_cfg.get("shell_network_enabled", True),
            "web_fetch_enabled": sandbox_cfg.get("web_fetch_enabled", True),
            "web_fetch_allowed_hosts": sandbox_cfg.get("web_fetch_allowed_hosts", []),
            # Computer-use: capture_screen sends a screenshot to the provider.
            # On by default (it is user-requested), but a privacy-conscious setup
            # can disable it as a hard kill-switch.
            "screen_capture_enabled": sandbox_cfg.get("screen_capture_enabled", True),
            "max_output_chars": sandbox_cfg.get("max_output_chars", 50000),
            "audit_log": resolve_state_path(sandbox_cfg.get("audit_log"), self.config) if sandbox_cfg.get("audit_log") else None,
        }

        # Roots
        self.allowed_roots: list[str] = default_project_roots(self.config)

    def _init_system_message(self) -> None:
        system_path = self.config.get("paths", {}).get("system_prompt", "")
        self.system_message, self.system_prompt_source = self._load_system_message(system_path)

    def _init_session(self) -> None:
        self._thread_state = threading.local()
        self.session = Session(self.system_message)
        self._last_interrupted_turn: dict[str, object] = {}
        self._pending_interrupted_work: dict[str, object] = {}
        self._sessions = None
        try:
            from ..session.sessions import SessionManager
            shared_session = shared_session_enabled(self.config)
            default_slot = "main" if shared_session else instance_session_slot(self.instance_id)
            self._sessions = SessionManager(resolve_state_path("memory/sessions", self.config), default_name=default_slot)
            # Multi-instance default: each terminal starts on its own fresh slot.
            # Compatibility escape hatch: runtime.shared_session: true restores the
            # old global-main/latest auto-resume behavior.
            if self._sessions and shared_session:
                latest_name = self._sessions.latest()
                if latest_name:
                    data = self._sessions.load(latest_name)
                    if data and data.get("messages"):
                        self.session.session_id = data.get("session_id", self.session.session_id)
                        self.session.turn_count = data.get("turn_count", 0)
                        self.session.messages = data.get("messages", [])
                        self.session.total_tokens = data.get("total_tokens", 0)
                        self.session.output_tokens = data.get("output_tokens", 0)
                        self.session.input_tokens = int(data.get("input_tokens", 0) or 0)
                        self.session.cache_hit_tokens = int(data.get("cache_hit_tokens", 0) or 0)
                        self.session.cache_miss_tokens = int(data.get("cache_miss_tokens", 0) or 0)
                        self.session.token_log = list(data.get("token_log", []) or [])
                        self.session.compacted_messages_count = int(data.get("compacted_messages_count", 0) or 0)
                        self.session.last_compacted_at = float(data.get("last_compacted_at", 0.0) or 0.0)
                        self.session.sanitize_for_provider()
                        if isinstance(data.get("_unfinished_tail_meta"), dict):
                            self._last_interrupted_turn = data["_unfinished_tail_meta"]
                        if isinstance(data.get("meta"), dict) and isinstance(data["meta"].get("compression"), dict):
                            self._pending_context_saving_meta = data["meta"]["compression"]
                        saved_pending = (data.get("meta") or {}).get("pending_interrupted_work") if isinstance(data.get("meta"), dict) else None
                        if isinstance(saved_pending, dict) and str(saved_pending.get("user") or "").strip():
                            # Only restore if the session didn't end with a completion
                            if not _session_ended_clean(data.get("messages", [])):
                                self._pending_interrupted_work = saved_pending
                        self._sessions._current_name = latest_name
        except Exception:
            traceback.print_exc()

    def _init_safety_and_profile(self) -> None:
        self.critic = AnswerCritic(
            resolve_state_path(self.config.get("paths", {}).get("critique_file", "critique/ANSWER.md"), self.config)
        )
        # Profile
        profile_path = resolve_state_path(self.config.get("paths", {}).get("memory_file", "memory/mo.db"), self.config)
        self.profile = Profile.load(profile_path)
        from ..learning.embeddings import build_embedder
        self.memory = EpisodicMemory(
            resolve_state_path("memory/learning.sqlite", self.config),
            embedder=build_embedder(self.config),
        )
        # Auto-detect current project from working directory or default roots
        cwd = self.project_cwd or os.getcwd()
        if cwd and self.allowed_roots:
            for root in self.allowed_roots:
                if cwd.lower().startswith(str(Path(root).resolve()).lower()):
                    self.profile.touch_project(root, Path(root).name)
                    break

    def _init_state(self) -> None:
        self.last_fallback_notice = ""
        self.last_handoff_notice = ""
        self.last_quarantine_notice = ""
        self._active_lane: str | None = None
        self._last_rendered_board: str | None = None
        self._live_steer_lock = threading.Lock()
        self._live_steer_items: list[dict[str, object]] = []
        if not isinstance(getattr(self, "_pending_interrupted_work", {}), dict):
            self._pending_interrupted_work = {}
        # Goal state
        self._goal_plan = None
        self._goal_active = False
        self._goal_runner = None
        self.workers = WorkerRegistry()
        agent_cfg = self.config.get("agent", {})
        self.worker_runtime = BackgroundWorkerRuntime(self, max_workers=int(agent_cfg.get("background_workers_max", 3) or 3))

        # Context-saving stats (tracked per-session for /status visibility).
        # compression_* is structural tool-output compression; truncation_* is
        # fallback capping of oversized tool results that would otherwise enter
        # provider context. Reports use both so savings are not undercounted.
        self.compression_total_saved = 0
        self.compression_total_ops = 0
        self.compression_last_pct = 0
        self.truncation_total_saved = 0
        self.truncation_total_ops = 0
        self.truncation_last_pct = 0
        self.context_momentum_compression_saved = 0
        self.context_momentum_compression_ops = 0
        self.context_momentum_truncation_saved = 0
        self.context_momentum_truncation_ops = 0
        self.session_compaction_total_saved = 0
        self.session_compaction_total_ops = 0
        pending_compression_meta = getattr(self, "_pending_context_saving_meta", None)
        if isinstance(pending_compression_meta, dict):
            self._restore_context_saving_meta({"compression": pending_compression_meta})

    def _init_tools(self) -> None:
        from tools import TOOL_DEFINITIONS
        self.tool_definitions = self._ordered_tool_definitions(TOOL_DEFINITIONS)
        # set_plan (MO-owned taskboard) is only exposed when the flag is on, so
        # flag-off runs see no behavior change and never make an inert call.
        if not self._model_owned_taskboard_enabled():
            self.tool_definitions = [
                d for d in self.tool_definitions
                if (d.get("function", {}).get("name") if "function" in d else d.get("name")) != "set_plan"
            ]

        # MCP (Model Context Protocol) — operator-configured and sandbox-gated.
        # Keep startup light: configured servers are started lazily before the
        # first provider tool set is built, so dynamic MCP schemas are still
        # visible to the model without spawning subprocesses during Agent init.
        self.mcp_manager = None
        self._mcp_manager_initialized = False

        # LSP — operator-configured language servers for live diagnostics; inert
        # until `lsp.servers` is listed. The lsp_diagnostics final-gate consumes it
        # to block "fixed/clean" claims on files the server still reports errors on.
        self.lsp_manager = None
        try:
            from core.lsp import LspManager
            lsp_mgr = LspManager.from_config(getattr(self, "config", None) or {}, root_path=project_cwd())
            self.lsp_manager = lsp_mgr
            if lsp_mgr.enabled:
                import atexit
                atexit.register(lsp_mgr.stop_all)
        except Exception:
            traceback.print_exc()

        try:
            from ..tooling.tool_registry import DeferredToolRegistry
            self._tool_registry = DeferredToolRegistry(self.tool_definitions)
        except Exception:
            self._tool_registry = None
            traceback.print_exc()

    def _load_system_message(self, path: str | None) -> tuple[str, str]:
        return load_system_prompt(path)

    @property
    def session(self) -> Session:
        thread_session = getattr(getattr(self, "_thread_state", None), "session", None)
        return thread_session or self._session

    @session.setter
    def session(self, value: Session) -> None:
        self._session = value

    @contextmanager
    def isolated_session(self, session: Session):
        """Temporarily route self.session to a thread-local session.

        Used by background goal workers so normal chat can continue without
        goal prompts/tool chains contaminating the main conversation session.
        """
        state = getattr(self, "_thread_state", None)
        if state is None:
            self._thread_state = threading.local()
            state = self._thread_state
        previous = getattr(state, "session", None)
        state.session = session
        try:
            yield
        finally:
            if previous is None:
                try:
                    delattr(state, "session")
                except AttributeError:
                    pass
            else:
                state.session = previous

    @property
    def active_provider(self) -> BaseProvider:
        return self.providers[self.provider_index]

    def _apply_profile_provider_preference(self) -> bool:
        """Deprecated compatibility hook.

        Provider lanes are config/code owned. Profile provider fields are kept as
        operator metadata only and must not reorder or select runtime providers.
        """
        return False

    def _ordered_tool_definitions(self, definitions: list[dict]) -> list[dict]:
        """Order full tool list by profile preference without filtering tools."""
        preferred = [str(name).strip() for name in getattr(getattr(self, "profile", None), "preferred_tools", []) or [] if str(name).strip()]
        if not preferred:
            return list(definitions)
        rank = {name: index for index, name in enumerate(preferred)}

        def key(item: dict) -> tuple[int, int]:
            name = str((item.get("function") or {}).get("name") or "")
            return (0, rank[name]) if name in rank else (1, len(rank))

        return sorted(list(definitions), key=key)

    def _reset_deferred_tools_for_turn(self) -> None:
        registry = getattr(self, "_tool_registry", None)
        if registry is not None:
            registry.reset_turn()

    def _ensure_mcp_manager(self) -> None:
        if getattr(self, "_mcp_manager_initialized", False):
            return
        self._mcp_manager_initialized = True
        try:
            from core.mcp import McpManager
            mgr = McpManager.from_config(getattr(self, "config", None) or {})
            mcp_defs = mgr.tool_definitions()
            if mcp_defs:
                self.mcp_manager = mgr
                self.tool_definitions = list(getattr(self, "tool_definitions", []) or []) + mcp_defs
                registry = getattr(self, "_tool_registry", None)
                if registry is not None:
                    registry.set_definitions(self.tool_definitions)
                import atexit
                atexit.register(mgr.shutdown)
            else:
                mgr.shutdown()  # disabled / no tools — release any subprocesses
        except Exception:
            traceback.print_exc()

    def _provider_tool_definitions(self) -> list[dict]:
        self._ensure_mcp_manager()
        definitions = list(getattr(self, "tool_definitions", []) or [])
        registry = getattr(self, "_tool_registry", None)
        if not getattr(self, "deferred_tool_registry_enabled", False) or registry is None:
            return self._scope_tools_for_active_role(definitions)
        # Compatibility: tests/review loops may temporarily replace
        # ``agent.tool_definitions`` with an explicit restricted set.  Treat that
        # override as authoritative instead of hiding non-core allowed tools.
        if not registry.matches_catalog(definitions):
            return self._scope_tools_for_active_role(definitions)
        return self._scope_tools_for_active_role(registry.active_definitions())

    def _scope_tools_for_active_role(self, definitions: list[dict]) -> list[dict]:
        """Hard-scope MCP tools when a role governs this turn: a role worker sees
        only its declared MCP tools (plus all core tools). No active role => no-op."""
        role = self._active_role()
        if not role or not getattr(role, "role", ""):
            return definitions
        try:
            from ..skills import scope_definitions_for_role
            return scope_definitions_for_role(definitions, role)
        except Exception:
            return definitions

    def _deferred_tool_registry_snapshot(self) -> dict:
        registry = getattr(self, "_tool_registry", None)
        if registry is None or not getattr(self, "deferred_tool_registry_enabled", False):
            return {}
        return registry.snapshot()

    def _execute_tool_search(self, arguments: dict) -> str:
        registry = getattr(self, "_tool_registry", None)
        if registry is None or not getattr(self, "deferred_tool_registry_enabled", False):
            try:
                from tools import execute_tool_search
                return execute_tool_search(arguments or {})
            except Exception as exc:
                return f"Error running tool_search: {exc}"
        return registry.search(arguments or {})

    def providers_for_surface(self, surface: str) -> list[BaseProvider]:
        """Return ordered provider candidates for a runtime surface."""
        return list(self._resolve_model_slot(surface).providers)

    def _resolve_model_slot(self, surface: str):
        resolution = resolve_model_slot(
            surface,
            list(getattr(self, "providers", []) or []),
            active_provider=self.active_provider if getattr(self, "providers", None) else None,
            config=getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {},
            review_chain_builder=prt_review_provider_chain,
        )
        self._last_model_slot_resolution = resolution
        return resolution

    def _review_provider_chain(self) -> list[BaseProvider]:
        """Review provider order: DeepSeek Pro, then Codex."""
        return list(self._resolve_model_slot("review").providers)

    def _ghost_provider_chain(self) -> list[BaseProvider]:
        """Ghost provider order: Flash, DeepSeek Pro, then Codex."""
        return list(self._resolve_model_slot("ghost").providers)

    @staticmethod
    def _provider_matches_config_selector(provider: BaseProvider | None, selector: str) -> bool:
        return provider_matches_selector(provider, selector)

    def complete_ghost_no_tools(
        self,
        *,
        surface: str,
        request: str,
        messages: list[dict],
        max_tokens: int,
        monitor: BackendMonitor | None = None,
    ) -> tuple[object, BaseProvider]:
        """Complete a Ghost provider call without exposing provider-side tools.

        Ghost side-panel grounding can come from a separate audited read-only
        scout, but provider calls here still receive ``tools=[]``.
        """
        errors: list[str] = []
        for provider in self.providers_for_surface(surface):
            provider_name = str(getattr(provider, "name", self.provider_name) or self.provider_name)

            # Capacity-aware skip: jump over providers known to be rate-limited
            if not get_capacity().can_accept(provider_name):
                errors.append(f"{provider_name}: skipped (capacity exhausted)")
                continue

            model_name = str(getattr(provider, "model", self.model) or self.model)
            append_provider_audit(
                "provider_request",
                surface=surface,
                provider=provider_name,
                model=model_name,
                request=request,
                session_id=getattr(self.session, "session_id", ""),
                worker_id=self._provider_worker_id(),
            )
            if monitor:
                monitor.emit("provider_request", {
                    "request": request,
                    "surface": surface,
                    "provider": provider_name,
                    "model": model_name,
                    "messages": len(messages),
                    "tools": 0,
                    "preview": preview_provider_messages(messages),
                })
            try:
                response = provider.complete(
                    messages=messages,
                    tools=[],
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                raw_error = str(exc)
                if is_rate_limit_error(raw_error) or fallback_reason(raw_error):
                    try:
                        get_capacity().record_error(provider_name, raw_error)
                    except Exception:
                        pass
                err_msg = clean_provider_error(raw_error)
                reason = fallback_reason(raw_error) or f"{surface}_error"
                errors.append(f"{provider_name}/{model_name}: {err_msg[:160]}")
                append_provider_audit(
                    "provider_error",
                    surface=surface,
                    provider=provider_name,
                    model=model_name,
                    request=request,
                    session_id=getattr(self.session, "session_id", ""),
                    worker_id=self._provider_worker_id(),
                    reason=reason,
                    ok=False,
                )
                if monitor:
                    monitor.emit("provider_error", {"request": request, "surface": surface, "provider": provider_name, "reason": reason, "error": err_msg[:300]})
                continue

            usage = getattr(response, "usage", None)
            usage_in, usage_out, usage_total = _usage_tokens(usage)
            usage_cache_hit, usage_cache_miss = _usage_cache_tokens(usage)
            if usage:
                self.session.record_usage(
                    provider=provider_name,
                    model=model_name,
                    input_tokens=usage_in,
                    output_tokens=usage_out,
                    total_tokens=usage_total,
                    cache_hit_tokens=usage_cache_hit,
                    cache_miss_tokens=usage_cache_miss,
                )
            text = str(getattr(response, "content", "") or "").strip()
            finish_reason = str(getattr(response, "finish_reason", "") or "")
            append_provider_audit(
                "provider_response",
                surface=surface,
                provider=provider_name,
                model=model_name,
                request=request,
                session_id=getattr(self.session, "session_id", ""),
                worker_id=self._provider_worker_id(),
                input_tokens=usage_in,
                output_tokens=usage_out,
                total_tokens=usage_total,
                ok=True,
            )
            if monitor:
                monitor.emit("provider_response", {
                    "request": request,
                    "surface": surface,
                    "provider": provider_name,
                    "model": model_name,
                    "finish_reason": finish_reason or "stop",
                    "tool_calls": 0,
                    "content_chars": len(text),
                    "preview": preview_provider_response(text, []),
                })
            if text:
                return response, provider

            reason = "empty_length" if finish_reason.lower() == "length" else "empty_response"
            errors.append(f"{provider_name}/{model_name}: {reason}")
            append_provider_audit(
                "provider_error",
                surface=surface,
                provider=provider_name,
                model=model_name,
                request=request,
                session_id=getattr(self.session, "session_id", ""),
                worker_id=self._provider_worker_id(),
                reason=reason,
                ok=False,
            )
            if monitor:
                monitor.emit("provider_error", {"request": request, "surface": surface, "provider": provider_name, "reason": reason, "error": "Ghost provider returned no visible text."})
            try:
                get_capacity().record_error(provider_name, reason)
            except Exception:
                pass

        detail = " | ".join(errors[-3:]) if errors else "no Ghost provider candidates"
        raise RuntimeError(f"Ghost providers unavailable: {detail}")

    @property
    def active_lane(self) -> str | None:
        return self._active_lane

    def _effective_lane(self) -> str | None:
        """Lane for this turn: a thread-local override (e.g. the companion's Guide
        mode) wins, else the agent's active lane. Thread-local so one surface's
        lane never leaks into a concurrent turn on another surface."""
        state = getattr(self, "_thread_state", None)
        override = getattr(state, "lane_override", None) if state is not None else None
        return override if override is not None else self._active_lane

    @contextmanager
    def lane_scope(self, lane: str | None):
        """Scope a per-turn lane on the calling thread (mirrors provider_scope).

        The companion uses this for Guide mode ('companion-guide') so the sandbox
        blocks actuation for that turn only, without racing the TUI's lane state.
        """
        state = getattr(self, "_thread_state", None)
        if state is None:
            self._thread_state = threading.local()
            state = self._thread_state
        previous = getattr(state, "lane_override", None)
        state.lane_override = lane
        try:
            yield
        finally:
            if previous is None:
                try:
                    delattr(state, "lane_override")
                except AttributeError:
                    pass
            else:
                state.lane_override = previous

    def _context_budget_tokens_for(self, provider: str, model: str) -> int:
        return resolve_context_budget_tokens(
            getattr(self, "context_budget_config", "auto"),
            provider=provider,
            model=model,
            reserve_tokens=int(getattr(self, "context_reserve_tokens", 16384) or 16384),
        )

    def _refresh_context_budget(self) -> None:
        self.context_budget_tokens = self._context_budget_tokens_for(self.provider_name, self.model)
        self.context_budget_source = context_budget_source(
            getattr(self, "context_budget_config", "auto"),
            provider=self.provider_name,
            model=self.model,
        )

    def _provider_context_max_chars(self) -> int | None:
        # Convert model-aware token budget into the existing Session character
        # guard. Keep the guard disabled only when no budget could be resolved.
        tokens = int(getattr(self, "context_budget_tokens", 0) or 0)
        return tokens * 4 if tokens > 0 else None

    def _adaptive_reasoning_level(self, user_input: str = "") -> str:
        """Pick the reasoning level for this turn (adaptive "auto" thinking depth).

        Conservative: only DROP to low on clearly-trivial turns (greetings, identity,
        term lookups) to save tokens/latency; everything else keeps the configured
        ceiling so real work never under-reasons.
        """
        base = str(getattr(self, "reasoning", "") or "").strip().lower()
        if base not in {"high", "medium", "low"}:
            base = "high"
        text = str(user_input or "").strip()
        if text:
            from .agent_utils import (
                _looks_like_trivial_greeting,
                _looks_like_identity_question,
                _looks_like_term_lookup,
            )
            if (_looks_like_trivial_greeting(text) or _looks_like_identity_question(text)
                    or _looks_like_term_lookup(text)):
                return "low"
        return base

    def _reasoning_context(self, user_input: str = "") -> str:
        level = self._adaptive_reasoning_level(user_input)
        if level not in {"high", "medium", "low"}:
            return ""
        return f"### Runtime reasoning preference\nReasoning level: {level}. Match effort to this setting while preserving evidence-first verification."

    def _provider_surface(self) -> str:
        state = getattr(self, "_thread_state", None)
        surface = str(getattr(state, "provider_surface", "") or "")
        if surface:
            return surface
        return str(getattr(self, "_current_route_source", "") or "main")

    def _provider_worker_id(self) -> str:
        state = getattr(self, "_thread_state", None)
        return str(getattr(state, "provider_worker_id", "") or "")

    def _active_role(self):
        """The role skill governing the current thread's turn, or None."""
        state = getattr(self, "_thread_state", None)
        return getattr(state, "active_role", None) if state is not None else None

    @contextmanager
    def provider_scope(self, surface: str, worker_id: str = "", role=None):
        state = getattr(self, "_thread_state", None)
        if state is None:
            self._thread_state = threading.local()
            state = self._thread_state
        previous_surface = getattr(state, "provider_surface", None)
        previous_worker_id = getattr(state, "provider_worker_id", None)
        previous_role = getattr(state, "active_role", None)
        state.active_role = role
        previous_provider_index = getattr(self, "provider_index", 0)
        previous_provider_name = getattr(self, "provider_name", "")
        previous_model = getattr(self, "model", "")
        previous_api_mode = getattr(self, "api_mode", "")
        previous_context_budget = getattr(self, "context_budget_tokens", 0)
        previous_context_source = getattr(self, "context_budget_source", "")
        state.provider_surface = surface
        state.provider_worker_id = worker_id
        try:
            yield
        finally:
            self.provider_index = previous_provider_index
            self.provider_name = previous_provider_name
            self.model = previous_model
            self.api_mode = previous_api_mode
            self.context_budget_tokens = previous_context_budget
            self.context_budget_source = previous_context_source
            if previous_surface is None:
                try:
                    delattr(state, "provider_surface")
                except AttributeError:
                    pass
            else:
                state.provider_surface = previous_surface
            if previous_worker_id is None:
                try:
                    delattr(state, "provider_worker_id")
                except AttributeError:
                    pass
            else:
                state.provider_worker_id = previous_worker_id
            state.active_role = previous_role

    def _is_foreground_session(self) -> bool:
        return getattr(self, "session", None) is getattr(self, "_session", None) and self._provider_surface() == "main"

    def _pre_turn_context_handoff(self, latest_user: str) -> bool:
        if not getattr(self, "context_handoff_enabled", True):
            return False
        if not self._is_foreground_session():
            return False
        maybe_compact_session(self, stage="pre_turn", latest_user=latest_user, extra_context=latest_user)
        triggered, metrics = should_auto_handoff(self, extra_context=latest_user)
        if not triggered:
            return False
        reason = (
            f"pre-turn context pressure {float(metrics.get('pressure') or 0.0):.0%} "
            f"[{metrics.get('trigger_dimension') or 'unknown'}]; "
            f"messages {metrics.get('message_count')}/{metrics.get('max_history')}; "
            f"chars {metrics.get('chars')}/{metrics.get('budget_chars')}"
        )
        self._perform_context_handoff(focus=latest_user, reason=reason, latest_user=latest_user)
        return True

    def _maybe_context_handoff(self, latest_user: str, *, extra_context: str = "") -> bool:
        if not getattr(self, "context_handoff_enabled", True):
            return False
        if not self._is_foreground_session():
            return False
        skip_user = str(getattr(self, "_context_handoff_skip_latest_user", "") or "").strip()
        skip_session = str(getattr(self, "_context_handoff_skip_session_id", "") or "")
        skip_count = int(getattr(self, "_context_handoff_skip_message_count", 0) or 0)
        if (
            skip_user
            and skip_user == str(latest_user or "").strip()
            and skip_session == str(getattr(self.session, "session_id", "") or "")
            and len(getattr(self.session, "messages", []) or []) <= skip_count
        ):
            return False
        maybe_compact_session(self, stage="post_context", latest_user=latest_user, extra_context=extra_context)
        triggered, metrics = should_auto_handoff(self, extra_context=extra_context)
        if not triggered:
            return False
        reason = (
            f"context pressure {float(metrics.get('pressure') or 0.0):.0%} "
            f"[{metrics.get('trigger_dimension') or 'unknown'}]; "
            f"messages {metrics.get('message_count')}/{metrics.get('max_history')}; "
            f"chars {metrics.get('chars')}/{metrics.get('budget_chars')}"
        )
        self._perform_context_handoff(focus=latest_user, reason=reason, latest_user=latest_user)
        return True

    def _recover_from_provider_context_overflow(
        self,
        *,
        latest_user: str,
        extra_context: str = "",
        monitor: BackendMonitor | None = None,
        request: int = 0,
        error_msg: str = "",
    ) -> bool:
        """Compact/handoff once after a provider rejects the payload as too large."""
        if not is_context_overflow_error(error_msg):
            return False
        if not self._is_foreground_session():
            if monitor:
                monitor.emit("session_event", {
                    "kind": "provider_context_overflow_recovery",
                    "request": int(request or 0),
                    "recovered": False,
                    "reason": "not_foreground",
                })
            return False

        before = context_pressure(self, extra_context=extra_context)
        compact_result = maybe_compact_session(
            self,
            stage="overflow_recovery",
            latest_user=latest_user,
            extra_context=extra_context,
            monitor=monitor,
            force=True,
        )
        triggered, metrics = should_auto_handoff(self, extra_context=extra_context)
        handoff_started = False
        if getattr(self, "context_handoff_enabled", True) and (triggered or not compact_result.get("changed")):
            reason = (
                f"provider context overflow recovery after request {int(request or 0)}; "
                f"pressure {float(metrics.get('pressure') or 0.0):.0%} "
                f"[{metrics.get('trigger_dimension') or 'provider-rejected'}]"
            )
            self._perform_context_handoff(
                focus=latest_user,
                reason=reason,
                latest_user=latest_user,
            )
            handoff_started = True

        after = context_pressure(self, extra_context=extra_context)
        recovered = bool(compact_result.get("changed") or handoff_started)
        if monitor:
            monitor.emit("session_event", {
                "kind": "provider_context_overflow_recovery",
                "request": int(request or 0),
                "recovered": recovered,
                "compacted": bool(compact_result.get("changed")),
                "handoff": handoff_started,
                "before_pressure": float(before.get("pressure") or 0.0),
                "after_pressure": float(after.get("pressure") or 0.0),
                "saved_chars": int(compact_result.get("saved_chars") or 0),
            })
        return recovered

    def _perform_context_handoff(self, *, focus: str = "", reason: str = "manual", latest_user: str = "", expose_notice: bool = False) -> str:
        old_session_id = str(getattr(self.session, "session_id", "") or "")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        current_name = str(getattr(getattr(self, "_sessions", None), "current_name", "main") or "main")
        archive_name = f"{current_name}-pre-handoff-{stamp}"
        if getattr(self, "_sessions", None) and hasattr(self._sessions, "save_snapshot"):
            try:
                self._sessions.save_snapshot(archive_name, self.session, extra_meta=self._session_save_extra_meta())
                # Cap historical pre-handoff snapshots so they don't accumulate
                # unbounded and make "the last session" ambiguous (drift hygiene).
                if hasattr(self._sessions, "prune_handoff_snapshots"):
                    self._sessions.prune_handoff_snapshots(current_name)
            except Exception:
                archive_name = "snapshot-unavailable"
        visible_messages = recent_visible_report_messages(
            list(getattr(self.session, "messages", []) or []),
            keep_recent=6,
        )
        document = build_handoff_document(self, focus=focus, reason=reason, latest_user=latest_user)
        compact_document = build_compact_summary(self, focus=focus, reason=reason, latest_user=latest_user)
        path = write_handoff_document(document)
        seed_session_from_handoff(self.session, compact_document, latest_user=latest_user, visible_messages=visible_messages, compact=True)
        # Preserve taskboard truth across the handoff. The session id just changed to a
        # mo-handoff-* id, but the active board's snapshots are keyed under the OLD id;
        # when last_task_board is momentarily None (e.g. the next turn's start, before the
        # lazy rebuild) the heartbeat falls back to read_recent_snapshots(session_id=new)
        # and would find nothing → "no board" (open=0) telemetry WHILE the board is still
        # active. Re-tag the live board to the new id and snapshot it under that id so the
        # board is never dropped and never reads as absent across the handoff.
        try:
            board = getattr(getattr(self, "gateway", None), "last_task_board", None)
            new_sid = str(getattr(self.session, "session_id", "") or "")
            if board is not None and new_sid and getattr(board, "open_count", None) and board.open_count() > 0:
                board.session_id = new_sid
                from ..tasking.task_board import record_snapshot
                record_snapshot(board, "handoff")
        except Exception:
            pass
        self._handoff_count = int(getattr(self, "_handoff_count", 0) or 0) + 1
        # Preserve context-saving momentum for adaptive handoff decisions while
        # starting fresh per-session counters for the new foreground session.
        self._carry_context_saving_stats_for_handoff()
        self.compression_total_ops = 0
        self.compression_total_saved = 0
        self.compression_last_pct = 0
        self.truncation_total_ops = 0
        self.truncation_total_saved = 0
        self.truncation_last_pct = 0
        self.last_handoff_path = str(path)
        self._context_handoff_skip_latest_user = str(latest_user or "").strip()
        self._context_handoff_skip_session_id = str(getattr(self.session, "session_id", "") or "")
        self._context_handoff_skip_message_count = len(getattr(self.session, "messages", []) or [])
        notice = (
            f"Context handoff opened a clean session. Previous session saved as {archive_name}; "
            f"handoff: {path}. Treat recalled context as orientation only — not proof of current state. "
            f"If any inconsistency appears, report it with the handoff evidence."
        )
        self.last_handoff_notice = notice if expose_notice else ""
        append_provider_audit(
            "context_handoff",
            surface=self._provider_surface(),
            provider=getattr(self, "provider_name", ""),
            model=getattr(self, "model", ""),
            session_id=old_session_id,
            reason=reason,
            ok=True,
        )
        monitor = get_monitor()
        if monitor:
            monitor.emit("context_handoff", {
                "reason": reason,
                "old_session_id": old_session_id,
                "new_session_id": str(getattr(self.session, "session_id", "") or ""),
                "archive": archive_name,
                "handoff_path": str(path),
                "visible_messages_kept": len(visible_messages),
                "text": notice,
            })
        return notice

    def consume_handoff_notice(self) -> str:
        notice = str(getattr(self, "last_handoff_notice", "") or "")
        self.last_handoff_notice = ""
        return notice

    def consume_quarantine_notice(self) -> str:
        notice = str(getattr(self, "last_quarantine_notice", "") or "")
        self.last_quarantine_notice = ""
        return notice

    def _main_provider_selectors(self) -> list[str]:
        return list(main_model_selectors(getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}))

    def _next_provider_index_for_surface(self) -> int | None:
        surface = self._provider_surface()
        providers = list(getattr(self, "providers", []) or [])
        current_index = int(getattr(self, "provider_index", 0) or 0)
        cap = get_capacity()
        if surface == "main":
            selectors = self._main_provider_selectors()
            if selectors:
                current = providers[current_index] if 0 <= current_index < len(providers) else None
                current_selector = next(
                    (idx for idx, selector in enumerate(selectors) if self._provider_matches_config_selector(current, selector)),
                    -1,
                )
                for selector in selectors[current_selector + 1:]:
                    match_index = next(
                        (idx for idx, provider in enumerate(providers)
                         if self._provider_matches_config_selector(provider, selector)
                         and cap.can_accept(provider.name)),
                        None,
                    )
                    if match_index is not None and match_index != current_index:
                        return match_index
                return None
        # Simple iteration: skip exhausted, wrap around once
        for index in range(current_index + 1, len(providers)):
            if cap.can_accept(providers[index].name):
                return index
        for index in range(0, current_index):
            if cap.can_accept(providers[index].name):
                return index
        return None

    def switch_to_vision_provider(self, reason: str = "capture_screen") -> bool:
        """Route to a vision-capable provider so a screenshot is actually seen.

        capture_screen only helps a provider that can SEE images. When the active
        provider is text-only, switch to a vision-capable one (e.g. openai-codex)
        for the continuation. Returns True if the active provider can now see —
        either it already could, or a switch happened. False means no vision
        provider is available (caller leaves an honest 'image omitted' trail)."""
        if getattr(self.active_provider, "supports_vision", False):
            return True
        cap = get_capacity()
        target = first_vision_provider_index(
            self.providers, can_accept=lambda name: cap.can_accept(name)
        )
        if target is None or target == self.provider_index:
            return getattr(self.active_provider, "supports_vision", False)
        old_provider, old_model = self.provider_name, self.model
        # R2: record the pre-vision provider selection ONCE per turn so the turn
        # boundary (gateway finally / provider_scope) can restore it. A screenshot
        # flips the shared agent onto a vision-capable provider only for THIS turn's
        # continuation; without a restore it silently pollutes the next turn — and
        # any other surface reading the shared provider — onto the vision model.
        if getattr(self, "_pre_vision_provider", None) is None:
            self._pre_vision_provider = (
                self.provider_index, self.provider_name, self.model,
                self.api_mode, self.context_budget_tokens, self.context_budget_source,
            )
        self.provider_index = target
        p = self.active_provider
        self.model = p.model
        self.provider_name = p.name
        self.api_mode = p.api_mode
        self.last_fallback_notice = f"Switched to {p.name}/{p.model} to see the screen"
        self._refresh_context_budget()
        append_provider_audit(
            "vision_switch",
            surface=self._provider_surface(),
            session_id=getattr(self.session, "session_id", ""),
            worker_id=self._provider_worker_id(),
            reason=reason,
            from_provider=old_provider,
            from_model=old_model,
            to_provider=self.provider_name,
            to_model=self.model,
            provider=self.provider_name,
            model=self.model,
        )
        return True

    def restore_vision_provider(self) -> bool:
        """Undo a per-turn ``capture_screen`` vision switch (R2).

        Restores the exact provider selection captured by
        ``switch_to_vision_provider`` so a screenshot taken during one turn cannot
        leave the shared agent stuck on the vision provider for the next turn (or a
        concurrent surface). No-op (returns ``False``) when no vision switch
        happened this turn, so it never reverts a deliberate, sticky error-driven
        fallback (``_next_provider``)."""
        snapshot = getattr(self, "_pre_vision_provider", None)
        if snapshot is None:
            return False
        from_provider, from_model = self.provider_name, self.model
        (
            self.provider_index,
            self.provider_name,
            self.model,
            self.api_mode,
            self.context_budget_tokens,
            self.context_budget_source,
        ) = snapshot
        self._pre_vision_provider = None
        if from_provider != self.provider_name or from_model != self.model:
            append_provider_audit(
                "vision_restore",
                surface=self._provider_surface(),
                session_id=getattr(self.session, "session_id", ""),
                worker_id=self._provider_worker_id(),
                reason="turn_end",
                from_provider=from_provider,
                from_model=from_model,
                to_provider=self.provider_name,
                to_model=self.model,
                provider=self.provider_name,
                model=self.model,
            )
        return True

    def _next_provider(self, reason: str = "") -> bool:
        """Switch to the next allowed fallback provider for the active surface."""
        next_index = self._next_provider_index_for_surface()
        if next_index is None:
            return False
        old_provider = self.provider_name
        old_model = self.model
        self.provider_index = next_index
        p = self.active_provider
        self.model = p.model
        self.provider_name = p.name
        self.api_mode = p.api_mode
        self.last_fallback_notice = f"Switched to {p.name}/{p.model}" + (f": {reason}" if reason else "")
        self._refresh_context_budget()
        # Adapt compression to new model's context window:
        # smaller budget → lower min_bytes (compress more aggressively)
        if getattr(self, 'tool_compress_enabled', True):
            new_budget = getattr(self, 'context_budget_tokens', 0) or 0
            if new_budget and new_budget < 100_000:
                self.tool_compress_min_bytes = 200  # aggressive for small-context models
            elif new_budget and new_budget >= 500_000:
                self.tool_compress_min_bytes = 500  # relaxed for large-context models
        append_provider_audit(
            "provider_fallback",
            surface=self._provider_surface(),
            session_id=getattr(self.session, "session_id", ""),
            worker_id=self._provider_worker_id(),
            reason=reason,
            from_provider=old_provider,
            from_model=old_model,
            to_provider=self.provider_name,
            to_model=self.model,
            provider=self.provider_name,
            model=self.model,
        )
        return True

    def add_live_steer(self, text: str, *, source: str = "ghost", worker_id: str = "", urgent: bool = False) -> str:
        """Queue provider-only steering context for the current foreground turn.

        This does not mutate task truth and cannot interrupt an in-flight provider
        request. The next safe provider checkpoint consumes it as context.
        """
        objective = str(text or "").strip()
        if not objective:
            return ""
        item_id = worker_id or f"steer-{int(time.time() * 1000)}"
        item = {
            "id": item_id,
            "text": objective[:1200],
            "source": str(source or "ghost")[:40],
            "urgent": bool(urgent),
            "created_at": time.time(),
        }
        lock = getattr(self, "_live_steer_lock", None)
        if lock is None:
            self._live_steer_lock = threading.Lock()
            lock = self._live_steer_lock
        with lock:
            items = list(getattr(self, "_live_steer_items", []) or [])
            items.append(item)
            self._live_steer_items = items[-5:]
        return item_id

    def _consume_live_steer_context(self, monitor: BackendMonitor | None = None) -> str:
        lock = getattr(self, "_live_steer_lock", None)
        if lock is None:
            self._live_steer_lock = threading.Lock()
            lock = self._live_steer_lock
        with lock:
            items = list(getattr(self, "_live_steer_items", []) or [])
            self._live_steer_items = []
        if not items:
            return ""
        for item in items:
            worker_id = str(item.get("id") or "")
            if worker_id and hasattr(self, "workers"):
                try:
                    self.workers.update(worker_id, "completed", "live steer consumed by current MO turn")
                except Exception:
                    traceback.print_exc()
        lines = [
            "### Live Operator Steering Update — provider context only",
            "The operator added this while the current turn was running. Apply it at the next safe checkpoint if it matches the current work; do not claim it as completed unless tools/evidence prove it. Keep the final report concise and mention any conflict/blocker.",
        ]
        for item in items[-3:]:
            prefix = "urgent" if item.get("urgent") else "update"
            lines.append(f"- {prefix}: {str(item.get('text') or '').strip()}")
        context = "\n".join(lines)
        if monitor:
            monitor.emit("live_steer", {"count": len(items), "preview": context[:500]})
        return context

    def _record_turn_memory_only(self, user_input: str, final_text: str) -> None:
        if not self._is_foreground_session():
            return
        memory = getattr(self, "memory", None)
        if not memory:
            return
        try:
            memory.index_turn(
                turn_id=f"turn-{int(time.time() * 1000)}",
                user=user_input,
                assistant=final_text,
            )
        except Exception:
            traceback.print_exc()

    def _review_final_answer(self, content: str, *, monitor: BackendMonitor | None = None):
        """Run answer critic with failure containment and telemetry."""
        try:
            result = self.critic.review(content)
            if monitor:
                monitor.emit("critic_review", {
                    "ok": bool(getattr(result, "ok", False)),
                    "hard_failures": len(getattr(result, "hard_failures", []) or []),
                    "warnings": len(getattr(result, "warnings", []) or []),
                    "redacted": any("redacted" in str(item).lower() for item in list(getattr(result, "warnings", []) or [])),
                })
            return result
        except Exception as exc:
            from ..review.critic import CritiqueResult
            if monitor:
                monitor.emit("critic_review", {"ok": False, "error": type(exc).__name__, "contained": True})
            return CritiqueResult(text=str(content or ""), warnings=[f"critic failure contained: {type(exc).__name__}"])

    def _scan_user_input(self, user_input: str) -> dict[str, object] | None:
        """Run lightweight local input threat scan before provider dispatch."""
        try:
            from ..gates.threat_scan import scan_text
            result = scan_text(user_input, surface="user_input")
            if not result.findings:
                return None
            return {
                "blocked": bool(result.blocked),
                "reason": result.reason(),
                "findings": [item.as_dict() for item in result.findings],
            }
        except Exception:
            return None

    def _record_turn_memory_and_learning(self, user_input: str, final_text: str) -> list[str]:
        """Persist normal foreground memory plus explicit feedback learning."""
        notes: list[str] = []
        if not self._is_foreground_session():
            return notes
        memory = getattr(self, "memory", None)
        if memory:
            try:
                memory.index_turn(
                    turn_id=f"turn-{int(time.time() * 1000)}",
                    user=user_input,
                    assistant=final_text,
                )
                # Footer-only tick (NOT added to `notes`, so it never appends to
                # non-TUI replies). Confirms the turn was committed to memory.
                self.push_status_note("memory noted")
            except Exception:
                traceback.print_exc()
        try:
            insights = extract_feedback_learning(user_input, final_text)
            if insights and record_feedback_learning(getattr(self, "profile", None), user_input, final_text):
                notes.append("Noted: " + self._compact_learning_note(insights))
        except Exception:
            traceback.print_exc()
        try:
            terms = record_terms_learning(getattr(self, "profile", None), user_input)
            if terms:
                notes.append("Term learned: " + ", ".join(terms[:2]))
        except Exception:
            traceback.print_exc()
        try:
            promote_workflow_candidate(getattr(self, "profile", None), user_input, final_text)
        except Exception:
            traceback.print_exc()
        try:
            workflow_result = record_workflow_candidate_result(getattr(self, "profile", None), user_input, final_text)
            if workflow_result.get("recorded"):
                notes.append(str(workflow_result.get("notice") or WORKFLOW_CANDIDATE_NOTICE))
        except Exception:
            traceback.print_exc()
        try:
            if memory and str(os.environ.get("MO_LEARNING_SUGGESTIONS_ENABLED", "1")).strip() != "0":
                from ..learning.proactive_learning import (
                    mine_learning_suggestions,
                    next_learning_suggestion_notice,
                    write_learning_suggestions,
                )

                profile_path = getattr(getattr(self, "profile", None), "_path", None)
                suggestions_path = (
                    Path(profile_path).parent / "learning_suggestions.jsonl"
                    if profile_path else Path(resolve_state_path("memory/learning_suggestions.jsonl", getattr(self, "config", {}) or {}))
                )
                suggestions = mine_learning_suggestions(getattr(memory, "path", None))
                if suggestions:
                    write_learning_suggestions(suggestions, path=suggestions_path)
                notice = next_learning_suggestion_notice(path=suggestions_path)
                if notice:
                    notes.append(notice)
        except Exception:
            traceback.print_exc()
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root(), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                notes.append("💡 Remember to commit and push your verified changes.")
        except Exception:
            pass
        for _note in notes:
            self.push_status_note(_note)
        return notes

    @staticmethod
    def _compact_learning_note(insights: dict[str, object], *, limit: int = 42) -> str:
        for values in insights.values():
            if isinstance(values, list) and values:
                text = " ".join(str(values[0] or "").split())
                return text[:limit].rstrip() or "learning updated"
        return "learning updated"

    @staticmethod
    def _append_after_turn_notes(text: str, notes: list[str]) -> str:
        clean = [str(note or "").strip()[:140] for note in notes if str(note or "").strip()]
        if not clean:
            return str(text or "")
        return (str(text or "").rstrip() + "\n" + "\n".join(clean)).strip()

    def _maybe_append_after_turn_notes(self, text: str, notes: list[str]) -> str:
        """Append learning/memory notes to the reply UNLESS a transient status
        surface (the TUI footer) is showing them — avoids duplicating the
        confirmation in both the response body and the footer."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return str(text or "")
        if getattr(self, "_status_footer_active", False):
            return str(text or "")
        return self._append_after_turn_notes(text, notes)

    def push_status_note(self, text: str) -> None:
        """Buffer a transient learning/memory confirmation for the TUI footer
        (e.g. 'Term learned: X', 'Noted: …'). Surface-agnostic; the footer reads
        recent ones and fades them out."""
        note = str(text or "").strip()
        if not note:
            return
        buf = getattr(self, "_status_notes", None)
        if buf is None:
            from collections import deque
            buf = deque(maxlen=6)
            self._status_notes = buf
        buf.append((note[:80], time.time()))

    def recent_status_notes(self, window: float = 6.0) -> list[str]:
        """Transient confirmations recorded within the last `window` seconds."""
        buf = getattr(self, "_status_notes", None)
        if not buf:
            return []
        cutoff = time.time() - max(1.0, float(window))
        return [t for (t, ts) in buf if ts >= cutoff]

    def _emit_session_event(self, monitor: BackendMonitor | None, kind: str, **payload: object) -> None:
        mon = monitor or get_monitor()
        if not mon:
            return
        data = {
            "kind": kind,
            "session_id": str(getattr(self.session, "session_id", "") or ""),
            "turn_count": int(getattr(self.session, "turn_count", 0) or 0),
            "messages": len(getattr(self.session, "messages", []) or []),
        }
        data.update(payload)
        mon.emit("session_event", data)

    def _emit_sanitize_event(self, monitor: BackendMonitor | None, meta: dict[str, object] | None, *, stage: str) -> None:
        if not isinstance(meta, dict) or not meta.get("changed"):
            return
        self._emit_session_event(
            monitor,
            "sanitize_for_provider",
            stage=stage,
            dropped_messages=int(meta.get("dropped_messages") or 0),
        )

    def _quarantine_unfinished_tail_before_turn(self, user_input: str, monitor: BackendMonitor | None = None) -> dict[str, object]:
        """Keep stale unfinished tool work from hijacking a fresh user turn."""
        session = getattr(self, "session", None)
        quarantine = getattr(session, "quarantine_unfinished_tail", None)
        if not callable(quarantine):
            return {"changed": False, "dropped_messages": 0}
        # A plain unanswered user message (no dangling tool calls) is only stale
        # when the operator returns with a casual greeting. During active
        # continuation, keep it so a question that failed on a provider hiccup
        # gets answered instead of silently deleted.
        drop_unanswered = self._looks_like_return_greeting(user_input)
        try:
            meta = quarantine(drop_unanswered_user=drop_unanswered) or {"changed": False, "dropped_messages": 0}
        except TypeError:
            meta = quarantine() or {"changed": False, "dropped_messages": 0}
        if not meta.get("changed"):
            return meta
        self._last_interrupted_turn = meta
        # Drift root cause: dropping the unfinished tail used to leave the NEXT
        # (often vague: "try again") turn with no thread, so MO free-associated
        # from the profile/recall and wandered into an unrelated project. Park the
        # dropped objective into the existing interrupted-work anchor so the next
        # turn re-anchors to what was actually happening (or asks), via
        # _pending_interrupted_work_context — instead of guessing.
        dropped_objective = str(meta.get("user") or "").strip()
        if dropped_objective:
            self._pending_interrupted_work = {"user": dropped_objective}
        # Surface the drop to the user (not just the monitor): a resumed chat
        # should never silently feel like it lost context.
        dropped = int(meta.get("dropped_messages") or 0)
        reason = str(meta.get("reason") or "unfinished_tool_turn")
        label = "unanswered question" if reason == "unanswered_user_turn" else "unfinished tool work"
        self.last_quarantine_notice = (
            f"note: dropped {dropped} stale message(s) from the previous session "
            f"({label}) so this turn starts clean."
        )
        if monitor:
            monitor.emit("session_quarantine", {
                "reason": reason,
                "dropped_messages": dropped,
                "next_user_preview": str(user_input or "")[:160],
            })
        return meta

    def _pause_interrupted_work_for_return(
        self,
        user_input: str,
        quarantine_meta: dict[str, object] | None = None,
        *,
        monitor: BackendMonitor | None = None,
    ) -> bool:
        """Silently park stale work on greetings; provider still writes the reply."""
        is_return_greeting = self._looks_like_return_greeting(user_input)
        if not is_return_greeting:
            return False
        pending = getattr(self, "_pending_interrupted_work", {})
        already_pending = isinstance(pending, dict) and str(pending.get("user") or "").strip()
        if already_pending:
            if monitor:
                monitor.emit("turn_intercept", {
                    "kind": "interrupted_work_already_paused",
                    "reason": str(pending.get("reason") or "paused_work"),
                    "pending_user_preview": str(pending.get("user") or "")[:240],
                    "visible_reply": "provider",
                })
            return True
        meta = quarantine_meta if isinstance(quarantine_meta, dict) and quarantine_meta.get("changed") else getattr(self, "_last_interrupted_turn", {})
        if not isinstance(meta, dict) or not meta.get("changed"):
            meta = self._recent_stalled_work_meta()
        if not isinstance(meta, dict) or not meta.get("changed"):
            return False
        self._pending_interrupted_work = dict(meta)
        self._last_interrupted_turn = {}
        self._drop_interrupted_session_tail(meta)
        if monitor:
            monitor.emit("turn_intercept", {
                "kind": "interrupted_work_paused_silent",
                "reason": str(meta.get("reason") or "paused_work"),
                "dropped_messages": int(meta.get("dropped_messages") or 0),
                "pending_user_preview": str(meta.get("user") or "")[:240],
                "visible_reply": "provider",
            })
        return True

    def _pending_interrupted_work_context(self, user_input: str) -> str:
        """Provider-only orientation for parked work; never a visible template."""
        pending = getattr(self, "_pending_interrupted_work", {})
        if not isinstance(pending, dict):
            return ""
        prior = " ".join(str(pending.get("user") or "").split())
        if not prior:
            return ""
        prior = redact_sensitive_text(prior)[:600]
        explicit_resume = self._looks_like_interrupted_resume_request(user_input)
        if explicit_resume:
            # Clear the flag now — model is resuming, work is no longer parked
            self._pending_interrupted_work = {}
            instruction = (
                "The current operator message appears to explicitly resume the parked work. "
                "Use this as the target only if it still matches the current request; verify with tools before claiming progress. "
                "If changing an existing large file, inspect only needed ranges and use targeted edit_file replacements/small chunks; do not emit a full-file write_file rewrite."
            )
        else:
            # A clearly-new substantive request (more than a short greeting/ambiguous
            # return) supersedes parked work: clear it and inject nothing, so stale
            # parked context can't pollute the new ask or nudge a resume (observed:
            # after a hard-stop, the next unrelated message read as "lost"). Only
            # short greetings / ambiguous follow-ups keep the "want to resume?" hint.
            if len(str(user_input or "").split()) >= 4:
                self._pending_interrupted_work = {}
                return ""
            instruction = (
                "Do not continue it, call tools for it, or imply it is active unless the operator explicitly asks to continue/resume it. "
                "For a greeting or ambiguous follow-up like 'you tell me', answer naturally with this orientation: you may briefly mention that prior work is parked, "
                "summarize it in a few words, and ask whether to resume it or start something else. Do not quote the full preview, and do not inventory the workspace just to guess."
            )
        return (
            "### Paused Interrupted Work — provider context only\n"
            "Runtime has parked prior unfinished work internally so a casual return cannot auto-resume stale tools.\n"
            f"Paused work preview: {prior}\n"
            f"{instruction}"
        )

    def _drop_interrupted_session_tail(self, meta: dict[str, object]) -> None:
        """Remove stale loaded work from provider-visible history after parking it."""
        try:
            drop_from = int(meta.get("drop_from_index", -1))
        except (TypeError, ValueError):
            return
        if drop_from < 0:
            return
        session = getattr(self, "session", None)
        messages = list(getattr(session, "messages", []) or [])
        if drop_from >= len(messages):
            return
        removed = messages[drop_from:]
        session.messages = messages[:drop_from]
        dropped = len(removed)
        meta["dropped_messages"] = max(int(meta.get("dropped_messages") or 0), dropped)
        if hasattr(session, "trimmed_messages_count"):
            session.trimmed_messages_count = int(getattr(session, "trimmed_messages_count", 0) or 0) + dropped
        if hasattr(session, "last_trimmed_at"):
            session.last_trimmed_at = time.time()
        if hasattr(session, "turn_count"):
            removed_user_turns = sum(1 for msg in removed if isinstance(msg, dict) and msg.get("role") == "user")
            session.turn_count = max(0, int(getattr(session, "turn_count", 0) or 0) - removed_user_turns)

    def _recent_stalled_work_meta(self) -> dict[str, object]:
        """Infer interrupted work already present in the current loaded session."""
        messages = list(getattr(getattr(self, "session", None), "messages", []) or [])
        if not messages:
            return {"changed": False, "dropped_messages": 0}
        last_assistant_text = ""
        for msg in reversed(messages[-8:]):
            if msg.get("role") == "assistant" and not msg.get("tool_calls"):
                last_assistant_text = str(msg.get("content") or "").strip()
                break
        if not self._looks_like_stalled_assistant_tail(last_assistant_text):
            return {"changed": False, "dropped_messages": 0}
        if not any(msg.get("role") == "assistant" and msg.get("tool_calls") for msg in messages[-40:]):
            return {"changed": False, "dropped_messages": 0}
        start = max(0, len(messages) - 40)
        for idx in range(len(messages) - 1, start - 1, -1):
            msg = messages[idx]
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "").strip()
            if content and not self._looks_like_return_greeting(content) and not self._looks_like_interrupted_resume_request(content):
                return {
                    "changed": True,
                    "dropped_messages": 0,
                    "reason": "stalled_work_after_return",
                    "user": content[:500],
                    "drop_from_index": idx,
                }
        return {"changed": False, "dropped_messages": 0}

    @staticmethod
    def _looks_like_stalled_assistant_tail(text: str) -> bool:
        value = str(text or "").strip().lower()
        return (
            value.startswith("[provider empty]")
            or value.startswith("[max provider requests]")
            or value.startswith("[max tool rounds]")
            or value.startswith("[tool arguments truncated]")
            or "malformed/truncated tool calls" in value
            or "provider hit its output limit" in value
            or "stopped before changing files" in value
            or "found unfinished work from the previous turn" in value
        )

    @staticmethod
    def _looks_like_return_greeting(user_input: str) -> bool:
        text = " ".join(str(user_input or "").strip().lower().split())
        if not text:
            return False
        return bool(re.fullmatch(r"(?:hi|hello|hey|yo)(?:\s+mo)?[.!?]*|(?:i'?m|im|i am)\s+back[.!?]*|back[.!?]*", text))

    @staticmethod
    def _looks_like_interrupted_resume_request(user_input: str) -> bool:
        return looks_like_interrupted_resume_request(user_input)



    def propose_work(self, user_input: str, monitor: BackendMonitor | None = None) -> str:
        """Ask Ghost for a no-tools intent handoff before executing work."""
        user_input = str(user_input or "").strip()
        if not user_input:
            return ""
        ghost_system = "\n\n".join(c for c in (GHOST_PROPOSAL_SYSTEM, build_ghost_work_guidance(user_input)) if c)
        # Ghost only needs recent context, not full history — prevents flash model OOM
        recent = self._ghost_context_messages(user_input)
        messages = [{"role": "system", "content": ghost_system}] + recent + [
            {"role": "user", "content": f"Plan the work for this request:\n{user_input}\n\nOutput both intent guardrails AND structured task rows as specified in your system prompt."}
        ]
        if monitor:
            monitor.emit("backend_status", {"message": "ghost intent handoff & planning running"})
        try:
            response, _provider = self.complete_ghost_no_tools(
                surface="ghost_proposal",
                request="ghost-1",
                messages=messages,
                max_tokens=self._ghost_proposal_max_tokens(),
                monitor=monitor,
            )
        except Exception:
            if monitor:
                monitor.emit("backend_status", {"message": "ghost intent handoff unavailable; continuing without proposal"})
            return ""
        text = str(getattr(response, "content", "") or "").strip()

        self._pending_turn_proposal = text
        return text

    def _ghost_proposal_max_tokens(self) -> int:
        """Output budget for the structured Ghost planning handoff.

        The proposal prompt asks for guardrails plus parseable task JSON. DeepSeek
        reasoning-style models can spend the old 1500-token cap before emitting
        visible text, so keep this lane large enough to reach the answer.
        """
        try:
            configured = int(self.max_tokens or 0)
        except Exception:
            configured = 0
        if configured <= 0:
            configured = 2500
        return max(2500, min(configured, 4000))

    def _ghost_context_messages(self, user_input: str) -> list[dict]:
        """Return compact recent context for Ghost — just the last few exchanges."""
        all_msgs = list(getattr(self.session, "messages", []) or [])
        # Take only the last 6 messages, filtering out tool chains
        recent = []
        for msg in all_msgs[-8:]:
            role = msg.get("role")
            # Skip tool messages entirely — providers reject orphaned tool roles
            if role == "tool":
                continue
            if role == "assistant" and msg.get("tool_calls"):
                names = [tc.get("function", {}).get("name", "?") for tc in (msg.get("tool_calls") or [])]
                recent.append({"role": "assistant", "content": f"[MO called tools: {', '.join(names)}]"})
            elif role in ("user", "assistant"):
                content = str(msg.get("content", ""))[:300]
                if content:
                    recent.append({"role": role, "content": content})
        return recent[-6:]

    def _profile_context_excerpt(self, *, max_chars: int = 1600) -> str:
        profile = getattr(self, "profile", None)
        if not profile or not hasattr(profile, "build_profile_context"):
            return ""
        try:
            return str(profile.build_profile_context(max_chars=max_chars) or "")[:max_chars]
        except Exception:
            return ""

    def _recent_prompt_context(self, *, limit: int = 4, max_chars: int = 900) -> str:
        rows: list[str] = []
        for msg in list(getattr(self.session, "messages", []) or [])[-limit:]:
            role = str(msg.get("role") or "")
            if role not in {"user", "assistant"} or msg.get("tool_calls"):
                continue
            content = " ".join(str(msg.get("content") or "").split())[:220]
            if content:
                rows.append(f"{role}: {redact_sensitive_text(content)}")
        return "\n".join(rows)[-max_chars:]

    @staticmethod
    def _clean_prompt_enhancement_result(text: str, *, max_chars: int = 700) -> str:
        value = str(text or "").strip().strip('"“”')
        value = re.sub(r"^\s*(?:PG|Prompt|Enhanced prompt)\s*:\s*", "", value, flags=re.I).strip()
        value = re.sub(r"```.*?```", "", value, flags=re.S).strip()
        value = " ".join(value.split())
        if len(value) > max_chars:
            value = value[:max_chars].rsplit(" ", 1)[0] + "…"
        return value

    def enhance_prompt_for_input(self, rough: str, *, include_marker: bool = False) -> str:
        """Provider-backed Ctrl+E prompt enhancement with local deterministic fallback.

        Rewrites the operator's typed message into a sharper prompt, personalized to
        their language and tone from the profile. The TUI replaces the input row with
        the result; Esc reverts to the original.
        """
        from ..context.prompt_enhancer import enhance_prompt

        rough = str(rough or "").strip()
        fallback = enhance_prompt(rough, getattr(self, "profile", None))
        if not rough:
            return ""
        system = (
            "You are MO's internal prompt enhancer, not the chat assistant. "
            "Rewrite the operator's rough text into one complete prompt that will replace the input row.\n"
            "Rules:\n"
            "- Return only the enhanced prompt text; no prefix, bullets, quotes, markdown, or explanation.\n"
            "- Write in the SAME LANGUAGE as the operator's input; never translate or switch (e.g. do not turn Arabic into English or vice versa).\n"
            "- Match the operator's tone and register from the profile (direct, informal, brief); mirror their wording. Do NOT add AI-polish, hedging, caution, or evidence-first/verification boilerplate they did not ask for.\n"
            "- Preserve the operator's exact intent and scope; do not broaden it or invent files/tests.\n"
            "- Preserve the operator's defined vocabulary and shorthand verbatim; never 'correct', expand, or translate a term the operator uses as-is.\n"
            "- Make it specific enough for MO: objective and any scope guardrails the operator implied — without imposing structure they did not.\n"
            "- If the rough text is already clear, tighten it instead of expanding it.\n\n"
            f"Operator/profile context:\n{self._profile_context_excerpt(max_chars=1500) or 'none'}\n\n"
            f"Recent visible context:\n{self._recent_prompt_context() or 'fresh session'}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Enhance this prompt for MO input replacement:\n{redact_sensitive_text(rough)[:1200]}"},
        ]
        try:
            response, _provider = self.complete_ghost_no_tools(
                surface="ghost_prompt_enhance",
                request="prompt-enhance",
                messages=messages,
                max_tokens=min(int(self.max_tokens or 500), 500),
                monitor=getattr(getattr(self, "gateway", None), "monitor", None),
            )
        except Exception:
            if include_marker and fallback and fallback.strip() != rough.strip():
                return fallback + "\n\n_[prompt enhanced]_"
            return fallback
        result = self._clean_prompt_enhancement_result(str(getattr(response, "content", "") or ""))
        enhanced = result or fallback
        if include_marker and enhanced and enhanced.strip() != rough.strip():
            return enhanced + "\n\n_[prompt enhanced]_"
        return enhanced



def create_agent(config_path: str | None = None) -> Agent:
    """Create and return a configured Agent instance."""
    return Agent(config_path)
