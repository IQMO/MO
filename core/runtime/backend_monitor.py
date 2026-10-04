"""Safe backend monitor events for MO proof work."""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import traceback

from ..state.paths import ENV_MO_STATE_HOME, resolve_state_path
from ..utils.number_utils import cache_hit_percentage
from .surface_identity import DESKTOP_SURFACES, normalize_runtime_surface
from ..utils.text_safety import (
    PROVIDER_TOKEN_PATTERN,
    SECRET_NAME_PATTERN,
    TELEGRAM_BOT_TOKEN_PATTERN,
)

SAFE_EVENT_TYPES = {
    "taskboard",
    "backend_status",
    "provider_request",
    "provider_request_admitted",
    "provider_response",
    "provider_stream",
    "assistant_text",
    "provider_error",
    "provider_retry",
    "provider_fallback",
    "provider_restore",
    "codex_headers_seen",
    "tool_call",
    "tool_call_coalesced",
    "tool_call_rejected",
    "tool_batch_replayed",
    "tool_result",
    "affected_tests",
    "threat_scan",
    "content_safety",
    "security_check",
    "final_gates",
    "documentation_update",
    "memory_index",
    "memory_embedding_index",
    "memory_embedding_error",
    "memory_index_error",
    "memory_index_skipped_repeat",
    "memory_init_error",
    "memory_backfill",
    "memory_backfill_error",
    "memory_recall",
    "memory_recall_error",
    "memory_miss_error",
    "memory_cleanup",
    "memory_cleanup_error",
    "memory_embed_error",
    "memory_fts5_warning",
    "sandbox_guard",
    "project_rule_target_review",
    "sandbox_blocked",
    "goal_step",
    "goal_auditor",
    "goal_finish",
    "worker_event",
    "worker_on_finish_error",
    "worker_registry_setattr_error",
    "code_graph_context",
    "tool_result_cap",
    "live_steer",
    "session_quarantine",
    "turn_start",
    "turn_context",
    "turn_health",
    "desktop_completion",
    "desktop_action_receipt",
    "computer_event",
    "background_verification_receipt",
    "component_resource",
    "systemcare_operation",
    "lsp_diagnostics",
    "turn_intercept",
    "turn_end",
    "turn_error",
    "turn_error_park_failed",
    "session_event",
    "session_compact",
    "slash_command",
    "context_handoff",
    "continuity_gate",
    "consistency_boundary",
    "unverified_completion_claim",
    "unverified_claim",
    "unfulfilled_action_promise",
    "unsourced_external_claim",
    "uncaptured_operator_knowledge",
    "unverified_material_completion_claim",
    "structured_execution_claim",
    "board_advance",
    "board_finalized",
    "board_complete_rejected",
    "board_completion",
    "local_extension_open_taskboard_blocked",
    "prt_event",
    "prt_review",
    "prt_maintainer",
    "critic_review",
    "learning_auto_promote",
    "learning_write_error",
    "heartbeat",
    "runtime_phase",
    "trace_coverage",
}

_ACTIVE_MONITOR: "BackendMonitor | None" = None
_MONITOR_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("mo_backend_monitor_context")


def set_monitor(monitor: "BackendMonitor | None") -> None:
    global _ACTIVE_MONITOR
    _ACTIVE_MONITOR = monitor


def get_monitor() -> "BackendMonitor | None":
    return _ACTIVE_MONITOR


