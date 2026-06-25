# UX

`UX/` is a clean-sheet, isolated terminal surface for MO Agent. It is not wired
into `mo.py`, does not import the current `interface/` package, and does not own
task truth.

Run the preview:

```bash
python -m UX --once
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
