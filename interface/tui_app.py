"""Prompt-toolkit app bootstrap mixin for `MoTui`."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
import urllib.parse

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.renderer import Renderer

from .activity import clip_text_to_cells, status_line_mode
from .formatting import DOTS4_PHASE_SECONDS
from .hints import HINT_INTERVAL
from .input import SlashAndPathCompleter
from .terminal_host import (
    publish_terminal_title,
    reset_terminal_background,
    sync_terminal_background_to_skin,
    terminal_busy_title,
    terminal_identity_title,
)
from .theme import build_tui_color_depth, build_tui_style

LOGO_LINES: tuple[str, ...] = (
    "  █   █   ███ ",
    "  ██ ██  █   █",
    "  █ █ █  █   █",
    "  █   █   ███ ",
)

# Windows Terminal can drop a session-scoped OSC 11 color when its renderer is
# recreated. Reassert infrequently so native scrollback stays aligned with MO.
_TERMINAL_BACKGROUND_RESYNC_SECONDS = 5.0
# Poll non-visual context at a quiet cadence. Working chrome uses the existing
# spinner's frame duration instead; an unsent draft does not stop real work.
# Idle status changes are event-driven on top of that existing frame clock: a
# compact token changes only at hint-rotation or notice/context expiry boundaries.
_UI_REFRESH_SECONDS = 1.0
# Key edits already invalidate Prompt Toolkit. Yield only the decorative working
# heartbeat for two existing frame periods so it cannot compete with a typing
# burst; data/output redraws remain immediate and are never queued behind this.
_INPUT_ACTIVITY_REDRAW_QUIET_SECONDS = DOTS4_PHASE_SECONDS * 2


class _WindowsInlineRenderer(Renderer):
    """Keep PTK's relative cursor aligned with the console's resized rows."""

    def _sync_reflowed_cursor(self) -> None:
        if self._last_screen is None or self._last_size is None:
            return
        # Win32Output reserves the last column; reflow uses the whole buffer.
        width = self.output.get_win32_screen_buffer_info().dwSize.X
        if width >= self._last_size.columns or width <= 0:
            return
        cursor = self._cursor_pos
        extra_rows = 0
        column = 0
        for y in range(cursor.y + 1):
            row = self._last_screen.data_buffer[y]
            end = cursor.x if y == cursor.y else max(
                (x + char.width for x, char in row.items() if char.char not in ("", " ")),
                default=0,
            )
            column = 0
            for x in range(min(end, self._last_size.columns)):
                cell_width = row[x].width
                if column + cell_width > width:
                    extra_rows += 1
                    column = 0
                column += cell_width
        self._cursor_pos = Point(
            x=column % width,
            y=cursor.y + extra_rows + column // width,
        )

    def erase(self, leave_alternate_screen: bool = True) -> None:
        # Both transcript commits and resize handling erase before redrawing.
        self._sync_reflowed_cursor()
        super().erase(leave_alternate_screen=leave_alternate_screen)

    def render(self, app, layout, is_done: bool = False) -> None:
        # An animation/input redraw can arrive before PTK's resize poll.
        self._sync_reflowed_cursor()
        super().render(app, layout, is_done=is_done)


def _status_refresh_token(state, *, now: float | None = None) -> tuple[object, ...]:
    """Describe only time-driven changes to the existing status row."""
    current = time.time() if now is None else float(now)
    mode = status_line_mode(state, now=current)
    if mode == "notice":
        return (
            mode,
            str(getattr(state, "_notice_text", "") or ""),
            float(getattr(state, "_notice_until", 0.0) or 0.0),
        )
    if mode == "hidden":
        return (mode,)
    contextual_hint = str(getattr(state, "_contextual_idle_hint", "") or "")
    contextual_until = float(
        getattr(state, "_contextual_idle_hint_until", 0.0) or 0.0
    )
    if contextual_hint and current <= contextual_until:
        return ("context", contextual_hint, contextual_until)
    agent = getattr(state, "agent", None)
    if getattr(agent, "_hints_enabled", True):
        return ("hint", int(current / HINT_INTERVAL))
    return ("idle",)


def _clear_terminal() -> None:
    """Clear screen + scrollback and home the cursor before the TUI renders.

    Only emits when stdout is a real terminal, so redirected/piped output (tests,
    ``mo -p``) is never polluted with escape codes.
    """
    out = sys.stdout
    if not getattr(out, "isatty", lambda: False)():
        return
    try:
        out.write("\033[3J\033[2J\033[H")  # scrollback, screen, cursor home
        out.flush()
    except Exception:
        pass


def _clear_terminal_screen() -> None:
    """Erase only the visible TUI before restoring the terminal's own colors.

    Unlike ``_clear_terminal``, this deliberately preserves native scrollback.
    It must run after the host background reset so erased cells use the terminal
    profile color instead of leaving a stale MO-colored render region behind.
    """
    out = sys.stdout
    if not getattr(out, "isatty", lambda: False)():
        return
    try:
        out.write("\033[2J\033[H")  # visible screen + cursor home; keep scrollback
        out.flush()
    except Exception:
        pass


def _active_provider_key_missing(agent) -> str:
    """Best-effort: name the active provider's logical credential if required but
    absent (so the first turn would fail on a TUI that looks ready), else "".

    Providers resolve keys through MO's canonical credential broker. Providers
    that authenticate externally or run on loopback need no brokered key.
    """
    try:
        cfg = getattr(agent, "config", {}) or {}
        if not isinstance(cfg, dict):
            return ""
        active = str(getattr(agent, "provider_name", "") or (cfg.get("model") or {}).get("default") or "")
        for provider_cfg in cfg.get("providers") or []:
            if not isinstance(provider_cfg, dict) or str(provider_cfg.get("name") or "") != active:
                continue
            env = str(provider_cfg.get("api_key_env") or "")
            kind = str(provider_cfg.get("type") or provider_cfg.get("api_mode") or "").lower()
            if kind == "mock" or kind == "codex_responses":
                return ""
            try:
                host = (
                    urllib.parse.urlsplit(str(provider_cfg.get("base_url") or "")).hostname
                    or ""
                ).lower()
            except ValueError:
                host = ""
            if host in {"127.0.0.1", "localhost", "::1"}:
                return ""
            from core.state.secrets import secret_status
            if env and secret_status(env, config=cfg, service="providers").present:
                return ""
            auth = provider_cfg.get("auth_path")
            if auth and Path(str(auth)).expanduser().is_file():
                return ""
            return env or "api_key_env"
    except Exception:
        pass
    return ""


def startup_header_fragment_lines(agent, gateway, *, columns: int | None = None) -> list[list[tuple[str, str]]]:
    """Return the cell-bounded launch overview through native scrollback."""
    from .activity import fit_fragments_to_cells
    from .input import terminal_columns
    from .launch_overview import startup_overview_fragment_lines, _text, _row, _safe_fragment
    from .native_terminal import _startup_runtime_summary, _startup_attention_summary

    columns = max(20, int(columns if columns is not None else terminal_columns()) - 1)

    provider = str(getattr(agent, "provider_name", "") or "unknown")
    runtime = _startup_runtime_summary(agent, gateway)
    # The persistent footer owns project/model metadata, while the OSC title
    # retains host discovery identity. Startup stays a compact launch overview
    # with one command-discovery row instead of restating either owner.
    info: tuple[tuple[str, str], ...] = (
        ("class:response-heading", "MO v1.0"),
        ("class:dim", runtime if runtime else "clear"),
    )
    rows: list[list[tuple[str, str]]] = []
    hint_line = [
        ("class:info", "/help"), ("class:dim", "  ·  "),
        ("class:info", "/status"), ("class:dim", "  ·  "),
        ("class:info", "/dashboard"),
    ]
    for index, logo in enumerate(LOGO_LINES):
        fragments: list[tuple[str, str]] = [("class:logo", logo)]
        if index < len(info):
            style, text = info[index]
            fragments.extend([("", "  "), (style, text)])
        elif index == len(info):
            fragments.append(("", "  "))
            fragments.extend(hint_line)
        rows.append(fragments)
    rows.extend(startup_overview_fragment_lines(agent, columns=columns))
    attention = _startup_attention_summary(agent)
    if attention:
        rows.append(_row("Attention", _text(attention), "/status", columns, style="class:low-balance"))
    # One paused-work row: a saved board and interrupted request may describe
    # the same work. Keep unrelated runtime attention above, not another copy.
    open_n = 0
    try:
        resumable = gateway.resumable_board() if gateway is not None else None
        open_n = int(resumable.open_count()) if resumable is not None else 0
    except Exception:
        pass
    pending = getattr(agent, "_pending_interrupted_work", {})
    paused = isinstance(pending, dict) and bool(str(pending.get("user") or "").strip())
    if open_n > 0 or paused:
        summary = f"{open_n} open task(s)" if open_n > 0 else "Paused request"
        rows.append(_row("Work", f"type 'resume' · {summary}", "/now", columns))
    # Prior conversation: a fresh terminal opens its own empty slot, so earlier
    # chats are invisible without this. Sits beneath the header beside the
    # resumable-task row — above the logo it reads as an error, not an offer.
    try:
        from core.runtime.continuity import render_previous_session_hint

        previous_session = render_previous_session_hint(agent)
        if previous_session:
            summary = _text(previous_session).removeprefix("previous conversation: ").removesuffix(" - type /session to reopen it")
            rows.append(_row("Last chat", summary, "/session", columns))
    except Exception:
        pass
    # Cold-start personalization: if MO has no operator name yet, invite the user to
    # seed the profile. Name auto-capture (terms_learning.capture_operator_name)
    # handles "I'm <Name>"; this nudge covers everyone who doesn't say it.
    prof = getattr(agent, "profile", None)
    if prof is not None and not str(getattr(prof, "user_name", "") or "").strip():
        rows.append(_row("Welcome", "Tell MO your name to personalize", "/profile", columns))
    # If the active provider has no key, the first turn would fail with a provider
    # error on a TUI that looks ready — surface it upfront and point to the fix.
    missing_env = _active_provider_key_missing(agent)
    if missing_env:
        rows.append(_row("Attention", f"No key for {_text(provider)}", "/doctor", columns, style="class:low-balance"))
    return [fit_fragments_to_cells([(style, _safe_fragment(text)) for style, text in row], columns)[0] for row in rows]


class TuiAppMixin:
    def _trace_palette_state(self, app) -> None:
        """Record palette visibility before the frame that should render it."""
        palette = getattr(self, "_palette", None)
        state = (bool(getattr(palette, "open", False)), bool(getattr(palette, "result_active", False)))
        previous = getattr(self, "_last_palette_state", None)
        if state == previous:
            return
        self._last_palette_state = state
        if previous is None and not state[0]:
            return
        monitor = getattr(getattr(self, "gateway", None), "monitor", None)
        if getattr(monitor, "enabled", False):
            monitor.emit("session_event", {
                "kind": "palette_state",
                "session_id": str(getattr(getattr(self.agent, "session", None), "session_id", "") or ""),
                "open": state[0], "result_active": state[1],
                "render_counter": app.render_counter,
            })

    def _trace_palette_render(self, app) -> None:
        """Compare the palette's item page with Prompt Toolkit terminal cells."""
        palette = getattr(self, "_palette", None)
        if not getattr(palette, "open", False) or getattr(palette, "result_active", False):
            self._last_palette_render_signature = None
            return
        monitor = getattr(getattr(self, "gateway", None), "monitor", None)
        if not getattr(monitor, "enabled", False):
            return
        try:
            screen = getattr(app.renderer, "_last_screen", None)
            window = getattr(self, "_palette_window", None)
            position = (
                screen.visible_windows_to_write_positions.get(window)
                if screen is not None and window is not None else None
            )
            items, start, end = palette._visible_items(self._terminal_columns())
            page = items[start:end]
            lines: list[str] = []
            if position is not None:
                # Inspect only this window's cells. Transcript text can contain the
                # same words and would otherwise make an absent item look drawn.
                for y in range(position.ypos, position.ypos + min(position.height, 12)):
                    row = screen.data_buffer.get(y, {})
                    lines.append("".join(
                        str(getattr(row.get(x), "char", " "))
                        for x in range(position.xpos, position.xpos + min(position.width, 512))
                    ))
            missing: list[int] = []
            clipped: list[int] = []
            scan_width = min(position.width, 512) if position is not None else 0
            for index, item in enumerate(page):
                position_number = start + index + 1
                visible_label = clip_text_to_cells(item.label, max(0, scan_width - 2))
                is_clipped = visible_label != item.label
                probe = visible_label.removesuffix("…") if is_clipped else item.label
                if not probe or not any(probe in line for line in lines):
                    missing.append(position_number)
                elif is_clipped:
                    clipped.append(position_number)
            view = "model" if items and all(item.value.startswith("/model ") for item in items) else "commands"
            bounds = (
                [position.xpos, position.ypos, position.width, position.height]
                if position is not None else None
            )
            session_id = str(getattr(getattr(self.agent, "session", None), "session_id", "") or "")
            signature = (session_id, view, len(items), start, end, palette.selected_idx,
                         tuple(bounds or ()), tuple(missing), tuple(clipped),
                         any("COMMANDS" in line for line in lines))
            if signature == getattr(self, "_last_palette_render_signature", None):
                return
            self._last_palette_render_signature = signature
            monitor.emit("session_event", {
                "kind": "palette_render", "session_id": session_id, "view": view,
                "item_count": len(items), "page": [start + 1, end],
                "selected": palette.selected_idx + 1,
                "window": bounds, "title_drawn": signature[-1],
                "drawn_item_count": len(page) - len(missing) - len(clipped),
                "clipped_item_positions": clipped, "missing_item_positions": missing,
                "scan_limited": bool(position and position.width > 512),
                "render_counter": app.render_counter,
            })
        except Exception:
            # Observation must never interrupt the terminal repaint.
            return

    def _after_terminal_render(self, app) -> None:
        self._trace_terminal_geometry(app, stage="after")
        self._trace_palette_render(app)

    def _before_terminal_render(self, app) -> None:
        self._trace_terminal_geometry(app, stage="before")
        self._trace_palette_state(app)

    def _trace_terminal_geometry(self, app, *, stage: str) -> None:
        """Record size transitions in the existing monitor without changing rendering."""
        monitor = getattr(getattr(self, "gateway", None), "monitor", None)
        if not getattr(monitor, "enabled", False):
            return
        try:
            size = app.output.get_size()
            geometry = (size.columns, size.rows)
            previous = getattr(self, "_traced_terminal_geometry", None)
            if geometry == previous:
                return
            self._traced_terminal_geometry = geometry
            renderer = app.renderer
            rendered = getattr(renderer, "_last_size", None)
            cursor = getattr(renderer, "_cursor_pos", None)
            payload = {
                "kind": "terminal_geometry",
                "session_id": str(getattr(getattr(self.agent, "session", None), "session_id", "")),
                "stage": stage,
                "size": geometry,
                "previous_size": previous,
                "rendered_size": (rendered.columns, rendered.rows) if rendered else None,
                "rendered_cursor": (cursor.x, cursor.y) if cursor else None,
                "full_screen": bool(renderer.full_screen),
                "render_counter": app.render_counter,
                "output": type(app.output).__name__,
            }
            console_info = getattr(app.output, "get_win32_screen_buffer_info", None)
            if callable(console_info):
                try:
                    info = console_info()
                    payload["console_cursor"] = (info.dwCursorPosition.X, info.dwCursorPosition.Y)
                    payload["console_window"] = (
                        info.srWindow.Left, info.srWindow.Top,
                        info.srWindow.Right, info.srWindow.Bottom,
                    )
                except (AttributeError, OSError, NotImplementedError):
                    pass
            monitor.emit("session_event", payload)
        except Exception:
            # Observing a resize must never interrupt the terminal's own repaint.
            return

    def _request_background_redraw(self) -> None:
        """Publish changed interface state through Prompt Toolkit's coalesced redraw."""
        if self._app:
            self._app.invalidate()

    def _flush_background_updates(self) -> bool:
        """Flush transcript lines held only while a workspace surface owns the screen."""
        return bool(
            self._native_scrollback_pending and self._flush_deferred_scrollback()
        )

    def _working_animation_active(self) -> bool:
        """Return the real work state shared by the TUI and terminal host."""
        registry = getattr(getattr(self, "agent", None), "workers", None)
        prt_running = bool(registry and any(
            record.kind == "prt" and record.state == "running"
            for record in registry.active()
        ))
        return bool(self.busy or self._goal_running or self._goal_worker_active or prt_running)

    def _decorative_working_redraw_due(self, *, now: float | None = None) -> bool:
        """Yield the periodic animation briefly while key edits render themselves."""
        current = time.monotonic() if now is None else float(now)
        last_input = float(getattr(self, "_last_input_change_at", 0.0) or 0.0)
        return current - last_input >= _INPUT_ACTIVITY_REDRAW_QUIET_SECONDS

    def _sync_terminal_background(self, *, now: float | None = None, force: bool = False) -> None:
        """Restore MO's host-owned background at boundaries or the periodic backstop."""
        app = getattr(self, "_app", None)
        if app is None:
            return

        current = time.monotonic() if now is None else float(now)
        next_sync = float(getattr(self, "_terminal_background_sync_at", 0.0))
        if not force and current < next_sync:
            return
        self._terminal_background_sync_at = current + _TERMINAL_BACKGROUND_RESYNC_SECONDS
        output = getattr(app, "output", None)

        def publish() -> None:
            sync_terminal_background_to_skin(output=output)

        loop = getattr(app, "loop", None)
        if loop is not None and hasattr(loop, "call_soon_threadsafe"):
            loop.call_soon_threadsafe(publish)
        else:
            publish()

    def _terminal_session_topic(self) -> str:
        """Return one redacted, session-stable first-user-message preview."""
        agent = getattr(self, "agent", None)
        session = getattr(agent, "session", None)
        sessions = getattr(agent, "_sessions", None)
        if session is None or sessions is None:
            return ""
        session_key = (
            str(getattr(session, "session_id", "") or id(session)),
            str(getattr(sessions, "current_name", "") or ""),
        )
        if session_key != getattr(self, "_terminal_title_topic_key", None):
            self._terminal_title_topic_key = session_key
            self._terminal_title_topic = ""
        cached = str(getattr(self, "_terminal_title_topic", "") or "")
        if cached:
            return cached
        preview = getattr(sessions, "_first_user_preview", None)
        if not callable(preview):
            return ""
        try:
            candidate = preview(getattr(session, "messages", ()), limit=160)
            from core.tooling.sandbox import redact_sensitive_text

            candidate = redact_sensitive_text(candidate)
        except Exception:
            return ""
        candidate = " ".join(str(candidate or "").split())
        if candidate:
            self._terminal_title_topic = candidate
        return candidate

    def _terminal_task_progress(self) -> tuple[int, int]:
        """Return the current row ordinal and total from the canonical board."""
        gateway = getattr(self, "gateway", None)
        agent = getattr(self, "agent", None)
        board = (
            getattr(gateway, "last_task_board", None)
            or getattr(agent, "_active_task_board", None)
        )
        tasks = list(getattr(board, "tasks", ()) or ())
        if not tasks:
            return 0, 0
        current = None
        next_ready = getattr(board, "next_ready_task", None)
        if callable(next_ready):
            try:
                current = next_ready()
            except Exception:
                current = None
        current_id = str(getattr(current, "id", "") or "")
        for index, task in enumerate(tasks, start=1):
            if task is current or (current_id and str(getattr(task, "id", "")) == current_id):
                return index, len(tasks)
        for index, task in enumerate(tasks, start=1):
            if str(getattr(task, "status", "") or "") in {"active", "blocked", "pending"}:
                return index, len(tasks)
        return len(tasks), len(tasks)

    def _stable_terminal_title(self) -> str:
        agent = getattr(self, "agent", None)
        task_current, task_total = self._terminal_task_progress()
        return terminal_identity_title(
            getattr(agent, "instance_id", ""),
            getattr(agent, "model", ""),
            topic=self._terminal_session_topic(),
            task_current=task_current,
            task_total=task_total,
            provider=getattr(agent, "provider_name", ""),
        )

    def _sync_terminal_title(self, busy: bool, *, now: float | None = None) -> None:
        """Advance the taskbar-visible title spinner, or restore the stable identity."""
        identity = self._stable_terminal_title()
        title = terminal_busy_title(now, identity=identity) if busy else identity
        if title == str(getattr(self, "_terminal_title_text", "")):
            return

        app = getattr(self, "_app", None)
        if app is None:
            return
        self._terminal_title_text = title
        output = getattr(app, "output", None)

        def publish() -> None:
            publish_terminal_title(title, output=output)

        loop = getattr(app, "loop", None)
        if loop is not None and hasattr(loop, "call_soon_threadsafe"):
            loop.call_soon_threadsafe(publish)
        else:
            publish()

    def _seed_startup_header(self) -> None:
        agent = getattr(self, "agent", None)
        gateway = getattr(self, "gateway", None)
        columns = getattr(self, "_terminal_columns", lambda: 80)()
        for fragments in startup_header_fragment_lines(agent, gateway, columns=columns):
            if hasattr(self, "_add_fragments_line"):
                self._add_fragments_line(fragments)
            else:
                # Compatibility for narrow TuiAppMixin harnesses that only test
                # the application contract and do not include transcript mixins.
                logo_text = fragments[0][1] if fragments else ""
                self._add("class:logo", logo_text)
        self._add("", "")

    def run(self):
        self._input_buf = Buffer(completer=SlashAndPathCompleter(self.agent), complete_while_typing=False, on_text_changed=self._on_input_changed, history=self._input_history)

        from .keybindings import build_tui_key_bindings
        from .layout import build_tui_root, prompt_prefix

        kb = build_tui_key_bindings(self)
        root = build_tui_root(self, self._input_buf, prompt_prefix())

        style = build_tui_style()
        color_depth = build_tui_color_depth()

        # Ordinary terminals stay inline for native selection and scrollback.
        # MO Shell owns a private projected ConPTY screen, so let this same TUI
        # fill that screen and keep its footer anchored to the final row.
        self._terminal_full_screen = os.environ.get("MO_SHELL_TERMINAL") == "1"
        self._app = Application(
            layout=Layout(root, focused_element=self._input_buf),
            key_bindings=kb,
            full_screen=self._terminal_full_screen,
            mouse_support=False,
            paste_mode=True,
            style=style,
            color_depth=color_depth,
            # The frame clock already coalesces animation. Prompt Toolkit's
            # postponement loop can busy-reschedule on Windows between frames.
            max_render_postpone_time=0,
            before_render=lambda app: self._before_terminal_render(app),
            after_render=lambda app: self._after_terminal_render(app),
        )
        if (
            not self._terminal_full_screen
            and callable(getattr(self._app.output, "get_win32_screen_buffer_info", None))
        ):
            renderer = self._app.renderer
            self._app.renderer = _WindowsInlineRenderer(
                renderer.style,
                renderer.output,
                mouse_support=renderer.mouse_support,
                cpr_not_supported_callback=renderer.cpr_not_supported_callback,
            )

        from .live_control import start_terminal_live_control

        self._live_control_host = start_terminal_live_control(self)
        self.agent._dashboard_dispatch = self._dashboard_action

        self._sync_terminal_background(force=True)
        # Start from the stable identity before the refresh thread publishes work frames.
        stable_title = self._stable_terminal_title()
        publish_terminal_title(
            stable_title,
            output=getattr(self._app, "output", None),
        )
        self._terminal_title_text = stable_title

        # Invalidate while the app is alive; prompt_toolkit is not running yet
        # when this thread starts, so don't exit just because is_running is false.
        self._refresh_stop.clear()

        # full_screen=False keeps MO in the main screen buffer (native selection +
        # wheel scrollback), but prompt_toolkit then anchors its render region at
        # the cursor's start row and never repaints rows above it. Anything printed
        # before launch (e.g. the `mo_trace serve` banner) would stay pinned at the
        # top AND steal rows from the height calc, hiding the bottom of the
        # transcript. Clear to a fresh top-left so the app owns the whole terminal.
        _clear_terminal()
        # Native-scrollback output must be seeded after the launch clear or the
        # clear sequence would erase the boot header we just printed.
        self._seed_startup_header()

        last_status_refresh_token = _status_refresh_token(self)

        def _refresh_loop():
            from core.design.terminal_handoff import claim_terminal_request
            from core.runtime.instance import get_instance_id

            nonlocal last_status_refresh_token
            next_design_context_poll = 0.0

            while not self._refresh_stop.is_set():
                now = time.monotonic()
                wall_time = time.time()
                if (
                    now >= next_design_context_poll
                    and self._app
                    and getattr(self._app, "loop", None) is not None
                ):
                    next_design_context_poll = now + _UI_REFRESH_SECONDS
                    context = claim_terminal_request(
                        instance_id=get_instance_id(),
                        pid=os.getpid(),
                        config=getattr(getattr(self, "agent", None), "config", None),
                    )
                    if isinstance(context, dict):
                        try:
                            target = {"project": context["project"]}
                            if context.get("expected_slot"):
                                target["expected_slot"] = context["expected_slot"]
                            outcome = self._dashboard_action(context["kind"], context["value"],
                                                             **target)
                            message = outcome.get("message", "")
                        except (ValueError, RuntimeError) as exc:
                            message = str(exc)
                        self._app.loop.call_soon_threadsafe(lambda value=message: self._set_notice(value))
                    elif context:
                        self._app.loop.call_soon_threadsafe(
                            lambda value=context: self._handle_input(value, owner_bound=True)
                        )
                self._sync_terminal_background(now=now)
                working = self._working_animation_active()
                decorative_working = (
                    working and self._decorative_working_redraw_due(now=now)
                )
                background_flushed = self._flush_background_updates()
                self._sync_terminal_title(working, now=wall_time)
                output_changed = self._live_tool_output_changed()
                status_refresh_token = _status_refresh_token(self, now=wall_time)
                status_changed = status_refresh_token != last_status_refresh_token
                last_status_refresh_token = status_refresh_token
                if (
                    self._app
                    and (decorative_working or output_changed or status_changed)
                    and not background_flushed
                ):
                    self._app.invalidate()
                # One frame clock owns the lane, footer, host title, and timed
                # status boundaries. Key edits yield only its decorative repaint;
                # stable idle state does not keep repainting.
                self._refresh_stop.wait(DOTS4_PHASE_SECONDS)

        threading.Thread(target=_refresh_loop, daemon=True).start()
        startup_input = str(getattr(self, "_startup_input", "") or "").strip()

        def _submit_startup_input() -> None:
            self._startup_input = ""
            if startup_input:
                self._handle_input(startup_input)

        try:
            with patch_stdout():
                self._app.run(pre_run=_submit_startup_input if startup_input else None)
        except KeyboardInterrupt:
            pass
        finally:
            self._refresh_stop.set()
            turn_thread = getattr(self, "_turn_thread", None)
            if turn_thread is not None and turn_thread.is_alive():
                self._request_current_turn_stop()
                turn_thread.join(timeout=5.0)
            workspace = getattr(self, "_workspace", None)
            if workspace is not None:
                try:
                    workspace.shutdown()
                except Exception:
                    pass
            stable_title = self._stable_terminal_title()
            publish_terminal_title(
                stable_title,
                output=getattr(self._app, "output", None),
            )
            self._terminal_title_text = stable_title
            live_host = getattr(self, "_live_control_host", None)
            if live_host is not None:
                live_host.stop()
            dashboard = getattr(self.agent, "_dashboard_server", None)
            if dashboard is not None:
                dashboard.close()
                self.agent._dashboard_server = None
            self.agent._dashboard_dispatch = None
            role_workspace = getattr(self.agent, "_terminal_role_workspace", None)
            if role_workspace is not None:
                role_workspace.close()
                self.agent._terminal_role_workspace = None
            # Restore the user's terminal palette before erasing the visible MO
            # render region. This prevents Dracula/silver cells from remaining
            # mixed with the host default background after Ctrl+C/Ctrl+D exits.
            reset_terminal_background(output=getattr(self._app, "output", None))
            _clear_terminal_screen()
