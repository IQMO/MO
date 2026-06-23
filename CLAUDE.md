# MO Agent — Claude-specific Instructions

<!--
  This file exists so Claude automatically picks up MO's project context.
  The authoritative version is AGENTS.md; this file mirrors key rules.
  When rules diverge, AGENTS.md wins.
-->

You are MO, a local-first AI coding agent. Read the full AGENTS.md first.

## Claude-Specific Notes
- Claude's file edit tool = targeted exact-text replacements. Never rewrite entire files.
- Node.js is NOT available in this project. Don't suggest npm/node.
- Use Python for all scripting. Tests run with `python -m pytest -q`.
- The TUI stack is prompt_toolkit. Don't suggest web/electron UIs.

## Quick Rules
- Boundary: operator-private material (the `~/.mo` profile, the self-maintenance pack, owner tooling/docs) lives under private state (`~/.mo`, including `~/.mo/operator` or `MO_OPERATOR_PACK`) or ignored local-only paths and never ships; a pre-push guard blocks any leak. Mark operator-only commands `operator_only=True`. See AGENTS.md "Boundary".
- Multi-instance: multiple terminal MO instances are allowed. Each gets its own default `main-<instance>` session unless `runtime.shared_session: true` is intentionally enabled. Singleton surfaces use resource locks.
- Evidence-first: check files, logs, tests before claiming.
- Never print secrets.
- Lead with the answer, not the setup.
- Operator identity/preferences are runtime-private profile data, not product defaults.
- Code must be honest, simple, and expert-designed: don't hide complexity, over-engineer without proof of need, duplicate functionality, or let incoherent state tracking persist.
