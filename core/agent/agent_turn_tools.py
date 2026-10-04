"""Tool-batch validation and gated dispatch for one agent turn."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Any

from .. import local_extensions
from ..runtime.backend_monitor import monitor_phase
from ..gates.post_provider_pipeline import _CONTINUE
from ..tasking.agent_taskboard import TASKBOARD_CONTROL_TOOLS
from ..tasking.task_evidence import (
    STALE_VERIFICATION_MARKER,
    shell_delivery_evidence_actions,
    tool_is_editing_shell,
    tool_is_inspection_shell,
    tool_is_verification_signal,
)
from ..tasking.results import (
    TaskTransitionResult,
    ToolExecutionRecord,
    taskboard_position,
)
from ..session.session import response_replay_metadata
from ..tooling.sandbox import (
    desktop_recipe_is_point_only,
    guard_tool_call as _guard_tool_call,
    redact_sensitive_text,
    shell_command_changes_indexed_project_state,
    shell_command_is_git_delivery_only,
)
from ..tooling.tool_constants import ACTUATION_TOOLS
from .agent_turn_state import TurnState, emit_security_check
from .agent_utils import _call_on_first_tool, _emit_task_board_update


_EXPLICIT_GRAPH_BUILD_RE = re.compile(
    r"(?i)\b(?:build|rebuild|refresh|update|regenerate)\b[^.?!\n]{0,48}"
    r"\b(?:code\s+|structural\s+)?graph\b"
    r"|\b(?:code\s+|structural\s+)?graph\b[^.?!\n]{0,48}"
    r"\b(?:build|rebuild|refresh|update|regenerate)\b"
)


def _explicit_graph_build_requested(user_input: str) -> bool:
    return bool(_EXPLICIT_GRAPH_BUILD_RE.search(str(user_input or "")))


_GRAPH_ACTIVITY_TOOLS = {
    "build_graph",
    "code_search",
    "find_callers",
    "find_callees",
    "graph_explain",
    "graph_neighbors",
    "graph_path",
    "graph_stats",
}

_MAX_CONSECUTIVE_PLAN_ONLY_REVISIONS = 2


def _tool_batch_is_plan_only_revision(tool_calls_data: list[dict[str, Any]]) -> bool:
    """Return whether a batch revises the plan without doing subject work.

    Exact batch signatures cannot catch a provider that changes row wording on
    every request. Catalog discovery may accompany a revision, but it does not
    inspect, change, verify, or complete the requested subject and therefore
    cannot make repeated plan rewriting productive.
    """
    if not tool_calls_data:
        return False
    saw_revision = False
    for item in tool_calls_data:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            return False
        name = str(function.get("name") or "")
        if name == "tool_search":
            continue
        if name != "set_plan":
            return False
        try:
            arguments = json.loads(str(function.get("arguments") or "{}"))
        except (TypeError, ValueError):
            return False
        if not isinstance(arguments, dict) or str(arguments.get("mode") or "start").strip().lower() != "revise":
            return False
        saw_revision = True
    return saw_revision


def _tool_search_activation_state(result: object) -> bool | None:
    """Return True for new schemas, False for already-active matches, else None."""
    try:
        payload = json.loads(str(result or ""))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("activated"), list):
        return None
    if payload["activated"]:
        return True
    already_active = payload.get("already_active")
    if isinstance(already_active, list) and already_active:
        return False
    return None


def _graph_activity_result_hint(tool_name: str, result: object) -> str:
    """Return bounded numeric graph evidence for the operator activity row."""
    name = str(tool_name or "").strip().lower()
    text = str(result or "")
    if name not in _GRAPH_ACTIVITY_TOOLS or not text:
        return ""
    if re.match(r"\s*(?:error|\[aborted\]|\[cancelled\])", text, re.IGNORECASE):
        return ""
    if re.search(r"\bno (?:code-graph matches|callers|callees|mo graph node|mo graph path)\b", text, re.IGNORECASE):
        return "0 results"
    if name in {"code_search", "find_callers", "find_callees"}:
        count = sum(1 for line in text.splitlines() if line.lstrip().startswith("- "))
        return f"{count} result{'s' if count != 1 else ''}" if count else ""
    if name == "build_graph":
        match = re.search(r"(\d+) nodes?,\s*(\d+) edges?", text, re.IGNORECASE)
        return f"{match.group(1)} nodes · {match.group(2)} edges" if match else ""
    if name == "graph_neighbors":
        match = re.search(r"returned_edges:\s*(\d+)", text, re.IGNORECASE)
        return f"{match.group(1)} edges" if match else ""
    if name == "graph_path":
        match = re.search(r"- hops:\s*(\d+)", text, re.IGNORECASE)
        return f"{match.group(1)} hops" if match else ""
    if name in {"graph_explain", "graph_stats"}:
        match = re.search(r"- (?:degree|nodes):\s*(\d+)", text, re.IGNORECASE)
        noun = "degree" if name == "graph_explain" else "nodes"
        return f"{noun} {match.group(1)}" if match else ""
    return ""


def _foreground_graph_arguments(
    tool_name: str,
    user_input: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Move unrequested graph maintenance off the foreground turn."""
    if tool_name != "build_graph" or _explicit_graph_build_requested(user_input):
        return arguments
    normalized = dict(arguments)
    normalized["_mo_background"] = True
    return normalized


def _tool_guard(*args: Any, **kwargs: Any) -> str:
    """Honor the established ``agent_turn.guard_tool_call`` patch seam."""
    facade = sys.modules.get("core.agent.agent_turn")
    guard = getattr(facade, "guard_tool_call", _guard_tool_call)
    return str(guard(*args, **kwargs) or "")


def _tool_invalidates_verification_reuse(tool_name: str, arguments: dict) -> bool:
    """Return whether an executed tool may make prior test evidence stale."""
    name = str(tool_name or "").strip().lower()
    if name in {"write_file", "edit_file"}:
        return True
    shell_name = "shell" if name == "test_runner" else name
    if tool_is_editing_shell(shell_name, arguments or {}):
        return True
    if name not in {"shell", "test_runner"}:
        return False
    # An arbitrary executable may mutate the checkout without containing a
    # recognizable shell write verb. Preserve proof only for commands whose
    # verification or read-only semantics are established by existing owners.
    return not (
        tool_is_verification_signal("shell", arguments or {})
        or tool_is_inspection_shell("shell", arguments or {})
    )


def _is_test_overlay_manifest_repair(tool_name: str, arguments: dict) -> bool:
    """Recognize the executed manifest writer, not a quoted mention of it."""
    if str(tool_name or "").strip().lower() not in {"shell", "test_runner"}:
        return False
    from tools.shell import _test_invocation

    module, options = _test_invocation(str((arguments or {}).get("command") or ""))
    return module == "core.diagnostics.test_preflight" and "--write-overlay-manifest" in options


def _tool_requires_project_index_maintenance(tool_name: str, arguments: dict) -> bool:
    """Return whether a successful call may change indexed project evidence."""
    name = str(tool_name or "").strip().lower()
    if name in {"write_file", "edit_file"}:
        return True
    if name not in {"shell", "test_runner"}:
        return False
    if tool_is_verification_signal("shell", arguments or {}) or tool_is_inspection_shell(
        "shell", arguments or {},
    ):
        return False
    # Repairing the private ignored-test manifest intentionally invalidates a
    # cached preflight failure, but it cannot change graph/history/knowledge.
    if _is_test_overlay_manifest_repair(name, arguments):
        return False
    return shell_command_changes_indexed_project_state(
        str((arguments or {}).get("command") or ""),
    )


def _verification_candidate_snapshot(project_cwd: str) -> tuple[str, dict[str, tuple[int, int, int]]] | None:
    """Capture working-file identity without reading file contents into Python.

    Git delivery changes refs/index state, so a diff against HEAD cannot prove
    whether the tested working files changed.  Paths plus nanosecond mtime,
    ctime, and size preserve that distinction and also catch hook edits.
    """
    import subprocess

    from ..diagnostics.source_inventory import discover_source_paths
    from ..runtime.subprocess_flags import apply_windows_hidden_process_flags

    try:
        cwd = Path(project_cwd or ".").expanduser().resolve(strict=False)
        options = {
            "cwd": str(cwd), "capture_output": True, "text": True,
            "encoding": "utf-8", "errors": "replace", "timeout": 10,
        }
        apply_windows_hidden_process_flags(options)
        resolved = subprocess.run(["git", "rev-parse", "--show-toplevel"], **options)
        if resolved.returncode != 0 or not str(resolved.stdout or "").strip():
            return None
        root = Path(str(resolved.stdout).strip()).resolve(strict=False)
        files: dict[str, tuple[int, int, int]] = {}
        for relative, _origin in discover_source_paths(root, include_test_overlay=True):
            stat = (root / relative).stat()
            files[relative.replace("\\", "/")] = (
                int(stat.st_mtime_ns), int(stat.st_ctime_ns), int(stat.st_size),
            )
        return str(root), files
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return None


def _verification_candidate_changes(
    before: tuple[str, dict[str, tuple[int, int, int]]] | None,
    after: tuple[str, dict[str, tuple[int, int, int]]] | None,
) -> set[str] | None:
    """Return changed candidate paths, or ``None`` when identity is unproven."""
    if before is None or after is None or before[0] != after[0]:
        return None
    old, new = before[1], after[1]
    return {path for path in old.keys() | new.keys() if old.get(path) != new.get(path)}


