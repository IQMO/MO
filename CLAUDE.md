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
- Public/private: work only in private `IQMO/rMO`; public `IQMO/MO` is a guarded `git archive` snapshot (`devmode/`+`docs/` are export-ignored). Publish via `python tools/publish_public.py`, never by hand. Dev work on MO (DEVMODE05/VS05) + operator identity = private; mark operator-only commands `operator_only=True`. See AGENTS.md "Public/Private Repository Boundary".
- Evidence-first: check files, logs, tests before claiming.
- Never print secrets.
- Lead with the answer, not the setup.
- Operator identity/preferences are runtime-private profile data, not product defaults.
