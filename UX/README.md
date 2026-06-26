# UX

`UX/` is a clean-sheet, isolated terminal surface for MO Agent. It is opt-in
through `python mo.py --ux` or `MO_NEXT_UX=1`; default `python mo.py` still uses
the current `interface/` package. `UX/` does not import `interface/` and does not
own task truth.

Current status, verification, and CPD history live in `STATUS.md`.

Run the static preview smoke render:

```bash
python -m UX --once
```

Run the safe Windows preview launcher to inspect the interactive UI without
touching the MO runtime. Preview replies are local `UX` notices, not real `MO`
answers:

```bat
UX\run_preview.bat
```

The preview launcher opens a real fullscreen TUI by default. The composer is the
focused input field; type there and press Enter.

```bat
UX\run_preview.bat
```

For a static one-screen render used by smoke checks:

```bat
UX\run_preview.bat --once
```

Run the Windows launcher in live mode:

```bat
UX\run_ux.bat
```

Run the same live UX through the normal MO entrypoint without replacing the
default interface:

```bash
python mo.py --ux
```

Or opt in for the current shell:

```bat
set MO_NEXT_UX=1
python mo.py
```

The live launcher defaults to live runtime mode. This sends one live message and
exits:

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
- New UX code stays in `UX/` until deliberately promoted. The only current
  production entrypoint integration is the explicit lazy opt-in hook.
- Display models are immutable snapshots.
- Gateway/taskboard/runtime own truth; UX renders snapshots only.
- Preview mode is local-only and must not label local echoes as `MO`.
- Idle rails stay quiet; motion is reserved for the landing signal and real
  busy/running activity.
- No owner profile, private operator paths, secrets, or local maintainer defaults
  are product behavior.
- No new dependency is introduced in this phase. The preview uses
  `prompt-toolkit` and `rich`, both already declared in `requirements.txt`.

## Structure

- `state/` - immutable display snapshots and backend-independent controller.
- `runtime/` - preview/live backends, runtime snapshot adapters, and lazy
  Agent/Gateway bridge.
- `render/` - Rich renderers and theme tokens.
- `shell/` - CLI parsing, interactive loop, and live-screen orchestration.
- Root modules such as `app.py`, `models.py`, and `controller.py` are
  compatibility shims only.

See `ARCHITECTURE.md` before adding new UX code. See `STATUS.md` for current
state/CPD, and `ROADMAP.md` for the remaining promotion path.

## Mode Status

- Phase 1 interactive shell: `python -m UX` opens a fullscreen prompt-toolkit
  TUI with a focused multiline composer, command palette, transcript updates,
  animated landing surface, quiet idle rails, and busy-only activity indicators.
- Phase 2 read-only runtime: `python -m UX --read-only` creates Agent/Gateway and
  renders a snapshot without sending messages.
- Phase 3 controlled actions: `python -m UX --live` sends messages through
  `Gateway.run_turn` from a background submit worker and renders the resulting
  snapshot. `--message` provides a non-interactive smoke path.
- Phase 4 comparison/coverage: `tests/test_ux_contract.py` and
  `tests/test_ux_controller.py` lock isolation, rendering, controller, and
  adapter behavior.

## UX Direction

Verified design inputs used for this phase:

- Polished terminal presence, model/session awareness, and LSP-oriented surfaces.
- Multi-pane command center shape, sessions, command palette, and theme support.
- Command/model surface separation and explicit agent modes.
- Planning/execution separation, routing lanes, and context compaction as
  first-class interface concepts.
- Fullscreen brand landing, compact command strip, prompt lane, and bottom
  model/status rail.

This folder documents MO-owned behavior and verified implementation details only.

Future dependency option:

- Textual is a strong Python option for a full async pane-based TUI, but it is
  not installed or declared here. Adding it needs explicit approval.
