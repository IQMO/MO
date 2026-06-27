"""Per-tool-call dispatch phase of the MO agent turn: turn-start intercepts, sandbox/self-mutation gating, tool execution, and tool audit/capping."""

import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..sandbox import guard_tool_call, redact_provider_tokens, redact_sensitive_text, shell_command_is_mutating
from ..backend_monitor import BackendMonitor
from ..session.handoff import context_pressure
from ..learning.workflow_learning import (
    promote_workflow_candidate,
    stage_workflow_source_candidate,
)
from ..consistency_boundary import changed_proposal_paths_for_last_commit
from ..behavior_gates import run_input_gates
from ..mo_control_context import resolve_mo_control_workspace
from .agent_utils import (
    URL_RE,
    WORKFLOW_ADOPTION_RE,
    WORKFLOW_APPROVAL_RE,
    WORKFLOW_EXPLICIT_RE,
    WORKFLOW_SOURCE_PATH_RE,
    _prune_tool_audit_log,
)
from ..owner_protocols import (
    is_owner_maintenance_activation,
    is_owner_interface_audit_activation,
    is_owner_comparison_activation,
    owner_comparison_readonly_source_roots,
)
from ..path_defaults import mo_home, operator_pack_root, repo_root
from ..tasking.task_evidence import taskboard_tool_summary


@dataclass(frozen=True)
class ToolExecutionPolicy:
    """Local execution metadata for prefetch and context-result handling."""

    parallel_prefetch: bool = False
    result_cap_exempt: bool = False
    reason: str = ""


_TOOL_EXECUTION_POLICIES = {
    # Self-bounded filesystem reads. They can prefetch in parallel and must not
    # be truncated by the fallback cap, otherwise MO can silently lose the back
    # of files it explicitly chose to inspect.
    "read_file": ToolExecutionPolicy(parallel_prefetch=True, result_cap_exempt=True, reason="bounded filesystem read"),
    "grep": ToolExecutionPolicy(parallel_prefetch=True, result_cap_exempt=True, reason="bounded filesystem search"),
    "find_files": ToolExecutionPolicy(parallel_prefetch=True, result_cap_exempt=True, reason="bounded filesystem listing"),
    # Additional read-only, side-effect-free inspection tools. These are safe to
    # pre-execute when independent, but they remain subject to the fallback cap.
    "git_status": ToolExecutionPolicy(parallel_prefetch=True, reason="read-only git status"),
    "project_bridge": ToolExecutionPolicy(parallel_prefetch=True, reason="read-only project instruction lookup"),
    "code_search": ToolExecutionPolicy(parallel_prefetch=True, reason="read-only code graph search"),
    "find_callers": ToolExecutionPolicy(parallel_prefetch=True, reason="read-only code graph traversal"),
    "find_callees": ToolExecutionPolicy(parallel_prefetch=True, reason="read-only code graph traversal"),
}


_READ_FAMILY_TOOLS = frozenset(
    name for name, policy in _TOOL_EXECUTION_POLICIES.items() if policy.result_cap_exempt
)


def _tool_execution_policy(name: str) -> ToolExecutionPolicy:
    return _TOOL_EXECUTION_POLICIES.get(str(name or "").strip().lower(), ToolExecutionPolicy())


def _tool_allows_parallel_prefetch(name: str) -> bool:
    return _tool_execution_policy(name).parallel_prefetch


