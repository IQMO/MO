"""Terminal interface turn runner that renders provider output and skin-colored graph-search/context receipts."""
from __future__ import annotations

import inspect
import os
import re
import threading
import time
import traceback

from core.provider.provider import clean_provider_error
from core.session.session import assistant_result_is_incomplete
from core.worker import ensure_worker_registry

from .activity import duration_text
from .formatting import explainer_activity_lines
from .response import response_line_fragments
from .transcript_grammar import RAIL


_DIFFSTAT_RE = re.compile(r"^(.*) (\+\d+) (-\d+)(, L\d+(?:-L\d+)?)?$")
_TIMED_TOOL_VERBS = frozenset({"shell", "test_runner"})


def _tool_label_from_activity(act: str) -> str:
    """Extract the tool label from a ``tooling (<label>)...`` activity string.

    The label is everything between the FIRST ``(`` and the trailing ``)...``.
    Splitting on ``(`` (the old behaviour) broke on any summary containing a
    paren — every shell ``python -c`` one-liner rendered as a garbage tail like
    ``).st_size for…`` because ``split("(")[-1]`` grabbed the last inner group.
    """
    if "(" not in act:
        return act
    inner = act[act.index("(") + 1:]
    if inner.endswith(")..."):
        return inner[:-4].rstrip()
    return inner.removesuffix("...").removesuffix(")").rstrip()


_CHIP_SHORTEN = {
    "read_file": "read", "edit_file": "edit", "write_file": "write",
    "find_files": "find", "code_search": "graph-search",
    "project_history": "history",
    "find_callers": "graph-callers", "find_callees": "graph-callees",
    "git_status": "git", "tool_search": "tools",
    "project_bridge": "project", "test_runner": "test",
    # Planning / tasking
    "set_plan": "plan", "complete_task": "done", "schedule_job": "sched",
    # System / diagnostics
    "system_health": "health", "credential_status": "cred",
    "build_graph": "graph-build", "graph_explain": "graph", "graph_neighbors": "graph",
    "graph_path": "graph", "graph_stats": "graph",
    # Web
    "web_search": "web", "web_fetch": "fetch",
    # Visual / design
    "mo_design": "design", "show_viz": "viz",
    "show_image": "img", "edit_image": "img", "generate_image": "img",
    "perceive": "view",
    # Profile / learning
    "record_profile_fact": "fact", "record_convention": "conv",
    # Repo
    "inspect_repo": "repo", "use_repo": "repo",
    # Migration
    "migrate": "migr",
    # Desktop / everywhere
    "desktop_sync": "sync",
    "everywhere_readiness": "hub", "everywhere_pair_android": "pair",
    # File transfer
    "file_transfer": "xfer",
    # SystemCare
    "systemcare_status": "care", "systemcare_scan": "care",
    "systemcare_plan": "care", "systemcare_apply": "care",
    "systemcare_rollback": "care", "systemcare_calibrate": "care",
    "systemcare_cancel": "care",
    # Computer-use
    "computer_targets": "ui", "computer_observe": "ui", "computer_act": "ui",
    "point_on_screen": "ui",
    # Phone
    "phone_context": "phone", "phone_click": "phone", "phone_set_text": "phone",
    "phone_scroll": "phone", "phone_key": "phone", "phone_files": "phone",
    "phone_storage_report": "phone", "phone_file_read": "phone",
    "phone_file_delete": "phone", "phone_capabilities": "phone",
    "phone_system_status": "phone", "phone_cache_report": "phone",
    "phone_cache_trim": "phone", "phone_packages": "phone",
    "phone_package_action": "phone", "phone_shell": "phone",
}

_REPEATABLE_TOOL_TARGETS = frozenset({
    "read_file", "find_files", "code_search", "find_callers", "find_callees",
    "git_status", "grep", "project_bridge", "tool_search", "web_fetch",
    "web_search",
})


_PATH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/][^\s\"'<>|]+)")
_COMPACT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:)?[^\\/\s\"'<>|]+[\\/][^\s\"'<>|]+(?:[\\/][^\s\"'<>|]+)*"
)
_TEST_FILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])(test_[A-Za-z0-9_.-]+\.py(?:::[A-Za-z0-9_.\[\]-]+)*)"
)
_GENERIC_PYTHON_CALLS = frozenset({
    "bool", "dict", "float", "getattr", "hasattr", "int", "len", "list",
    "main", "print", "range", "run", "set", "sorted", "str", "tuple",
})