def backend_event_catalog(root: str | Path, *, cache: dict[str, Any] | None = None, verify_content: bool = False) -> dict[str, Any]:
    """Audit current producer declarations on demand; normal startup never scans."""
    import ast

    from ..diagnostics.source_inventory import discover_source_paths

    source_root = Path(root).resolve(strict=False)
    cached = cache if cache is not None else {}
    digest = hashlib.sha256()
    events: set[str] = set()
    phases: set[str] = set()
    errors: list[str] = []
    count = 0
    python_count = 0
    for relative, _origin in discover_source_paths(source_root, include_test_overlay=False):
        count += 1
        is_python = relative.endswith(".py")
        python_count += int(is_python)
        try:
            path = (source_root / relative).resolve(strict=False)
            if not path.is_relative_to(source_root):
                raise ValueError("source path escapes root")
            stat = path.stat()
            signature = (str(path), stat.st_mtime_ns, stat.st_size)
            entry = cached.get(relative)
            verified_content = path.read_bytes() if verify_content else None
            if entry is not None and verified_content is not None and hashlib.sha256(verified_content).digest() != entry["digest"]:
                entry = None
            if entry is None or entry["signature"] != signature:
                content = verified_content if verified_content is not None else path.read_bytes()
                file_events: set[str] = set()
                file_phases: set[str] = set()
                error = ""
                try:
                    if is_python and (b"emit" in content or b"monitor_phase" in content):
                        tree = ast.parse(content, filename=relative)
                        forwarders = {}
                        for function in ast.walk(tree):
                            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                continue
                            parameters = [*function.args.posonlyargs, *function.args.args]
                            if parameters and parameters[0].arg in {"self", "cls"}:
                                continue
                            for call in ast.walk(function):
                                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "emit":
                                    continue
                                argument = call.args[0] if call.args else next(
                                    (item.value for item in call.keywords if item.arg == "event_type"), None,
                                )
                                if isinstance(argument, ast.Name):
                                    for index, parameter in enumerate(parameters):
                                        if parameter.arg == argument.id:
                                            forwarders[function.name] = (index, parameter.arg)
                        for node in ast.walk(tree):
                            if not isinstance(node, ast.Call):
                                continue
                            is_emit = isinstance(node.func, ast.Attribute) and node.func.attr == "emit"
                            is_phase = isinstance(node.func, ast.Name) and node.func.id == "monitor_phase"
                            forwarded = forwarders.get(node.func.id) if isinstance(node.func, ast.Name) else None
                            if not (is_emit or is_phase or forwarded):
                                continue
                            index, keyword = forwarded or (0, "event_type" if is_emit else "phase")
                            first = node.args[index] if len(node.args) > index else next(
                                (item.value for item in node.keywords if item.arg == keyword), None,
                            )
                            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                                continue
                            if is_emit or forwarded:
                                file_events.add(first.value)
                            else:
                                file_phases.add(first.value)
                except (SyntaxError, ValueError) as exc:
                    error = type(exc).__name__
                entry = {
                    "signature": signature,
                    "digest": hashlib.sha256(content).digest(),
                    "events": file_events,
                    "phases": file_phases,
                    "error": error,
                }
                cached[relative] = entry
            digest.update(relative.encode("utf-8") + b"\0" + entry["digest"])
            events.update(entry["events"])
            phases.update(entry["phases"])
            if entry["error"]:
                errors.append(f"{relative}: {entry['error']}")
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append(f"{relative}: {type(exc).__name__}")
            digest.update(f"{relative}\0{type(exc).__name__}".encode("utf-8"))
            continue
    return {
        "source_digest": digest.hexdigest(),
        "source_files": count,
        "python_files": python_count,
        # Dictionaries preserve the whole name catalog through the monitor's
        # ordinary bounded-list redactor without relaxing payload limits.
        "declared_events": {name: True for name in sorted(events)},
        "declared_phases": {name: True for name in sorted(phases)},
        "unregistered_events": sorted(events - SAFE_EVENT_TYPES),
        "source_errors": errors,
        "source_error_count": len(errors),
    }


@contextmanager
def monitor_phase(phase: str, *, monitor: "BackendMonitor | None" = None, **metadata: Any):
    """Time one existing runtime operation without storing its inputs or results."""
    sink = monitor if monitor is not None else get_monitor()
    if sink is None or not getattr(sink, "enabled", True):
        yield
        return
    started_at = time.time()
    started = time.perf_counter()
    context = dict(_MONITOR_CONTEXT.get({}) or {})
    outcome = "ok"
    try:
        yield
    except BaseException as exc:
        outcome = "error" if isinstance(exc, Exception) else "interrupted"
        raise
    finally:
        try:
            sink.emit("runtime_phase", {
                **context,
                **metadata,
                "phase": phase,
                "started_at": round(started_at, 6),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "outcome": outcome,
            })
        except Exception:
            # A diagnostic sink failure cannot change the measured operation.
            pass


@contextmanager
def active_monitor(monitor: "BackendMonitor | None"):
    """Temporarily make `monitor` the global event sink for helper subsystems.

    Some subsystems emit through `get_monitor()` instead of receiving the turn
    monitor directly. Gateway wraps each turn with this so memory, graph, sandbox,
    and worker telemetry lands in the same trace as the turn.
    """
    previous = get_monitor()
    if monitor is not None:
        set_monitor(monitor)
    try:
        yield
    finally:
        set_monitor(previous)


def active_monitor_path() -> Path | None:
    """Return the active monitor path only when it belongs to the current monitor dir."""
    monitor = get_monitor()
    path = Path(getattr(monitor, "path", "")) if monitor is not None else None
    if not path or not path.exists():
        return None
    try:
        if path.parent.resolve() != BackendMonitor._monitor_dir().resolve():
            return None
    except Exception:
        return None
    return path


