# Mologrthim

Mologrthim is MO's operations floor: one native window that shows what MO is
really doing across every running MO, and the place to assign work, hire a
proposed specialist or pause a goal. It is a projection of records. It never
runs an Agent, never dispatches a specialist directly and never stops work.

## What the floor shows (and what each part reads)

| On the floor | Record it reads |
| --- | --- |
| One bay per running MO (Terminals and MO Desktop), its request and taskboard | the heartbeat ledger (`core.runtime.instance.recent_instance_snapshots`) |
| The brain at the centre with the goal loop ring (step N of M, pausing, paused) | the heartbeat's goal summary (`core.context.coordination_state.goal_summary_lines`) |
| Specialists at their stations, their state and rank plates | worker history in the backend monitor (`worker_event`: the architect's verdict and the measured difficulty) and the project's role packs |
| Candidates on the hiring pads, with their CV (why they were proposed) | `core.skills.role_candidates` |
| Beams between MOs | messages between MOs (`run/mo-messages.jsonl`) |
| Taskboard and power walls | heartbeat taskboard rows; the resource sampler (`core.runtime.resources`) |
| Learning wall and archive shelves (lit while indexing) | `core.learning.status`, `memory_index` monitor events |
| The brain's panel: profile, memory, lessons adopted and waiting | `core.diagnostics.personalization` (read-only, refreshed every ten minutes) |
| MO Care by the power wall and its report card | `core.systemcare.mo_care.recent_findings` |
| Review gate (verified / refused) | the same worker verdicts |
| Owner desk | only when the private profile bridge offers commands (`core.local_extensions.command_specs`); absent for everyone else |

Rank is earned, never guessed: points = each report the architect verified ×
how hard the task was (simple 1, moderate 2, complex 3, measured from the work
the worker did) − 2 × each correction; tiers at 5, 15 and 35 points. A report
nobody checked counts as a run only. A specialist with no checked work says so.
Worker events written before the project root was recorded match a specialist
only when exactly one open project has that role. Work still open when its MO
closed or crashed shows as stopped, not working: each heartbeat names its
process's monitor run (`monitor_run`), and an open worker event whose run no
live MO names never reported.

## Acting from the floor

Every action reaches a running MO through MO's existing Terminal handoff
(`core.design.terminal_handoff`), never around it:

- **Assign** sends a normal request to the chosen MO; its lead decides who works
  on it, dispatches the specialist and checks the report, so rank stays true.
  "Ask <specialist>" phrases the request for that specialist; it still goes
  through the lead. "New MO" starts a normal Terminal and sends the assignment as
  its first request once its heartbeat appears.
- **Hire** sends "hire <name>" to that project's MO, which handles it locally.
- **Pause after this step** and **Resume** send `/goal pause` and `/goal resume`
  as that MO's own commands; the running step always finishes.
- **Open terminal** brings that MO's terminal forward. MO Desktop takes requests
  in its own composer.
- **Investigate** on a MO Care report opens the separate report terminal;
  **Dismiss** marks it in MO Care's record.
- The **+** in the title bar starts a fresh normal MO Terminal.

## Owners and lifecycle

- `snapshot.py`: `FloorObserver` builds the snapshot (monitor files read
  incrementally, roles cached briefly) and `conversation()` reads the selected
  MO's last visible messages from its saved session, redacted.
- `bridge.py`: the WebView bridge. One observer thread every two seconds while
  the window is visible; hidden or minimised means no observation and no
  sampling. Actions use the handoff only.
- `app.py`: the native WebView host on the shared window, theme and entrance
  owners (`mo_desktop.app_window`, `mo_desktop.mo_renderer`), the same pattern
  as MO Files and SystemCare; `open_role_workspace` serves `/role show` and the
  Project Architect's `role_work show`.
- `window.py`: `MologrthimWindow`, the launcher used by the Work cube, the
  tray, Project Architect activation on Desktop and Terminal's `/role show`.
- `floor.html`, `floor.css`, `floor.js`: the Canvas2D isometric floor and the
  side panel, coloured from the active skin, with reduced-motion support.
  Animation runs only while the window is visible.

The floor writes no state of its own except a dismissed MO Care report. Closing
it releases its process and never stops an MO or its workers. Startup imports
nothing from this package; it loads only when the floor opens.

## Boundaries

| Concern | Authority |
| --- | --- |
| Roles, rosters, registration and dispatch | `core/skills/`, `core/agent/agent_turn_dispatch.py` |
| Verdicts and measured difficulty | `core/worker/` |
| Candidates and hiring | `core/skills/role_candidates.py`, the local "hire" intercept |
| Goal loop and its safe pause | `core/goal/goal.py` |
| Messages between MOs | `core/runtime/mo_messages.py` |
| MO Care | `core/systemcare/mo_care.py` |
| Terminal launch, focus and handoff | `mo_desktop.design_studio.routing`, `core.design.terminal_handoff` |
| Skin and window chrome | `mo_desktop.visuals`, `interface.desktop_brand` |

Mologrthim must not grow its own Agent, scheduler, conversation store, role
registry or task ledger.
