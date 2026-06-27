# UX Status

This is the current status record for the isolated next-generation terminal UX.
It supersedes chat summaries and older proposal notes for the `UX/` folder.

## Current State

- `interface/` remains the default production terminal interface.
- `UX/` is opt-in and isolated. It does not import `interface/`, and `interface/`
  does not import `UX`.
- `mo.py` has an explicit lazy opt-in hook only: `python mo.py --ux` or
  `MO_NEXT_UX=1`. Default `python mo.py` behavior is unchanged.
- Runtime truth stays outside the UX layer. Gateway, taskboard, and runtime
  adapters own truth; UX renders immutable `SessionSnapshot` values.
- Direct UX launch defaults to live runtime mode. Local preview requires
  `--preview` or `UX\run_preview.bat`.
- Live UX uses real MO in its own UX process by submitting through
  `Gateway.run_turn(route_source="ux")`; it does not import or replace the
  current `interface/` package.
- Preview mode is local-only. It labels local preview replies as `UX`, not `MO`,
  and tells the operator how to start live mode.
- Idle rails are static. Spinner motion appears only while a turn is actually
  busy/running. The landing signal remains animated as visual identity.
- No new dependency has been added. The UX uses existing `prompt-toolkit` and
  `rich`.

## Run Modes

| Mode | Command | Runtime effect |
| --- | --- | --- |
| Interactive preview | `UX\run_preview.bat` | Local-only; no Gateway turn. |
| Explicit preview | `python -m UX --preview` | Local-only; no Gateway turn. |
| Static preview smoke | `python -m UX --smoke` | Local-only smoke render. |
| Static preview render | `python -m UX --preview --once` | Local-only render. |
| Direct live UX | `python -m UX` | Sends real turns through Gateway as route source `ux`. |
| Static live render | `python -m UX --once` | Creates runtime snapshot; sends no turn. |
| Read-only runtime | `python -m UX --read-only` | Creates runtime snapshot; sends no turn. |
| Live UX launcher | `UX\run_ux.bat` | Sends real turns through Gateway as route source `ux`. |
| Live via MO entrypoint | `python mo.py --ux` | Lazy-loads UX and sends real turns through Gateway as route source `ux`. |
| One live message | `python mo.py --ux --message "..."` | Sends one real provider-backed turn. |

## Implemented

- Isolated package structure: `state/`, `runtime/`, `render/`, and `shell/`.
- Immutable display models and backend-independent controller.
- Prompt-toolkit fullscreen TUI with animated landing, compact work screen,
  transcript grouping, task rows, runtime lane rows, command palette, multiline
  composer, history search, context token chips, plan-lens toggle, and busy input
  lock.
- Background submit worker so live Gateway turns do not run on the render/input
  thread.
- Runtime-backed snapshots render their actual UX mode (`UX live`,
  `UX read-only`, or `UX runtime bridge`) instead of looking like preview.
- Direct UX entry defaults to live runtime; preview is explicit.
- Conservative runtime lane adapter: reported lanes render as-is; absent lane
  truth falls back to a neutral runtime row.
- Opt-in `mo.py` promotion hook without default replacement and without top-level
  `UX` imports.

## Verification

Latest verified UX-only code state for the current opt-in UX:

- `python -m pytest tests\test_ux_tui.py tests\test_ux_controller.py tests\test_ux_app.py tests\test_ux_contract.py tests\test_ux_runtime.py -q` -> `49 passed`
- `python -m ruff check UX tests\test_ux_tui.py tests\test_ux_controller.py tests\test_ux_app.py tests\test_ux_contract.py tests\test_ux_runtime.py` -> clean
- `python -m UX --smoke` -> passed
- `python -m UX --preview --message hi --width 80` -> passed as local-only preview
- `python -m UX --preview --once --width 80` -> passed
- `python -m UX --once --width 80` -> passed with `surface: UX live`
- `python -m UX --read-only --width 80` -> passed
- `python -m UX --message hi --width 80` -> reached live runtime and rendered
  `surface: UX live`; provider socket access was blocked by local sandbox
  `WinError 10013`

Focused verification for the preview command-truth correction:

- The prompt-toolkit UX hero and command palette advertise `/model`, not the
  nonexistent `/models`.
- Preview `/model` returns the displayed provider/model instead of an unknown
  command response.
- Preview transcript still labels local output as `UX`.
- Live runtime tests verify Gateway receives `route_source="ux"`.
- Direct `python -m UX --message ...` tests verify message handling defaults to
  live runtime instead of preview.

## CPD Record

For this UX track, CPD means the change was committed, pushed to `main`, and the
pre-push privacy guard passed. It does not mean the new UX became the default
production interface unless that is stated explicitly.

| Commit | Status | Notes |
| --- | --- | --- |
| `33cc316` | CPD complete | Advanced next UX surface: work screen, background submit, opt-in `mo.py --ux`, runtime-lane adapter, docs/tests. |
| `35594a5` | CPD complete | Quieted idle rails, removed decorative idle spinners, clarified preview echo as `UX` local output. |
| status-doc refresh | CPD by the commit containing this file | Records current state, verification, run modes, and remaining default-replacement gates. |

## Remaining Before Default Replacement

1. Operator visual acceptance from `UX\run_preview.bat` and `python mo.py --ux`.
2. Approved live-provider smoke turn through `python mo.py --ux --message ...`
   or `UX\run_ux.bat --message ...`.
3. Default switch only after acceptance, with explicit rollback to the current
   `interface/` surface.
4. Old-interface deprecation/removal only after parity is proven in real daily
   use.