# Built from the canonical fragments in core.utils.text_safety so this
# monitor, the sandbox redactor, and the answer-critic stay in coverage
# lockstep (SEC-1 was caused by these diverging).
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,'\"}]+"),
    re.compile(
        r"(?i)((?:" + SECRET_NAME_PATTERN + r")[\"']?\s*[:=]\s*)[^\s,'\"}]+"
    ),
    re.compile(r"(?i)\bsk-[a-z0-9_\-]{6,}\b"),
    re.compile(r"(?i)\b(?:" + PROVIDER_TOKEN_PATTERN + r")\b"),
    re.compile(r"(?i)" + TELEGRAM_BOT_TOKEN_PATTERN),
    re.compile(
        r"(?i)-----begin\s+[a-z0-9 ]*private\s+key-----"
        r".*?(?:-----end\s+[a-z0-9 ]*private\s+key-----|$)",
        re.DOTALL,
    ),
)


def redact_monitor_text(value: Any, limit: int = 700) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[redacted]" if match.groups() else "[redacted]", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


@contextmanager
def monitor_context(**values: Any):
    """Attach best-effort correlation fields to monitor events in this context."""
    current = dict(_MONITOR_CONTEXT.get({}) or {})
    clean = {str(k): v for k, v in values.items() if v not in (None, "")}
    token = _MONITOR_CONTEXT.set({**current, **clean})
    try:
        yield
    finally:
        try:
            _MONITOR_CONTEXT.reset(token)
        except Exception:
            traceback.print_exc()


def current_monitor_context() -> dict[str, Any]:
    """Return the active monitor correlation context for gate/runtime hooks."""
    try:
        return dict(_MONITOR_CONTEXT.get({}) or {})
    except Exception:
        traceback.print_exc()
        return {}


def _safe_monitor_value(value: Any, *, limit: int = 6000) -> Any:
    if isinstance(value, dict):
        return {redact_monitor_text(k, 120): _safe_monitor_value(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_monitor_value(item, limit=limit) for item in list(value)[:80]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_monitor_text(value, limit)


_TOOL_OPERATION_KEYS = ("action", "operation", "method", "mode")
_SAFE_OPERATION_LABEL = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,47}$")
_TOOL_OPERATION_FAMILIES = {
    "code_search": "search",
    "find_callees": "search",
    "find_callers": "search",
    "find_files": "search",
    "grep": "search",
    "read_file": "read",
    "web_fetch": "fetch",
    "web_search": "search",
    "write_file": "write",
    "edit_file": "edit",
    "shell": "execute",
}
_TURN_COUNTER_EVENT_TYPES = frozenset({
    "provider_request",
    "provider_response",
    "provider_error",
    "provider_retry",
    "provider_fallback",
    "provider_restore",
    "tool_call",
    "tool_call_coalesced",
    "tool_call_rejected",
    "tool_result",
    "tool_result_cap",
    "session_compact",
    "context_handoff",
    "turn_end",
})


def _tool_operation_label(tool_name: str, arguments: dict[str, Any]) -> str:
    """Return a bounded non-content operation label for monitor grouping."""
    for key in _TOOL_OPERATION_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str):
            continue
        candidate = raw.strip().lower().replace(" ", "_").replace("-", "_")
        if _SAFE_OPERATION_LABEL.fullmatch(candidate):
            return candidate
    return _TOOL_OPERATION_FAMILIES.get(str(tool_name or "").strip().lower(), "invoke")


def _update_argument_fingerprint(digest: Any, value: Any, *, ordered: bool = False) -> None:
    """Feed a canonical typed value into a digest without building a large copy."""
    if isinstance(value, dict):
        digest.update(b"d")
        for key in value if ordered else sorted(value, key=lambda item: str(item)):
            _update_argument_fingerprint(digest, str(key), ordered=ordered)
            _update_argument_fingerprint(digest, value[key], ordered=ordered)
        digest.update(b";")
        return
    if isinstance(value, (list, tuple)):
        digest.update(b"l")
        for item in value:
            _update_argument_fingerprint(digest, item, ordered=ordered)
        digest.update(b";")
        return
    if value is None:
        digest.update(b"n;")
        return
    if isinstance(value, bool):
        digest.update(b"b1;" if value else b"b0;")
        return
    if isinstance(value, (int, float)):
        digest.update(b"i" + repr(value).encode("ascii", errors="replace") + b";")
        return
    text = str(value)
    digest.update(b"s" + str(len(text)).encode("ascii") + b":")
    for start in range(0, len(text), 4096):
        digest.update(text[start:start + 4096].encode("utf-8", errors="replace"))
    digest.update(b";")


