# MO Agent Map

Compact orientation for providers. `AGENTS.md` is authoritative for rules.

## Runtime Truth
- `core/prompts/system.md` — MO runtime behavior prompt.
- `core/self_capability_preflight.py` — owner-only self-maintenance preflight/stop gates; inert without the private pack.

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
- `core/mcp/` — MCP client + manager (enabled by default, inert until servers are listed).

## Runtime State
- Live runtime state (logs, audits) lives under the private state home (`~/.mo` / `MO_HOME` / `MO_STATE_HOME`), NOT the checkout.
- Multiple terminal instances are allowed: stable `MO_INSTANCE_ID`, default `main-<instance>` session; `runtime.shared_session: true` is the legacy shared-main escape hatch.
- Singleton surfaces are resource-locked: headless service, Telegram poller, scheduler, Desktop Companion tray/hotkey.
- Logical private-state `memory/traces/` — trace artifacts and validator input.
- Logical private-state `memory/taskboards/` — append-only taskboard snapshots + `current.json`.
- Logical private-state `memory/structural_graph/` — graph, code map, focused map artifacts.
- Owner-private session records are gitignored/local only; never tracked or shipped.

## Verification
- Focused tests first: `python -m pytest tests/<target>.py -q`.
- Tiered sweeps: `-m smoke` (~156 tests) or `-m "smoke or unit"` (~1,060 tests) — auto-tiered by conftest.
- Broad Python suite when behavior changes: `python -m pytest -q` (use a shell timeout of 300s+).
- Do not use Node tooling; this is a Python project.

## Working Rules
- Evidence before claims; use files, traces, tests, and runtime signals.
- Prefer existing MO systems over new parallel mechanisms.
- Keep protocols split into modules, not oversized root prompts.
- Never print secrets or credential values.
- Boundary: operator-private material lives under private state (`~/.mo`, including `~/.mo/operator` or `MO_OPERATOR_PACK`) or ignored local-only paths and never ships; a pre-push guard blocks leaks. Operator-only commands use `operator_only=True`. See AGENTS.md "Boundary".
