# MO Agent Map

Compact orientation for providers. `AGENTS.md` is authoritative for rules.

## Runtime Truth
- `core/prompts/system.md` — MO runtime behavior prompt.
- `core/self_capability_preflight.py` — preflight and stop gates for owner-only
  self-maintenance work (inert unless the owner's private pack is present).

## Core Surfaces
- `core/gateway.py` — turn coordination and taskboard lifecycle (flat by design).
- `core/agent/` — agent hub, turn loop, slash commands, PRT, utils.
- `core/tasking/` — `core/tasking/task_board.py`, contract gate, task manager, evidence.
- `core/review/` — diff review pipeline, scorer, iteration, finding patterns.
- `core/goal/` — goal runner and goal auditor.
- `core/graph/` — `core/graph/structural_graph.py`, code graph, code-map HTML.
- `core/learning/` — memory, knowledge store, workflow/feedback/trace learning.
- `core/session/` — session, closeout, momentum.
- `core/provider/` — providers, audit, capacity.
- `core/ghost/` — ghost side-check routing, context, audit.

## Runtime State
- Live runtime state (logs, audits) lives under the private state home
  (`~/.mo` / `MO_HOME` / `MO_STATE_HOME`), NOT under the checkout. A `logs/`
  dir in the checkout is only dev-run residue and is gitignored.
- `memory/traces/` — live trace artifacts and validator input.
- `memory/taskboards/` — append-only taskboard snapshots + `current.json`.
- `memory/structural_graph/` — graph, code map, focused map artifacts.
- Owner-private session records are gitignored and exist only in the owner's
  local checkout (never tracked, never shipped).

## Verification
- Focused tests first: `python -m pytest tests/<target>.py -q`.
- Tiered sweeps: `-m smoke` (~156 tests, seconds), `-m "smoke or unit"`
  (~1,060 tests, <1 min) — auto-tiered by conftest.
- Broad Python suite when behavior changes: `python -m pytest -q`
  (full suite needs a shell timeout of 300s+).
- Do not use Node tooling; this is a Python project.

## Working Rules
- Evidence before claims; use files, traces, tests, and runtime signals.
- Prefer existing MO systems over new parallel mechanisms.
- Keep protocols split into modules, not oversized root prompts.
- Never print secrets or credential values.
- Boundary: operator-private material lives in gitignored `operator/` + `docs/` + `~/.mo` and never ships; a pre-push guard blocks leaks. Operator-only commands use `operator_only=True`. See AGENTS.md "Boundary".