def tool_call_names(tool_calls: Any) -> list[str]:
    """Extract tool/function names from a tool_calls list.

    Accepts both provider-SDK objects (``tc.function.name``) and plain dicts
    (``tc["function"]["name"]``). Missing names render as ``"?"``. Single source
    of truth for transcript/preview flattening across monitor, companion, and handoff.
    """
    names: list[str] = []
    for tc in tool_calls or []:
        fn = getattr(tc, "function", None)
        if fn is None and isinstance(tc, dict):
            fn = tc.get("function", tc)
        if hasattr(fn, "name"):
            names.append(str(getattr(fn, "name", "?") or "?"))
        elif isinstance(fn, dict):
            names.append(str(fn.get("name") or "?"))
        else:
            names.append("?")
    return names


def preview_provider_messages(messages: list[dict], limit: int = 700) -> str:
    preview_lines: list[str] = []
    for msg in messages[-6:]:
        role = str(msg.get("role") or "?")
        if role == "system":
            preview_lines.append("system: [system prompt hidden]")
            continue
        content = msg.get("content") or ""
        if role == "tool":
            preview_lines.append(f"tool: [tool result chars={len(str(content))}]")
            continue
        if msg.get("tool_calls"):
            names = tool_call_names(msg.get("tool_calls"))
            preview_lines.append(f"assistant: [tool calls: {', '.join(names)}]")
            continue
        preview_lines.append(f"{role}: {redact_monitor_text(content, 220)}")
    return redact_monitor_text("\n".join(preview_lines), limit)


def preview_provider_response(content: str, tool_calls: list[Any] | None, limit: int = 700) -> str:
    parts: list[str] = []
    if content:
        parts.append(redact_monitor_text(content, 420))
    if tool_calls:
        parts.append("tool calls: " + ", ".join(tool_call_names(tool_calls)))
    return redact_monitor_text("\n".join(parts), limit)