def _shorten_target(target: str, limit: int = 52) -> str:
    """Boundary-aware truncation of a tool target for display only.

    The real command/args are unchanged. Keep path filenames visible when a
    command line is too long; a clipped ``Get-Content -Path project\\...\\hints.py``
    is more useful than a left-only ``Get-Content -Path project\\interface...``.
    """
    target = target.strip()
    if len(target) <= limit:
        return target
    path_match = None
    for match in _COMPACT_PATH_RE.finditer(target):
        if "\\" in match.group(0) or "/" in match.group(0):
            path_match = match
    if path_match:
        path = path_match.group(0)
        sep = "\\" if "\\" in path else "/"
        parts = [part for part in re.split(r"[\\/]+", path) if part]
        tail = sep.join(parts[-2:] if len(parts) >= 2 else parts)
        replacement = f"…{sep}{tail}" if tail else "…"
        compacted = (target[:path_match.start()] + replacement + target[path_match.end():]).strip()
        if len(compacted) <= limit:
            return compacted
        prefix = target[:path_match.start()].strip()
        suffix = target[path_match.end():].strip()
        verb = prefix.split()[0] if prefix else ""
        compacted = " ".join(part for part in (verb, replacement, suffix) if part)
        if len(compacted) <= limit:
            return compacted
        compacted = " ".join(part for part in (verb, replacement) if part)
        if len(compacted) <= limit:
            return compacted
    tail_len = max(12, limit // 3)
    tail = target[-tail_len:].lstrip()
    head_len = max(1, limit - len(tail) - 1)
    head = target[:head_len].rstrip()
    for sep in (" ", "/", "\\"):
        idx = head.rfind(sep)
        if idx > head_len // 2:
            head = head[:idx].rstrip()
            break
    return f"{head}…{tail}"


def _compact_project_path_token(token: str, project_root: str) -> str:
    if not token or not project_root:
        return token
    suffix = ""
    candidate = token
    while candidate and candidate[-1] in ".,;:)]}":
        suffix = candidate[-1] + suffix
        candidate = candidate[:-1]
    if not candidate:
        return token
    try:
        root = os.path.abspath(os.path.expanduser(project_root))
        path = os.path.abspath(os.path.expanduser(candidate))
        if os.path.normcase(os.path.commonpath([root, path])) != os.path.normcase(root):
            return token
        rel = os.path.relpath(path, root)
    except Exception:
        return token
    root_name = os.path.basename(root.rstrip("\\/")) or root
    if rel == ".":
        return root_name + suffix
    sep = "\\" if "\\" in candidate else "/"
    rel = rel.replace("\\", sep).replace("/", sep)
    return f"{root_name}{sep}{rel}{suffix}"


def _compact_project_paths(text: str, project_root: str) -> str:
    """Render local workspace paths as repo-relative labels for transcript rows."""
    value = str(text or "")
    if not value or not project_root:
        return value
    return _PATH_TOKEN_RE.sub(lambda m: _compact_project_path_token(m.group(0), project_root), value)


def _test_display_target(command: str) -> str:
    """Describe verification purpose without exposing its shell/bootstrap wrapper."""
    text = " ".join(str(command or "").split())
    if not text:
        return ""
    remote = bool(re.search(r"(?i)(?:^|\s|[;&|])ssh(?:\.exe)?\s", text))
    prefix = "remote " if remote else ""
    tests: list[str] = []
    for match in _TEST_FILE_RE.finditer(text):
        target = match.group(1)
        if target not in tests:
            tests.append(target)
    if tests:
        extra = f" +{len(tests) - 1}" if len(tests) > 1 else ""
        return f"{prefix}{tests[0]}{extra}"
    if re.search(r"(?i)\bsystemctl\s+is-active\b", text):
        return f"{prefix}service readiness"
    if re.search(r"(?i)\bsystemctl\s+(?:show|status)\b", text):
        return f"{prefix}service state"
    if re.search(r"(?i)\bpytest\b", text):
        return f"{prefix}pytest"
    if re.search(r"(?i)(?:^|[\\/\s])python(?:\.exe)?(?:\s|$)", text):
        calls = [
            match.group(1)
            for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]{2,63})\s*\(", text)
            if match.group(1).lower() not in _GENERIC_PYTHON_CALLS
        ]
        if calls:
            return f"{prefix}{calls[-1]} check"
        imported = re.search(
            r"\bfrom\s+[A-Za-z_][A-Za-z0-9_.]*\s+import\s+([A-Za-z_][A-Za-z0-9_]*)",
            text,
        )
        if imported:
            return f"{prefix}{imported.group(1)} verification"
        return f"{prefix}Python verification"
    if remote:
        return "remote verification"
    return text


def _reasoning_body(text: str) -> str:
    """Strip the reasoning marker (the ASCII ``[reasoning]`` tag from the agent, or
    a leading ``💭``) so only the reasoning content shows — no bracketed tag."""
    body = str(text).strip()
    if body.startswith("[reasoning]"):
        body = body[len("[reasoning]"):].strip()
    return body.lstrip("💭").strip()


def _reasoning_gist(text: str) -> str:
    """Collapse a reasoning chunk to one line: the model's own first sentence.

    Display-only — no summarization pass. Keeps the collapsed view honest (it's
    literally the start of what the model reasoned) while the full chain stays
    behind /show reasoning.
    """
    body = _reasoning_body(text)
    line = body.splitlines()[0] if body else ""
    for end in (". ", "? ", "! "):
        i = line.find(end)
        if 0 < i < 100:
            line = line[: i + 1]
            break
    if len(line) > 100:
        line = line[:99].rstrip() + "…"
    return f"{line}  · /show reasoning" if line else "thinking…  · /show reasoning"


