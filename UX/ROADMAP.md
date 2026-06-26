# UX Roadmap

This file tracks the remaining promotion path for the isolated next-generation
terminal UX. Current state, verification, and CPD history live in `STATUS.md`.
Runtime truth stays in Gateway, taskboard, and adapters; this folder owns
presentation only.

## Done

- Isolated `UX/` package with no imports from the current `interface/`.
- Immutable display models and backend-independent controller.
- Preview, read-only runtime, live runtime, and one-message smoke modes.
- Windows launchers for preview and live runs.
- Prompt-first fullscreen TUI with centered landing surface, composer, and status
  rail.
- OpenDev-style landing motion with animated signal field; idle rails remain
  quiet and busy/running state owns spinner motion.
- Compact work surface for transcript, real taskboard rows, and reported runtime
  lanes.
- Background submit worker so live turns do not run on the render/input thread.
- Composer affordances: multiline input, history search, command palette, context
  token chips, busy read-only state, and plan-lens toggle.
- Conservative runtime lane adapter: reported lanes are rendered; absent lane
  truth falls back to a neutral runtime row.
- Explicit promotion hook: `mo --ux` or `MO_NEXT_UX=1`, with no default behavior
  change and no top-level `UX` import in `mo.py`.
- Preview echo is explicitly local: transcript labels preview output as `UX`,
  not `MO`, and points to live mode.
- Contract tests for isolation, launch behavior, controller behavior, runtime
  adapters, and TUI animation.

## Remaining Before Default Replacement

1. Operator visual acceptance from `UX\run_preview.bat` and `python mo.py --ux`.
2. Approved live-provider smoke turn through `python mo.py --ux --message ...`
   or `UX\run_ux.bat --message ...`; this intentionally is not run by default
   because it can spend provider quota.
3. Default switch: make `mo` use the new UX by default only after acceptance,
   keeping an explicit rollback to the current `interface/`.
4. Old-interface deprecation/removal only after parity is proven in real daily
   use.
