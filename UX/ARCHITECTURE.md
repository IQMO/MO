# UX Architecture

`UX/` is the isolated next-generation MO terminal surface. It is built around a
strict dependency direction so the visual layer can become polished without
taking ownership of runtime truth.

## Package Map

- `UX/state/` - immutable display models plus the backend-independent
  controller. No Rich, no MO runtime imports, no current `interface/` imports.
- `UX/runtime/` - preview/live backends, duck-typed runtime adapters, and the
  lazy Agent/Gateway bridge. Runtime imports from `core` must stay inside
  functions or methods that explicitly create/use the runtime.
- `UX/render/` - Rich renderers and theme tokens. Reads `SessionSnapshot`; does
  not call Gateway, Agent, tools, or the current `interface/`. Panel components
  live in `render/panels.py`; whole-screen composition lives in
  `render/screen.py`.
- `UX/shell/` - CLI parsing, batch/interactive command behavior, and live-screen
  orchestration.

The root modules (`UX/app.py`, `UX/models.py`, `UX/controller.py`,
`UX/layout.py`, `UX/theme.py`, `UX/adapters.py`) are compatibility shims only.
New code should import from the packages above.

## Dependency Direction

```text
shell  -> render, runtime, state
render -> state
runtime -> state
state  -> stdlib only
```

Forbidden directions:

- `state -> runtime`
- `state -> render`
- `render -> runtime`
- any `UX/* -> interface`
- top-level `UX/* -> core`

## Runtime Truth

`UX` renders `SessionSnapshot`. It does not complete tasks, infer work success,
or rewrite task status. Gateway/taskboard/runtime own truth; adapters only
convert runtime state into immutable display rows.

Live UX is a real MO surface: the runtime bridge creates the normal Agent and
Gateway in the UX process and submits turns with `route_source="ux"`. Direct
`python -m UX` launch defaults to this live runtime path. The current
production `interface/` package remains the default renderer for `python mo.py`
and is not imported by UX.

## Current Build Rules

- Preview mode is explicit and local-only. It can capture input and render a
  transcript, but it must label local output as `UX`, never as `MO`.
- Live mode sends turns only through `Gateway.run_turn(route_source="ux")` via
  the runtime bridge.
- Read-only mode can instantiate runtime state and render a snapshot, but sends
  no turn.
- Idle rails stay static. Motion is allowed for the landing signal and for
  actual busy/running activity.
- Submit work belongs in `shell/` orchestration. Render functions must stay
  pure snapshot-to-fragments/panels.
- Promotion toward `mo.py` must remain explicit and lazy until the operator
  accepts the UX as default.

See `STATUS.md` for current CPD state and `ROADMAP.md` for the default-switch
gate.