class TurnRunnerMixin:
    def _reanchor_render(self) -> None:
        """Force prompt-toolkit to re-anchor and fully repaint the inline region.

        With ``full_screen=False`` PTK pins its render to the cursor's start row
        and never repaints rows above it. Once the terminal scrolls (long output,
        or the layout briefly exceeding the screen) that anchor drifts and the
        orphaned rows pin at the top, swallowing the transcript — the only manual
        cure is a resize or Ctrl+L. ``renderer.clear()`` does exactly that: erase
        + home the cursor so the next draw is full. Marshalled onto the app loop
        because the renderer is not thread-safe and turns run on a worker thread.
        """
        scrollback_enabled = getattr(self, "_scrollback_transcript_enabled", None)
        if callable(scrollback_enabled) and scrollback_enabled():
            # Native transcript lines are already committed above the live app.
            # Clearing the renderer here can disturb terminal-owned scrollback and
            # solves a drift problem that only the managed viewport can have.
            return
        app = getattr(self, "_app", None)
        if app is None:
            return

        def _do() -> None:
            try:
                renderer = getattr(app, "renderer", None)
                if renderer is not None:
                    renderer.clear()
            except Exception:
                pass
            try:
                app.invalidate()
            except Exception:
                pass

        loop = getattr(app, "loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(_do)
                return
            except Exception:
                pass
        _do()

    def _diffstat_fragments(self, text: str, base_style: str) -> list[tuple[str, str]]:
        """Split a trailing ' +A -B' edit diffstat into green/red fragments.

        Returns a single base-styled fragment when no diffstat is present, so
        non-edit activity lines render exactly as before.
        """
        match = _DIFFSTAT_RE.match(text)
        if not match:
            return [(base_style, text)]
        head, added, removed, location = (
            match.group(1), match.group(2), match.group(3), match.group(4) or ""
        )
        fragments = [
            (base_style, head + " "),
            ("class:diff-add", added),
            (base_style, " "),
            ("class:diff-del", removed),
        ]
        if location:
            fragments.append((base_style, location))
        return fragments

    def _tool_display_project_root(self) -> str:
        return str(
            getattr(getattr(self, "agent", None), "project_cwd", "")
            or os.environ.get("MO_PROJECT_CWD", "")
            or os.getcwd()
        )

    def _tool_line_fragments(
        self,
        label: str,
        *,
        repeat_count: int = 1,
        elapsed_text: str = "",
        timeout_text: str = "",
        outcome: str = "",
    ) -> list[tuple[str, str]]:
        """Build '▸ [tool] target  +A -B' — tool name as a fg-only chip, target dim
        and boundary-truncated, trailing edit diffstat kept green/red."""
        diff: list[tuple[str, str]] = []
        match = _DIFFSTAT_RE.match(label)
        if match:
            label = match.group(1).rstrip()
            diff = [("class:dim", "  "), ("class:diff-add", match.group(2)),
                    ("class:dim", " "), ("class:diff-del", match.group(3))]
            if match.group(4):
                diff.append(("class:dim", match.group(4)))
        parts = label.split(None, 1)
        verb = parts[0] if parts else label
        target = " ".join(parts[1].split()) if len(parts) > 1 else ""
        chip = _CHIP_SHORTEN.get(verb, verb)
        frags: list[tuple[str, str]] = [RAIL, ("class:dim", "▸ "), ("class:tool-chip", f"[{chip}]")]
        # generate_image has no path target — annotate it with the resolved image
        # backend + model so the operator sees the source (ChatGPT plan vs API key).
        if verb == "generate_image" and not target:
            try:
                from core import imagegen
                backend = imagegen.resolve_backend(getattr(self.agent, "config", None))
                if backend:
                    src = {"codex": "plan", "openai_compatible": "api"}.get(backend["kind"], backend["kind"])
                    target = f"{backend.get('model', '')} · {src}".strip(" ·")
            except Exception:
                target = ""
        target_limit = 52
        if elapsed_text:
            columns = int(getattr(self, "_terminal_columns", lambda: 80)() or 80)
            target_limit = max(16, min(52, columns - len(elapsed_text) - len(timeout_text) - 24))
        target = _shorten_target(
            _compact_project_paths(
                _test_display_target(target) if verb == "test_runner" else target,
                self._tool_display_project_root(),
            ),
            target_limit,
        )
        if target:
            frags.append(("class:dim", f" {target}"))
        if repeat_count > 1:
            frags.append(("class:dim", f" x{repeat_count}"))
        frags.extend(diff)
        if outcome:
            successful = outcome in {"passed", "done"}
            symbol = "✓" if successful else "✗"
            # Tool results follow the active skin: successful work uses its brand
            # color, while failures use its critical status color.
            style = "class:info" if successful else "class:notification-critical"
            frags.extend([("class:dim", "  · "), (style, f"{symbol} {outcome}")])
        if elapsed_text:
            frags.extend([("class:dim", "  · "), ("class:activity", elapsed_text)])
            if timeout_text:
                frags.append(("class:dim", f" / {timeout_text} timeout"))
        return frags

    def _live_tool_row_count(self) -> int:
        fragments = self._get_live_tool_fragments()
        return 1 + sum(text.count("\n") for _, text in fragments) if fragments else 0

    def _live_tool_process_snapshot(self) -> dict:
        label = str(getattr(self, "_live_tool_label", "") or "")
        if not label:
            return {}
        now = time.time()
        checked = float(getattr(self, "_live_tool_timeout_checked_at", 0.0) or 0.0)
        if now - checked < 0.25:
            return getattr(self, "_live_tool_snapshot", {})
        self._live_tool_timeout_checked_at = now
        from core.tooling.shell_processes import active_shell_processes

        started = float(getattr(self, "_live_tool_started_at", 0.0) or 0.0)
        thread_id = getattr(self, "_live_tool_thread_id", None)
        verb, _, target = label.partition(" ")
        target = " ".join(target.lower().split())
        eligible = [
            item for item in active_shell_processes()
            if float(item.get("started") or 0.0) >= started - 0.5
            and (thread_id is None or item.get("thread_id") == thread_id)
        ]
        matched = [item for item in eligible if target and target[:80] in str(item.get("command") or "").lower()]
        candidates = matched or (eligible if verb == "test_runner" else [])
        self._live_tool_snapshot = max(candidates, key=lambda item: item["started"]) if candidates else {}
        return self._live_tool_snapshot

    def _live_tool_output_changed(self) -> bool:
        snapshot = self._live_tool_process_snapshot()
        stamp = (snapshot.get("pid"), snapshot.get("last_output_at"))
        changed = stamp != getattr(self, "_live_tool_output_stamp", (None, None))
        self._live_tool_output_stamp = stamp
        return changed

    def _get_live_tool_fragments(self) -> list[tuple[str, str]]:
        if not getattr(self, "_show_tool_activity", True):
            return []
        label = str(getattr(self, "_live_tool_label", "") or "")
        if not label:
            return []
        started = float(getattr(self, "_live_tool_started_at", 0.0) or 0.0)
        now = time.time()
        timeout = int(getattr(self, "_live_tool_timeout", 0) or 0)
        snapshot = self._live_tool_process_snapshot()
        if snapshot:
            timeout = self._live_tool_timeout = max(1, int(snapshot.get("timeout") or 0))
        fragments = self._tool_line_fragments(
            label,
            elapsed_text=duration_text(now - started),
            timeout_text=duration_text(timeout) if timeout else "",
        )
        if snapshot:
            from .transcript_view import fit_cells

            width = max(12, int(getattr(self, "_terminal_columns", lambda: 80)()) - 4)
            progress = explainer_activity_lines(str(snapshot.get("output_tail") or ""))
            if progress:
                for line in progress:
                    fragments.extend([("", "\n"), RAIL, ("class:dim", fit_cells(line, width).rstrip())])
                return fragments
            last_output = float(snapshot.get("last_output_at") or 0.0)
            quiet = duration_text(now - (last_output or started))
            status = f"Running · no new output for {quiet}" if last_output else f"Running · no output yet ({quiet})"
            fragments.extend([("", "\n"), RAIL, ("class:dim", fit_cells(status, width).rstrip())])
            # Treat subprocess output as plain text, never terminal controls.
            tail = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", str(snapshot.get("output_tail") or ""))
            lines = ["".join(ch for ch in line if ch.isprintable() or ch == "\t").expandtabs(4) for line in tail.splitlines() if line.strip()]
            for line in lines[-2:]:
                fragments.extend([("", "\n"), RAIL, ("class:dim", fit_cells(line, width).rstrip())])
        return fragments

    def _finish_live_tool_activity(
        self,
        *,
        outcome: str = "",
        persist: bool = True,
        full_test_suite: bool = False,
    ) -> None:
        label = str(getattr(self, "_live_tool_label", "") or "")
        if not label:
            return
        started = float(getattr(self, "_live_tool_started_at", 0.0) or 0.0)
        elapsed_seconds = max(0.0, time.time() - started) if started else 0.0
        show_result = bool(outcome and (outcome not in {"done", "passed"} or (full_test_suite and elapsed_seconds >= 10.0)))
        displayed_outcome = "passed" if show_result and outcome == "done" else (outcome if show_result else "")
        elapsed = duration_text(elapsed_seconds) if show_result else ""
        self._live_tool_label = ""
        self._live_tool_started_at = 0.0
        self._live_tool_timeout = 0
        self._live_tool_timeout_checked_at = 0.0
        self._live_tool_snapshot = {}
        self._live_tool_thread_id = None
        if persist:
            self._add_tool_activity_line(label, outcome=displayed_outcome, elapsed_text=elapsed)

    def _add_tool_activity_line(
        self,
        tool_name: str,
        *,
        outcome: str = "",
        elapsed_text: str = "",
    ) -> None:
        """Render an indented '▸ [tool] target' activity line, colouring +A/-B."""
        parts = str(tool_name or "").split(None, 1)
        has_target = len(parts) > 1
        verb = parts[0] if parts else ""
        if verb == "complete_task":
            # Taskboard callbacks already render the evidence-gated state. A
            # second generic "[done]" tool chip looks like an independent claim
            # and adds no useful transcript evidence.
            return
        fragments = self._tool_line_fragments(
            tool_name,
            outcome=outcome,
            elapsed_text=elapsed_text,
        )
        key = (
            str(tool_name or "").strip()
            if (not has_target or verb in _REPEATABLE_TOOL_TARGETS)
            and outcome in {"", "done", "passed"}
            else ""
        )
        if key and key == getattr(self, "_last_tool_activity_key", ""):
            count = int(getattr(self, "_last_tool_activity_count", 1) or 1) + 1
            self._last_tool_activity_count = count
            replace = getattr(self, "_replace_last_transcript_fragments", None)
            if callable(replace) and replace(
                self._tool_line_fragments(
                    tool_name, repeat_count=count, outcome=outcome, elapsed_text=elapsed_text,
                ),
                required_style="class:tool-chip",
            ):
                return
            native = getattr(self, "_scrollback_transcript_enabled", None)
            # Native output is append-only. Suppress only an exact consecutive
            # successful display row; tool receipts and live activity stay intact.
            if callable(native) and native():
                canonical = list(getattr(self, "_lines", []) or [])
                if canonical[-len(fragments):] == fragments:
                    return
        self._last_tool_activity_key = key
        self._last_tool_activity_count = 1
        self._add_fragments_line(fragments)

    def _start_prompt_enhance(self, original: str) -> None:
        """Hybrid Ctrl+E: show the instant local enhancement now, refine in the bg.

        The deterministic local pass is instant (no provider call), so the input
        row updates with zero latency. The slower provider rewrite then runs on a
        daemon thread and only replaces the shown text if it's a real improvement
        AND the operator hasn't edited it meanwhile. The original is stashed so Esc
        reverts.
        """
        if getattr(self, "_enhance_in_flight", False):
            return
        original_stripped = str(original or "").strip()
        instant = ""
        try:
            fn = getattr(self.agent, "enhance_prompt_local", None)
            if callable(fn):
                instant = str(fn(original) or "").strip()
        except Exception:
            traceback.print_exc()
        if instant and instant != original_stripped:
            self._pre_enhance_text = original
            self._enhance_holder_active = True
            self._input_buf.text = instant
            self._input_buf.cursor_position = len(instant)
            self._set_notice("Enhanced · refining…")
        else:
            instant = ""
            self._set_notice("Refining…")
        self._enhance_in_flight = True
        self._enhance_shown_text = instant  # detect operator edits before the swap
        if self._app:
            self._app.invalidate()
        threading.Thread(target=self._run_enhance_thread, args=(original,), daemon=True).start()

    def _run_enhance_thread(self, original: str) -> None:
        refined = ""
        try:
            fn = getattr(self.agent, "enhance_prompt_for_input", None)
            if callable(fn):
                refined = str(fn(original) or "").strip()
        except Exception:
            traceback.print_exc()

        def _apply() -> None:
            self._enhance_in_flight = False
            shown = str(getattr(self, "_enhance_shown_text", "") or "")
            current = str(self._input_buf.text or "").strip()
            original_stripped = str(original or "").strip()
            # Swap to the provider refinement only if it's a genuine improvement
            # and the operator hasn't typed over the instant result meanwhile.
            untouched = (current == shown) or (not shown and current == original_stripped)
            if refined and refined != current and refined != original_stripped and untouched:
                self._pre_enhance_text = original
                self._enhance_holder_active = True
                self._input_buf.text = refined
                self._input_buf.cursor_position = len(refined)
                self._set_notice("Enhanced — Esc to revert")
            elif shown:
                self._set_notice("Enhanced — Esc to revert")
            else:
                self._set_notice("No change")
            if self._app:
                self._app.invalidate()

        loop = getattr(self._app, "loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(_apply)
                return
            except Exception:
                traceback.print_exc()
        _apply()

    def _maybe_notify_model_change(self) -> None:
        """Surface a provider/model fallback the MOMENT it happens.

        The runtime auto-falls-through the provider chain on rate/route/balance
        blocks, which can silently land on a weaker model (e.g. big-pickle) — the
        operator had to ask "why did you change model". This is called live from
        on_activity (the agent fires on_activity at the fallback point) so the
        notice appears immediately, not buffered until the turn finishes
        reporting. It dedupes against the last-notified model so each real change
        shows exactly once, and a post-turn call acts as a backstop.
        """
        try:
            now = (getattr(self.agent, "provider_name", ""), getattr(self.agent, "model", ""))
            if not now[1]:
                return
            last = getattr(self, "_last_notified_model", None)
            if last is None or now == last:
                return
            reason = str(getattr(self.agent, "last_fallback_notice", "") or "").strip()
            tail = f" - {reason}" if reason else ""
            kind = str(getattr(self.agent, "last_model_change_kind", "") or "")
            if kind == "provider_restore":
                message = f"↩ Model restored: selected {now[0]}/{now[1]}{tail}"
            elif kind in {"vision_switch", "vision_restore"}:
                message = f"◉ Vision route: now on {now[0]}/{now[1]}{tail}"
            else:
                message = f"⚠ Model fallback: now on {now[0]}/{now[1]}{tail}"
            self._add_line("notice", [("class:model-fallback", message)])
            self._last_notified_model = now
        except Exception:
            traceback.print_exc()

    def _maybe_warn_low_balance(self, *, threshold: float = 2.00) -> None:
        """Drop a colored low-balance notice into the transcript, once per session.

        Reads the cached DeepSeek balance (kept fresh by the footer); fires only on
        the official DeepSeek API and only the first time it goes under threshold.
        """
        if getattr(self, "_low_balance_notified", False):
            return
        try:
            from core.provider.deepseek_balance import balance_amount
            amount = balance_amount(getattr(self.agent, "active_provider", None))
        except Exception:
            amount = None
        if amount is None or amount >= threshold:
            return
        self._low_balance_notified = True
        try:
            self._add_line(
                "notice",
                [("class:low-balance", f"⚠ DeepSeek balance is low: ${amount:.2f} (below ${threshold:.2f}) - top up soon.")],
            )
        except Exception:
            traceback.print_exc()

    def _run_turn_thread(self, user_input: str):
        cancel_event = getattr(self, "_current_turn_cancel_event", None) or threading.Event()
        result = ""
        self._current_turn_cancel_event = cancel_event
        # Snapshot the model so a mid-turn provider fallback is surfaced to the
        # operator (e.g. deepseek-v4-pro -> big-pickle on a rate/route block).
        model_at_start = (getattr(self.agent, "provider_name", ""), getattr(self.agent, "model", ""))
        # Baseline for the live model-fallback notice: any change away from this
        # is surfaced the instant on_activity fires at the fallback point.
        self._last_notified_model = model_at_start
        main_worker_id = getattr(self, "_active_main_worker_id", "") or ""
        route_source = "user"
        queued_request = False
        if main_worker_id:
            registry = ensure_worker_registry(self.agent)
            record = registry.get(main_worker_id)
            route_source = str(getattr(record, "source", "") or "user")
            queued_request = str(getattr(record, "route", "") or "") == "queue"
            registry.update(main_worker_id, "running", "main MO turn running")
        self.busy = True
        self.activity_text = "preparing..."
        self.activity_started_at = time.time()
        self.board_text = ""
        self._reasoning_gist_shown = False  # one collapsed-reasoning gist line per turn
        self._last_reasoning_body = ""  # suppress back-to-back duplicate reasoning segments
        self._last_tool_activity_key = ""
        self._last_tool_activity_count = 0
        self._turn_edit_additions = 0
        self._turn_edit_deletions = 0
        # Re-anchor at the turn boundary so any render drift accumulated since the
        # last turn (full_screen=False pins above the anchor) can't persist.
        self._reanchor_render()

        try:
            def on_board_event(_event: dict):
                # One structured callback owns Terminal refreshes. The event is
                # informational; the shared board remains display truth, so
                # callback markup cannot forge task state.
                board = self.gateway.last_task_board
                if board:
                    self.board_text = board.render()
                    self._request_background_redraw()

            def on_token(token: str):
                self.activity_text = "receiving answer"
                self._request_background_redraw()

            def on_action(event: dict):
                if not isinstance(event, dict):
                    return
                tool = str(event.get("tool") or "")
                live_label = str(getattr(self, "_live_tool_label", "") or "")
                live_tool = live_label.split(None, 1)[0] if live_label else ""
                if tool in _TIMED_TOOL_VERBS and tool == live_tool:
                    if event.get("pending_background"):
                        # The live row already showed the launch. Keep background
                        # status polling transient so it cannot accumulate duplicate
                        # [test] rows between unrelated tool calls.
                        self._finish_live_tool_activity(persist=False)
                    else:
                        if event.get("blocked"):
                            outcome = "blocked"
                        elif not event.get("successful") or event.get("error"):
                            outcome = "failed"
                        else:
                            outcome = "passed" if tool == "test_runner" else "done"
                        self._finish_live_tool_activity(
                            outcome=outcome,
                            full_test_suite=bool(event.get("full_test_suite")),
                        )
                if not event.get("successful"):
                    return
                diffstat = str(event.get("diffstat") or "")
                match = _DIFFSTAT_RE.match(f"edit{diffstat}")
                if not match:
                    return
                self._turn_edit_additions += int(match.group(2)[1:])
                self._turn_edit_deletions += int(match.group(3)[1:])
                self._request_background_redraw()

            def on_activity(act: str):
                if str(act).startswith("Context supplied: "):
                    self._add_tool_activity_line("context supplied · " + str(act).partition(": ")[2])
                    self._request_background_redraw()
                    return
                if str(act).startswith("◈"):
                    self._add_line("notice", [("class:dim", str(act))])
                    self._request_background_redraw()
                    return
                self._finish_live_tool_activity()
                self.activity_text = act
                # Surface a provider/model fallback the instant it happens — the
                # agent calls on_activity at the fallback point, so this no longer
                # waits for the turn to finish reporting.
                self._maybe_notify_model_change()
                if self._show_tool_activity and "tooling" in act:
                    label = _tool_label_from_activity(act)
                    verb = label.split(None, 1)[0] if label else ""
                    if verb in _TIMED_TOOL_VERBS:
                        self._live_tool_label = label
                        self._live_tool_started_at = time.time()
                        self._live_tool_timeout = 0
                        self._live_tool_timeout_checked_at = 0.0
                        self._live_tool_snapshot = {}
                        self._live_tool_thread_id = threading.get_ident()
                    else:
                        self._add_tool_activity_line(label)
                self._request_background_redraw()

            interim_seen: list[str] = []

            def on_assistant_text(text: str, metadata: dict | None = None):
                # Interim prose that came alongside a tool call. Keep it visible
                # on the in-progress rail without making it look like the final gated
                # answer. Prose keeps the canonical response colour; the rail, rather
                # than a dim duplicate text style, owns that lifecycle distinction.
                # This is also the funnel the autopilot worktree-child streams through,
                # so the /show toggles gate both live turns and the forwarded sweep.
                meta = metadata if isinstance(metadata, dict) else {}
                clean = str(text or "").strip()
                if not clean:
                    return
                is_reasoning = clean.startswith("💭") or clean.startswith("[reasoning]")
                is_tool = clean.startswith("▸") or "tooling (" in clean
                if is_tool and not getattr(self, "_show_tool_activity", True):
                    return
                # Colour by lifecycle: reasoning keeps its dim italic treatment,
                # tool activity stays dim, and prose accompanying tool calls uses
                # one restrained activity mark plus muted body text. Final prose
                # remains the only path that receives answer/report typography.
                if is_reasoning:
                    if getattr(self, "_show_reasoning", False):
                        body = _reasoning_body(clean)
                        norm = " ".join(body.split())
                        # Verbose reasoners (e.g. DeepSeek) often re-emit the same
                        # segment back-to-back; render each distinct segment once so
                        # the transcript doesn't fill with duplicated thinking.
                        if norm and norm == getattr(self, "_last_reasoning_body", ""):
                            return
                        self._last_reasoning_body = norm
                        interim_seen.append(clean)
                        self._add_line("reasoning", [("class:reasoning", f"⋯ {body}")])
                    elif not getattr(self, "_reasoning_gist_shown", False):
                        # Collapsed view: one gist line for the turn, suppress the rest.
                        self._reasoning_gist_shown = True
                        self._add_line("reasoning", [("class:reasoning", f"⋯ {_reasoning_gist(clean)}")])
                elif is_tool:
                    interim_seen.append(clean)
                    if "tooling (" in clean:
                        # Forwarded activity ("tooling (tool target +A -B)...") — render
                        # with the same chip + coloured-diffstat treatment as live tools
                        # (the raw string put +A/-B inside the parens, so the plain
                        # diffstat renderer never coloured them).
                        self._add_tool_activity_line(_tool_label_from_activity(clean))
                    else:
                        self._add_line("tool", self._diffstat_fragments(clean, "class:dim"))
                elif meta.get("with_tool_calls"):
                    interim_seen.append(clean)
                    logical_lines: list[str] = []
                    for raw_line in clean.splitlines():
                        line = raw_line.strip()
                        if not line:
                            if logical_lines and logical_lines[-1]:
                                logical_lines.append("")
                            continue
                        bullet = re.match(r"^([-*•]\s+)(.+)$", line)
                        body = bullet.group(2) if bullet else line
                        sentences = [
                            sentence.strip()
                            for sentence in re.split(
                                r'(?<=[.!?])\s+(?=[A-Z0-9"“‘(])',
                                body,
                            )
                            if sentence.strip()
                        ]
                        for sentence_index, sentence in enumerate(sentences):
                            marker = bullet.group(1) if bullet and sentence_index == 0 else ""
                            logical_lines.append(f"{marker}{sentence}")
                    while logical_lines and not logical_lines[-1]:
                        logical_lines.pop()

                    first_content = True
                    for line in logical_lines:
                        if not line:
                            self._add_line("tool", [("class:activity", "  ")])
                            continue
                        is_first = first_content
                        decorated = line
                        if is_first and not re.match(
                            r"^\s*(?:[-*•]\s+)?(?:\*\*|`)", decorated
                        ):
                            lead = re.match(
                                r"^(\s*(?:[-*•]\s+)?)(\S+)\s+(\S+)(.*)$",
                                decorated,
                            )
                            if lead:
                                decorated = (
                                    f"{lead.group(1)}**{lead.group(2)} "
                                    f"{lead.group(3)}**{lead.group(4)}"
                                )
                        muted = [
                            (
                                "class:response-bullet-head"
                                if is_first and style == "class:response-bullet-head"
                                else "class:dim",
                                value,
                            )
                            for style, value in response_line_fragments(decorated, inline_only=True)
                        ]
                        self._add_line(
                            "tool",
                            [("class:activity", "⋯ " if is_first else "  "), *muted],
                        )
                        first_content = False
                    # This is assistant prose from the current turn even though
                    # the rail marks it as an in-progress tool preamble. Preserve
                    # that continuity so the final block does not add a false
                    # turn boundary between the two sentences.
                    self._last_speaker = "MO"
                else:
                    interim_seen.append(clean)
                    self._add_response_block(clean)
                self._request_background_redraw()

            def on_operator_visual(ansi):
                # Render the visual INSIDE the transcript as fragments, so it appears
                # inline where it belongs — drawn by prompt_toolkit and scrolling with
                # the conversation, not dumped to the raw terminal. The (text-only)
                # model never sees this; it is purely for the operator.
                self._add_ansi_block(str(ansi or ""))

            def on_operator_image(path):
                # A generated/produced image FILE: render it to inline ANSI in the
                # transcript (same render show_image uses), captioned with a running
                # [Image #N] reference so the operator can point at it. MO Desktop
                # handles the same signal by previewing the file in its cube panel.
                try:
                    from core.visualize.terminal_image import available, render
                    if available():
                        ansi = render(str(path or ""))
                        if not ansi.startswith("[image render"):
                            self._operator_image_count = getattr(self, "_operator_image_count", 0) + 1
                            caption = f"\x1b[2m[Image #{self._operator_image_count}]\x1b[0m\n"
                            self._add_ansi_block(caption + ansi)
                except Exception:
                    pass

            try:
                gateway_kwargs = {
                    "on_board_event": on_board_event,
                    "on_token": on_token,
                    "on_activity": on_activity,
                    "on_action": on_action,
                    "cancel_event": cancel_event,
                    "on_assistant_text": on_assistant_text,
                    "on_operator_visual": on_operator_visual,
                    "on_operator_image": on_operator_image,
                }
                gateway_sig = inspect.signature(self.gateway.run_turn)
                accepts_gateway_kwargs = any(
                    p.kind == p.VAR_KEYWORD for p in gateway_sig.parameters.values()
                )
                if "route_source" in gateway_sig.parameters or accepts_gateway_kwargs:
                    gateway_kwargs["route_source"] = route_source
                if "queued_request" in gateway_sig.parameters or accepts_gateway_kwargs:
                    gateway_kwargs["queued_request"] = queued_request
                result = self.gateway.run_turn(user_input, **gateway_kwargs)
            except Exception as exc:
                detail = clean_provider_error(str(exc))
                result = "\n".join([
                    "MO interface error: turn failed",
                    "  where: TUI turn runner",
                    "Fix: try again or run /status; check monitor if this repeats.",
                    f"  detail: {detail}",
                ])

            if hasattr(self.agent, "autosave_session"):
                self.agent.autosave_session()
            if hasattr(self.agent, "consume_quarantine_notice"):
                q_notice = self.agent.consume_quarantine_notice()
                if q_notice:
                    self._add_line("notice", [("class:activity", q_notice)])
            if hasattr(self.agent, "consume_handoff_notice"):
                notice = self.agent.consume_handoff_notice()
                if notice:
                    self._add_line("notice", [("class:activity", notice)])
            result_clean = str(result or "").strip()
            already_shown = bool(result_clean) and result_clean in interim_seen
            if result and not str(result).startswith("[ABORTED]") and not already_shown:
                hide = bool(getattr(self.agent, "_turn_hide_response_marker", False))
                self.agent._turn_hide_response_marker = False
                self._add_response_block(result, hide_marker=hide)
            # Keep the board's final state visible until the next turn.
            elif str(result or "").startswith("[ABORTED]"):
                self._add_line("system", [("class:dim", "stopped current turn")])
            if main_worker_id:
                from core.worker import summarize_worker_result

                result_text = str(result or "")
                if result_text.startswith("[ABORTED]"):
                    state = "cancelled"
                    note = "main MO turn stopped"
                else:
                    state = "blocked" if assistant_result_is_incomplete(result_text) else "completed"
                    note = "main MO turn finished"
                result_summary, evidence = summarize_worker_result(result)
                ensure_worker_registry(self.agent).update(
                    main_worker_id,
                    state,
                    note,
                    result_summary=result_summary,
                    evidence=evidence,
                )
                self._active_main_worker_id = ""
        finally:
            self._finish_live_tool_activity()
            self.agent._turn_hide_response_marker = False
            if self._current_turn_cancel_event is cancel_event:
                self._current_turn_cancel_event = None
            self.busy = False
            self._busy_escape_count = 0
            self.activity_text = ""
            self.activity_started_at = 0.0
            self._maybe_notify_model_change()
            self._maybe_warn_low_balance()
            # Flush any PRT report deferred during this turn (kept out of the main response body).
            _pending_prt = getattr(self, "_pending_prt_lines", None)
            if _pending_prt:
                self._pending_prt_lines = None
                self._add_fragments_block([
                    [(_style, f"  {_line}" if _line else "")]
                    for _style, _line in _pending_prt
                ])
                self._reanchor_render()
            # extrathink confirmation is ambient, not a text banner: the activity lane
            # and footer "MO" glow gold while MO runs its own method (see display_delegates).
            # Completed taskboards leave the final MO report in transcript; incomplete
            # boards stay visible so unresolved work remains clear. Re-anchor here too
            # since the layout shrinks at turn end (board/activity lane removed), the
            # case most likely to orphan rows at the top.
            self._reanchor_render()
            requeue_live_steers = getattr(self, "_requeue_unconsumed_live_steers", None)
            if callable(requeue_live_steers):
                requeue_live_steers(result)
            apply_pending_model = getattr(self, "_apply_pending_palette_model_selection", None)
            if callable(apply_pending_model):
                apply_pending_model()
            self._process_next_queued_input()
