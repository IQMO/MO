# UX

`UX/` is a clean-sheet, isolated terminal surface for MO Agent. It is not wired
into `mo.py`, does not import the current `interface/` package, and does not own
task truth.

Run the preview:

```bash
python -m UX --once
```

Run the Windows launcher in live mode:

```bat
UX\run_ux.bat
```

The launcher defaults to `--live`, so this also sends one live message and exits:

```bat
UX\run_ux.bat --message "who are you?"
```

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

- `models.py` - immutable display snapshots.
- `layout.py` - Rich renderers for session, lanes, task board, transcript, and
  composer.
- `adapters.py` - duck-typed runtime snapshot adapters; no top-level `core` or
  `interface` imports.
- `app.py` - local preview runner; captures input only inside the preview.
- `controller.py` - backend-independent input/state controller.
- `runtime.py` - lazy MO Agent/Gateway bridge for explicit read-only/live modes.

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