class BackendMonitor:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else self._new_run_path()
        self.run_id = uuid.uuid4().hex[:12]
        self._argument_fingerprint_key = os.urandom(32)
        self._turn_counters: dict[str, dict[str, int]] = {}
        self._seq = 0
        self._lock = threading.Lock()
        self._unregistered_events: set[str] = set()
        self._trace_coverage_enabled = os.environ.get("MO_BACKEND_MONITOR_COVERAGE") == "1"
        self._trace_coverage_cache: dict[str, Any] = {}
        self._trace_coverage_digest = ""
        self._trace_coverage_lock = threading.Lock()
        self._trace_coverage_error = ""
        self._trace_coverage_checked_at: float | None = None
        self.enabled = not self._disabled_by_env()
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if path is None:
                    self._cleanup_old_logs(keep=50, min_age_seconds=3600)
            except Exception:
                # Diagnostics must never stop MO startup.
                traceback.print_exc()
        self.process: subprocess.Popen | None = None

    def tool_event_metadata(self, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, str]:
        """Build duplicate-safe tool metadata without exposing arguments.

        The digest key exists only for this monitor process, so equal arguments
        can be counted inside one trace without creating a stable cross-run
        identifier or making paths/content recoverable from the log.
        """
        clean_arguments = arguments if isinstance(arguments, dict) else {}
        digest = hashlib.blake2b(
            key=self._argument_fingerprint_key,
            digest_size=12,
            person=b"mo-tool-args",
        )
        _update_argument_fingerprint(digest, clean_arguments)
        return {
            "operation": _tool_operation_label(tool_name, clean_arguments),
            "argument_fingerprint": digest.hexdigest(),
        }

    def provider_request_metadata(self, request: dict[str, Any]) -> dict[str, Any]:
        """Fingerprint the final provider payload without retaining its content.

        Preserve dictionary and list order here: unlike tool-argument equality,
        prompt-cache investigation must see reordered schemas/replay items.
        Digests are comparable only within this monitor's private key lifetime.
        Prefix checkpoints bound the log while hashing each input item once.
        """
        def new_digest():
            return hashlib.blake2b(
                key=self._argument_fingerprint_key, digest_size=12, person=b"mo-provider",
            )

        def fingerprint(value: Any) -> str:
            digest = new_digest()
            _update_argument_fingerprint(digest, value, ordered=True)
            return digest.hexdigest()

        items = request.get("input", request.get("messages", []))
        if not isinstance(items, list):
            items = [items]
        instructions = request.get("instructions")
        if instructions is None:
            instructions = [
                item for item in items
                if isinstance(item, dict) and item.get("role") in {"system", "developer"}
            ]
        tools = request.get("tools", [])
        options = {
            key: value for key, value in request.items()
            if key not in {"instructions", "input", "messages", "tools", "timeout"}
        }
        digest = new_digest()
        digest.update(b"l")
        prefixes = []
        for index, item in enumerate(items, 1):
            _update_argument_fingerprint(digest, item, ordered=True)
            if index <= 8 or (index & (index - 1) == 0 and len(prefixes) < 31) or index == len(items):
                prefix = digest.copy()
                prefix.update(b";")
                prefixes.append({"through_item": index, "fingerprint": prefix.hexdigest()})
        digest.update(b";")
        reasoning = request.get("reasoning") or {}
        effort = request.get("reasoning_effort", reasoning.get("effort"))
        context = reasoning.get("context")
        allowed_efforts = {"none", "minimal", "low", "medium", "high", "xhigh", "ultra"}
        visible_options = {
            name: request[name]
            for name in ("stream", "store", "temperature", "max_tokens", "max_output_tokens")
            if isinstance(request.get(name), (bool, int, float))
        }
        thinking = (request.get("extra_body") or {}).get("thinking") or {}
        if thinking.get("type") in {"disabled", "enabled"}:
            visible_options["thinking"] = thinking["type"]
        return {
            "wire_fingerprint_version": 1,
            "wire_instructions_fingerprint": fingerprint(instructions),
            "wire_tools_fingerprint": fingerprint(tools),
            "wire_options_fingerprint": fingerprint(options),
            "wire_input_fingerprint": digest.hexdigest(),
            "wire_input_items": len(items),
            "wire_input_prefixes": prefixes,
            "wire_tool_count": len(tools),
            "wire_options": visible_options,
            "reasoning_effort": effort if effort in allowed_efforts else "not_sent" if effort is None else "other",
            "reasoning_context": context if context == "all_turns" else "not_sent" if context is None else "other",
        }

    @staticmethod
    def provider_usage_metadata(usage: Any) -> dict[str, Any]:
        """Project only supplied numeric usage fields, preserving absent vs zero.

        This is raw provider evidence, not another token-accounting normalizer.
        Canonical totals and cache semantics remain with Agent usage accounting.
        """
        def read(value: Any, name: str) -> Any:
            return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

        def counts(value: Any, names: tuple[str, ...]) -> dict[str, int]:
            return {
                name: count for name in names
                if isinstance(count := read(value, name), int) and not isinstance(count, bool) and count >= 0
            }

        result: dict[str, Any] = counts(usage, (
            "input_tokens", "prompt_tokens", "output_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cache_read_input_tokens",
            "cache_write_tokens", "prompt_cache_write_tokens", "cache_write_input_tokens", "cache_creation_input_tokens",
        ))
        for name in ("input_tokens_details", "prompt_tokens_details", "output_tokens_details", "completion_tokens_details"):
            details = counts(read(usage, name), ("cached_tokens", "cache_write_tokens", "cache_creation_tokens", "reasoning_tokens"))
            if details:
                result[name] = details
        return result

    def _augment_turn_counters(self, event_type: str, payload: dict[str, Any]) -> str:
        """Attach cumulative outer-user-turn economy counters in emission order."""
        user_turn_id = str(payload.get("user_turn_id") or payload.get("turn_id") or "").strip()
        if not user_turn_id:
            return ""
        payload.setdefault("user_turn_id", user_turn_id)
        if event_type == "turn_start":
            self._turn_counters[user_turn_id] = {
                "provider_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_calls": 0,
                "compactions": 0,
                "continuations": 0,
            }
        counters = self._turn_counters.setdefault(user_turn_id, {
            "provider_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
            "compactions": 0,
            "continuations": 0,
        })
        if event_type == "provider_request":
            counters["provider_requests"] += 1
        elif event_type == "provider_response":
            counters["input_tokens"] += max(0, int(payload.get("input_tokens") or 0))
            counters["output_tokens"] += max(0, int(payload.get("output_tokens") or 0))
        elif event_type == "tool_call":
            counters["tool_calls"] += 1
        elif event_type == "session_compact":
            counters["compactions"] += 1
        elif event_type == "session_event" and str(payload.get("kind") or "").endswith("_auto_continuation"):
            counters["continuations"] += 1
        is_continuation_event = (
            event_type == "session_event"
            and str(payload.get("kind") or "").endswith("_auto_continuation")
        )
        if event_type in _TURN_COUNTER_EVENT_TYPES or is_continuation_event:
            payload.update({f"turn_{key}": value for key, value in counters.items()})
        return user_turn_id

    @staticmethod
    def _disabled_by_env() -> bool:
        return os.environ.get("MO_BACKEND_MONITOR_DISABLED") == "1" or os.environ.get("MO_BACKEND_MONITOR") == "0"

    @staticmethod
    def _monitor_dir() -> Path:
        configured = os.environ.get("MO_BACKEND_MONITOR_DIR")
        if configured:
            return Path(configured)
        state_home = os.environ.get(ENV_MO_STATE_HOME, "").strip()
        if state_home:
            return Path(state_home) / "logs" / "monitor"
        return Path(resolve_state_path("logs/monitor"))

    @staticmethod
    def _new_run_path() -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        return BackendMonitor._monitor_dir() / f"backend_monitor-{stamp}-{suffix}.jsonl"

    @staticmethod
    def _cleanup_old_logs(keep: int = 50, min_age_seconds: float = 3600) -> None:
        """Keep recent monitor logs without deleting an active live-session log.

        Tests and short-lived Gateways can create many monitors quickly; deleting
        logs that are only seconds old destroys the evidence trail for a running
        MO session. Cleanup therefore only removes logs beyond the keep count
        after they are old enough to be safely considered historical.
        """
        parent = BackendMonitor._monitor_dir()
        if not parent.exists():
            return
        now = time.time()
        files = sorted(parent.glob("backend_monitor-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                if now - old.stat().st_mtime < min_age_seconds:
                    continue
                old.unlink()
            except OSError:
                pass

    def _check_trace_coverage(self, *, boundary: str) -> None:
        """Check every turn boundary; sample long turns at most once a minute."""
        if not self._trace_coverage_lock.acquire(blocking=False):
            return
        try:
            now = time.monotonic()
            if (
                boundary == "provider_request"
                and self._trace_coverage_checked_at is not None
                and now - self._trace_coverage_checked_at < 60
            ):
                return
            self._trace_coverage_checked_at = now
            verify_content = boundary == "turn_end"
            with monitor_phase("trace_coverage_check", monitor=self, boundary=boundary, content_verified=verify_content):
                catalog = backend_event_catalog(
                    Path(__file__).resolve().parents[2], cache=self._trace_coverage_cache,
                    verify_content=verify_content,
                )
            digest = str(catalog["source_digest"])
            status = "source_changed" if self._trace_coverage_digest else "current"
            self._trace_coverage_error = ""
            if digest == self._trace_coverage_digest:
                return
            self._trace_coverage_digest = digest
            self.emit("trace_coverage", {"status": status, "catalog_version": 1, **catalog})
        except Exception as exc:
            error = type(exc).__name__
            if error != self._trace_coverage_error:
                self._trace_coverage_error = error
                self.emit("trace_coverage", {"status": "check_failed", "error_type": error})
        finally:
            self._trace_coverage_lock.release()

    def emit(self, event_type: str, payload: dict | str) -> None:
        """Append a monitor event, best-effort only.

        The backend monitor is diagnostics, not runtime authority.  Disk/path/JSON
        failures are swallowed so deleting or breaking the monitor never changes
        MO's provider/tool/session behavior.
        """
        if not self.enabled:
            return
        if self._trace_coverage_enabled and event_type in {"turn_start", "provider_request", "turn_end"}:
            correlation = {
                key: payload[key] for key in ("turn_id", "user_turn_id", "session_id", "instance_id", "surface", "request")
                if isinstance(payload, dict) and key in payload
            }
            with monitor_context(**correlation):
                self._check_trace_coverage(boundary=event_type)
        if event_type not in SAFE_EVENT_TYPES:
            # Never store an unregistered payload. Make the missing coverage
            # visible once per type so diagnostics cannot silently look complete.
            with self._lock:
                if event_type in self._unregistered_events:
                    return
                self._unregistered_events.add(event_type)
            self.emit("trace_coverage", {
                "status": "unregistered_event",
                "event_type": str(event_type)[:120],
            })
            return
        try:
            safe_payload = payload if isinstance(payload, dict) else {"message": str(payload)}
            safe_payload = _safe_monitor_value(safe_payload)
            if not isinstance(safe_payload, dict):
                safe_payload = {"message": str(safe_payload)}
            context = _safe_monitor_value(_MONITOR_CONTEXT.get({}) or {})
            if isinstance(context, dict):
                for key, value in context.items():
                    safe_payload.setdefault(key, value)
            with self._lock:
                completed_turn_id = self._augment_turn_counters(event_type, safe_payload)
                self._seq += 1
                event = {
                    "ts": round(time.time(), 3),
                    "run_id": self.run_id,
                    "seq": self._seq,
                    "type": event_type,
                    "payload": safe_payload,
                }
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            if event_type == "turn_end" and completed_turn_id:
                with self._lock:
                    self._turn_counters.pop(completed_turn_id, None)
        except Exception:
            return

    def emit_text(self, text: str) -> None:
        self.emit("backend_status", {"message": text})

    def open_window(self) -> None:
        if not self.enabled or os.environ.get("MO_OPEN_BACKEND_MONITOR") != "1":
            return
        if self.process and self.process.poll() is None:
            return
        root = Path(__file__).resolve().parents[1]
        # Optional local diagnostic launchers resolve from the profile extension
        # root, not the checkout. Absent on user clones, open_window no-ops.
        from ..state.paths import local_extension_root
        monitor = local_extension_root() / "mo_monitor.py"
        if not monitor.exists():
            return
        log_path = str(self.path.resolve())
        os.environ["MO_BACKEND_MONITOR_PATH"] = log_path
        self.process = subprocess.Popen(
            [sys.executable, str(monitor), log_path],
            cwd=str(root),
            stdout=None,
            stderr=None,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )

    def close_window(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def latest_monitor_path() -> Path | None:
    """Return the newest process monitor file, or None.

    A process monitor may contain several session and surface ids; recency does
    not make it authoritative for any particular conversation.
    """
    parent = BackendMonitor._monitor_dir()
    if not parent.exists():
        return None
    files = sorted(parent.glob("backend_monitor-*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


# Surfaces whose turns are NOT part of a Main-MO logical run and must be
# excluded from its economy record.
# MO Desktop surfaces are excluded from the Main-MO economy record: MO Desktop runs
# on its own isolated session and must not leak into the operator's main-turn trace.
# The companion package shares MO Desktop's isolated session, so it is part of the
# same canonical desktop family (see core.runtime.surface_identity.DESKTOP_SURFACES).
MO_DESKTOP_SURFACES = DESKTOP_SURFACES

def economy_summary(
    monitor_path: str | Path | None = None,
    *,
    turn_ids: "set[str] | frozenset[str] | None" = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    exclude_surfaces: "set[str] | frozenset[str] | None" = None,
    instance_ids: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Deterministic provider/tool/error/result-cap counts from a backend monitor.

    The authoritative source of session economy so records never need to be
    estimated or stale.
    Tool errors are counted from ``tool_result.error``/``is_error`` and sandbox
    blocks from ``tool_result.blocked``. Lower-level
    ``tool_error``/``sandbox_blocked`` telemetry is counted only when no nearby
    matching ``tool_result`` exists; dispatch often records both for one
    failed/blocked call, while a few internal guards can emit standalone
    lower-level telemetry.

    Logical-run scoping: one process monitor can hold a Main-MO run plus
    handoff segments plus MO Desktop calls.
    ``turn_ids`` restricts counting to exact turn correlation ids. ``session_ids``
    restricts counting to a run's segment ids. ``instance_ids`` can further restrict
    to specific process instances. ``exclude_surfaces`` drops events tagged by either
    route_source or normalized surface (e.g. ``MO_DESKTOP_SURFACES``).
    Defaults preserve whole-file behavior for existing callers.
    """
    path = Path(monitor_path) if monitor_path else (active_monitor_path() or latest_monitor_path())
    out = {
        "source": path.name if path else None,
        "provider_requests": 0, "provider_responses": 0, "provider_errors": 0,
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cache_hit_tokens": 0, "cache_miss_tokens": 0, "cache_write_tokens": 0,
        "tool_calls": 0, "tool_errors": 0, "sandbox_blocked": 0,
        "result_cap_events": 0,
        "error_tools": [], "blocked_tools": [], "blocked_reasons": [],
    }
    if not path or not Path(path).exists():
        return out
    _err_tools: "set[str]" = set()
    _blk_tools: "set[str]" = set()
    _blk_reasons: "set[str]" = set()
    events: list[tuple[str, dict[str, Any]]] = []
    for line in Path(path).open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        p = d.get("payload", {}) or {}
        if turn_ids is not None:
            tid = p.get("turn_id") or d.get("turn_id")
            if tid not in turn_ids:
                continue
        if session_ids is not None:
            sid = p.get("session_id") or d.get("session_id")
            if sid not in session_ids:
                continue  # not part of this logical run's segments
        if instance_ids is not None:
            iid = p.get("instance_id") or d.get("instance_id")
            if iid not in instance_ids:
                continue
        if exclude_surfaces:
            exclude = {_monitor_surface_key(item) for item in exclude_surfaces}
            surfaces = _monitor_surface_keys(p, d)
            if exclude.intersection(surfaces):
                continue  # Side-check turn — not part of the Main-MO run
        events.append((str(t or ""), p if isinstance(p, dict) else {}))

    def has_nearby_tool_result(index: int, tool: str, field: str) -> bool:
        # Sandbox guard telemetry is emitted immediately before the canonical
        # dispatch result. A small lookahead de-duplicates the paired events while
        # still preserving truly standalone guard failures.
        for next_t, next_p in events[index + 1:index + 21]:
            if next_t != "tool_result":
                continue
            if str(next_p.get("tool") or "") != tool:
                continue
            if field == "blocked" and next_p.get("blocked"):
                return True
            if field == "error" and (next_p.get("error") or next_p.get("is_error")):
                return True
        return False

    for index, (t, p) in enumerate(events):
        if t == "provider_request":
            out["provider_requests"] += 1
        elif t == "provider_response":
            out["provider_responses"] += 1
            out["input_tokens"] += int(p.get("input_tokens") or 0)
            out["output_tokens"] += int(p.get("output_tokens") or 0)
            out["total_tokens"] += int(p.get("total_tokens") or 0)
            out["cache_hit_tokens"] += int(p.get("cache_hit_tokens") or 0)
            out["cache_miss_tokens"] += int(p.get("cache_miss_tokens") or 0)
            out["cache_write_tokens"] += int(p.get("cache_write_tokens") or 0)
        elif t == "provider_error":
            out["provider_errors"] += 1
        elif t == "tool_call":
            out["tool_calls"] += 1
        elif t == "tool_result":
            if p.get("error") or p.get("is_error"):
                out["tool_errors"] += 1
                _err_tools.add(str(p.get("tool") or ""))
            if p.get("blocked"):
                reason = p.get("reason") or ""
                out["sandbox_blocked"] += 1
                _blk_tools.add(str(p.get("tool") or ""))
                _blk_reasons.add(str(reason or ""))
        elif t == "tool_error":
            tool = str(p.get("tool") or "")
            if not has_nearby_tool_result(index, tool, "error"):
                out["tool_errors"] += 1
                _err_tools.add(tool)
        elif t == "sandbox_blocked":
            tool = str(p.get("tool") or "")
            reason = p.get("reason") or ""
            if not has_nearby_tool_result(index, tool, "blocked"):
                out["sandbox_blocked"] += 1
                _blk_tools.add(tool)
                _blk_reasons.add(str(reason or ""))
        elif t == "tool_result_cap":
            out["result_cap_events"] += 1
    out["error_tools"] = sorted(t for t in _err_tools if t)
    out["blocked_tools"] = sorted(t for t in _blk_tools if t)
    out["blocked_reasons"] = sorted(r for r in _blk_reasons if r)
    return out


def _monitor_surface_key(value: Any) -> str:
    if value in (None, ""):
        return ""
    return normalize_runtime_surface(value)


def _monitor_surface_keys(payload: dict[str, Any], event: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for value in (
        payload.get("route_source"),
        payload.get("surface"),
        event.get("route_source"),
        event.get("surface"),
    ):
        raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if raw:
            keys.add(raw)
        normalized = _monitor_surface_key(raw)
        if normalized:
            keys.add(normalized)
    return keys


def format_economy_record(summary: dict[str, Any] | None = None) -> str:
    """Markdown economy block from `economy_summary()` — the canonical Gate 2f record."""
    s = summary or economy_summary()
    tool_status = f"errors: {s.get('tool_errors', 0)}, sandbox-blocked: {s.get('sandbox_blocked', 0)}"
    lines = [
        "### Economy Record (Gate 2f — runtime/monitor, authoritative)\n"
        f"- Source: {s.get('source')}\n"
        f"- Provider requests: {s.get('provider_requests', 0)} "
        f"(responses: {s.get('provider_responses', 0)}, errors: {s.get('provider_errors', 0)})\n"
        f"- Provider tokens: {int(s.get('total_tokens') or 0):,} total "
        f"({int(s.get('input_tokens') or 0):,} input, {int(s.get('output_tokens') or 0):,} output)\n"
    ]
    cache_hit = int(s.get("cache_hit_tokens") or 0)
    cache_miss = int(s.get("cache_miss_tokens") or 0)
    cache_write = int(s.get("cache_write_tokens") or 0)
    input_tokens = int(s.get("input_tokens") or 0)
    if cache_hit or cache_miss or cache_write:
        cache_ratio = cache_hit_percentage(input_tokens, cache_hit)
        ratio_text = "ratio unavailable" if cache_ratio is None else f"{cache_ratio:.0f}% hit"
        lines.append(
            f"- Provider cache: {ratio_text} "
            f"({cache_hit:,} hit, {cache_miss:,} miss"
            + (f", {cache_write:,} write" if cache_write else "")
            + "; provider-reported)\n"
        )
    lines.extend([
        f"- Tool calls: {s.get('tool_calls', 0)} ({tool_status})\n",
        f"- Result-cap events: {s.get('result_cap_events', 0)}\n",
    ])
    error_tools = [str(t).strip() for t in (s.get("error_tools") or []) if str(t).strip()]
    blocked_tools = [str(t).strip() for t in (s.get("blocked_tools") or []) if str(t).strip()]
    if error_tools:
        lines.append(f"- Error tools: {', '.join(sorted(error_tools))}\n")
    if blocked_tools:
        lines.append(f"- Blocked tools: {', '.join(sorted(blocked_tools))}\n")
    return "".join(lines)
