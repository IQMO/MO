# UX Roadmap

This file tracks the isolated next-generation terminal UX until it is ready to
replace the current `interface/` surface. Runtime truth stays in Gateway,
taskboard, and adapters; this folder owns presentation only.

## Done

- Isolated `UX/` package with no imports from the current `interface/`.
- Immutable display models and backend-independent controller.
- Preview, read-only runtime, live runtime, and one-message smoke modes.
- Windows launchers for preview and live runs.
- Prompt-first fullscreen TUI with centered landing surface, composer, status
  rail, and animated idle signal.
- Contract tests for isolation, launch behavior, controller behavior, runtime
  adapters, and TUI animation.

## Remaining To Final Product

1. Visual polish pass: make idle, busy, transcript, and task states feel like one
   coherent high-contrast product instead of separate prototype screens.
2. Composer pass: command palette, multiline editing affordance, history search,
   file/context chips, and clear busy/blocked input states.
3. Runtime event pass: stream live agent events into the UI without blocking
   input or faking task ownership.
4. Taskboard pass: render the real MO-owned taskboard when runtime supplies it,
   with compact progress, blockers, and evidence state.
5. Agent-lane pass: show thinking, execution, compaction, and background work as
   live rails only when the runtime reports them.
6. Transcript pass: improve density, wrapping, tool/result grouping, and failure
   states for real coding turns.
7. Mode/control pass: model/provider display, autonomy controls, read-only/live
   indicators, and safe operator-only command hiding.
8. Verification pass: screenshot or terminal-frame smoke checks, live runtime
   smoke path, narrow regression tests, then broader pytest before promotion.
9. Promotion pass: wire `mo.py`/current launcher to the new UX behind an explicit
   flag, keep rollback to `interface/`, and remove no old interface code until
   parity is proven.
