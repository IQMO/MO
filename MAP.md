# MO Agent Map

Compact orientation for providers. `AGENTS.md` is authoritative for rules.

## Runtime Truth
- `core/prompts/system.md` — MO runtime behavior prompt.
- `core/owner_protocols.py`, `core/self_maintenance/` — owner-only activation, preflight, and stop gates; inert without the owner's profile protocol files and owner token.

## Core Surfaces
- `core/gateway.py` — turn coordination and taskboard lifecycle (flat by design).
- `core/agent/` — agent hub, turn loop, slash commands, PRT, utils.
- `core/behavior_gates.py` / `core/final_gates.py` / `core/claim_verification.py` — declarative enforcement registries the turn loop routes through: input-phase (threat scan + malicious-code refusal, before any provider call) and final-phase answer enforcement (contract, self-protocol truth, done-claim, verify-edits, and the verify-before-claiming claim gates). Owner-protocol terminal stop gates stay a separate mechanism in the turn loop.
- `core/work_patterns.py`, `core/agent/agent_dna.py` — compact internal work guidance, including the lean-build ladder that checks reuse/deletion/stdlib/native options before adding code.
- `core/tasking/` — `core/tasking/task_board.py`, contract gate, task manager, evidence, and procedure rows seeded from work patterns.
- `core/review/` — diff review pipeline, scorer, iteration, finding patterns; PRT can flag proven overengineering as maintainability risk.
- `core/goal/` — goal runner and goal auditor.
- `core/graph/` — `core/graph/structural_graph.py`, code graph, code-map HTML; `core/lsp/` — local language-server diagnostics bridge (`lsp.servers`), off by default; the `lsp_diagnostics` final-gate blocks "fixed/clean" claims on files the server still flags.
- `core/learning/` — memory, knowledge store, workflow/feedback/trace learning.
- `core/session/` — session, closeout, momentum; old completed tool chains can compact Python source reads to recoverable structure skeletons.
- `core/code_skeleton.py` — Python AST skeleton compressor for session momentum only; keeps imports/signatures/docstrings, drops bodies, and returns empty on no-gain/non-Python so callers keep existing behavior.
- `core/provider/` — providers, audit, capacity.
- `core/ghost/` — ghost side-check routing, context, audit.
- `core/mcp/` — MCP client + manager (enabled by default, inert until servers are listed).

## Runtime State
- Live runtime state (logs, audits) lives under the profile state home (`~/.mo` / `MO_HOME` / `MO_STATE_HOME`), NOT the checkout.
- Multiple terminal instances are allowed: stable `MO_INSTANCE_ID`, default `main-<instance>` session; `runtime.shared_session: true` is the legacy shared-main escape hatch.
- Singleton surfaces are resource-locked: headless service, Telegram poller, scheduler, desktop Ghost tray/hotkey.
- Logical profile-state `memory/traces/` — trace artifacts and validator input.
- Logical profile-state `memory/taskboards/` — append-only taskboard snapshots + `current.json`.
- Logical profile-state `memory/structural_graph/` — graph, code map, focused map artifacts.
- Owner-only session records are gitignored/local only; never tracked or shipped.

## Verification
- Focused tests first: `python -m pytest tests/<target>.py -q`.
- Tiered sweeps: `-m smoke` (~250 tests) or `-m "smoke or unit"` (~1,615 tests) — auto-tiered by conftest.
- Full suite (parallel): `python -m pytest -q -n auto --dist loadfile` (~1 min). A session fixture builds the repo code graph once into a shared cache so workers load it instead of each re-parsing the tree. Serial `python -m pytest -q` works too (~2x slower).
- Do not use Node tooling; this is a Python project.

## Working Rules
- Evidence before claims; use files, traces, tests, and runtime signals.
- Prefer existing MO systems over new parallel mechanisms.
- Keep protocols split into modules, not oversized root prompts.
- Never print secrets or credential values.
- Boundary: owner-only material lives under profile state (`~/.mo`, including `~/.mo/operator` or `MO_OPERATOR_PACK`) or ignored local-only paths and never ships; a pre-push guard blocks leaks. Operator-only commands use `operator_only=True`. See AGENTS.md "Boundary".
