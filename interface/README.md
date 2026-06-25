# Interface Cleanup Reminder

This directory is being cleaned/certified by extraction and production-readiness checks, not redesigned.

Active follow-up: interface production-readiness and cleanup checks (maintainer notes).

## Why this matters

MO's taskboard is product truth, not decoration. The provider can propose and explain, but task progress must remain evidence-backed:

```text
Gateway owns task lifecycle/finalization
Agent maps tool/runtime evidence
Interface renders truth only
```

Any interface cleanup that lets provider prose, callback markup, or display code create/complete/block tasks is wrong.

## What has already been extracted

`interface/main_terminal.py` has been reduced by moving focused seams into flat interface modules:

- `command_registry.py` / `slash_commands.py` — slash command metadata compatibility.
- `command_palette.py` — palette state, fragments, drilldowns, and model choices.
- `theme.py` — prompt-toolkit style map.
- `worker_status.py` — worker status text.
- `tui_goal.py` — goal UI lifecycle mixin.
- `terminal_loop.py` + `native_terminal.py` — app/native loop composition and fallback terminal loop.
- `task_board_view.py` — taskboard display fragments.
- `formatting.py` — small formatting helpers.
- `activity.py` — activity/status/footer display helpers.
- `display_delegates.py` — MoTui display delegate wrappers for activity/status/footer/boards/Ghost panel.
- `response.py` — assistant response typography.
- `response_mixin.py` — response/proposal transcript helper wrappers for `MoTui`.
- `transcript_view.py` — transcript wrapping helpers.
- `transcript.py` — transcript viewport helpers.
- `transcript_state.py` — transcript storage/viewport mixin used by `MoTui`.
- `turn_runner.py` — Gateway turn bridge mixin; preserves taskboard truth callbacks.
- `tui_app.py` — prompt-toolkit app bootstrap/run mixin and boot logo lines.
- `ghost_panel.py` — Ghost panel frame/wrapping/render helpers.
- `ghost_history.py` — Ghost side-panel history helpers.
- `ghost_controller.py` — Ghost side-panel ask/routing controller mixin.
- `layout.py` — prompt-toolkit panel layout construction.
- `keybindings.py` — prompt-toolkit keybinding construction.
- `palette_mixin.py` — small command-palette compatibility wrappers.
- `keybindings.py` + `turn_runner.py` + `core/prompt_enhancer.py` — **Ctrl+E** prompt enhancement rewrites the typed message in place (off-thread, profile-personalized language/tone); **Esc** reverts. Never sends.
- `queueing.py` — pending input queue/steer mixin.
- `input_dispatch.py` — TUI slash/input dispatch mixin.

Each extraction kept compatibility wrappers on `MoTui` where tests/imports still rely on them.

## Current rule before touching anything

Before changing a seam:

1. Read the current files involved.
2. Read the tests covering that seam.
3. Name the protected behavior.
4. Add or confirm focused characterization tests.
5. Move one seam only.
6. Run focused tests.
7. Run full gates when repo state allows.
8. Commit that seam separately.

Do not guess. Do not call something dead unless imports/tests prove it.

## Protected behavior to keep

- Simple chat/no-tool/no-runtime-signal turns must not fabricate task progress.
- The TUI must render `gateway.last_task_board` truth, not arbitrary callback markup.
- Final answers cannot complete open tasks by prose.
- Failed verification remains blocked.
- Native mouse selection/copy contract stays: `full_screen=False`, `mouse_support=False`; full transcript navigation is MO internal viewport scrolling, not guaranteed terminal scrollback.
- Slash commands are control actions and must not echo raw commands as chat.
- Ctrl+E prompt enhancement must replace the input buffer only (Esc reverts to the original); it may use local operator-profile guidance, but must not send, create taskboards, or mark progress.
- Queue/steer behavior remains: first busy input queues, second Enter steers, third Enter requests stop, Esc cancels queued input; three busy Esc presses stop MO.
- Ghost can route/queue/work through existing runtime paths, but Ghost text does not own task state.

## Remaining cleanup map

Confirmed remaining in `main_terminal.py` after the latest extraction:

- Import/composition surface for `MoTui` mixins.
- `MoTui.__init__` state initialization.
- Compatibility helper `_strip_md` delegating to `ghost.py`.
- Compatibility exports: `run_main_loop`, `should_open_backend_monitor`, `_record_session`.

Do not split further just to chase line count unless the state initialization or compatibility surface has clear tests and a clear owner.

## UX audit 2026-06-10 (operator-approved, completed)

Verified clean by sweep: prompt-toolkit TUI colors centralized in `theme.py`
(desktop Ghost/Tk surfaces keep local visual tokens); keyboard layering
(palette > completion > ghost > transcript scroll > cursor) sound;
transcript/ghost/board scrolling bounded with clip indicators; `activity.py`
display helpers well-factored; "legacy" markers in palette/registry are
legitimate guards, not leftovers.

Fixed: D003 status markers single-sourced from `core/tasking/task_board.py`
(`STATUS_MARKERS`/`status_marker`, regression-guarded); transcript bottom-
anchors short content (no void between answer and bottom panels); Ctrl+C
cancels work when busy via the 3-stage busy-escape and exits only when idle
(Ctrl+D always exits); `/moon` keeps a single animation tick thread with a
stop event; `rich_view.py` Rich shim removed (no live path imported it —
the live working indicator is `activity.py`).
