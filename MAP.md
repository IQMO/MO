# MO Agent Map

Compact orientation for providers. `AGENTS.md` is authoritative for rules.

## Runtime Truth
- `core/prompts/system.md` — MO runtime behavior prompt.
- `core/local_extensions.py` — neutral profile-extension bridge; empty profiles load no private commands, hooks, board rows, or closeout machinery.

## Core Surfaces
- `core/gateway.py` — turn coordination and taskboard lifecycle (flat by design).
- `core/agent/` — agent hub, turn loop, slash commands, PRT, utils.
- `core/gates/behavior_gates.py` / `core/gates/final_gates.py` / `core/gates/claim_verification.py` — declarative enforcement registries the turn loop routes through: input-phase (threat scan + malicious-code refusal, before any provider call) and final-phase answer enforcement (contract, task truth, done-claim, verify-edits, and the verify-before-claiming claim gates). Private extension gates load only through `core/local_extensions.py`.
- `core/context/work_patterns.py`, `core/context/`, `core/agent/agent_dna.py` — compact internal work guidance, context builders, and workspace awareness, including the lean-build ladder that checks reuse/deletion/stdlib/native options before adding code.
- `core/tasking/` — `core/tasking/task_board.py`, contract gate, task manager, evidence, and procedure rows seeded from work patterns.
- `core/review/` — diff review pipeline, scorer, iteration, finding patterns; PRT can flag proven overengineering as maintainability risk.
- `core/goal/` — goal runner and goal auditor.
- `core/graph/` — `core/graph/structural_graph.py`, code graph, code-map HTML; `core/lsp/` — local language-server diagnostics bridge (`lsp.servers`), off by default; the `lsp_diagnostics` final-gate blocks "fixed/clean" claims on files the server still flags.
- `core/learning/` — episodic memory (FTS5 + optional local-embedding RRF recall, env-tunable horizon `MO_MEMORY_MAX_TURNS`), workflow/feedback/trace learning. Per-turn mining stays inert, but the narrow universal safe class auto-promotes (`learning.auto_promote`, default on) so confirmed learnings actually inject; everything else needs `/learning confirm`. `/learning reconcile` consolidates confirmed near-duplicates; operator corrections record the skill `correction` outcome (un-blinding retirement) and log to the inert `skill_evolution.json`.
- `core/session/` — session, closeout, momentum; old completed tool chains can compact Python source reads to recoverable structure skeletons.
- `core/tooling/code_skeleton.py`, `core/tooling/` — tool sandbox/registry/compression plus the Python AST skeleton compressor used by session momentum.
- `core/provider/`, `core/runtime/`, `core/state/`, `core/skills/` — providers; monitor/heartbeat/locks/service; profile-home paths/init/secrets/migration; local skill packs and seeds.
- `core/ghost/` — ghost side-check routing, context, audit.
- `core/mcp/` — MCP client + manager (enabled by default, inert until servers are listed).

## Runtime State
- Live runtime state (logs, audits) lives under the profile state home (`~/.mo` / `MO_HOME` / `MO_STATE_HOME`), NOT the checkout.
- Multiple terminal instances are allowed: stable `MO_INSTANCE_ID`, default `main-<instance>` session; `runtime.shared_session: true` is the legacy shared-main escape hatch.
- Singleton surfaces are resource-locked: headless service, Telegram poller, scheduler, desktop Ghost tray/hotkey.
- Logical profile-state `memory/traces/`, `memory/taskboards/`, `memory/structural_graph/` — trace artifacts, taskboard snapshots, graph/code-map artifacts.
- Private profile-extension records live under `~/.mo/operator` (or explicit profile env overrides), are gitignored/local only, and are never tracked or shipped.

## Verification
- Maintainer-local tests live in ignored `tests/`; they are used before maintainer CPD (Commit, Push, Deploy) but must never be tracked or pushed.
- Run `python -m core.diagnostics.test_preflight --collect` before broad/full local sweeps; `test_runner` does this automatically for broad pytest commands.
- The local pytest bootstrap repeats the public/private guard before broad collection so privacy/term failures surface first.
- Focused tests first when the overlay is present: `python -m pytest tests/<target>.py -q`.
- Tiered/full sweeps remain local-only: `-m smoke`, `-m "smoke or unit"`, or bounded parallel full suite such as `python -m pytest -q -n 4 --dist loadfile`; avoid `-n auto` unless the operator explicitly wants all CPU threads used.
- Do not use Node tooling; this is a Python project.

## Working Rules
- Evidence before claims; use files, traces, tests, and runtime signals.
- Prefer existing MO systems over new parallel mechanisms.
- Keep product guidance split into modules, not oversized root prompts.
- Never print secrets or credential values.
- Boundary: private profile-extension material (`~/.mo/operator`) and maintainer QA live under profile state or ignored local-only paths and never ship; a pre-push guard blocks leaks and tracked `tests/`. Private commands come only from profile extensions. See AGENTS.md "Boundary".
