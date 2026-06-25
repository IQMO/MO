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
  not call Gateway, Agent, tools, or the current `interface/`.
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

## Next Visual Build

The polished interface work should land in this order:

1. Replace stacked panel composition with a stable command-center screen.
2. Add dedicated lane/task/transcript/composer render modules under `render/`.
3. Keep live orchestration in `shell/`, not in render functions.
4. Add parity tests before promoting any behavior toward `mo.py`.
