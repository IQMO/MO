# UX

`UX/` is a clean-sheet, isolated terminal surface for MO Agent. It is not wired
into `mo.py`, does not import the current `interface/` package, and does not own
task truth.

Run the preview:

```bash
python -m UX --once
```

Run the safe Windows preview launcher to inspect the UI without touching the MO
runtime:

```bat
UX\run_preview.bat
```

The preview launcher renders one clean screen by default. To use the temporary
raw input loop while the real composer is still being built:

```bat
UX\run_preview.bat --interactive
```

Run the Windows launcher in live mode:

```bat
UX\run_ux.bat
```

The launcher defaults to `--live --width 120`, so this also sends one live
message and exits:

```bat
UX\run_ux.bat --message "who are you?"
```

Override the default launcher width:

```bat
set UX_WIDTH=140
UX\run_ux.bat
```

If `UX_WIDTH` is not set, both launchers use the terminal's current width.

Run the local smoke path:

```bash
python -m UX --smoke
```

Load MO runtime state without sending a turn:

```bash
python -m UX --read-only
```

Send real turns through the MO Gateway:

```bash
python -m UX --live
```

Run one deterministic live smoke message and exit:

```bash
python -m UX --live --message "who are you?"
```

## Phase Contract

- Current production TUI stays in `interface/`.
- New UX code stays in `UX/` until deliberately promoted.
- Display models are immutable snapshots.
- Gateway/taskboard/runtime own truth; UX renders snapshots only.
- No owner profile, private operator paths, secrets, or local maintainer defaults
  are product behavior.
- No new dependency is introduced in this phase. The preview uses `rich`, already
  declared in `requirements.txt`.

## Structure

- `state/` - immutable display snapshots and backend-independent controller.
- `runtime/` - preview/live backends, runtime snapshot adapters, and lazy
  Agent/Gateway bridge.
- `render/` - Rich renderers and theme tokens.
- `shell/` - CLI parsing, interactive loop, and live-screen orchestration.
- Root modules such as `app.py`, `models.py`, and `controller.py` are
  compatibility shims only.

See `ARCHITECTURE.md` before adding new UX code.

## Mode Status

- Phase 1 interactive shell: `python -m UX` accepts local input and renders
  transcript updates.
- Phase 2 read-only runtime: `python -m UX --read-only` creates Agent/Gateway and
  renders a snapshot without sending messages.
- Phase 3 controlled actions: `python -m UX --live` sends messages through
  `Gateway.run_turn` and renders the resulting snapshot. `--message` provides a
  non-interactive smoke path.
- Phase 4 comparison/coverage: `tests/test_ux_contract.py` and
  `tests/test_ux_controller.py` lock isolation, rendering, controller, and
  adapter behavior.

## UX Direction

Verified inspiration used for this phase:

- Crush: polished terminal presence, model/session awareness, MCP/LSP-oriented
  surfaces.
- Ox: multi-pane agent command center, sessions, command palette, themes.
- OpenCode: command/model surface and agent mode separation.
- OpenDev paper: planning/execution separation, routing lanes, context
  compaction as first-class interface concepts.

Unverified names from the external note (`CodeWhale`, `Pilotty`) are intentionally
not treated as implementation facts without a concrete repository or source.

Future dependency option:

- Textual is a strong Python option for a full async pane-based TUI, but it is
  not installed or declared here. Adding it needs explicit approval.
