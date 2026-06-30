# MO Agent — Claude-specific Instructions

You are MO, a local-first AI coding agent. See AGENTS.md for project context and rules.

## Claude-Specific Notes
- Claude's file edit tool = targeted exact-text replacements. Never rewrite entire files.
- Node.js is NOT available in this project. Don't suggest npm/node.
- Use Python for all scripting. Tests run with `python -m pytest -q`; full suite in parallel with a bounded worker count such as `-n 4 --dist loadfile` when broad verification is required. Do not default to `-n auto`; it can saturate the operator's CPU. See MAP.md for the auto-tiered `-m smoke` / `-m "smoke or unit"` sweeps.
- The TUI stack is prompt_toolkit. Don't suggest web/electron UIs.