class AgentTurnDispatchMixin:
    """Deterministic turn-start intercepts and the per-tool-call dispatch phase."""

    def _prepare_turn_start(self, user_input: str, *, monitor: BackendMonitor | None = None, cancel_event: object = None) -> dict[str, object]:
        """Run shared pre-provider turn setup and deterministic local intercepts."""
        text = str(user_input or "").strip()
        if not text:
            return {"final_text": "", "kind": "empty", "user_input": text, "pre_handoff": False}
        if getattr(cancel_event, "is_set", lambda: False)():
            return {"final_text": "[ABORTED] Current turn stopped.", "kind": "aborted", "user_input": text, "pre_handoff": False}

        # Input-phase behavior gates (declarative registry): prompt-injection threat
        # scan + malicious-code refusal, evaluated before any provider call.
        gate_outcome, gate_events = run_input_gates(self, text)
        if monitor:
            for event_name, payload in gate_events:
                monitor.emit(event_name, payload)
        if gate_outcome:
            if monitor and gate_outcome.monitor_event:
                monitor.emit(*gate_outcome.monitor_event)
            return {"final_text": gate_outcome.message, "kind": gate_outcome.kind, "user_input": text, "pre_handoff": False}

        quarantine_meta = self._quarantine_unfinished_tail_before_turn(text, monitor=monitor)
        self._pause_interrupted_work_for_return(text, quarantine_meta, monitor=monitor)
        self._active_lane = None
        pre_handoff = self._pre_turn_context_handoff(text)
        if not pre_handoff:
            self.session.add_user(text)
            self.session.turn_count += 1

        intercepts = (
            ("init", self._maybe_handle_init_turn, False),
            ("workflow_control", self._maybe_handle_workflow_control_turn, True),
            ("identity", self._maybe_handle_identity_turn, True),
        )
        for kind, handler, record_memory in intercepts:
            response = handler(text)
            if response is None:
                continue
            if monitor:
                payload = {"kind": kind, "result_chars": len(response)}
                monitor.emit("turn_intercept", payload)
            self.session.add_assistant(response)
            if record_memory:
                self._record_turn_memory_only(text, response)
            return {"final_text": response, "kind": kind, "user_input": text, "pre_handoff": pre_handoff}

        return {"final_text": None, "kind": "provider", "user_input": text, "pre_handoff": pre_handoff}

    def _maybe_handle_init_turn(self, user_input: str) -> str | None:
        """Handle /init as a deterministic private setup check."""
        text = str(user_input or "").strip()
        if not text.startswith("/init"):
            return None
        from ..initializer import initialize_mo, render_init_report

        report = initialize_mo(home=getattr(self, "runtime_home", None), project_path=getattr(self, "project_cwd", None))
        return render_init_report(report)

    def _maybe_handle_identity_turn(self, user_input: str) -> str | None:
        """Deterministic identity/model answer; private names are not auth."""
        text = " ".join(str(user_input or "").strip().lower().split())
        if not text:
            return None
        identity_match = bool(re.search(r"\b(who are you|what are you|what is mo|who made you|who created you|are you chatgpt|are you claude)\s*[?.!]*\s*$", text))
        model_match = bool(re.search(r"\b(what model|which model|current model|your model|who is your model|model are you using)\b", text))
        if not identity_match and not model_match:
            return None
        provider = str(getattr(self, "provider_name", "") or "provider")
        model = str(getattr(self, "model", "") or "model")
        return (
            f"I'm MO — a local-first coding agent by IQMO. I'm flying around `{provider}/{model}` right now; "
            "that model is my runtime engine, not my identity. I use tools through MO's sandbox and land edits only with evidence."
        )

    def _maybe_handle_workflow_control_turn(self, user_input: str) -> str | None:
        """Handle local skill adoption/promotion without a provider call.

        External skills/workflows are untrusted source material. MO stages them
        as inert local skill candidates, then requires explicit approval before
        any relevance-gated guidance is used.
        """
        text = str(user_input or "").strip()
        if not text:
            return None
        learning = self._maybe_handle_learning_control_turn(text)
        if learning is not None:
            return learning
        if WORKFLOW_APPROVAL_RE.search(text) and not WORKFLOW_ADOPTION_RE.search(text):
            result = promote_workflow_candidate(getattr(self, "profile", None), text, "workflow approval handled locally")
            if result.get("promoted"):
                skill_path = str(result.get("skill_path") or "")
                path_line = f"\nSkill pack: {skill_path}" if skill_path else ""
                return f"Skill promoted: `{result.get('id', '')}`{path_line}\nApplies only when relevant; current scope, tools, sandbox, and Gateway taskboard truth still win."
            if result.get("blocked"):
                return f"Skill promotion blocked: {result.get('reason', 'unsafe skill candidate')}"
            return f"No skill promoted: {result.get('reason', 'no matching skill candidate')}"
        if not WORKFLOW_ADOPTION_RE.search(text):
            return None
        # Fail open: WORKFLOW_ADOPTION_RE also matches bare "use the same method/skill
        # to …", which is ordinary work, not an adoption command. Only treat this as a
        # workflow-adoption turn when the intent is unambiguous (the literal word
        # "workflow") OR a concrete source was supplied (URL / file path / pasted
        # block). Otherwise return None so the provider handles the turn normally —
        # never hijack an ordinary request with the "give me a workflow source" prompt.
        has_explicit = bool(WORKFLOW_EXPLICIT_RE.search(text))
        has_source = bool(
            URL_RE.search(text)
            or self._extract_workflow_source_path(text)
            or self._extract_inline_workflow_source(text)
        )
        if not (has_explicit or has_source):
            return None
        loaded = self._load_workflow_adoption_source(text)
        if not loaded.get("ok"):
            return loaded.get("message") or "Give me a skill/workflow file path, URL, or pasted guidance text to stage."
        staged = stage_workflow_source_candidate(
            getattr(self, "profile", None),
            str(loaded.get("content") or ""),
            source_label=str(loaded.get("label") or "workflow source"),
            source_kind=str(loaded.get("kind") or "text"),
            request_text=text,
        )
        if not staged.get("staged"):
            reason = staged.get("reason", "could not stage skill")
            prefix = "Skill adoption blocked" if staged.get("blocked") else "Skill adoption failed"
            return f"{prefix}: {reason}"
        candidate = staged.get("candidate") or {}
        duplicate = "already staged" if staged.get("duplicate") else "staged"
        return (
            f"Skill candidate {duplicate}: `{staged.get('id', '')}`\n"
            f"When: {candidate.get('trigger', '')}\n"
            f"Do: {candidate.get('behavior', '')}\n"
            f"Avoid: {candidate.get('anti_pattern', '')}\n"
            f"Source: {candidate.get('source_label', '')}\n"
            f"Approve with: approve skill candidate {staged.get('id', '')}"
        )

    def _load_workflow_adoption_source(self, user_input: str) -> dict[str, object]:
        text = str(user_input or "")
        url_match = URL_RE.search(text)
        if url_match:
            url = url_match.group(0).rstrip(".,;")
            args = {"url": url}
            block_reason = guard_tool_call("web_fetch", args, lane=self._active_lane, allowed_roots=self.allowed_roots, sandbox_config=self.sandbox_config)
            if block_reason:
                self._write_tool_audit("web_fetch", args, "", block_reason)
                return {"ok": False, "message": f"Skill source blocked: {block_reason}"}
            result = self._dispatch_tool("web_fetch", args)
            self._write_tool_audit("web_fetch", args, result, None)
            if str(result).startswith("Error"):
                return {"ok": False, "message": f"Skill source fetch failed: {result[:240]}"}
            return {"ok": True, "kind": "url", "label": url, "content": result}

        path = self._extract_workflow_source_path(text)
        if path:
            args = {"path": path}
            block_reason = guard_tool_call("read_file", args, lane=self._active_lane, allowed_roots=self.allowed_roots, sandbox_config=self.sandbox_config)
            if block_reason:
                self._write_tool_audit("read_file", args, "", block_reason)
                return {"ok": False, "message": f"Skill source blocked: {block_reason}"}
            result = self._dispatch_tool("read_file", args)
            self._write_tool_audit("read_file", args, result, None)
            if str(result).startswith("Error"):
                return {"ok": False, "message": f"Skill source read failed: {result[:240]}"}
            return {"ok": True, "kind": "file", "label": path, "content": result}

        inline = self._extract_inline_workflow_source(text)
        if inline:
            return {"ok": True, "kind": "text", "label": "inline workflow text", "content": inline}
        return {"ok": False, "message": "Give me a skill/workflow file path, URL, or pasted guidance text to stage."}

    @staticmethod
    def _extract_workflow_source_path(text: str) -> str:
        match = WORKFLOW_SOURCE_PATH_RE.search(str(text or ""))
        if not match:
            return ""
        value = next((part for part in match.groups() if part), "")
        return value.strip().strip("`'\".,;:()[]{}")

    # Assistant-narration shapes that must NEVER be mined as an "adopted workflow"
    # (a user-presented workflow is imperative/structured, not "Let me check … Now
    # let me …"). Guards against staging MO's own multi-step narration — or
    # carried-over session text — as a workflow candidate.
    _NARRATION_MARKERS = (
        "let me ", "let me.", "i'll ", "i will ", "now let me", "now i'", "let's ",
        "found it", "found e:", "found the", "good —", "good, ", "okay,", "looking at",
        "checking ", "likely what you meant", "let me now", "let me actually",
    )

    @staticmethod
    def _extract_inline_workflow_source(text: str) -> str:
        raw = str(text or "")
        if ":" not in raw:
            return ""
        inline = raw.split(":", 1)[1].strip()
        if len(inline) < 24:
            return ""
        low = inline.lower()
        # Reject MO's own narration / contaminated session text — only stage an
        # explicitly user-presented workflow, never the assistant's step prose.
        if any(marker in low for marker in AgentTurnDispatchMixin._NARRATION_MARKERS):
            return ""
        return inline

    def _maybe_handle_learning_control_turn(self, text: str) -> str | None:
        """Natural-language alias for local learning/skill management."""
        clean = " ".join(str(text or "").strip().split())
        low = clean.lower()
        if not low:
            return None
        suggestion_match = re.search(r"learning-suggestion:[a-z0-9_:-]+", clean, flags=re.I)
        if suggestion_match and re.search(r"\b(confirm|approve|accept|save)\b", low):
            return self._cmd_learning_review("confirm", suggestion_match.group(0))
        if suggestion_match and re.search(r"\b(dismiss|reject|ignore|drop)\b", low):
            return self._cmd_learning_review("dismiss", suggestion_match.group(0))
        if re.fullmatch(r"(?:show|list|review|what(?:'s| is))\s+(?:what\s+)?(?:you\s+)?(?:learned|learning|local skills|skills)(?:\s+status)?[?.!]*", low):
            return self._cmd_learning("status")
        if re.fullmatch(r"(?:show|list|review)\s+(?:pending\s+)?(?:learning|suggestions|skill candidates|skills pending)(?:\s+pending)?[?.!]*", low):
            return self._cmd_learning("pending")
        return None

    def _detect_tool_abuse(self, name: str, arguments: dict) -> str:
        if not hasattr(self, "_tool_history"):
            self._tool_history: list = []
        if not hasattr(self, "_tool_abuse_warned"):
            self._tool_abuse_warned: set = set()
        warning = ""
        path_or_pattern = ""
        summary = str(arguments.get("path") or arguments.get("pattern") or arguments.get("command") or "")[:120]
        if name == "read_file":
            path_or_pattern = str(arguments.get("path", ""))
            same_file = [1 for t, p, s in self._tool_history if t == "read_file" and p == path_or_pattern]
            consecutive = 0
            for t, p, s in reversed(self._tool_history):
                if t == "read_file" and p == path_or_pattern:
                    consecutive += 1
                else:
                    break
            if path_or_pattern and consecutive >= 2:
                warning = f"[TOOL USE NOTICE] You have read {path_or_pattern!r} {consecutive + 1}x consecutively. You already have its contents. Proceed with analysis instead of re-reading."
            elif path_or_pattern and len(same_file) >= 3:
                warning = f"[TOOL USE NOTICE] You have read {path_or_pattern!r} {len(same_file) + 1}x recently. Re-read is wasteful. Use the content you already have."
        elif name == "shell":
            cmd = str(arguments.get("command", ""))
            path_or_pattern = cmd[:120]
            trivial_markers = [
                'python -c "print(', 'python -c "x=', 'python -c "f=open',
                'python -c "lines=', 'python -c "import sys; print',
                'python -c "open(', 'type tmp\\', 'type tmp/',
            ]
            for marker in trivial_markers:
                if marker in cmd:
                    warning = "[TOOL USE NOTICE] This shell command appears trivial. Consider read_file/grep instead of shell for basic file I/O."
                    break
            same_cmd = [1 for t, p, s in self._tool_history if t == "shell" and p == path_or_pattern]
            if len(same_cmd) >= 2 and path_or_pattern not in self._tool_abuse_warned:
                self._tool_abuse_warned.add(path_or_pattern)
                warning = f"[TOOL USE NOTICE] This shell command has been run {len(same_cmd) + 1}x. Results are already in context."
        elif name in ("grep", "find_files"):
            path_or_pattern = str(arguments.get("pattern", ""))
            same_pattern = [1 for t, p, s in self._tool_history if t == name and p == path_or_pattern]
            if len(same_pattern) >= 2 and path_or_pattern not in self._tool_abuse_warned:
                self._tool_abuse_warned.add(path_or_pattern)
                warning = f"[TOOL USE NOTICE] {name} on {path_or_pattern!r} has been run {len(same_pattern) + 1}x. Results are already in context."
        self._tool_history.append((name, path_or_pattern, summary))
        if len(self._tool_history) > 80:
            self._tool_history = self._tool_history[-60:]
            # Keep the warned-set bounded and in sync with retained history: a
            # pattern that aged out can warn again, and the set can't grow forever.
            live = {p for _t, p, _s in self._tool_history}
            self._tool_abuse_warned &= live
        return warning

    @staticmethod
    def _parsed_tool_arguments(tc_data: dict) -> dict:
        raw = str(((tc_data.get("function") or {}).get("arguments")) or "")
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _safe_tool_summary(name: str, arguments: dict) -> str:
        return taskboard_tool_summary(name, arguments)

    @staticmethod
    def _tool_result_is_error(result: str) -> bool:
        text = str(result or "").lower()
        if text.startswith("error") or "[path blocked]" in text or "[shell blocked]" in text:
            return True
        exit_match = re.search(r"\[exit code\s+(-?\d+)\]", text)
        return bool(exit_match and int(exit_match.group(1)) != 0)

    @staticmethod
    def _safe_int(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _current_tool_context_saved_chars(self) -> int:
        return self._safe_int(getattr(self, "compression_total_saved", 0)) + self._safe_int(getattr(self, "truncation_total_saved", 0))

    def _current_tool_context_saving_ops(self) -> int:
        return self._safe_int(getattr(self, "compression_total_ops", 0)) + self._safe_int(getattr(self, "truncation_total_ops", 0))

    def _carried_tool_context_saved_chars(self) -> int:
        return self._safe_int(getattr(self, "context_momentum_compression_saved", 0)) + self._safe_int(getattr(self, "context_momentum_truncation_saved", 0))

    def _carried_tool_context_saving_ops(self) -> int:
        return self._safe_int(getattr(self, "context_momentum_compression_ops", 0)) + self._safe_int(getattr(self, "context_momentum_truncation_ops", 0))

    def _tool_context_saved_chars(self) -> int:
        """Chars kept out of provider context by compression/truncation, including carried handoff momentum."""
        return self._current_tool_context_saved_chars() + self._carried_tool_context_saved_chars()

    def _tool_context_saving_ops(self) -> int:
        return self._current_tool_context_saving_ops() + self._carried_tool_context_saving_ops()

    def _carry_context_saving_stats_for_handoff(self) -> None:
        """Move current-session context savings into handoff momentum counters."""
        self.context_momentum_compression_ops = self._safe_int(getattr(self, "context_momentum_compression_ops", 0)) + self._safe_int(getattr(self, "compression_total_ops", 0))
        self.context_momentum_compression_saved = self._safe_int(getattr(self, "context_momentum_compression_saved", 0)) + self._safe_int(getattr(self, "compression_total_saved", 0))
        self.context_momentum_truncation_ops = self._safe_int(getattr(self, "context_momentum_truncation_ops", 0)) + self._safe_int(getattr(self, "truncation_total_ops", 0))
        self.context_momentum_truncation_saved = self._safe_int(getattr(self, "context_momentum_truncation_saved", 0)) + self._safe_int(getattr(self, "truncation_total_saved", 0))

    def _restore_context_saving_meta(self, meta: dict | None) -> None:
        """Restore context-saving counters from saved session metadata."""
        compression = meta.get("compression") if isinstance(meta, dict) else None
        if not isinstance(compression, dict):
            self.compression_total_ops = 0
            self.compression_total_saved = 0
            self.compression_last_pct = 0
            self.truncation_total_ops = 0
            self.truncation_total_saved = 0
            self.truncation_last_pct = 0
            self.context_momentum_compression_ops = 0
            self.context_momentum_compression_saved = 0
            self.context_momentum_truncation_ops = 0
            self.context_momentum_truncation_saved = 0
            self.session_compaction_total_ops = 0
            self.session_compaction_total_saved = 0
            return
        self.compression_total_ops = self._safe_int(compression.get("total_ops"))
        self.compression_total_saved = self._safe_int(compression.get("total_saved"))
        self.compression_last_pct = self._safe_int(compression.get("last_pct"))
        self.truncation_total_ops = self._safe_int(compression.get("truncation_ops"))
        self.truncation_total_saved = self._safe_int(compression.get("truncation_saved"))
        self.truncation_last_pct = self._safe_int(compression.get("truncation_last_pct"))
        self.context_momentum_compression_ops = self._safe_int(compression.get("momentum_ops"))
        self.context_momentum_compression_saved = self._safe_int(compression.get("momentum_saved"))
        self.context_momentum_truncation_ops = self._safe_int(compression.get("momentum_truncation_ops"))
        self.context_momentum_truncation_saved = self._safe_int(compression.get("momentum_truncation_saved"))
        self.session_compaction_total_ops = self._safe_int(compression.get("session_compaction_ops"))
        self.session_compaction_total_saved = self._safe_int(compression.get("session_compaction_saved"))

    def _cap_tool_result_for_context(self, result: str, *, monitor: BackendMonitor | None = None, tool_name: str = "") -> str:
        """Cap oversized tool output and count the real context savings.

        Structural compression is preferred, but uncompressible large outputs
        still avoid context growth through this fallback cap. Counting it here
        keeps `/usage`, goal-finish, closeout, and session metadata honest.
        """
        text = redact_provider_tokens(str(result or ""))  # strip leaked secret tokens before context/provider
        if _tool_execution_policy(str(tool_name or "").strip().lower()).result_cap_exempt:
            return text  # self-bounded, model-requested data — never severed here
        limit = max(0, int(getattr(self, "tool_result_max_chars", 0) or 0))
        if not limit or len(text) <= limit:
            return text
        marker = "\n[...truncated...]"
        capped = text[:limit] + marker
        saved = max(0, len(text) - len(capped))
        if saved <= 0:
            return capped
        self.truncation_total_saved = max(0, int(getattr(self, "truncation_total_saved", 0) or 0)) + saved
        self.truncation_total_ops = max(0, int(getattr(self, "truncation_total_ops", 0) or 0)) + 1
        pct = round((saved / max(1, len(text))) * 100, 1)
        self.truncation_last_pct = pct
        if monitor:
            monitor.emit("tool_compress", {
                "format": "truncate",
                "before_chars": len(text),
                "after_chars": len(capped),
                "saved_chars": saved,
                "saved_pct": pct,
                "tool": tool_name,
            })
        return capped

    def _project_scoped_tool_arguments(self, name: str, arguments: dict | None) -> dict:
        """Apply the active project cwd to relative/default tool paths.

        Entry points may run from MO's install root while the operator invoked MO
        from another project. The provider should still see normal project-root
        relative paths, and private MO state must not be the accidental target.
        """
        args = dict(arguments or {})
        project_root = Path(getattr(self, "project_cwd", "") or os.getcwd()).expanduser().resolve(strict=False)

        def resolve_value(value: object) -> str:
            text = str(value or "").strip()
            if not text:
                return text
            p = Path(text).expanduser()
            if p.is_absolute():
                return str(p)
            return str((project_root / p).resolve(strict=False))

        if name in {"read_file", "write_file", "edit_file"} and args.get("path"):
            args["path"] = resolve_value(args.get("path"))
        elif name in {"find_files", "grep"}:
            args["root"] = resolve_value(args.get("root") or str(project_root))
        elif name in {"shell", "test_runner", "git_status"}:
            args["workdir"] = resolve_value(args.get("workdir") or str(project_root))
        elif name == "project_bridge":
            args["path"] = resolve_value(args.get("path") or str(project_root))
        return args

    @staticmethod
    def _owner_comparison_source_read_tool(name: str, arguments: dict | None) -> bool:
        """Return True when a tool may inspect OWNER_COMPARISON source roots read-only."""
        if name in {"read_file", "find_files", "grep", "git_status", "project_bridge"}:
            return True
        if name in {"shell", "test_runner"}:
            return not shell_command_is_mutating(str((arguments or {}).get("command") or ""))
        return False

    def _effective_allowed_roots_for_tool(self, user_input: str, name: str, arguments: dict | None) -> list[str] | None:
        """Extend roots for OWNER_COMPARISON source intake and the configured MO control
        workspace without widening write scope.

        Empty/None roots mean UNRESTRICTED (access.mode full) — appending
        anything to them would invert the meaning into "only these paths",
        locking MO out of everything else. Never append to empty roots.
        """
        roots = list(getattr(self, "allowed_roots", None) or [])
        if not roots:
            return roots
        read_like = self._owner_comparison_source_read_tool(name, arguments)
        root_keys = {str(root).casefold() for root in roots}

        def append_root(path: Path) -> None:
            value = str(path.expanduser().resolve(strict=False))
            key = value.casefold()
            if key not in root_keys:
                roots.append(value)
                root_keys.add(key)

        # Owner protocols live outside the public checkout after the layout
        # migration. Add those private roots only when the owner-only activation
        # gates are already true; user clones have neither pack nor owner token.
        #
        # STICKY for the session: a DEVMODE05/IFDEV05 run writes its artifacts to
        # ~/.mo/memory/devmode and ~/.mo/operator across MANY turns, but only the FIRST
        # turn's user_input is the "start DEVMODE05" activation — later turns are
        # continuations or operator follow-ups ("continue", "yes", a mid-run instruction)
        # that don't match the activation phrase. Recomputing the write-path extension
        # per-turn from user_input therefore DROPPED it on those turns and sandbox-blocked
        # edit_file to MO's own runtime dirs (the recurring DEVMODE05 block). Once a
        # write-protocol turn is seen, keep the private runtime write paths for the rest of
        # this agent session so the block can't recur mid-run.
        if is_owner_maintenance_activation(user_input) or is_owner_interface_audit_activation(user_input):
            self._owner_write_paths_active = True
        write_protocol = bool(getattr(self, "_owner_write_paths_active", False))
        if write_protocol or (is_owner_comparison_activation(user_input) and read_like):
            append_root(operator_pack_root())
            append_root(mo_home() / "memory" / "devmode")

        if not read_like:
            return roots
        # The control workspace is operator-configured policy context; the
        # context block advertises it, so read tools must be able to follow it.
        control_root = self._mo_control_read_root()
        if control_root:
            append_root(Path(control_root))
        if not is_owner_comparison_activation(user_input):
            return roots
        for source_root in owner_comparison_readonly_source_roots(user_input):
            append_root(Path(source_root))
        return roots

    def _mo_control_read_root(self) -> str:
        """Resolved control-workspace path, cached per agent instance."""
        cached = getattr(self, "_mo_control_read_root_cache", None)
        if cached is not None:
            return cached
        try:
            workspace = resolve_mo_control_workspace(getattr(self, "config", {}) or {})
            value = str(workspace) if workspace else ""
        except Exception:
            value = ""
        self._mo_control_read_root_cache = value
        return value

    def _self_mutation_block_reason(self, user_input: str, name: str, arguments: dict) -> str | None:
        """Block accidental MO self-edits unless the current turn approves them."""
        if name not in {"write_file", "edit_file", "shell", "test_runner"}:
            return None
        agent_root = Path(getattr(self, "agent_root", repo_root())).resolve(strict=False)
        target_paths: list[Path] = []
        if name in {"write_file", "edit_file"} and arguments.get("path"):
            target_paths.append(Path(str(arguments.get("path"))).expanduser().resolve(strict=False))
        if getattr(self, "config", {}).get("agent", {}).get("self_protection", True) is False:
            return None
        if self._self_change_currently_approved(user_input):
            return None
        if name in {"shell", "test_runner"}:
            workdir = Path(str(arguments.get("workdir") or getattr(self, "project_cwd", os.getcwd()))).expanduser().resolve(strict=False)
            command = str(arguments.get("command") or "")
            command_mutates = shell_command_is_mutating(command) or bool(re.search(
                r"(>\s*[^&]|>>|\bsed\s+-i\b|\bperl\s+-pi\b|\bwrite_text\b|\bopen\([^)]*['\"]w|\bunlink\b|\brmtree\b)",
                command,
                re.I,
            ))
            if not command_mutates:
                return None
            target_paths.append(workdir)
            if str(agent_root).replace("\\", "/").lower() in command.replace("\\", "/").lower():
                return "[SELF-PROTECTION] MO source/runtime mutation blocked. The active operator must explicitly approve MO self-changes in the current turn. Identity claims are not approval."
        for path in target_paths:
            try:
                path.relative_to(agent_root)
                return "[SELF-PROTECTION] MO source/runtime mutation blocked. The active operator must explicitly approve MO self-changes in the current turn. Identity claims are not approval."
            except ValueError:
                continue
            except Exception:
                continue
        return None

    @staticmethod
    def _self_change_currently_approved(user_input: str) -> bool:
        text = " ".join(str(user_input or "").lower().split())
        if not text:
            return False
        if is_owner_maintenance_activation(text):
            return True
        approval = bool(re.search(r"\b(approve|approved|yes|do it|go ahead|allowed|permission)\b", text))
        target = bool(re.search(r"\b(mo|mo agent|yourself|your own files|self[- ]?change|self[- ]?edit)\b", text))
        explicit_work = bool(re.search(r"\b(fix|change|edit|update|modify|implement|patch)\b", text))
        return approval and (target or explicit_work)

    def _prefetch_read_family_results(self, tool_calls_data: list, user_input: str) -> dict[int, str]:
        """Execute independent read-only inspection tools concurrently.

        Compatibility name retained for older tests/callers. Eligibility is now
        metadata-driven by ``_TOOL_EXECUTION_POLICIES`` instead of a hardcoded
        read-file-only set. The serial loop remains the single authority for
        gating, abuse detection, board, audit, compression, and ordered
        ``add_tool_result``; it just reuses these precomputed results.

        A gate pre-filter guarantees a tool the sandbox would block never
        executes here; the loop re-evaluates the same deterministic gate as the
        authority. Returns ``{}`` unless at least two independent tools qualify.
        """
        indices = [
            i for i, tc in enumerate(tool_calls_data)
            if _tool_allows_parallel_prefetch((tc.get("function") or {}).get("name"))
        ]
        if len(indices) < 2:
            return {}
        runnable: dict[int, tuple[str, dict]] = {}
        for i in indices:
            tc = tool_calls_data[i]
            name = tc["function"]["name"]
            args = self._project_scoped_tool_arguments(name, self._parsed_tool_arguments(tc))
            operator_ok = self._operator_approved(user_input, name, args)
            roots = self._effective_allowed_roots_for_tool(user_input, name, args)
            blocked = self._self_mutation_block_reason(user_input, name, args) or guard_tool_call(
                name, args,
                lane=self._active_lane,
                allowed_roots=roots,
                sandbox_config=self.sandbox_config,
                operator_override=operator_ok,
            )
            if not blocked:
                runnable[i] = (name, args)
        if len(runnable) < 2:
            return {}
        results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=min(len(runnable), 8)) as pool:
            futures = {pool.submit(self._dispatch_tool, n, a): i for i, (n, a) in runnable.items()}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as exc:  # mirror _dispatch_tool's own failure contract
                    results[idx] = f"Error executing tool: {exc}"
        return results

    def _dispatch_tool(self, name: str, arguments: dict) -> str:
        """Execute a tool and return the result. Sandbox already approved it."""
        from tools import TOOL_EXECUTORS

        if name == "tool_search":
            executor = getattr(self, "_execute_tool_search", None)
            if callable(executor):
                return executor(arguments or {})

        # Dead-end guard: stop retrying SSH after repeated failures
        ssh_dead = self._check_ssh_dead_end(name, arguments)
        if ssh_dead:
            return ssh_dead

        executor = TOOL_EXECUTORS.get(name)
        if not executor:
            mgr = getattr(self, "mcp_manager", None)
            if mgr is not None and mgr.is_mcp_tool(name):
                result = mgr.call(name, arguments or {})
                max_out = int(self.sandbox_config.get("max_output_chars", 50000) or 50000)
                if len(result) > max_out:
                    result = result[:max_out] + "\n[...output truncated at sandbox limit...]"
                return result
            return f"Error: Unknown tool '{name}'"

        runtime_arguments = dict(arguments or {})
        if name in {"shell", "test_runner"}:
            runtime_arguments["_clean_env"] = bool(self.sandbox_config.get("clean_env", True))

        try:
            result = executor(runtime_arguments)
        except Exception as exc:
            return f"Error executing {name}: {exc}"

        max_out = int(self.sandbox_config.get("max_output_chars", 50000) or 50000)
        if len(result) > max_out:
            result = result[:max_out] + "\n[...output truncated at sandbox limit...]"

        # Track SSH failures for dead-end detection
        if name == "shell":
            cmd = str((arguments or {}).get("command") or "")
            if "ssh" in cmd.lower() or "scp" in cmd.lower() or "ssh-" in cmd.lower():
                self._track_ssh_result(result)

        return result

    def _check_ssh_dead_end(self, name: str, arguments: dict) -> str | None:
        """Return a short-circuit message if SSH has failed too many times this turn."""
        if name != "shell":
            return None
        cmd = str((arguments or {}).get("command") or "")
        if not ("ssh" in cmd.lower() or "scp" in cmd.lower() or "ssh-" in cmd.lower()):
            return None
        limit = int(getattr(self, "_ssh_dead_end_limit", 4))
        failures = int(getattr(self, "_ssh_consecutive_failures", 0))
        if failures >= limit:
            return (
                f"[SSH DEAD-END] SSH has failed {failures} consecutive times this turn "
                f"(connection refused, key rejected, or sandbox blocked). "
                f"Stop retrying SSH — use web_fetch for HTTP checks or report SSH as unavailable."
            )
        return None

    def _track_ssh_result(self, result: str) -> None:
        """Track SSH command results for dead-end detection."""
        is_failure = (
            "[Command completed with exit code 255]" in result
            or "Connection refused" in result
            or "Permission denied" in result
            or "Host key verification failed" in result
            or "Could not resolve" in result
            or "[SSH DEAD-END]" in result
        )
        if is_failure:
            count = int(getattr(self, "_ssh_consecutive_failures", 0)) + 1
            setattr(self, "_ssh_consecutive_failures", count)
        else:
            # Reset on success
            setattr(self, "_ssh_consecutive_failures", 0)

    def _write_tool_audit(self, tool_name: str, arguments: dict, result: str, block_reason: str | None) -> None:
        """Write a redacted tool audit entry to logs/tool_audit.jsonl."""
        try:
            audit_path = self.sandbox_config.get("audit_log")
            if os.environ.get("PYTEST_CURRENT_TEST") and not audit_path:
                return  # Avoid polluting production logs during tests
            audit_path = audit_path or "logs/tool_audit.jsonl"
            log_path = Path(audit_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            safe_args = {}
            for k, v in (arguments or {}).items():
                if k in {"content", "old_text", "new_text"}:
                    safe_args[f"{k}_chars"] = len(str(v or ""))
                else:
                    safe_args[k] = redact_sensitive_text(str(v or "")[:200])
            entry = {
                "ts": time.time(),
                "surface": self._provider_surface(),
                "worker_id": self._provider_worker_id(),
                "tool": tool_name,
                "arguments": safe_args,
                "result_chars": len(str(result or "")),
                "blocked": bool(block_reason),
                "block_reason": str(block_reason or ""),
            }
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _prune_tool_audit_log(log_path)

            if not block_reason:
                self._trigger_prt_adaptive_gate(tool_name, arguments)
                self._check_git_boundary_after_tool(tool_name, arguments, result)
        except Exception:
            traceback.print_exc()

    def _adaptive_compress_min_bytes(self) -> int:
        base = int(getattr(self, "tool_compress_min_bytes", 500) or 500)
        try:
            pressure = context_pressure(self).get("pressure", 0.0)
            if pressure > 0.8:
                base = int(base * 0.6)
            elif pressure > 0.5:
                base = int(base * 0.8)
        except Exception:
            traceback.print_exc()
        avg_size = int(getattr(self, "_avg_tool_result_chars", 0) or 0)
        if avg_size > 10_000:
            base = int(base * 0.7)
        return max(100, min(base, 2_000))

    def _check_git_boundary_after_tool(self, tool_name: str, arguments: dict, result: str) -> None:
        """Emit consistency findings for git commit/push tool boundaries."""
        if tool_name != "shell":
            return
        command = str((arguments or {}).get("command") or "")
        low = command.lower()
        if "git commit" not in low and "git push" not in low:
            return
        proposal_paths = changed_proposal_paths_for_last_commit() if "git commit" in low else []
        self._run_consistency_boundary(
            "commit_push",
            command=command,
            tool_result=result,
            proposal_paths=proposal_paths,
        )

    @staticmethod
    def _operator_approved(user_input: str, tool_name: str, arguments: dict) -> bool:
        """Deterministically decide whether a hard-boundary shell op was requested.

        Private names are never authentication. The current operator must ask for
        the risky action itself in the current turn; the model cannot self-grant
        approval just because it chose a git/deploy command.
        """
        if tool_name != "shell":
            return False
        cmd = str((arguments or {}).get("command", "")).lower()
        text = " ".join(str(user_input or "").lower().split())
        if not cmd or not text:
            return False
        if re.search(r"\b(i am|i'm|im)\s+\w+\b", text) and not re.search(r"\b(push|deploy|release|commit|approve|approved|go ahead|proceed)\b", text):
            return False
        if "git push" in cmd or re.search(r"\bpush\s+(?:to\s+)?(?:origin|github|remote|main|prod|production)\b", cmd):
            return bool(re.search(r"\b(push|publish|release)\b", text) or re.search(r"\b(approve|approved|go ahead|proceed|do it)\b", text))
        if re.search(r"\b(deploy(?:ment)?|release|production|prod|go live|vps|remote)\b", cmd):
            return bool(re.search(r"\b(deploy(?:ment)?|release|production|prod|go live|vps|remote)\b", text) and not re.search(r"\b(do not|don't|dont|no)\b", text))
        if re.search(r"\b(secret|credential|private key|token|bearer|wallet|billing|payment)\b", cmd):
            return bool(re.search(r"\b(approve|approved|go ahead|proceed|do it)\b", text) and re.search(r"\b(secret|credential|key|token|wallet|billing|payment)\b", text))
        if re.search(r"\b(reset --hard|git clean|force[- ]?push|delete repo|drop table|truncate)\b", cmd):
            return bool(re.search(r"\b(approve|approved|go ahead|proceed|do it)\b", text) and re.search(r"\b(reset|clean|force[- ]?push|delete|drop|truncate)\b", text))
        return False