_OVERLAY_PREFLIGHT_FAILURE_MARKERS = (
    "[preflight] missing .mo-test-overlay.json",
    "[preflight] invalid .mo-test-overlay.json",
    "[preflight] overlay source check failed",
    "[preflight] stale test overlay",
    "[preflight] test overlay changed without manifest refresh",
)


def _invalidate_repaired_overlay_failures(
    state: TurnState,
    verification_results: dict[str, str],
) -> None:
    """Forget only proof made obsolete by a successful overlay-manifest repair."""
    for key, value in list(verification_results.items()):
        text = str(value or "")
        lowered = text.lower()
        if "[running in background" in lowered:
            if STALE_VERIFICATION_MARKER not in text:
                verification_results[key] = text + "\n" + STALE_VERIFICATION_MARKER
            continue
        if any(marker in lowered for marker in _OVERLAY_PREFLIGHT_FAILURE_MARKERS):
            verification_results.pop(key, None)
    state.final_gates_fired.discard("verify_edits")


def _verification_preserving_scratch_cleanup(
    arguments: dict,
    turn_modified_files: list[tuple[str, str]],
    project_root: str,
    verification_results: dict[str, str] | None = None,
) -> Path | None:
    """Identify one literal removal of this turn's Git-ignored scratch only.

    Unknown or compound shell commands retain normal mutation invalidation.
    This establishes only the cleanup command's scope, not candidate freshness.
    """
    import subprocess
    from .agent_turn_dispatch import _decode_verification_target_reuse_key
    from ..tooling.sandbox import _split_shell_words
    from ..tooling.scratch import live_project_scratch_paths

    command = str(arguments.get("command") or "").strip()
    if not project_root or not command or re.search(r"[;&|<>`$%^!@*?\[\]{}()\r\n]", command):
        return None
    tokens = [part.strip("\"'") for part in _split_shell_words(command)]
    if len(tokens) < 2:
        return None
    name = tokens[0].casefold()
    flags = {
        "rmdir": {"/s", "/q", "--"},
        "rd": {"/s", "/q"},
        "rm": {"-r", "-f", "-rf", "-fr", "--recursive", "--force", "--"},
        "remove-item": {"-literalpath", "-recurse", "-force"},
    }.get(name)
    if flags is None or (name == "remove-item" and "-literalpath" not in {part.casefold() for part in tokens}):
        return None
    paths = [part for part in tokens[1:] if part.casefold() not in flags]
    if len(paths) != 1 or paths[0].startswith("-"):
        return None
    try:
        root = Path(project_root).expanduser().resolve(strict=False)
        workdir = Path(arguments.get("workdir") or root).expanduser().resolve(strict=False)
        scratch_root = (root / "tmp").resolve(strict=False)
        literal_target = workdir / paths[0]
        if literal_target.is_symlink() or getattr(literal_target, "is_junction", lambda: False)():
            return None
        target = literal_target.resolve(strict=False)
        if target == scratch_root or not target.is_relative_to(scratch_root):
            return None
        if not scratch_root.is_relative_to(root) or not target.exists():
            return None
        for key, value in (verification_results or {}).items():
            decoded = _decode_verification_target_reuse_key(key)
            if decoded is not None:
                if any(Path(path).is_relative_to(target) for path in decoded[1]):
                    return None
                continue
            if STALE_VERIFICATION_MARKER in value:
                continue
            if key.startswith("verification-family:"):
                proof_root = key.split(":", 3)[-1]
            elif key.startswith("automatic-affected-tests:"):
                proof_root = key.partition(":")[2].rpartition(":")[0]
            else:
                return None  # Unknown verification inputs cannot exclude scratch.
            proof_path = Path(proof_root).resolve(strict=False)
            if target.is_relative_to(proof_path) or proof_path.is_relative_to(target):
                return None
        owned = {root / path for path in live_project_scratch_paths(root, turn_modified_files)}
        if not any(path == target or path.is_relative_to(target) for path in owned):
            return None
        entries = [target] if target.is_file() else target.rglob("*")
        removed_files = []
        for entry in entries:
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                return None
            if entry.is_file():
                if entry not in owned:
                    return None
                removed_files.append(entry.relative_to(root).as_posix())
        if not removed_files:
            return None
        from ..runtime.subprocess_flags import apply_windows_hidden_process_flags

        options = {
            "cwd": str(root), "capture_output": True, "timeout": 10,
            "input": ("\0".join(removed_files) + "\0").encode("utf-8"),
        }
        apply_windows_hidden_process_flags(options)
        ignored = subprocess.run(["git", "check-ignore", "--stdin", "-z"], **options)
        ignored_paths = set(ignored.stdout.decode("utf-8").rstrip("\0").split("\0"))
        return target if ignored.returncode == 0 and ignored_paths == set(removed_files) else None
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError):
        return None


def _invalidate_verification_epoch(
    state: TurnState,
    verification_results: dict[str, str],
    *,
    changed_path: str = "",
) -> None:
    """Drop reusable proof without erasing work that is still running."""
    from .agent_turn_dispatch import _decode_verification_target_reuse_key

    changed = Path(changed_path).expanduser().resolve(strict=False) if changed_path else None
    for key, value in list(verification_results.items()):
        root = ""
        if key.startswith("verification-family:"):
            root = key.split(":", 3)[-1]
        elif key.startswith("automatic-affected-tests:"):
            root = key.partition(":")[2].rpartition(":")[0]
        else:
            decoded = _decode_verification_target_reuse_key(key)
            if decoded is not None:
                root = decoded[0]
        # A private report or a file in a different project does not change the
        # tested checkout. Unknown/custom command scopes remain conservative.
        if changed is not None and root and not changed.is_relative_to(Path(root).resolve(strict=False)):
            continue
        if "[Running in background" in value:
            if STALE_VERIFICATION_MARKER not in value:
                verification_results[key] = value + "\n" + STALE_VERIFICATION_MARKER
        else:
            verification_results.pop(key, None)
    state.final_gates_fired.discard("verify_edits")
    state.final_gates_fired.discard("lsp_diagnostics")
    state.project_change_context = None


def _tool_result_is_pending_background(tool_name: str, result: object) -> bool:
    """Return whether a tool result represents launched, unfinished work."""
    return (
        str(tool_name or "").strip().lower() in {"shell", "test_runner"}
        and "[Running in background" in str(result or "")
    )


def _should_request_action_confirmation(tool_name: str, block_reason: str | None) -> bool:
    """Ask for high-impact approval only when every earlier gate can execute."""
    return str(tool_name or "") in ACTUATION_TOOLS and not block_reason


def _is_visual_computer_perception_tool(tool_name: str, arguments: dict[str, Any]) -> bool:
    name = str(tool_name or "").strip().lower()
    if name == "computer_act":
        return str((arguments or {}).get("kind") or "desktop").strip().lower() == "desktop"
    if name != "computer_observe":
        return False
    kind = str((arguments or {}).get("kind") or "desktop").strip().lower().replace("-", "_")
    operation = str((arguments or {}).get("operation") or "").strip().lower().replace("-", "_")
    return kind == "screen" or (kind == "browser" and operation == "capture")


_IMAGE_REPLAY_NOTE = (
    "[IMAGE OUTPUT NOT REPLAYED] The original tool result included image data "
    "that was already delivered on the previous tool output. MO replayed only "
    "the text portion to avoid retaining or duplicating base64 image payloads."
)


