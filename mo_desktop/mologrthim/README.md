# Mologrthim

Mologrthim owns the presentation of MO's project specialists and their work.
The app name identifies the workspace. Employees use the existing role names
and responsibilities; they have no separate character names or invented rank.

## Current implementation

- `snapshot.py` projects project-bound skill roles and their latest current-process
  worker records, plus the host's exact conversation, session-bound taskboard,
  correlated monitor receipts and count-only learning status. Workers have no
  session identity and are presented as project workers. It writes no state and
  starts no work. Worker reports remain reports, not accepted outcomes.
- `app.py` owns the frameless native workroom and its Terminal/Desktop adapters.
  Both surfaces use this one implementation.
- `/role show` and Project Architect activation retain their existing behavior.
  The execution role still gates specialist registration, dispatch and reports.
  Moving the presentation does not migrate those permissions or remove the
  role from selectors.
- The four-cube launcher's Work group opens this same room dimmed, with a live
  Terminal chooser. Opening the chooser creates no conversation and activates
  no role. Selecting a Terminal requests an observation-only room in that exact
  instance and slot. It has no Talk to MO input. The plus beside Close starts a
  fresh normal Terminal and opens its room with the canonical input path enabled;
  no message or work starts automatically. Its project is the launch host's current project.
- `connections.py` reuses the heartbeat-backed Terminal catalog, typed handoff,
  normal Terminal launcher and native resource sampler. A correlated heartbeat
  acknowledgement closes the chooser only after the selected Terminal opens its
  room. Changed selections and older processes without the new action are explicit;
  no fallback redirects to another Terminal or activates a role. Pending opens
  expire, and repeated clicks cannot launch duplicate Terminals.
- `scene.py` paints the skin-derived 2.5D room and faceless cube bodies using
  Pillow. Tk owns the window, text and input, and this is the sole roster
  renderer. No game engine is required.
- Select an employee to inspect its current assignment, note, evidence and
  returned report; an accent marker identifies the selected employee in the room.
  Select the central MO desk to return to the same conversation. Scroll inside
  the inspector for longer records. Tab selects
  the next role; rosters larger than four use pages. Empty rosters are explicit.
  The conversation panel retains visible user/MO messages with native scrolling
  and selection. In talk mode its composer submits through the existing host input owner;
  a busy host keeps its canonical queue semantics. Drafts survive repaint and
  failed submission. Open conversation returns to the owning surface.
  Taskboard, learning and activity inspectors retain their selected source and
  refresh from its current data; they do not keep a frozen text copy.
  Drag the room background to move the window. Exit or Escape closes only the
  view. The observer remains bound to its exact conversation and project without
  requiring an active execution role; `/role show` keeps its role-bound lifecycle.
- The role remains the execution boundary during this visual phase. Opening
  the launcher view grants no specialist registration or dispatch permissions.

## Ownership boundary

| Concern | Existing authority |
| --- | --- |
| Role identity, responsibilities and project binding | `core/skills/model.py`, `core/skills/roles.py`, and skill packs |
| Registration, dispatch and role admission | `core/agent/agent_turn_dispatch.py` and the Agent role lifecycle |
| Live assignment state and concurrency | `core/worker/` |
| Conversation, task truth and evidence | Existing sessions, taskboards and evidence gates |
| Skin, typography and geometry | `mo_desktop.visuals`, `interface.desktop_ui`, `interface.desktop_brand` |
| Cube forms and authored motion | `mo_desktop.design`, `mo_desktop.emotes` |
| Design artifacts and visual review | Existing MO Design workflow |
| Terminal discovery, launch and control | `mo_desktop.everywhere`, `mo_desktop.design_studio.routing`, `core.design.terminal_handoff` |
| Process measurements and load bands | `core.runtime.resources`, `interface.workspace_panel.health_load_status` |
| Scene, character poses and presentation selection | This package |

The app must not grow its own Agent, scheduler, conversation store, role
registry, configuration store or task ledger. Runtime code imports its native
view lazily. Opening or closing the view never starts or stops project work.

## Visual direction

The visual surface is a frameless, terminal-like 2.5D workroom with an
exit control and a new-conversation plus. Faceless cube bodies express activity through posture and finite
motion. Employees share MO's geometry and skin while differing in proportions,
stance and movement. Preserve the canonical main cube identity in its owner;
scene-specific poses belong here.

Keep Mologrthim-specific artwork and motion recipes in this package. Reuse the
existing form and emote contracts instead of copying their sampling or easing
implementation. A separate scene category must not silently change global
Desktop emotes or their exported cross-renderer contract. The hosting surface's
existing clock drives finite selection/state-change motion. Unchanged snapshots
reuse their rendering; room plates, controls, glyphs and role/state sprites are cached. Hidden
windows stop motion and skip painting. Skin changes invalidate the artwork.
Soft lighting is blurred at a smaller resolution before composition; the
architectural geometry retains its antialiased render pass.

The workroom reflects actual states such as queued, running, blocked and report
received. Animation cannot imply task completion, approval, worker presence or
experience that the source records do not establish. The wall taskboard shows
existing rows and opens their evidence. The inspector retains all supplied
taskboard rows and their complete redacted titles, blockers and evidence; the
wall cards are compact previews. Blocked tasks stay in
the board's In progress column with an error-colored status; they never appear
among Finished tasks. Extra rows have a count and remain available in the
inspector. MO's desk reports active assignment count, not inferred brain activity.
Archive receipts describe recorded index results, never an invented ongoing
indexing operation. Learning shows
profile-wide pending/adopted/memory counts with an explicit unavailable state.
Activity opens the recorded correlated details. Monitor reads use the existing
bounded tail reader on the caller-owned monitor and require both exact session
and monitor run identity. Hosts perform source I/O outside the GUI lane.
New archive receipts, changed taskboard rows and changed available learning
counts briefly illuminate their room connection and display a receipt label.
These cues reuse the finite emote clock and settle after 1.2 seconds. The first
snapshot and first asynchronous source read establish a baseline; unchanged receipts do not replay. Hidden views
consume refreshed state without replaying its changes when reopened.

The room's resource strip shows its shared host, MO totals and the system.
CPU uses total machine capacity; memory is working set. Mologrthim runs in its
host process, so the display explicitly labels shared Desktop/Terminal usage;
it cannot attribute an independent window-only cost. The sampler's opt-in
exclusive-root attribution counts nested Terminals once in the combined total.
Unknown readings remain unknown, including the first CPU baseline. Inspect the
strip for per-Terminal PIDs, process counts and sampling status. Load colors
reuse the existing Health bands. High system pressure offers closing the visuals
and showing the host Terminal; this releases scene artwork and observation,
never stops work, and does not promise that unrelated system load will fall.
Resource and connection reads share the existing single-inflight two-second
observation cadence. Hidden views do not sample or discover instances.
Resource-only changes update their text and color in place. They do not repaint
the room or the dimmed chooser; entering another load band updates its advice.

Coordination pause is deliberately not exposed: the requested safe-boundary
pause semantics require work in the existing orchestration owner. Closing the
window never calls goal stop or changes worker execution.

## Backend work remains separate

Removal of the Project Architect role entry needs an explicit migration of
the existing admission and session routes. Keep those
checks authoritative throughout that migration.

Candidate profiles should project real responsibilities, calibration evidence,
work references and the reason for a proposed specialization. The process-local
worker registry alone is not a durable CV. Missing history stays identified as
missing. Do not create a second personnel database to fill that gap.

Review-round controls belong to the orchestration owner. Their semantics and
existing settings must be established there before exposing controls here;
the scene must never manufacture extra review runs.
