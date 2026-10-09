# MO prompt contract

MO has one shared behavioral kernel and explicit surface-owned additions. This
directory is model guidance; typed runtime owners remain the source of truth
for permissions, execution, evidence, persistence, and completion.

| Source | Category | Consumed by | Owns | Must not own |
| --- | --- | --- | --- | --- |
| `shared.md` | identity + universal behavior | Terminal and Desktop system messages | current scope, evidence, authority, privacy, untrusted-input stance, runtime precedence | surface layout, tool availability, implementation detail |
| `system.md` | Terminal surface policy | primary Agent session and non-Desktop Agent surfaces | engineering workflow, Terminal tools, taskboard use, feature closeout, current product routes | Desktop presentation or Desktop actuation policy |
| `mo_desktop/persona.py` | Desktop surface policy | isolated Desktop session | compact companion presentation, screen loop, structured choices, Desktop-only boundaries | Terminal planning/taskboard behavior |
| `core/agent/agent_turn.py` context bridge | dynamic context | the current provider request | current surface, task, profile, role, project, graph, and bounded runtime context | durable identity or permission changes |
| provider tool schemas | tool-use contract | every supported provider adapter | callable names, parameters, descriptions, and result shapes | authorization or completion truth |

## Enforcement owners

| Invariant | Runtime owner |
| --- | --- |
| path/network/secret execution safety | sandbox, credential broker, and dispatch gates |
| task truth and evidence | `core/tasking/`, Gateway, and final-answer gates |
| Desktop actuation and completion | `core/desktop/` and `core/gates/desktop_completion.py` |
| phone/device authority | Everywhere authentication/scopes and native host gates |
| untrusted external result scanning | Agent tool-result and consistency boundaries |
| session persistence/continuity | `core/session/` and bounded Everywhere continuity owners |

Prompt prose may explain these invariants but cannot relax them. A new common
rule belongs in `shared.md`; a real surface difference belongs in its surface
owner; enforceable safety or correctness belongs in typed runtime code first.
The explicitly gated `MO_ALLOW_SYSTEM_PROMPT_OVERRIDE=1` developer path remains
a full prompt replacement for compatibility; it is not normal product
composition and it cannot bypass typed runtime enforcement.

The shared scope policy carries the operator's requested method, interface, and
exclusions across both surfaces. Terminal tool-selection guidance prefers the
program's documented CLI/API and relevant session metadata for nonvisual process
work; missing startup flags do not justify switching to screenshots. Direct
visual observation remains available for visual tasks. These are provider
instructions, not a runtime classifier of whether a task needs the screen.

## Verification

Run the prompt and provider semantic gates without a live provider call:

```powershell
python -m core.diagnostics.prompt_check
python -m core.diagnostics.provider_contract
```

`prompt_check` validates shared policy IDs, composition parity, size ceilings,
and forbidden surface leakage. `provider_contract` checks deterministic message,
tool schema, tool-call/result linkage, and multimodal conversion across the chat
and Responses adapters. Paid/live provider smokes remain separate acceptance.
Composition checks establish that the guidance reaches each surface; they do not
establish that a live provider follows it on every turn.