def _json_tool_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(
        dict(arguments or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class AgentTurnToolLoopMixin:
    """Validate tool batches and dispatch every call through the single gate."""

    def _project_rule_snapshots_for_turn(self):
        """Return the per-turn rule-chain ledger, seeding the active project."""
        from ..context.project_context import project_rule_snapshot_key

        snapshots = getattr(self, "_project_rule_snapshots", None)
        if not isinstance(snapshots, dict):
            snapshots = {}
            self._project_rule_snapshots = snapshots
        primary = getattr(self, "_project_rule_snapshot", None)
        if primary is not None and all(
            hasattr(primary, field) for field in ("project_root", "files", "digests")
        ):
            snapshots.setdefault(project_rule_snapshot_key(primary), primary)
        return snapshots

    def _remember_project_rule_snapshot(self, snapshot, *, previous=None):
        """Replace one reviewed chain without creating a second rules owner."""
        if snapshot is None or not all(
            hasattr(snapshot, field) for field in ("project_root", "files", "digests")
        ):
            return snapshot
        from ..context.project_context import project_rule_snapshot_key

        snapshots = self._project_rule_snapshots_for_turn()
        if previous is not None:
            snapshots.pop(project_rule_snapshot_key(previous), None)
        snapshots[project_rule_snapshot_key(snapshot)] = snapshot
        primary = getattr(self, "_project_rule_snapshot", None)
        if primary is previous or (
            primary is not None
            and previous is not None
            and project_rule_snapshot_key(primary) == project_rule_snapshot_key(previous)
        ):
            self._project_rule_snapshot = snapshot
        return snapshot

    @staticmethod
    def _target_project_rule_scope(name: str, arguments: dict[str, Any]) -> Path | None:
        """Return the filesystem scope established by one canonical tool call."""
        if name in {"read_file", "write_file", "edit_file"}:
            value = arguments.get("path")
            if not value:
                return None
            return Path(str(value)).expanduser().resolve(strict=False).parent
        if name in {"find_files", "grep"}:
            value = arguments.get("root")
            if not value:
                return None
            path = Path(str(value)).expanduser().resolve(strict=False)
            return path.parent if path.is_file() else path
        if name in {"shell", "test_runner", "git_status"}:
            value = arguments.get("workdir")
            if not value:
                return None
            return Path(str(value)).expanduser().resolve(strict=False)
        return None

    def _defer_unreviewed_project_rules(
        self,
        state: TurnState,
        tool_calls_data: list[dict[str, Any]],
        provider_request: int,
    ) -> bool:
        """Deliver every newly targeted project's rules before its batch runs."""
        # Direct dispatcher tests and internal callers do not own a provider turn.
        # run_turn explicitly activates this preflight before provider/tool work.
        if not bool(getattr(self, "_project_rule_preflight_active", False)):
            return False
        from dataclasses import replace
        from ..context.project_context import (
            project_rule_scope_is_project,
            project_rule_snapshot_key,
            project_rules_changed,
            render_project_rule_context,
            resolve_project_rules,
        )
        from ..graph.structural_graph import build_project_orientation, project_root
        from ..state.paths import mo_home
        from ..tooling.sandbox import path_allowed

        snapshots = self._project_rule_snapshots_for_turn()
        active_root = project_root(
            self._effective_project_cwd()
            if hasattr(self, "_effective_project_cwd")
            else getattr(self, "project_cwd", None)
        )
        delivered = []
        orientation_roots = set()
        for item in tool_calls_data:
            function = item.get("function") if isinstance(item, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if name == "project_bridge":
                continue
            arguments = self._parsed_tool_arguments(item)
            scope = self._target_project_rule_scope(name, arguments)
            if scope is None:
                continue
            current = resolve_project_rules(scope)
            if (
                getattr(self, "_project_rule_snapshot", None) is None
                and current.project_root == active_root
            ):
                continue
            if not project_rule_scope_is_project(current):
                continue
            key = project_rule_snapshot_key(current)
            previous = snapshots.get(key)
            if previous is not None and not project_rules_changed(previous, current):
                continue
            if previous is not None:
                current = replace(
                    current,
                    created=previous.created,
                    creation_error=previous.creation_error,
                    rechecks=previous.rechecks + 1,
                )
            self._remember_project_rule_snapshot(current, previous=previous)
            delivered.append(current)
            if (
                current.project_root != active_root
                and not current.project_root.is_relative_to(mo_home() / "personal")
                and path_allowed(
                    str(current.project_root),
                    self._effective_allowed_roots_for_tool(state.user_input, name, arguments),
                )
            ):
                orientation_roots.add(current.project_root)

        if not delivered:
            return False
        blocks = []
        intent = self._turn_intent_for(state.user_input)
        query, _source = self._context_query_for(state.user_input, intent)
        orientation_budget = 3000 // max(1, len(orientation_roots))
        for snapshot in delivered:
            blocks.append(
                f"### Target project: {snapshot.project_root}\n"
                + render_project_rule_context(snapshot)
            )
            if (
                snapshot.project_root in orientation_roots
                and intent.include_code_graph_context
                and self._provider_surface() not in {"mo_desktop", "companion"}
            ):
                orientation = build_project_orientation(
                    query, cwd=str(snapshot.project_root),
                    profile=getattr(self, "profile", None),
                    max_chars=orientation_budget, build_if_missing=False,
                )
                blocks.extend(orientation.values())
                orientation_roots.remove(snapshot.project_root)
        self.session.add_assistant(
            "[TARGET PROJECT RULE REVIEW] The requested tool batch was deferred before "
            "execution because it newly targeted the project contract(s) below. Review "
            "these rules, then reissue only the still-needed calls. None of the deferred "
            "calls ran.\n\n" + "\n\n".join(blocks)
        )
        self.session.mark_last_assistant_internal()
        if state.monitor:
            state.monitor.emit(
                "project_rule_target_review",
                {
                    "request": provider_request,
                    "project_count": len(delivered),
                    "projects": [str(snapshot.project_root) for snapshot in delivered],
                },
            )
        if state.on_activity:
            state.on_activity("target project rules loaded; reviewing before tools run...")
        return True

    def _create_project_rules_for_tool(self, user_input, name, arguments):
        """Materialize the reviewed target starter only for permitted project work."""
        from ..context.project_context import project_rule_snapshot_key, resolve_project_rules

        scope = self._target_project_rule_scope(name, arguments)
        if scope is None:
            return None
        current = resolve_project_rules(scope)
        snapshot = self._project_rule_snapshots_for_turn().get(project_rule_snapshot_key(current))
        if snapshot is None or snapshot.files or snapshot.created or snapshot.creation_error:
            return None
        root = snapshot.project_root
        if name in {"write_file", "edit_file"}:
            target = Path(arguments.get("path") or root)
        elif name in {"shell", "test_runner"} and (
            _tool_invalidates_verification_reuse(name, arguments)
            or tool_is_verification_signal(name, arguments)
        ):
            target = Path(arguments.get("workdir") or snapshot.scope_path)
        else:
            return None
        target = (snapshot.scope_path / target).resolve(strict=False)
        if not target.is_relative_to(root) or target == root / "AGENTS.md":
            return None

        from dataclasses import replace
        from ..context.project_context import discover_project_context_files
        from ..context.project_docs import AGENTS_STARTER, create_project_rule_starter

        # A concurrently supplied contract belongs to its author. The final
        # recheck will deliver it; never replace it with the generic starter.
        if discover_project_context_files(root):
            return None
        role_tools = self._role_allowed_tool_names()
        extension_tools = local_extensions.tool_allowlist(self, user_input)
        write_args = {"path": str(root / "AGENTS.md"), "content": AGENTS_STARTER}
        denied = (
            ("write_file is unavailable in this role" if role_tools is not None and "write_file" not in role_tools else "")
            or ("write_file is unavailable in this workflow" if extension_tools is not None and "write_file" not in extension_tools else "")
            or local_extensions.tool_block_reason(self, user_input, "write_file", write_args)
            or _tool_guard(
                "write_file", write_args, lane=self._effective_lane(),
                allowed_roots=self._effective_allowed_roots_for_tool(user_input, "write_file", write_args),
                sandbox_config=self.sandbox_config,
            )
        )
        if denied:
            self._remember_project_rule_snapshot(
                replace(snapshot, creation_error=str(denied)), previous=snapshot,
            )
            return None
        try:
            report = create_project_rule_starter(root)
        except (OSError, ValueError) as exc:
            self._remember_project_rule_snapshot(
                replace(snapshot, creation_error=type(exc).__name__), previous=snapshot,
            )
            return None
        updated = replace(snapshot, created=report.created)
        self._remember_project_rule_snapshot(updated, previous=snapshot)
        if report.created:
            current = resolve_project_rules(snapshot.scope_path)
            # The provider already received this exact starter preview. Adopt
            # only those bytes; unexpected concurrent rules still need review.
            if current.files == report.created and len(current.contents) == 1 and current.contents[0].content == AGENTS_STARTER.strip():
                self._remember_project_rule_snapshot(
                    replace(current, created=report.created), previous=updated,
                )
        return report.created[0] if report.created else None

    def _prepare_effective_tool_arguments(
        self,
        state: TurnState,
        tool_calls_data: list[dict[str, Any]],
    ) -> None:
        for tc_data in tool_calls_data:
            fn = tc_data.get("function") if isinstance(tc_data, dict) else None
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "")
            if not name:
                continue
            arguments = self._project_scoped_tool_arguments(name, self._parsed_tool_arguments(tc_data))
            arguments = local_extensions.normalize_tool_arguments(self, state.user_input, name, arguments)
            arguments = _foreground_graph_arguments(name, state.user_input, arguments)
            fn["arguments"] = _json_tool_arguments(arguments)

    @staticmethod
    def _response_items_with_tool_arguments(
        response_items: list[Any],
        tool_calls_data: list[dict[str, Any]],
    ) -> list[Any]:
        arguments_by_call_id: dict[str, str] = {}
        for tc_data in tool_calls_data:
            if not isinstance(tc_data, dict):
                continue
            call_id = str(tc_data.get("id") or "")
            fn = tc_data.get("function") or {}
            if call_id and isinstance(fn, dict):
                arguments_by_call_id[call_id] = str(fn.get("arguments") or "{}")
        if not arguments_by_call_id:
            return list(response_items)

        normalized_items: list[Any] = []
        for item in response_items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            if str(item.get("type") or "") != "function_call":
                normalized_items.append(dict(item))
                continue
            call_id = str(item.get("call_id") or item.get("id") or "")
            arguments = arguments_by_call_id.get(call_id)
            if arguments is None:
                normalized_items.append(dict(item))
                continue
            updated = dict(item)
            try:
                original = json.loads(item.get("arguments"))
                unchanged = isinstance(original, dict) and _json_tool_arguments(original) == arguments
            except (TypeError, ValueError):
                unchanged = False
            # Preserve generated bytes unless dispatch changed their meaning.
            if not unchanged:
                updated["arguments"] = arguments
            normalized_items.append(updated)
        return normalized_items

    def _report_repeated_tool_batch_stop(
        self,
        state: TurnState,
        final_text: str,
        provider_request: int,
        *,
        kind: str,
        repeat_count: int,
        action: str = "stop_exact_repeat",
    ) -> str:
        marker = "[REPEATED TOOL BATCH BLOCKED]"
        if not str(final_text or "").startswith(marker):
            final_text = f"{marker} {str(final_text or '').strip()}".rstrip()
        active_task = state.task_board.active_task_id() if state.task_board else None
        if state.monitor:
            state.monitor.emit(
                "tool_call_rejected",
                {
                    "kind": kind,
                    "request": provider_request,
                    "action": action,
                    "active_task": active_task or "",
                    "repeat_count": repeat_count,
                },
            )
        self.session.add_assistant(final_text)
        emit_security_check(state.turn_modified_files, state.monitor)
        return final_text

    def _recover_blocked_tool_batch(
        self,
        state: TurnState,
        tool_calls_data: list[dict[str, Any]],
        provider_request: int,
        *,
        instruction: str = "",
    ) -> str | object:
        if not state.blocked_tool_batch_guided:
            state.blocked_tool_batch_guided = True
            if state.monitor:
                state.monitor.emit(
                    "provider_retry",
                    {
                        "request": provider_request,
                        "provider": self.provider_name,
                        "reason": "blocked_tool_batch_repeat",
                        "action": "guide_from_exact_blocker",
                    },
                )
            self.session.add_assistant(
                instruction or self._blocked_tool_recovery_instruction(
                    tool_calls_data, state.blocked_tool_batch_reason,
                )
            )
            self.session.mark_last_assistant_internal()
            return _CONTINUE
        final_text = (
            state.blocked_tool_batch_reason
            or "The requested tool batch was blocked before execution."
        )
        if state.monitor:
            state.monitor.emit(
                "tool_call_rejected",
                {
                    "kind": "blocked_tool_batch_repeat",
                    "request": provider_request,
                    "action": "report_exact_blocker",
                },
            )
        self._record_runtime_diagnostic(
            "tool_blocked",
            "the provider repeated an identical batch that had not executed",
            request=provider_request,
            detail=final_text,
        )
        return self._report_repeated_tool_batch_stop(
            state, final_text, provider_request,
            kind="blocked_tool_batch_repeat", repeat_count=2,
        )

    def _replay_completed_tool_batch(
        self,
        state: TurnState,
        tool_calls_data: list[dict[str, Any]],
        provider_request: int,
    ) -> str | object | None:
        """Attach cached batch results to fresh repeated tool-call ids."""
        batch_signature = self._tool_call_batch_signature(tool_calls_data)
        cached_results = list(getattr(state, "completed_tool_batch_results", None) or [])
        if (
            not batch_signature
            or batch_signature != getattr(state, "completed_tool_batch_signature", "")
            or len(cached_results) != len(tool_calls_data)
        ):
            return None

        verification_results = self._verification_results_for_state(state)
        refreshed_results: list[dict[str, object]] = []
        pending_background = False
        for tc_data, cached in zip(tool_calls_data, cached_results):
            result = str(cached.get("result") or "")
            name = str((tc_data.get("function") or {}).get("name") or "")
            if _tool_result_is_pending_background(name, result):
                arguments = self._parsed_tool_arguments(tc_data)
                verification_key = self._verification_reuse_key(name, arguments)
                if state.on_activity:
                    state.on_activity("waiting for the existing verification result")
                self._wait_for_background_verification(
                    verification_key, verification_results, arguments,
                    cancel_event=state.cancel_event,
                )
                result = str(verification_results.get(verification_key) or result)
                pending_background = pending_background or _tool_result_is_pending_background(name, result)
            refreshed = dict(cached)
            refreshed["result"] = result
            refreshed_results.append(refreshed)
            if cached.get("had_images"):
                result = f"{result}\n{_IMAGE_REPLAY_NOTE}".strip()
            self.session.add_tool_result(str(tc_data.get("id") or ""), result)
        state.completed_tool_batch_results = refreshed_results

        if pending_background:
            state.completed_tool_batch_replay_count = 0
            if state.on_activity:
                state.on_activity("reused running verification; awaiting its terminal result")
            return _CONTINUE

        state.completed_tool_batch_replay_count += 1
        if state.on_activity:
            state.on_activity("replayed completed tool results for repeated request")
        if state.monitor:
            state.monitor.emit(
                "tool_batch_replayed",
                {
                    "request": provider_request,
                    "surface": self._provider_surface(),
                    "worker_id": self._provider_worker_id(),
                    "tools": [
                        str((item.get("function") or {}).get("name") or "")
                        for item in tool_calls_data
                    ],
                    "repeat_count": state.completed_tool_batch_replay_count,
                },
            )
        self._record_runtime_diagnostic(
            "completed_tool_batch_replayed",
            "the provider repeated an exact completed tool batch; MO replayed cached outputs on fresh call ids",
            request=provider_request,
            detail=f"repeat_count={state.completed_tool_batch_replay_count}",
        )
        doom_threshold = max(2, int(getattr(self, "doom_loop_tool_batch_threshold", 3) or 3))
        if state.completed_tool_batch_replay_count == doom_threshold - 1:
            if state.monitor:
                state.monitor.emit(
                    "provider_retry",
                    {
                        "request": provider_request,
                        "provider": self.provider_name,
                        "reason": "completed_tool_batch_repeat",
                        "action": "guide_from_existing_result",
                    },
                )
            self.session.add_assistant(
                "[REPEATED TOOL RECOVERY] The previous identical tool batch already "
                "completed, and MO attached its cached result to this fresh call. Do "
                "not request that exact batch again. Continue from the existing result "
                "or use different arguments."
            )
            self.session.mark_last_assistant_internal()
            return _CONTINUE
        if state.completed_tool_batch_replay_count < doom_threshold:
            return _CONTINUE

        final_text = (
            "MO preserved the completed tool result and left the active task open after "
            "the same action was requested repeatedly. It did not rerun the action."
        )
        return self._report_repeated_tool_batch_stop(
            state,
            final_text,
            provider_request,
            kind="completed_tool_batch_replay_exhausted",
            repeat_count=state.completed_tool_batch_replay_count,
        )

    def _prepare_tool_batch(
        self,
        state: TurnState,
        response: Any,
        finish_reason: str,
        provider_request: int,
    ) -> list[dict[str, Any]] | str | object:
        """Validate and record one provider tool-call batch before execution."""
        # Record the assistant's tool call request
        tool_calls_data = []
        for tc in response.tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                args = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
                tid = tc.get("id", "")
            else:
                fn = getattr(tc, "function", tc)
                name = getattr(fn, "name", "") if hasattr(fn, "name") else fn.get("name", "")
                args = getattr(fn, "arguments", "{}") if hasattr(fn, "arguments") else fn.get("arguments", "{}")
                tid = getattr(tc, "id", "") if hasattr(tc, "id") else tc.get("id", "")
            tool_calls_data.append(
                {
                    "id": tid,
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            )

        unoffered = sorted(
            {
                str(item.get("function", {}).get("name") or "")
                for item in tool_calls_data
                if str(item.get("function", {}).get("name") or "")
                not in state.offered_tool_names
            }
        )
        if unoffered and "tool_search" in state.offered_tool_names:
            # Routing is a schema working set, not tool authority. A provider
            # may remember a tool from an earlier turn. Use normal discovery
            # and its exact route/role filters before normal gated dispatch.
            for name in unoffered:
                self._execute_tool_search({"tools": [name]})
            available = {
                self._tool_definition_name(definition)
                for definition in self._provider_tool_definitions()
            }
            unoffered = [name for name in unoffered if name not in available]
        if unoffered:
            if state.monitor:
                state.monitor.emit(
                    "tool_call_rejected",
                    {
                        "request": provider_request,
                        "reason": "unoffered",
                        "tools": unoffered[:12],
                        "offered_count": len(state.offered_tool_names),
                    },
                )
            self._record_runtime_diagnostic(
                "tool_call_rejected",
                "a provider tool call rejected because the tool was not offered in that request",
                request=provider_request,
                detail="reason=unoffered",
                tool=unoffered[0],
            )
            # Reuse blocked-batch recovery. The scope prefix distinguishes an
            # unavailable call from that same call becoming admitted later.
            signature = "unoffered:" + self._tool_call_batch_signature(
                tool_calls_data, include_task_progress=True,
            )
            if signature != state.blocked_tool_batch_signature:
                state.clear_blocked_tool_batch()
                state.blocked_tool_batch_signature = signature
                state.blocked_tool_batch_reason = (
                    f"Tools not offered for this request: {', '.join(unoffered[:5])}. "
                    "The batch did not execute."
                )
            recovery = self._recover_blocked_tool_batch(
                state, tool_calls_data, provider_request,
                instruction=self._unoffered_tool_instruction(unoffered),
            )
            if recovery is _CONTINUE and state.on_activity:
                state.on_activity("tool not in current scope - requesting a valid call...")
            return recovery

        state.tool_rounds += 1

        argument_block = self._tool_call_argument_block_reason(tool_calls_data, finish_reason)
        if argument_block:
            action, state.malformed_tool_prompts = self._malformed_tool_action(
                argument_block=argument_block,
                malformed_tool_prompts=state.malformed_tool_prompts,
                finish_reason=finish_reason,
                provider_requests=provider_request,
                monitor=state.monitor,
                on_activity=state.on_activity,
            )
            if action == "retry":
                return _CONTINUE
            final_text = "Provider repeatedly produced malformed/truncated tool calls; stopped before changing files. Try a smaller edit or switch model."
            notes = self._record_turn_memory_and_learning(state.user_input, final_text, on_activity=state.on_activity)
            final_text = self._maybe_append_after_turn_notes(final_text, notes)
            self.session.add_assistant(final_text)
            # Turn-end security check on modified files
            emit_security_check(state.turn_modified_files, state.monitor)
            return final_text

        # Canonicalize once before signatures, provider history, guards, audit,
        # and execution. The stored call must describe the operation that ran.
        self._prepare_effective_tool_arguments(state, tool_calls_data)
        if self._defer_unreviewed_project_rules(state, tool_calls_data, provider_request):
            return _CONTINUE
        missing_reads = [str(args.get("path") or "") for item in tool_calls_data
                         if str((item.get("function") or {}).get("name") or "") == "read_file"
                         for args in (self._parsed_tool_arguments(item),)
                         if str(args.get("path") or "") in state.missing_read_paths
                         and not Path(str(args.get("path") or "")).exists()]
        if missing_reads and len(missing_reads) == len(tool_calls_data) and len(set(missing_reads)) == 1:
            missing_path = missing_reads[0]
            state.blocked_tool_batch_signature = self._tool_call_batch_signature(tool_calls_data)
            state.blocked_tool_batch_reason = "The requested file is still missing; the read remains unfinished."
            state.blocked_tool_batch_guided = missing_path in state.missing_read_guided_paths
            state.missing_read_guided_paths.add(missing_path)
            return self._recover_blocked_tool_batch(
                state, tool_calls_data, provider_request,
                instruction=("[MISSING FILE] Changing read_file view, offset, or limit cannot find this path. "
                             "Use a verified source or explain the missing source to the operator."),
            )

        plan_only_revision = _tool_batch_is_plan_only_revision(tool_calls_data)
        if (
            plan_only_revision
            and state.consecutive_plan_revision_batches
            >= _MAX_CONSECUTIVE_PLAN_ONLY_REVISIONS
        ):
            final_text = (
                "MO stopped repeated plan-only revisions after "
                f"{_MAX_CONSECUTIVE_PLAN_ONLY_REVISIONS} consecutive revisions "
                "without an evidence-producing action. The current taskboard and "
                "its evidence remain open."
            )
            self._record_runtime_diagnostic(
                "plan_revision_churn",
                "the provider repeatedly rewrote the open plan without doing evidence-producing work",
                request=provider_request,
                detail=f"consecutive_revisions={state.consecutive_plan_revision_batches}",
                tool="set_plan",
            )
            return self._report_repeated_tool_batch_stop(
                state,
                final_text,
                provider_request,
                kind="plan_revision_churn",
                repeat_count=state.consecutive_plan_revision_batches,
            )

        batch_signature = self._tool_call_batch_signature(tool_calls_data)
        batch_is_task_progress = self._tool_batch_is_task_completion_progress(tool_calls_data, state.task_board)
        if (
            batch_signature
            and state.blocked_tool_batch_signature
            and batch_signature != state.blocked_tool_batch_signature
        ):
            state.clear_blocked_tool_batch()
        if (
            batch_signature
            and state.exhausted_tool_batch_signature
            and batch_signature != state.exhausted_tool_batch_signature
        ):
            state.clear_exhausted_tool_batch()
        if batch_is_task_progress:
            state.clear_repeat_tracking()
            state.clear_completed_tool_batch()
            state.clear_exhausted_tool_batch()
            state.clear_blocked_tool_batch()
        elif batch_signature and batch_signature == state.exhausted_tool_batch_signature:
            final_text = (
                state.exhausted_tool_batch_reason
                or "MO left the active task open because the same non-cacheable action "
                "was requested repeatedly. Its previous result remains in the transcript, "
                "and MO did not run the action again."
            )
            if not state.exhausted_tool_batch_guided:
                state.exhausted_tool_batch_guided = True
                state.clear_repeat_tracking()
                if state.monitor:
                    state.monitor.emit(
                        "provider_retry",
                        {
                            "request": provider_request,
                            "provider": self.provider_name,
                            "reason": "noncacheable_tool_batch_repeat",
                            "action": "guide_from_existing_result",
                        },
                    )
                self.session.add_assistant(
                    "[REPEATED TOOL RECOVERY] The previous identical non-cacheable "
                    "tool batch already returned a result in this transcript. Do not "
                    "request it again. Continue from that result, wait for the existing "
                    "background work/result if applicable, or use different arguments."
                )
                self.session.mark_last_assistant_internal()
                return _CONTINUE
            self._record_runtime_diagnostic(
                "repeated_tool_batch",
                "the provider repeated an exhausted non-cacheable tool batch",
                request=provider_request,
                detail="action=stop_exact_repeat",
            )
            return self._report_repeated_tool_batch_stop(
                state,
                final_text,
                provider_request,
                kind="repeated_tool_batch_exhausted",
                repeat_count=state.repeated_tool_batch_count,
            )
        elif batch_signature and batch_signature == state.blocked_tool_batch_signature:
            return self._recover_blocked_tool_batch(state, tool_calls_data, provider_request)
        elif batch_signature and batch_signature == state.completed_tool_batch_signature:
            state.clear_repeat_tracking()
        elif batch_signature and batch_signature == state.last_tool_batch_signature:
            state.repeated_tool_batch_count += 1
        else:
            if (
                state.completed_tool_batch_signature
                and batch_signature != state.completed_tool_batch_signature
            ):
                state.clear_completed_tool_batch()
            state.last_tool_batch_signature = batch_signature
            state.repeated_tool_batch_count = 1 if batch_signature else 0
        doom_threshold = max(2, int(getattr(self, "doom_loop_tool_batch_threshold", 3) or 3))
        if batch_signature and state.repeated_tool_batch_count >= doom_threshold:
            repeated_names = {
                str((tc_data.get("function") or {}).get("name") or "").strip()
                for tc_data in tool_calls_data
                if isinstance(tc_data, dict) and isinstance(tc_data.get("function"), dict)
            }
            state.exhausted_tool_batch_signature = batch_signature
            state.exhausted_tool_batch_reason = (
                "MO left the active task open because the same non-cacheable action was "
                f"requested {doom_threshold} times. Its previous result remains in the "
                "transcript, and MO did not run the action again."
            )
            state.exhausted_tool_batch_guided = True
            state.clear_repeat_tracking()
            if state.monitor:
                state.monitor.emit(
                    "provider_retry",
                    {
                        "request": provider_request,
                        "provider": self.provider_name,
                        "reason": "repeated_tool_batch",
                        "action": "guide_exact_repeated_batch",
                        "tools": sorted(name for name in repeated_names if name),
                        "repeat_count": doom_threshold,
                    },
                )
            self._record_runtime_diagnostic(
                "repeated_tool_batch",
                "an identical completed tool batch was skipped without hiding its tools",
                request=provider_request,
                detail=f"repeat_count={doom_threshold}",
                tool=sorted(repeated_names)[0] if repeated_names else "",
            )
            if state.on_activity:
                state.on_activity("repeated tool request skipped; recovering with existing results")
            self.session.add_assistant(
                "[REPEATED TOOL RECOVERY] MO skipped an identical non-cacheable tool "
                f"batch after {doom_threshold} requests. Continue from the existing "
                "tool result already present in the transcript, wait for the existing "
                "background work/result if applicable, or use different arguments. "
                "Do not request that exact batch again; if no authorized route can "
                "finish the active task, report the exact blocker."
            )
            self.session.mark_last_assistant_internal()
            return _CONTINUE

        # Surface interim prose that accompanies tool calls. Providers
        # often answer the user's question in prose AND call a tool in
        # the same response. That prose used to reach only the livelog
        # (state.monitor preview), never the main transcript, so direct
        # answers were silently lost. Emit it to the UI now.
        interim_text = str(response.content or "").strip()
        if interim_text and state.on_assistant_text and not self._looks_like_raw_tool_payload(interim_text):
            try:
                self._emit_assistant_text(
                    state.on_assistant_text,
                    interim_text,
                    {
                        "with_tool_calls": True,
                        "request": provider_request,
                        "surface": self._provider_surface(),
                    },
                )
                state.next_progress_reminder_at = time.monotonic() + 60.0
            except Exception:
                traceback.print_exc()
            if state.monitor:
                state.monitor.emit(
                    "assistant_text",
                    {
                        "request": provider_request,
                        "surface": self._provider_surface(),
                        "chars": len(interim_text),
                        "with_tool_calls": True,
                    },
                )

        msg = {
            "role": "assistant",
            "content": response.content or "",
            "tool_calls": tool_calls_data,
        }
        reasoning = str(
            getattr(response, "reasoning_content", None)
            or getattr(response, "reasoning", None)
            or ""
        )
        if reasoning:
            msg["reasoning_content"] = reasoning
        response_items = list(getattr(response, "response_items", None) or [])
        if response_items:
            msg.update(response_replay_metadata(
                self._response_items_with_tool_arguments(response_items, tool_calls_data),
                self.model, str(getattr(response, "response_id", "") or ""),
                usage=getattr(response, "usage", None),
                reasoning_context=str(getattr(response, "reasoning_context", "") or ""),
            ))
        self.session.add_message(msg)

        return tool_calls_data

    def _dispatch_tool_batch(
        self,
        state: TurnState,
        tool_calls_data: list[dict[str, Any]],
        provider_request: int,
    ) -> str | object:
        """Dispatch a prepared batch without splitting or bypassing its gate."""
        plan_only_revision = _tool_batch_is_plan_only_revision(tool_calls_data)
        checkpoint_error = self._checkpoint_terminal_provider_turn(state.user_input, monitor=state.monitor)
        if checkpoint_error is not None:
            return checkpoint_error
        replayed = self._replay_completed_tool_batch(state, tool_calls_data, provider_request)
        if replayed is not None:
            checkpoint_error = self._checkpoint_terminal_provider_turn(state.user_input, monitor=state.monitor)
            return checkpoint_error if checkpoint_error is not None else replayed

        # Pre-execute independent read-only inspection calls concurrently.
        # The loop below stays the single authority for gating/board/
        # audit/ordering; this only removes avoidable wall-clock waits.
        prefetched_results = self._prefetch_read_family_results(
            tool_calls_data,
            state.user_input,
            task_board=state.task_board,
        )
        batch_tool_results: dict[str, str] = {}
        verification_results = self._verification_results_for_state(state)
        batch_sequence_start = len(state.tool_sequence)
        batch_signature = self._tool_call_batch_signature(tool_calls_data)
        completed_batch_results: list[dict[str, object]] = []
        project_index_maintenance_needed = False

        # Dispatch each tool call through the sandbox
        for idx, tc_data in enumerate(tool_calls_data):
            if getattr(state.cancel_event, "is_set", lambda: False)():
                return self._abort_computer_turn()
            name = tc_data["function"]["name"]
            arguments = self._parsed_tool_arguments(tc_data)
            active_task_before, phase_before = taskboard_position(state.task_board)
            task_transition: TaskTransitionResult | None = None
            pending_background = False
            computer_tool = False
            computer_call = None
            try:
                from core.desktop.runtime import COMPUTER_TOOLS, clear_current_events

                computer_tool = name in COMPUTER_TOOLS
                if computer_tool:
                    clear_current_events()
                    try:
                        from core.desktop.tool_actions import normalize_computer_call

                        computer_call = normalize_computer_call(name, arguments)
                    except Exception:
                        # Status detail is presentation-only; execution revalidates the call.
                        computer_call = None
            except Exception:
                computer_tool = False
                computer_call = None
            diffstat = (
                self._safe_tool_diffstat(name, arguments)
                if state.on_activity or state.on_action
                else ""
            )
            if state.on_activity:
                # Prettify MCP tool names (mcp__server__tool -> "server · tool")
                # so the activity line clearly shows an MCP tool is running.
                label = name
                if computer_call is not None:
                    label = f"{name} {computer_call.kind}:{computer_call.operation}"
                if name.startswith("mcp__"):
                    mcp_parts = name.split("__", 2)
                    if len(mcp_parts) == 3:
                        label = f"mcp:{mcp_parts[1]} · {mcp_parts[2]}"
                # Show WHAT the tool acts on (path/pattern), so a read-heavy
                # phase reads as real progress, not a blank stream. Kept inside
                # the parens so the existing "(name)..." parsers still work.
                # Keep the raw summary intact here. The TUI owns display-only
                # compaction so workspace paths keep their filenames and the
                # same logic is used for read/edit/write/shell rows.
                target = str(self._safe_tool_summary(name, arguments) or "").strip()
                if computer_call is not None:
                    redundant = {
                        str(computer_call.kind).casefold(),
                        str(computer_call.operation).casefold(),
                    }
                    if name == "computer_act":
                        redundant.add(str(arguments.get("action") or "").casefold())
                    if target.casefold() in redundant:
                        target = ""
                # Append +added/-removed after the path/summary so the TUI can
                # colour the trailing "+A -R" green/red.
                suffix = f" {target}{diffstat}" if target else diffstat
                state.on_activity(f"tooling ({label}{suffix})...")

            if state.monitor:
                tool_monitor_metadata = state.monitor.tool_event_metadata(name, arguments)
                state.monitor.emit(
                    "tool_call",
                    {
                        "request": provider_request,
                        "surface": self._provider_surface(),
                        "worker_id": self._provider_worker_id(),
                        "tool": name,
                        **tool_monitor_metadata,
                    },
                )
            else:
                tool_monitor_metadata = {}
            state.tool_call_counts[name] = state.tool_call_counts.get(name, 0) + 1
            verification_key = ""
            scratch_cleanup_target = None
            candidate_snapshot_before = None
            manifest_repair = False
            git_delivery = False

            # === Phase 2d: dispatch each tool call through the gate ===
            # THE SINGLE GATE
            operator_ok = self._operator_approved_for_turn(
                state.user_input, name, arguments
            ) or local_extensions.operator_override_approved(self, state.user_input, name, arguments)
            effective_roots = self._effective_allowed_roots_for_tool(state.user_input, name, arguments)
            phone_confirmation_retry = None
            phone_action_precondition = None
            systemcare_action_precondition = None
            computer_no_progress = None
            if name == "computer_act":
                try:
                    from core.gates.actuation_completion import computer_no_progress_block_reason

                    computer_no_progress = computer_no_progress_block_reason(
                        state.tool_sequence,
                        action_tools=frozenset({"computer_act"}),
                    )
                except Exception:
                    computer_no_progress = (
                        "[SAFETY BLOCK] Computer progress evidence could not be evaluated; "
                        "the next computer action was not executed."
                    )
            if name.startswith("phone_"):
                try:
                    from core.desktop.policy import pending_phone_confirmation_retry_block_reason

                    raw_operator_input = getattr(self, "_conversation_user_input", lambda value: value)(
                        state.user_input
                    )
                    phone_confirmation_retry = pending_phone_confirmation_retry_block_reason(
                        raw_operator_input,
                        name,
                    )
                except Exception:
                    phone_confirmation_retry = (
                        "[SAFETY BLOCK] Phone confirmation retry policy is unavailable; "
                        "the phone tool was not executed."
                    )
            if name in ACTUATION_TOOLS and name.startswith("phone_"):
                try:
                    from tools.phone_semantic import phone_action_precondition_block_reason

                    phone_action_precondition = phone_action_precondition_block_reason(
                        name,
                        arguments,
                    )
                except Exception:
                    phone_action_precondition = (
                        "[SAFETY BLOCK] Phone action precondition policy is unavailable; "
                        "the phone action was not executed."
                    )
            if name in ACTUATION_TOOLS and name.startswith("systemcare_"):
                try:
                    from tools.systemcare import systemcare_action_precondition_block_reason

                    systemcare_action_precondition = systemcare_action_precondition_block_reason(
                        name,
                        arguments,
                        getattr(self, "config", None),
                    )
                except Exception:
                    systemcare_action_precondition = (
                        "[SYSTEMCARE SAFETY BLOCK] SystemCare action preconditions are unavailable; "
                        "the action was not executed."
                    )
            block_reason = (
                computer_no_progress
                or phone_confirmation_retry
                or phone_action_precondition
                or systemcare_action_precondition
                or local_extensions.tool_block_reason(self, state.user_input, name, arguments)
                or _tool_guard(
                    name,
                    arguments,
                    lane=self._effective_lane(),
                    allowed_roots=effective_roots,
                    sandbox_config=self.sandbox_config,
                    operator_override=operator_ok,
                )
            )
            # Requested ordinary actions execute through the normal scope,
            # sandbox, target, and observation gates. Only the independent
            # high-impact policy pauses for an additional confirmation.
            if _should_request_action_confirmation(name, block_reason):
                try:
                    from core.desktop.policy import action_confirmation_block_reason

                    raw_operator_input = getattr(self, "_conversation_user_input", lambda value: value)(
                        state.user_input
                    )
                    block_reason = action_confirmation_block_reason(raw_operator_input, name, arguments)
                except Exception:
                    block_reason = (
                        "[SAFETY BLOCK] Computer action confirmation policy is unavailable; "
                        "the computer action was not executed."
                    )

            screen_image_uris: list[str] = []
            if not block_reason:
                starter = self._create_project_rules_for_tool(state.user_input, name, arguments)
                if starter is not None:
                    from ..context.project_docs import AGENTS_STARTER

                    state.turn_modified_files.append((str(starter), AGENTS_STARTER))
                    batch_tool_results.clear()
                    project_index_maintenance_needed = True
                    _invalidate_verification_epoch(state, verification_results, changed_path=str(starter))
                    if state.monitor:
                        state.monitor.emit("session_event", {"kind": "project_rule_starter", "path": str(starter)})
            if block_reason:
                result = block_reason
            else:
                # Lazy board creation happens after sandbox approval so
                # blocked tool attempts do not appear as active work.
                if not state.task_board and state.on_first_tool:
                    state.task_board = _call_on_first_tool(state.on_first_tool, name, arguments)
                # Reuse the concurrently-prefetched inspection result when present
                # (gate already passed identically here); else execute inline.
                if name == "map_project":
                    self._map_project_callbacks = {
                        "on_activity": state.on_activity,
                        "cancel_event": state.cancel_event,
                    }
                if name == "generate_image":
                    self._image_gen_callbacks = {
                        "on_activity": state.on_activity,
                        "cancel_event": state.cancel_event,
                    }
                computer_activity_started = False
                try:
                    if computer_tool:
                        try:
                            from ..runtime.heartbeat import record_computer_activity

                            record_computer_activity(
                                self,
                                name,
                                arguments=arguments,
                                active=True,
                                surface=self._provider_surface(),
                            )
                            computer_activity_started = True
                        except Exception:
                            # The operator cue is observational and must never
                            # prevent the underlying computer tool from running.
                            computer_activity_started = False
                    batch_reused = False
                    self._record_verification_selection(state, name, arguments)
                    verification_key = self._verification_reuse_key(
                        name,
                        arguments,
                        state.task_board,
                    )
                    manifest_repair = _is_test_overlay_manifest_repair(name, arguments)
                    git_delivery = (
                        str(name or "").strip().lower() in {"shell", "test_runner"}
                        and shell_command_is_git_delivery_only(
                            str((arguments or {}).get("command") or ""),
                        )
                    )
                    candidate_state_matters = bool(
                        verification_results
                        or state.project_change_context
                        or state.final_gates_fired.intersection(
                            {"verify_edits", "lsp_diagnostics"}
                        )
                    )
                    if git_delivery or (manifest_repair and candidate_state_matters):
                        candidate_snapshot_before = _verification_candidate_snapshot(
                            str(
                                (arguments or {}).get("workdir")
                                or getattr(self, "_effective_project_cwd", lambda: "")()
                                or "."
                            ),
                        )
                    if verification_results and name in {"shell", "test_runner"}:
                        scratch_cleanup_target = _verification_preserving_scratch_cleanup(
                            arguments, state.turn_modified_files,
                            getattr(self, "_effective_project_cwd", lambda: "")(),
                            verification_results,
                        )
                    if name in TASKBOARD_CONTROL_TOOLS:
                        # Taskboard controls are implemented by AgentTaskBoard
                        # against the live board below, not by a second
                        # stateless executor that can disagree with it.
                        result = ""
                    elif idx in prefetched_results:
                        result = prefetched_results[idx]
                    else:
                        result, batch_reused = self._dispatch_or_reuse_batch_tool(
                            name,
                            arguments,
                            batch_tool_results,
                            cancel_event=state.cancel_event,
                            verification_cache=verification_results,
                            verification_key=verification_key,
                        )
                    if batch_reused and state.monitor:
                        state.monitor.emit(
                            "tool_call_coalesced",
                            {
                                "request": provider_request,
                                "surface": self._provider_surface(),
                                "tool": name,
                            },
                        )
                finally:
                    if computer_activity_started:
                        try:
                            from ..runtime.heartbeat import record_computer_activity

                            record_computer_activity(
                                self,
                                name,
                                arguments=arguments,
                                active=False,
                                surface=self._provider_surface(),
                            )
                        except Exception:
                            pass
                    if name == "map_project":
                        try:
                            delattr(self, "_map_project_callbacks")
                        except AttributeError:
                            pass
                    if name == "generate_image":
                        try:
                            delattr(self, "_image_gen_callbacks")
                        except AttributeError:
                            pass
                # Computer-use vision: lift a canonical/compatibility screenshot —
                # or a perceived image (perceive reuses the same marker) — out of the
                # temp/source file into image part(s) for the model to SEE. A
                # result may carry more than one marker line (e.g. multi-page
                # render); collect each into the image list.
                visual_computer_perception = (
                    _is_visual_computer_perception_tool(name, arguments)
                )
                if visual_computer_perception or name == "perceive":
                    from tools.screen import SCREEN_IMAGE_MARKER, discard_capture, is_pending_capture, load_image_data_uri

                    if SCREEN_IMAGE_MARKER in result:
                        text_lines: list[str] = []
                        for line in result.split("\n"):
                            marker, sep, path = line.partition(f"{SCREEN_IMAGE_MARKER}:")
                            if sep and not marker.strip():
                                if visual_computer_perception and not is_pending_capture(path.strip()):
                                    text_lines.append("[Untrusted screenshot reference ignored.]")
                                    continue
                                uri = load_image_data_uri(path.strip())
                                if uri:
                                    screen_image_uris.append(uri)
                                # Now base64 in the model's context. Drop the temp file;
                                # a `perceive` path is the operator's and is left alone.
                                discard_capture(path.strip())
                            else:
                                text_lines.append(line)
                        result = "\n".join(text_lines).strip() or "[image captured]"
                        # Images are useful only to a provider that accepts
                        # image input. If the home provider cannot, route file
                        # perception to an eligible image-input provider; screen
                        # observation keeps its bounded observer path.
                        if screen_image_uris:
                            from ..provider.provider import provider_accepts_image_input

                            if not provider_accepts_image_input(self.active_provider):
                                if visual_computer_perception or self._provider_surface() in {"mo_desktop", "companion"}:
                                    target_kind = (
                                        str(arguments.get("kind") or ("desktop" if name == "computer_act" else "screen")).strip().lower()
                                        if visual_computer_perception else "file"
                                    )
                                    try:
                                        from core.desktop.runtime import active_target

                                        visual_target = active_target(target_kind)
                                        visual_target_id = visual_target.target_id if visual_target is not None else ""
                                    except Exception:
                                        visual_target_id = ""
                                    observation = self._bounded_vision_observation(
                                        screen_image_uris,
                                        result,
                                        monitor=state.monitor,
                                        user_input=getattr(self, "_conversation_user_input", lambda value: value)(
                                            state.user_input
                                        ),
                                        target_kind=target_kind,
                                        target_id=visual_target_id,
                                    )
                                    screen_image_uris.clear()
                                    result = f"{result}\n{observation}".strip()
                                else:
                                    # Engineering surfaces retain their existing
                                    # image-input transfer. Desktop keeps its own
                                    # primary model and uses the observer above.
                                    self.switch_to_vision_provider()
                # Operator-facing payload markers are an internal transport, not
                # syntax that arbitrary tool output may activate. The channel
                # owner enforces exact producer names, final-line framing, and
                # process-owned ANSI references before any visual delivery.
                if isinstance(result, str) and (
                    "__MO_TERMINAL_VISUAL__" in result
                    or "__MO_OPERATOR_IMAGE__" in result
                ):
                    from ..visualize.operator_visual import resolve_tool_result

                    result = resolve_tool_result(
                        name,
                        result,
                        on_operator_visual=state.on_operator_visual,
                        on_operator_image=state.on_operator_image,
                    )
                pending_background = _tool_result_is_pending_background(name, result)
                # AgentTaskBoard is the only owner of set_plan/complete_task
                # validation, mutation, persistence, and provider-facing result
                # text. Other successful tools only contribute evidence to an
                # existing work row; the ordinary chat response is not a row.
                if (
                    state.task_board
                    and (state.task_board.tasks or name in TASKBOARD_CONTROL_TOOLS)
                    and not self._tool_result_is_error(result, tool_name=name)
                    # A full-suite marker is not verification evidence. Generic
                    # background shell launches still retain their established
                    # execute-row evidence semantics.
                    and not (pending_background and name == "test_runner")
                ):
                    active_before = str(state.task_board.active_task_id() or "") if name == "complete_task" else ""
                    advanced = self._advance_task_board_after_tool(
                        state.task_board,
                        name,
                        arguments,
                        monitor=state.monitor,
                        user_input=state.user_input,
                        verification_results=verification_results,
                    )
                    if name == "set_plan":
                        result = self._set_plan_result_message()
                        candidate = getattr(self, "_last_set_plan_result", None)
                        if isinstance(candidate, TaskTransitionResult):
                            task_transition = candidate
                    elif name == "complete_task":
                        result = self._complete_task_result_message()
                        candidate = getattr(self, "_last_complete_task_result", None)
                        if isinstance(candidate, TaskTransitionResult):
                            task_transition = candidate
                        if advanced and active_before and active_before not in result:
                            result = f"Task {active_before} marked as complete."
                elif name in TASKBOARD_CONTROL_TOOLS and not block_reason:
                    result = f"Error: {name} requires an active taskboard runtime."
                if state.on_board_event and state.task_board:
                    rendered = state.task_board.render()
                    if rendered != getattr(self, "_last_rendered_board", None):
                        self._last_rendered_board = rendered
                        _emit_task_board_update(
                            state.task_board,
                            update="updated",
                            on_board_event=state.on_board_event,
                        )

            # Write tool audit log (blocked or executed)
            if pending_background:
                background_key = f"_background_{name}"
                state.tool_call_counts[background_key] = state.tool_call_counts.get(background_key, 0) + 1
            tool_is_error = self._tool_result_is_error(result, tool_name=name)
            if name == "read_file" and not block_reason and str(result).startswith("Error: File not found:"):
                state.missing_read_paths.add(str(arguments.get("path") or ""))
            semantic_rejection = bool(task_transition is not None and not task_transition.ok)
            semantic_reason = task_transition.reason if semantic_rejection else ""
            if tool_is_error:
                state.tool_error_counts[name] = state.tool_error_counts.get(name, 0) + 1
            if not block_reason:
                project_index_maintenance_needed = (
                    project_index_maintenance_needed
                    or _tool_requires_project_index_maintenance(name, arguments)
                )
            if not block_reason and _tool_invalidates_verification_reuse(name, arguments):
                # A later call in this same batch must not reuse stale file reads
                # or Git inspection output. Verification has a separate tested-
                # candidate identity and survives metadata-only delivery.
                batch_tool_results.clear()
                candidate_preserved = False
                candidate_changes = None
                if candidate_snapshot_before is not None:
                    candidate_changes = _verification_candidate_changes(
                        candidate_snapshot_before,
                        _verification_candidate_snapshot(candidate_snapshot_before[0]),
                    )
                if (
                    manifest_repair
                    and not tool_is_error
                    and candidate_changes is not None
                    and all(
                        path.replace("\\", "/").casefold() == "tests/.mo-test-overlay.json"
                        for path in candidate_changes
                    )
                ):
                    _invalidate_repaired_overlay_failures(state, verification_results)
                    candidate_preserved = True
                elif git_delivery and candidate_changes == set():
                    candidate_preserved = True
                if git_delivery and candidate_changes != set():
                    # Delivery hooks are executable project code. A hook may
                    # rewrite source even when `git add` or `git push` itself
                    # would ordinarily leave graph inputs alone. Refresh when
                    # the snapshot proves a change or cannot prove stability.
                    project_index_maintenance_needed = True
                if (
                    not candidate_preserved
                    and (scratch_cleanup_target is None or scratch_cleanup_target.exists())
                ):
                    _invalidate_verification_epoch(
                        state, verification_results,
                        changed_path=str(arguments.get("path") or "") if name in {"write_file", "edit_file"} else "",
                    )
            if block_reason:
                self._record_runtime_diagnostic(
                    "tool_blocked",
                    f"a `{name}` tool call blocked before execution",
                    detail=str(block_reason),
                    tool=name,
                    request=provider_request,
                )
            elif tool_is_error:
                self._record_runtime_diagnostic(
                    "tool_error",
                    f"an error returned by the `{name}` tool",
                    detail=str(result),
                    tool=name,
                    request=provider_request,
                )
            self._write_tool_audit(
                name,
                arguments,
                result,
                block_reason,
                tool_error=tool_is_error,
            )
            computer_events: list[dict[str, object]] = []
            if computer_tool:
                try:
                    from core.desktop.runtime import drain_current_events

                    computer_events = drain_current_events()
                except Exception:
                    computer_events = []
            active_task_after, phase_after = taskboard_position(state.task_board)
            verification_signal = bool(
                tool_is_verification_signal(name, arguments)
                and not pending_background
            )
            state.tool_sequence.append(
                ToolExecutionRecord(
                    tool=name,
                    action=(
                        str(arguments.get("action") or ("status" if name == "git_status" else "")).strip().lower()
                        if name in {"computer_act", "mo_design", "git_status"}
                        else ",".join(sorted(shell_delivery_evidence_actions(name, arguments)))
                    ),
                    view=(
                        str(arguments.get("view") or "").strip().lower()
                        if name == "mo_design"
                        else ""
                    ),
                    presentation_only=bool(
                        name == "computer_act"
                        and str(arguments.get("action") or "").strip().lower()
                        in {"recipe", "recipe_run"}
                        and desktop_recipe_is_point_only(arguments)
                    ),
                    blocked=bool(block_reason),
                    block_reason=str(block_reason or ""),
                    error=tool_is_error,
                    verification=verification_signal,
                    semantic_rejection=semantic_rejection,
                    semantic_reason=semantic_reason,
                    computer_events=tuple(computer_events),
                    task_transition=task_transition,
                    active_task_before=active_task_before,
                    active_task_after=active_task_after,
                    phase_before=phase_before,
                    phase_after=phase_after,
                )
            )

            # Per-action hook: secondary observers can receive every tool
            # MO runs, with a sanitized arg summary —
            # so the audit reflects what MO actually DID, not just the
            # request and the final reply. Best-effort; never breaks a turn.
            if state.on_action:
                try:
                    state.on_action(
                        {
                            "tool": name,
                            "summary": self._safe_tool_summary(name, arguments),
                            "blocked": bool(block_reason),
                            "error": tool_is_error,
                            "successful": not (block_reason or tool_is_error or semantic_rejection),
                            "detail": (
                                redact_sensitive_text(str(block_reason or semantic_reason or result or ""))[:240]
                                if block_reason or tool_is_error or semantic_rejection else ""
                            ),
                            "result_hint": _graph_activity_result_hint(name, result),
                            "pending_background": pending_background,
                            "full_test_suite": verification_key.startswith("verification-family:"),
                            "diffstat": diffstat,
                            "computer_events": computer_events,
                        }
                    )
                except Exception:
                    pass

            if state.monitor:
                tool_result_payload = {
                    "request": provider_request,
                    "surface": self._provider_surface(),
                    "worker_id": self._provider_worker_id(),
                    "tool": name,
                    "blocked": bool(block_reason),
                    "reason": str(block_reason or "")[:240],
                    "error": tool_is_error,
                    "outcome": (
                        "blocked" if block_reason else
                        "error" if tool_is_error else
                        "rejected" if semantic_rejection else
                        "success"
                    ),
                    "semantic_reason": str(semantic_reason or "")[:80],
                    "active_task_before": active_task_before,
                    "active_task_after": active_task_after,
                    "phase_before": phase_before,
                    "phase_after": phase_after,
                    "chars": len(result),
                    **tool_monitor_metadata,
                }
                if tool_is_error:
                    tool_result_payload["error_excerpt"] = redact_sensitive_text(
                        str(result or "")
                    )[:500]
                state.monitor.emit(
                    "tool_result",
                    tool_result_payload,
                )
            if not block_reason and not tool_is_error and not semantic_rejection and name in {"write_file", "edit_file"}:
                file_path = arguments.get("path", "")
                file_content = arguments.get("new_text" if name == "edit_file" else "content", "")
                if file_path:
                    state.turn_modified_files.append((file_path, file_content))
            if name == "tool_search" and not block_reason and not tool_is_error and not semantic_rejection:
                activation_state = _tool_search_activation_state(result)
                if activation_state is True:
                    state.noop_tool_searches = 0
                elif activation_state is False:
                    state.noop_tool_searches += 1
            # The latest complete_task result is the authority for the next
            # compaction boundary. A rejected later completion must clear an
            # earlier hint so active work is never aged as though it resolved.
            if name == "complete_task":
                self._work_resolved_hint = bool(
                    not block_reason and not tool_is_error and not semantic_rejection
                )

            # Label injected instructions in external/untrusted tool output
            # (web_fetch pages, MCP responses) BEFORE capping, so a payload in
            # the truncated tail is still caught. Annotate-only, never block.
            result = self._scan_external_tool_result_for_injection(
                result,
                tool_name=name,
                arguments=arguments,
                monitor=state.monitor,
            )
            result = self._cap_tool_result_for_context(result, monitor=state.monitor, tool_name=name)
            if batch_signature:
                completed_batch_results.append(
                    {
                        "result": str(result or ""),
                        "had_images": bool(screen_image_uris),
                    }
                )

            if screen_image_uris:
                self.session.add_tool_result(tc_data["id"], result, image_data_uris=screen_image_uris)
            else:
                self.session.add_tool_result(tc_data["id"], result)
            checkpoint_error = self._checkpoint_terminal_provider_turn(state.user_input, monitor=state.monitor)
            if checkpoint_error is not None:
                return checkpoint_error

        if project_index_maintenance_needed:
            maintain = getattr(self, "_maintain_project_indexes", None)
            if callable(maintain):
                with monitor_phase("index_schedule", monitor=state.monitor):
                    maintain(reason="project-edit")

        batch_records = state.tool_sequence[batch_sequence_start:]
        failed_record = next(
            (
                record
                for record in batch_records
                if record.blocked or record.error or record.semantic_rejection
            ),
            None,
        )
        successful_plan_revision = plan_only_revision and any(
            record.tool == "set_plan" and record.successful
            for record in batch_records
        )
        successful_evidence_action = any(
            record.tool not in {"set_plan", "tool_search"} and record.successful
            for record in batch_records
        )
        if successful_plan_revision:
            state.consecutive_plan_revision_batches += 1
        elif successful_evidence_action:
            state.consecutive_plan_revision_batches = 0
        if batch_signature and failed_record is not None:
            if failed_record.blocked:
                blocker = failed_record.block_reason or "The previous tool batch was blocked before execution."
            elif failed_record.semantic_rejection:
                blocker = failed_record.semantic_reason or "The previous tool batch was rejected before completion."
            else:
                blocker = "The previous tool batch returned an error before completion."
            if batch_signature != state.blocked_tool_batch_signature:
                state.blocked_tool_batch_guided = False
            state.blocked_tool_batch_signature = batch_signature
            state.blocked_tool_batch_reason = str(blocker).strip()
            state.clear_completed_tool_batch()
            state.clear_exhausted_tool_batch()
            state.clear_repeat_tracking()
        batch_has_taskboard_control = any(
            str((item.get("function") or {}).get("name") or "") in TASKBOARD_CONTROL_TOOLS
            for item in tool_calls_data
            if isinstance(item, dict)
        )
        if (
            batch_signature
            and not failed_record
            and not batch_has_taskboard_control
            and len(completed_batch_results) == len(tool_calls_data)
        ):
            # Pending verification is a cacheable in-flight result, not an
            # exhausted tool batch. Replays refresh only that cached slot while
            # preserving every other result, so no duplicate work is launched.
            state.completed_tool_batch_signature = batch_signature
            state.completed_tool_batch_results = completed_batch_results
            state.completed_tool_batch_replay_count = 0
            state.clear_exhausted_tool_batch()
            state.clear_blocked_tool_batch()
        elif batch_signature and not failed_record:
            state.clear_completed_tool_batch()
            state.clear_exhausted_tool_batch()
            state.clear_blocked_tool_batch()
        discovery_threshold = max(
            2,
            int(getattr(self, "doom_loop_tool_batch_threshold", 3) or 3),
        )
        if state.noop_tool_searches >= discovery_threshold:
            final_text = (
                "MO stopped repeated tool discovery after "
                f"{state.noop_tool_searches} searches only rediscovered already-active tools. The active "
                "catalog and prior evidence remain available, and unfinished work stays open."
            )
            self._record_runtime_diagnostic(
                "tool_search_churn",
                "the provider repeatedly searched an already-active tool catalog",
                request=provider_request,
                detail=f"noop_searches={state.noop_tool_searches}",
                tool="tool_search",
            )
            return self._report_repeated_tool_batch_stop(
                state,
                final_text,
                provider_request,
                kind="tool_search_churn",
                repeat_count=state.noop_tool_searches,
                action="stop_noop_discovery",
            )
        if getattr(state.cancel_event, "is_set", lambda: False)():
            return self._abort_computer_turn()

        return _CONTINUE
