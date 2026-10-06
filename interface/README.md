# Interface Runtime Notes

This directory owns MO's prompt_toolkit TUI. Keep changes extraction-safe and
runtime-truth-safe; do not redesign while fixing seams.

## Why this matters

MO's taskboard is product truth, not decoration. The provider can propose and explain, but task progress must remain evidence-backed:

```text
Gateway owns task lifecycle/finalization
Agent maps tool/runtime evidence
Interface renders truth only
```

Any interface cleanup that lets provider prose, callback markup, or display code create/complete/block tasks is wrong.
Explicitly excluded delivery actions do not become execute-row requirements;
positive commit, push and deploy requirements still need their matching evidence.
Completed-board summaries abbreviate redundant done/open counts while retaining
task rows, duration, edit counts and the existing scroll controls.

The active tool's catalog identity and executing runtime phase drive the warm activity pulse.
During a Codex Responses request, the same transient activity line shows sampled
stream phases with the request number; its turn clock keeps running between signals.
These receipts show transport activity, not completion or verified work.
Basic file, shell, Git, test and web operations stay neutral; other native tools
reuse the executable catalog rather than a second feature whitelist. The pulse
ends with foreground activity and does not prove a successful tool result.
Automatic context preparation, configured LSP checks, affected-test selection,
conversation-memory writes and answer checks use the same activity line.
Context preparation appears as **Knowledge**; its `▸ [context] supplied` receipt
uses the same activity-row renderer as `[read]` and `[edit]`, including the rail,
chip, target shortening, and wrapping. Dashboard Checks retains the full supplied
source list. A supplied category does not prove model adherence.
When background project maintenance advances after the first provider request,
the same callback and active-skin roles render `refreshed code graph`, followed
when applicable by `refreshed project documentation + project history` after the
shared worker finishes. These use the existing context row; there is no second
panel, palette, or graph-specific color authority. A project-documentation
receipt requires nonempty source context; repeating the request or a heading
alone is not delivered documentation.
Skipped checks do not become successful checks or a fabricated LSP activity.
Large Dashboard map renders do not block Checks, LSP settings, or terminal controls.
Checks samples the selected conversation's own turn records; unrelated terminal
logs and metadata-only session loads do not displace its evidence.
Response tables preserve literal/code pipes and fit narrow terminals by wrapping
cells or repeating the identifying column across smaller column groups.

Token displays keep provider input/output totals separate from context reductions.
The footer labels result-cap text estimates as `capped ~…t` and serialized
momentum reduction as `compacted …ch`; it does not combine them into a saved-token
percentage. `/usage` and `/status` identify serialized characters explicitly.
Provider-reported prefix-cache counts remain independent accounting.
The footer prioritizes the project and model; token, context-reduction and provider
usage details remain visible in a secondary style. Final responses emphasize the
opening result line; exact `[Verification scope]` disclosures remain visible in
a secondary style without changing verification decisions or their wording.
Exact consecutive successful inspection rows coalesce in the managed viewport
and are emitted once in native scrollback. Intervening messages, failures and
mutation rows remain visible; execution receipts and live activity are unchanged.

## Current composition

- PRT has one command-list root. Its submenu offers automatic pre/post selection (`/prt`),
  current uncommitted changes (`/prt .`), recorded
  reviews (`/prt report`), and a custom commit/range/path. Duplicate auto/fix
  controls remain retired: worktree/path targets report only; committed targets
  with confirmed findings start the existing normal Agent worker for correction
  and focused verification, followed by fresh assessment of the original change
  plus corrections until the target is met or progress stops with a reason.
  Plain `/prt` selects uncommitted work when present, otherwise HEAD, and names
  pre-commit or post-commit mode in the current terminal. PRT uses the worker registry's live state for review
  and correction activity. Starting a review identifies its existing MO instance
  in one persistent, skin-colored notice on the transcript's system rail, not a
  chat message; blocked worker outcomes appear with their reason, completed
  reviews and final corrected candidates keep their existing report renderer.
  Reports retain duration, origin, source and coverage, using the active skin's
  `prt-*` roles. Report routing defers output during an active assistant response
  and owns the completion notice, so worker callbacks must not duplicate it. The
  unread marker names the actual review state (`clean`, `unresolved`, or
  `incomplete`) rather than implying delivery readiness. A result that finishes
  during an active MO turn joins the next provider checkpoint without
  interrupting the in-flight response; otherwise it remains pending for the next
  turn.
  Snapshot, concurrency, provider recovery and verification rules belong to the
  [PRT review contract](../core/MAINTAINING.md#prt-review-contract); UI adapters
  project that state without adding admission rules or mandatory reruns.

`interface/main_terminal.py` has been reduced by moving focused seams into flat interface modules:

- `command_registry.py` — sole built-in slash-command inventory, dispatch-name,
  metadata, query, and Command Center list owner; agent handlers derive from
  registered command roots. Runtime-backed choices such as loader-visible skills
  become ordinary arrow/Enter rows from this same registry instead of a second
  transcript-only list. Saved-session rows consume the age and first-message
  preview returned by `SessionManager.list_sessions()`; choosing one reuses the
  existing session switch and transcript-reset control, closes the palette, and
  leaves report rendering for commands that actually return reports.
- `command_palette.py` — palette state, fragments, drilldowns, and model choices.
  Tabs and result actions keep the selected choice visible at narrow widths.
  Reports reuse the cell-aware transcript wrapper, including long paths and wide
  Unicode. Below 60 columns, the selected command and description use two rows;
  navigation hints get their own row on compact terminals.
- `theme.py` — prompt-toolkit style map.
- `worker_status.py` — worker status text.
- `tui_goal.py` — goal UI lifecycle mixin.
- `terminal_loop.py` + `native_terminal.py` — styled TUI startup plus shared runtime/session helpers; there is no second interactive command loop.
- `live_control.py` — the existing TUI Live Control lane. Each terminal process
  advertises `terminal:<MO_INSTANCE_ID>` as its reconnect identity; its visible
  label remains presentation only, and the hub's current host ID owns every
  lease. Remote Escape reports the exact local queue-cancel/stop-escalation
  result instead of replacing it with a generic acknowledgement.
  Snapshot deduplication records successful sends; failed sends retry unchanged
  content through the existing watcher. Each lease owns its watcher stop event;
  a delayed send from a closed lease cannot resume watching or suppress the
  replacement lease's next snapshot.
- `task_board_view.py` — taskboard display fragments.
- `formatting.py` — small formatting helpers.
- `activity.py` — activity/status/footer display helpers; Codex quota reset countdowns use the compact `R6D 7H` form. Project mapping uses this same activity lane plus the canonical worker registry and does not own a separate panel or transcript visual.
- `input.py` — the alternate prompt-toolkit input path reuses `activity.py`'s canonical footer fragments, including project/token/provider/notification status and the active-worker cluster, instead of maintaining a reduced duplicate footer.
- `display_delegates.py` — MoTui display delegate wrappers for activity/status/footer/boards.
- `response.py` — assistant response typography. Markdown tables stay in this
  canonical formatter and derive their maximum width from the real response
  columns minus the transcript inset, without a fixed 40-column floor.
- `response_mixin.py` — response transcript helper wrappers for `MoTui`.
- `transcript_view.py` — transcript wrapping helpers.
- `transcript.py` — compatibility managed-viewport helpers.
- `transcript_grammar.py` — the single transcript line-grammar source: gutter glyph + turn spacing per line *kind* (`user`, `mo`, `tool`/`reasoning`/`notice` on the rail, `system`, `visual`, `cont`).
- `transcript_state.py` — canonical transcript storage and presentation mixin
  used by `MoTui`; owns the `_add_line(kind, …)` /
  `_ensure_blank_line()` grammar seam. Styled native scrollback is the normal
  presentation, while the canonical bounded buffer still feeds Live Control
  snapshots. Final assistant, Goal, and PRT reports route their already-formatted
  logical rows through one `_add_fragments_block()` commit, so prompt-toolkit
  suspends/redraws the inline app once per report; incremental tool and reasoning
  rows retain the immediate line path. New lines commit to native scrollback while
  the operator types; only the visible split workspace holds them in the canonical
  buffer, then commits one bounded batch when that surface closes. This prevents
  duplicate split-grid lines without freezing ordinary transcript progress behind
  an unsent draft. The old managed viewport is an explicit compatibility mode.
- `turn_runner.py` — Gateway turn bridge mixin; preserves taskboard truth callbacks
  and labels structural queries explicitly as graph search/callers/callees rather
  than generic search activity. Live shell activity shows bounded recent output
  and time since the last output from the existing process tracker, including
  while the composer contains a draft. Failed commands remain visible in the
  transcript. Shell tools have closed stdin; they cannot consume the TUI's keys
  or answer interactive prompts. Interrupted turns retain tool results as
  history and explicitly mark missing results as unknown.
  Edit/write activity may include `+A -R, Lstart-Lend`: the existing activity
  producer computes the old source range before execution, and the transcript
  renders it without changing file-edit behavior. A new file starts at `L1`.
  These locations describe the intended edit, not proof that it succeeded.
- `tui_app.py` — prompt-toolkit app bootstrap/run mixin and boot logo lines. Its
  refresh thread advances working chrome at the canonical spinner's 80 ms frame
  interval. Buffer edits already invalidate Prompt Toolkit, so only the decorative
  working heartbeat yields for two frame periods during an active key burst and
  then resumes even when the draft remains unsent. That same existing frame clock
  publishes one bounded host title: the redacted first request, current/total task
  position when present, stable instance ID, and provider/model; its spinner prefix
  appears only during real work. The same lightweight poll detects
  new work without a one-second idle-to-working delay. Idle workspaces do not
  repaint on a clock; context polling stays at one second. Event-driven state still calls
  Prompt Toolkit's native coalesced `Application.invalidate()` immediately; MO
  does not add another draft-lifetime repaint queue or a global
  `min_redraw_interval`, either of which would delay visible progress or input.
  `max_render_postpone_time=0` avoids Prompt Toolkit's repeated postponement
  callbacks on Windows while retaining native redraw coalescing. Rendering still
  follows the same frame clock and input invalidations. Reduce a proven event
  producer or rendered surface before considering throttling; never hide renderer
  cost behind a draft-lifetime queue. See the official
  [Application reference](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/reference.html#prompt_toolkit.application.Application).
  Inline Windows rendering accounts for console row reflow before erasing or
  repainting after a width reduction, including attachment to MO Shell. This
  keeps the old activity/composer rows out of committed scrollback while retaining
  the same layout, theme and native history; Shell's full-screen renderer stays
  under its existing owner.
- `layout.py` — prompt-toolkit panel layout construction. Ordinary one-pane
  rendering keeps the inactive split-workspace grid behind a lazy dynamic child
  so composer edits do not materialize hidden workspace containers.
- `keybindings.py` — prompt-toolkit keybinding construction.
- Large bracketed pastes stay unsent in the existing composer holder, whose label
  owns the Enter/Esc hints. Enter submits the held text once and clears that label;
  Esc restores the preceding draft. There is no separate timed paste notice that
  can survive submission. Transient notices render without a spinner; actual
  foreground work keeps its existing activity animation.
- `keybindings.py` + `turn_runner.py` + `core/context/prompt_enhancer.py` — **Ctrl+E** prompt enhancement rewrites the typed message in place: the instant preview only corrects the operator's text, then an off-thread refinement uses the currently selected provider/model with bounded profile, workflow, work-pattern, and current-project guidance without canned boilerplate; **Esc** reverts. Never sends.
- `queueing.py` — pending input queue/steer mixin. The request has one normal user
  transcript echo; queue/steer controls use transient notices, while the existing
  footer owns live pending counts. Promotion and queued execution do not append
  permanent pending-status rows or repeat the request. Enter promotes the latest
  queued item to a live steer, while Alt+Up/Up removes an unconsumed queue/steer and restores its
  exact text to the composer; an already-consumed steer is copied back as an
  explicit corrective follow-up.
  Once current-turn cancellation is pending, repeated Esc/Ctrl+C does not
  restart stop escalation or repeat transcript notices. Esc can still cancel
  queued input or an unconsumed steer; Ctrl+C preserves those follow-ups.
  Dashboard and connected-surface Stop use that immediate Ctrl+C stop owner,
  so one Stop request cancels the current turn and preserves queued follow-ups.
- `input_dispatch.py` — TUI slash/input dispatch mixin and command-palette
  compatibility wrappers; also owns `_open_internal_terminal`.
- `workspace_model.py` — canonical pane identity, project membership, remembered project focus, and the two destination labels: **This machine** and **MO host**. It contains no runner catalog or command presets; every added pane is an ordinary blank terminal.
- `workspace.py` — split-terminal controller. The existing MO TUI remains in its original project; each selected project displays its own live panes. The rail and serving host reuse `Profile.project_locations()` for curated operator entries and project/repo facts, without another persisted registry. Named facts retain their display names. A named project without a usable folder stays visible as **no folder** and cannot launch; an explicitly named personal path is displayed without inspecting its contents. Recent launch folders are not promoted into this list. The current project and groups with open panes stay reachable even if their directory disappears. `/projects` retains its separate recent-folder history, labeled **Recent** in the launch overview. Switching projects preserves their processes and drafts, while an empty project has no input recipient. Added local panes are real PTYs and MO-host panes reuse the existing authenticated Everywhere terminal actuators and `mo_session` lifecycle. Its one vertical rail discovers already-running MO terminals from the same canonical Live Control host list and attaches a selected terminal as a normal pane instead of launching a replacement. It owns canonical focus, the focused-pane full-window toggle, the footer's live MO-instance count, and exact per-pane input routing without copying terminal or worker state.
- `workspace_pty.py` — lazily loaded cross-platform local-terminal lifecycle
  owner. It inherits the user's shell environment but removes MO
  instance/session/workspace/project routing markers so a nested `mo` command
  creates its own identity and binds the shell's current directory. Focused local panes receive raw keys and paste through this
  existing transport, so interactive full-screen programs own their input. It
  exposes only the exact PTY root PID for bounded Health attribution.
- `workspace_conpty.py` — dependency-free Windows ConPTY adapter over the native
  pseudoconsole API, including exact child/pipe/resize/close ownership.
- `workspace_screen.py` — MO-owned bounded VT screen projection for local PTY
  backends; malformed or unterminated escape payloads remain bounded.
- `workspace_remote.py` — workspace adapter over the existing Everywhere owners:
  **MO host** starts a bounded terminal on the serving hub, while the rail can
  attach an already-advertised terminal instance from canonical Live Control
  status without replacing it. The adapter matches that exact instance to its
  `mo_session` host, obtains an authenticated lease, validates bounded sequenced
  snapshots, forwards complete composer submissions and bounded control keys,
  keeps idle leases alive, and re-resolves fresh host authority before every
  lease. Closing a started pane stops only its hub-owned terminal;
  closing an attached pane releases only the workspace lease and leaves the
  original terminal running. Desktop host actions remain owned by their
  portable and external-control callers rather than changing this destination.
  Hub starts pin the serving process's resolved non-secret profile routing into
  the tmux session so stale multiplexer state cannot select another profile;
  controller and provider credentials remain separate.
- `workspace_panel.py` — skin-owned responsive grid plus one keyboard-only left vertical terminal rail; no workspace mouse-selection path exists. It derives geometry from canonical pane order, lets a lone final pane span its row instead of painting dead grid slots, keeps each pane within the responsive scrollable grid, and reuses the existing working/idle/attention state markers. The main pane header stays role-only (`MO · main`) because its host title and footer already retain the instance/model identity; added terminal panes show their own title (`MO · <worker-id> · <model>` when published) and cycle through the same six skin-owned accents as MO Phone; the matching rail entry keeps that accent while focus adds emphasis. The rail is the sole selector: **+ New terminal** and **Health** come first, then **RUNNING** — every MO terminal: this window's panes, MO host terminals to attach, and MO terminals in other windows on this machine (from the shared instance heartbeats: one background reader while the rail is open, every 3 seconds, repainting only on change) — each with its state flush right and, beneath it, its project and what it is working on; then **PROJECTS**, one flat list with the location (`local`, `server`, `no folder`) flush right and the active project's terminal count. The active project keeps the primary workspace title background when the cursor is elsewhere; the cursor is the skin's selection bar (no underline), and the separator is a quiet line close to the background. A long list scrolls under one pinned key-help footer that follows the selection. The rail grows only to 32 cells on roomy terminals. There is no combined UI pane-count cap; the host supervisor retains its own resource bound. Opening the rail keeps the current pane or Health selected. The new-terminal action retains **This machine** / **MO host** and defaults to the selected project's source. Rail, separator, and content widths stay inside the real terminal width even below the normal 41-column split. Alt+F projects only the focused tile at full workspace geometry until the second press restores the same grid. An MO host pane says "Starting MO on the MO host…" until the host MO is up, shows a short (8-character) id in its title, and keeps the host MO's own activity line under its transcript. Main-MO and remote logical transcript rows reflow to tile width, while local PTY rows retain terminal-owned wrapping and blend a default ANSI-black canvas into the active skin without discarding explicit child backgrounds.
- `core/runtime/resources.py` — the shared dependency-free Windows/Linux sampler owns bounded machine readings and exact process-tree attribution. Native probes retain process creation times and admit a parent/child edge only when the child does not predate the parent, so a reused numeric PID cannot pull older unrelated processes into Health totals. While Health is open one background worker reads every 2 seconds (one reading, one repaint) and ends when Health closes; it completes the CPU baseline itself, and Health shows each machine and terminal in aligned tables with the reading's age. Opening the rail runs the independent terminal-discovery/host-observation refresh, with overlapping requests coalesced by the existing worker. Cached host readings retain their source age; neither Health nor the ordinary rail polls hosts while left open. Health shows each machine once, each attached terminal once, a local total only when multiple local trees make it informative, and the selected provider with `/status` for the full runtime report. Unknown, partial, warming, unsupported and stale readings remain explicit. Added host reads use the existing status/socket contract without taking a control lease. Rail and Health reuse readable terminal roles and canonical pane positions, while exact IDs retain routing ownership. `Alt+Up/Down` and `Alt+Home/End` scroll Health without moving pane focus. Health uses accent headings and selection, neutral readings, and semantic color only for load or observation warnings.
  A failed local Health read shows the existing unavailable state; reopening
  Health requests another reading.
- The paired host zoom shortcuts below apply on Windows; other hosts keep their native terminal zoom behavior.
- `/workspace [new [local|host]|next|prev|focus N|close [N]|single|status]` controls the split. Bare `/workspace` opens a terminal for the selected project when its workspace is otherwise inactive, and reports status while the workspace is active. `/workspace new local|host` requires a project on that source; the rail's **New terminal** opens on the chosen machine directly: in the selected project when it is there, otherwise in the same-named project on that machine (on this machine, the folder MO started in), otherwise the first one listed. `/workspace single` closes the selected project's children and returns to main MO; other projects keep running. Each successfully opened terminal sends one standard host `Ctrl+-` zoom step so additional panes fit more content; closing that terminal sends the matching `Ctrl++` step. `Ctrl+B` directly shows or hides the terminal side panel from the main composer, an MO-host composer, or a focused local PTY; the retired `Ctrl+B`, then `N`/`X` prefix paths are not retained. In the rail, arrows select and `1`–`9` switch straight to that RUNNING terminal; Enter focuses a pane, attaches an MO host terminal, brings another window's MO terminal to the front (for an MO Shell pane, its MO Shell window, named in the pane's heartbeat), selects a project, opens `+ New terminal`, or opens **Health**; and `x` closes only a selected attached pane. Health keeps the rail and replaces the grid with a scrollable snapshot of machine resources and attached terminal trees; a local total appears only when more than one local tree exists, and `/status` owns the detailed runtime report. Connected hosts supply their own authenticated readings; first intervals, unavailable measurements and stale observations stay explicit. Typing `/` on a highlighted pane also accepts it before delivering the slash, so pane 1 opens MO's palette and local Codex/Claude/shell panes open their own command UI. The live MO activity lane and active Taskboard stay inside pane 1 and its scrollback; its existing status/hint row, composer, and footer are bounded to pane 1 instead of spanning the split. When a focused local PTY owns raw input, pane 1 keeps a read-only composer projection so focus changes do not reflow the split; the shared `BufferControl` remains singular and is never routed to that inactive projection. Local PTYs receive only their title and terminal screen, so Codex/Claude/shell chrome is not duplicated by an outer MO input surface. `Alt+Left/Right` moves between the selected project's panes, while `Alt+F` toggles the focused pane between the grid and the full workspace window. `Alt+Up/Down` scrolls main/host transcript rows, reads a local shell's bounded VT history, or sends Page Up/Page Down to a local full-screen child so Codex/Claude keeps ownership of its own transcript. `Alt+Home/End` jumps main/host/local history to its oldest/newest boundary, while full-screen local children receive their native Home/End keys. Ordinary arrows remain composer/history or raw child input. Outside split mode, `Alt+Up` still recalls the latest queued or steered input. Focused local PTYs own other raw keys, including arrows, paste, and `F4`; focused MO-host panes keep the visible composer and submit one complete ordinary message on Enter. Slash input and `F4` open the exact host's command menu: arrows navigate, Enter opens or activates a choice, and `/help` lists selectable commands. Host model and session choices use that host's existing registry and dispatcher over its authenticated Live Control lease; exact `/workspace` controls still belong to the outer window. `Ctrl+C` interrupts only the focused local PTY or MO-host lease. `Alt+X` closes the focused added terminal; the original main MO pane remains the owning window and is not treated as a child terminal. Pane positions are local to the selected project. The rail keeps compact state glyphs while navigation is open, with ordinary state changes repainting at their event boundaries. The footer keeps normal status ownership and does not repeat pane identity or shortcuts. When the full `/activity` panel is visible it exclusively owns Goal/Background/PRT labels; the footer's compact worker cluster remains only when that panel is hidden, including split mode. `/activity` separately owns the opt-in Goal/Background/PRT panel.
- `internal_terminal.py` — light internal terminal: `run_in_terminal` shell-out to run codex/claude/any command on the real console (`/terminal`, `Ctrl+T`); no PTY, no emulator, no new deps.

Each extraction kept compatibility wrappers on `MoTui` where tests/imports still rely on them.

## Rule before touching anything

Before changing a seam:

1. Read the current files involved.
2. Read the tests covering that seam.
3. Name the protected behavior.
4. Add or confirm focused characterization tests.
5. Move one seam only.
6. Run focused tests.
7. Run broader gates only when the repository contract or an explicit release
   boundary requires them.

Do not guess. Do not call something dead unless imports/tests prove it.

## Protected behavior to keep

- Simple chat/no-tool/no-runtime-signal turns must not fabricate task progress.
- The TUI must render `gateway.last_task_board` truth, not arbitrary callback markup.
- The busy activity lane renders current-turn edit totals with the skin-owned
  `diff-add`/`diff-del` classes. After every task is complete, the count line may
  append the same nonzero added/deleted totals and one frozen elapsed duration
  derived from the board's own `created_at`/`updated_at`; this is display-only
  and must not keep ticking or alter persisted task truth.
- Foreground activity keeps one quiet system presentation: only the compact
  lane spinner animates, `MO` retains the skin's activity emphasis, and the
  remaining sentence stays dim. The lane adds `MO` only once: MO Design uses
  the action label `Designing`, and catalog fallback labels omit a leading
  `mo_` brand prefix while tool-log names remain unchanged. Tool rows use elapsed/timeout text without a
  second spinner or `running` tag; background test launches remain transient so
  polling cannot accumulate duplicate `[test]` rows. Taskboards retain their
  independent evidence/status colors, and the footer is not part of the activity
  animation.
- With the default model-owned taskboard, visible rows come from Gateway truth:
  stable procedure-shaped work may arrive pre-seeded from
  `core/runtime/turn_intent.py` plus `core/tasking/procedure.py`, while ordinary
  reviews and other model-plan work start empty until `set_plan`. The TUI
  renders either source and later evidence-gated
  `complete_task` updates; it never invents rows.
- Final answers cannot complete open tasks by prose.
- Failed verification remains blocked.
- Goal start, active-row changes, completion, pause, and block transitions append
  concise transcript milestones using the existing `notification-goal` skin role.
  The live Goal board remains the detailed view; evidence-only updates that do
  not change progress or the active row are deduplicated instead of producing
  repeated transcript lines. The backend monitor records the visible stage,
  state, and progress counts without storing objective or task text, so trace
  validation can distinguish backend work from operator-visible updates. Goal
  stop signals the in-flight Agent turn, records a paused finish event, and then
  persists the taskboard as paused/resumable.
- Bracketed activity rows such as `[system_health]` and `[set_plan]` are generic
  renderings of the tool name emitted by the agent loop. They do not own or
  duplicate tool execution; tool-specific behavior stays behind the core
  dispatch and taskboard boundaries documented in `core/MAINTAINING.md`.
  Verification rows name the test target or remote check rather than repeating
  SSH environment bootstrap paths; the live row shows elapsed/limit text but no
  progress bar. Only a completed unscoped full-suite run may show a semantic
  `✓`/`✗` result and final duration, and only after ten seconds; scoped tests and
  ordinary shell rows do not invent completion or timing evidence.
- Scroll/selection contract: the styled prompt-toolkit TUI is the default when
  available. Ordinary one-pane MO keeps `full_screen=False`; a split workspace
  or rail temporarily owns prompt-toolkit's alternate full-screen buffer, then
  restores the main buffer's inline origin on exit. The live inline frame is
  erased before entry, keeping native transcript rows intact and preventing
  stale composer rows or added blank space on return.
  Prompt-toolkit mouse support stays off in every mode. Workspace panes, the
  terminal rail, and its launcher are keyboard-only; they expose no clickable
  selection path. Finalized styled lines print into native scrollback outside
  split mode. Outside split mode, the terminal
  therefore owns full-session mouse-wheel scrolling,
  Shift+PageUp/Shift+PageDown, drag selection, and copy; ordinary arrows and
  PageUp/PageDown keep their normal editor/history behavior. The empty live
  transcript region remains a flexible spacer, so status, composer, and footer
  stay anchored to the terminal bottom while history scrolls above them. Set
  `runtime.scrollback_transcript: false` only for the compatibility managed
  viewport, where Up/Down and PageUp/PageDown scroll MO's bounded viewport.
  MO Shell is the bounded exception: its existing host identity already selects
  that managed viewport inside a private projected ConPTY, so the same canonical
  TUI stays full-screen there and anchors its footer to the final projected row.
  Workspace entry and exit preserve that host baseline instead of switching the
  Shell back to the ordinary inline buffer.
  Both transcript paths keep stored text in logical order and apply Unicode bidi
  display ordering only to each final wrapped row, so RTL sentence order is
  readable without changing copied/session content.
  Interactive Terminal startup now requires this prompt-toolkit TUI; there is
  no second plain REPL with separate command dispatch or output semantics.
  The compact launch overview reuses profile projects, accepted learning and
  selectable learned skills, with cell-bounded rows and existing slash commands.
  The footer remains the persistent project/provider/model owner and the OSC
  title remains the host-discovery identity, so startup restates neither. Its one
  `/help`/`/status`/`/dashboard`/`Ctrl+B` discovery row owns command orientation;
  the empty composer placeholder only invites a message.
  The logo is MO's four-cube mark in half blocks. Beside it: the build id (read from
  git's files, no git process) and, when others run, how many live MO terminals the
  heartbeats report. Server aliases stay off the start screen: startup performs no
  network probe and `/status` lists them.
  Unavailable learning reads remain unknown rather than reporting zero skills.
  The active skin, composer, native scrollback and resume/session owners remain
  unchanged; launch rendering creates no additional persisted state.
- The existing backend monitor records terminal size transitions before and after
  rendering as `session_event` / `terminal_geometry`, correlated with the current
  session. Records include the previous and rendered dimensions plus renderer and
  available Windows console cursor coordinates. Unchanged frames add no records;
  this evidence does not repair or certify the displayed pixels.
- The monitor records `palette_state` before a palette visibility change renders,
  then changed `palette_render` summaries from its exact Prompt Toolkit window:
  item/page counts, window bounds, and positions of rows that are present,
  clipped by width, or absent from the rendered cells. It never stores palette
  labels, queries, transcript text, or command results. A missing render after
  an observed opening is distinguishable from a palette that was never opened;
  this evidence does not certify host pixels outside Prompt Toolkit.
- Skin background ownership is centralized through semantic tokens and
  prompt-toolkit classes (`app-bg`, `surface-bg`, `input-bg`). Built-in skin data
  lives as one validated file per skin under `interface/skins/`; its immutable
  registry is the only definition list, while `interface/theming.py` owns runtime
  switching, persistence, and bridge helpers. Custom palettes may set the
  separator/input-border color independently; palettes without that field keep
  their derived color. Layouts apply the classes at the
  container/window level so Default, Dracula, Silver, Cold, and future skins can paint
  their own MO-owned viewport/composer backgrounds without scattered
  per-fragment background hacks. Default's footer, reasoning, input-placeholder,
  and palette-hint roles retain at least 4.5:1 contrast against their mapped
  backgrounds; tests derive those pairs from the runtime style map. The TUI requests truecolor through
  `interface/theme.py` and still respects explicit prompt-toolkit colour depth
  environment overrides. In ordinary one-pane mode (`full_screen=False`),
  MO paints its rendered region and also asks the current terminal session to
  match the active skin background with OSC 11 so host-owned
  padding/scrollbar-gap pixels do not stay black. When enabled, the same existing
  owner reasserts that color after each native transcript commit and each
  alternate/main-buffer transition, while its five-second cadence remains a
  missed-event backstop. This is per terminal session: it does not edit Windows
  Terminal profiles, hide scrollbars, change opacity, or control title-bar
  chrome/old scrollback outside MO's live session.
- The prompt-toolkit TUI prefixes the stable host title with the same canonical
  spinner while a turn, goal, or PRT worker runs. This is visible wherever the
  host displays the title (including Windows tab/taskbar titles). Idle and exit
  restore the stable instance/model identity. This is presentation only and
  never creates task truth or a second host progress indicator.
- Canonical screen observation refreshes the current owner-scoped desktop target
  before capture. `tools/screen.py` first attempts a non-activating render of that
  exact non-minimized Windows target. Its visible-window crop fallback requires
  the same target to be foreground; failed target rendering never widens capture
  to the display. Primary-display capture applies only when no target is bound.
  Target-relative regions must remain inside the refreshed bounds. See the
  [Desktop maintenance contract](../mo_desktop/MAINTAINING.md#process-and-lane-boundaries).
- The composer uses the same full terminal width as the footer separators for
  its top separator, editor row, and bottom separator. Side/corner glyphs are
  intentionally omitted because host-owned terminal edge cells can clip them.
  Its height is exact to the current wrapped input rows, so an empty composer
  stays one row instead of stretching blank space between separators. The
  one-entry height cache is valid only for the exact draft object, terminal
  width, and row cap; a changed identity or dimension recomputes it. It is not a
  status, footer, border, transcript-output, or generic renderer cache. RTL
  drafts use a display-only bidi processor with cursor mappings; the shared
  buffer remains in logical order for editing, submission, and history. The same
  live `BufferControl` moves into the split workspace; never bind the shared
  input buffer to a second hidden composer because prompt-toolkit focus can land
  on the invisible copy and make terminal-pane input appear blocked.
- Workspace pane headers remain identity-only: `MO · main` for the owning main
  pane, and `<destination> · <instance or pane ID> · <detail>` for added panes.
  The main instance ID and model remain in the host title and footer instead of
  being repeated inside the same window. Local detail is the project label or a
  nested MO's model; a host may supply its advertised terminal label, with the
  generic repeated ID omitted. Rail and Health display readable roles with
  current pane positions, not creation counters or internal IDs as the primary
  name. Generic PTY `running` is not an assertion that its child application is
  idle, and `exited` is not task success. Nested MO terminals publish their
  identity through the existing OSC title; the workspace consumes it instead of
  guessing from transcript text. Focus, state, position and hints stay in their
  existing visual or command owners instead of being duplicated into the title.
- Built-in skins are `default`, `dracula`, `silver`, and `cold`. To add one,
  copy `interface/skins/template.py.example`, give it one file, and register its
  validated `SKIN` once in `interface/skins/__init__.py`; see the local
  [skin guide](skins/README.md). `~/.mo/skin` stores only the selected ID. Do not
  fork color paths for TUI, MO Desktop, dashboard, code map, or visualizer
  surfaces.
- Unified transcript line grammar: every producer routes its left edge through
  `_add_line(kind, …)` (see `transcript_grammar.py`) — producers pass a *kind*,
  never a raw marker, so gutters cannot drift. Your messages (`❯`) and MO's
  answers (`›`) sit at the outer edge; one deduplicated blank row separates each
  submitted message from MO's live status, interim prose, tools, or final answer.
  A turn's chrome (tools, reasoning, notices) nests on one `│` rail; queued
  messages use the normal user gutter, with controls in transient notices and
  live queue/steer counts in the footer. ANSI visuals are full-bleed blocks with room around
  them. Interim assistant prose that accompanies tool calls uses one restrained
  activity mark on that rail, emphasizes its opening two-word lead (including
  colon-led text), and keeps the remaining body muted. Inline emphasis reuses
  the response formatter without final-report heading classification.
  Explicit paragraphs, bullets, and sentence boundaries
  each receive a railed logical line; natural width wrapping keeps the same rail
  and hanging indentation. Only the final answer receives response/report
  typography, and final-answer prose remains paragraph-wrapped rather than
  sentence-split.
  Do not reintroduce per-producer hardcoded gutters/indents.
- Transcript notices (colored and non-blocking): low DeepSeek balance (`< $2.00`, once per session), immediate provider/model fallback when a failed request changes the active route, and a distinct turn-end restoration to the operator-selected model. Reuse the `_add_line("notice", …)` rail path + theme styles; they are informational, not gates. Task completion remains visible in the taskboard; suppress the redundant generic `complete_task`/`[done]` transcript chip.
- Skill-delivery receipts stay internal to provider continuity and are not
  projected into the transcript. Post-turn learning events remain separately
  visible only when the learning lifecycle produces one.
- Post-turn learning-lifecycle events use that same persistent notice rail:
  learned rules and terms, skill outcomes, staged workflow candidates, adopted
  patterns, and actionable review prompts. The footer remains for compact
  operational state and never truncates or duplicates those learning events.
- The TUI renders the dirty-checkout post-turn reminder as a short contextual
  idle-line hint for 20 seconds instead of rotating it through footer
  notifications. `/hints off` disables rotating tips, not this status reminder;
  unrelated transient notes remain in the footer lane.
- Rotating hints are curated one-line discovery copy, not exhaustive command
  help. Built-in tips fit the 80-column status budget; non-comment lines in the
  optional MO-home `hints.txt` replace them for that process, and edits load after
  restart. The existing TUI refresh loop redraws only at rotation or status-expiry
  boundaries, while an active notice may retain the row during foreground work in
  ordinary mode or pane 1. Do not add wrapping, another row, or a second timer.
- Parked interrupted work (after a hard-stop): a short greeting/ambiguous return keeps the "resume?" hint, but a clearly-new substantive request (>= 4 words) clears the park so stale context can't pollute the new ask or auto-resume.
- Command Center session selection restores the chosen session through the same
  session-manager owner, replaces the visible transcript from that saved record,
  and refreshes the Gateway-owned active taskboard; it does not merely switch an
  internal session ID or leave the previous chat visible.
- Provider-bound terminal work is checkpointed through the existing atomic
  session snapshot before the first provider call, before tool dispatch, and
  after each recorded tool result. Full requests and recorded results survive
  in interrupted history; a clean final autosave replaces the checkpoint.
  After forced shutdown, `/resume` or session selection restores saved evidence.
  Missing results mean execution is unknown, so verify state before retrying.
  Exit bookkeeping and writes still in progress are not guaranteed to survive.
  Surface-scoped sessions never write the terminal manager, and no prior slot is
  silently adopted.
- Slash commands are control actions and must not echo raw commands as chat.
- `/send <path> :: <device>` resolves one stable transfer target, persists
  sender retry custody, and sends through `core.transfer`; an ambiguous label
  fails closed. `/transfers` combines hub records with local queued/failed
  outbox work without displaying a source path; `accept <id> [path]` is the
  explicit target-side confirmation, `cancel <id>` releases queued custody or
  cancels a hub record, and `retry <outbox-id>` reuses the retained sender copy.
  Transfer progress/notices reuse the existing footer/notice lane at the left
  and never create task truth.
- Local session commands re-synchronize the cached board view from
  `gateway.last_task_board`: `/new` clears prior-session rows, while `/resume`
  and named switches render the selected session's saved board without executing it.
  A status/report question after restoration remains boardless: it reconciles the
  compact handoff with current source history and sends only bounded dialogue to
  the provider, while persisted tool evidence stays available for an explicitly
  requested investigation. A bare continuation completes a pending report before
  considering an unrelated parked board; explicit taskboard wording selects that board.
  Explicit selection also restores retained boards older than 24 hours. Goal
  reports survive independently, but `/goal resume` requires a paused worker in
  the current process; after restart, continue explicitly from the saved session.
  `set_plan(mode="revise")` reconnects that same session's open model/procedure board.
  Fresh startup loads its stable slot before any save, preserving the durable session.
- Slash command metadata has one registry authority for help, palette categories,
  aliases, subcommands, busy admission, and result presentation. Typing `/` or
  running `/help` opens the same tabbed
  command palette, and roots such as `/mo` or `/sk` filter that same palette.
  Main tabs contain one row per command; aliases match that canonical row and
  populate canonical Recent entries. Extra actions live beneath their command,
  not as repeated top-level capabilities. Required-argument rows prefill the
  composer without submitting placeholder text. Saved-session and other dynamic
  choices refresh on input; the cached root catalog invalidates on extension
  configuration/reload, not on every animation frame. The existing host menu
  protocol projects the same registry as navigable rows rather than local tabs.
  Discrete arguments such as `/prt report` remain keyboard-selectable; whitespace
  closes the palette only for free-form arguments. Handlers return semantic
  report, notice, control, or error results so terminal, one-shot, and Telegram
  surfaces do not infer control actions from hidden display strings. The Terminal
  keeps reports/errors in the Command Center, while notices and successful
  controls close it and use the existing transient-notice lane. PRT's explicit
  start action also leaves its persistent system notice in the transcript.
  One-shot and Telegram render the same result choices as labeled exact commands;
  displaying a choice never executes it.
- Selecting a saved session or a complete `/session` action executes that
  leaf immediately. Only the session root and Share/Unshare/Remove selectors
  open submenus; argument completion retains the same session inventory.
- `/role` lists the current project's available roles as direct activation
  choices, including the shipped Book Writer and Project Architect. The same
  inventory supplies Tab completion. Selecting a role activates it and returns
  to the conversation with a notice. Role changes save immediately, including
  before the first message. Conversation requests use the same lifecycle via
  `role_work activate/off`. `/role show` opens the native Mologrthim workroom
  against this Terminal's own roster and workers, without starting a
  task or another Agent; with no active role, it selects Project Architect first.
  It never implicitly replaces a different active role. Conversation returns to this Terminal. Closing the
  view keeps the conversation; leaving the role, changing conversation/project,
  or exiting Terminal closes its view. `/role status` shows its state and
  `/role off` leaves it. Direct background role requests retain their syntax.
- `/projects` is recent-working-directory history, not a project switcher or a
  complete inventory. The Ctrl+B rail instead uses curated
  `Profile.project_locations()` entries and the current project to select terminal
  groups without changing an existing MO session's project. Named entries without
  a folder remain visible but cannot launch a terminal.
  `/projects` shows the current folder marker, paths, session counts,
  and last-opened times in grouped entries. `/undo` removes conversation context
  while retaining historical terminal scrollback; its notice makes that boundary
  explicit. Invalid `/show` or `/hints` arguments leave preferences unchanged.
  `/doctor layout` renders the existing detailed, read-only layout report;
  `/doctor` and `/doctor --json` keep the general health report. `/doctor
  personalization [--json]` renders the canonical read-only audit of profile
  structure, accepted learning, recall availability, session/closeout retention,
  file/graph health, and project-file recurrence. It reports exact mechanical
  maintenance, evidence-review, and operator-decision boundaries without applying
  changes or claiming semantic profile freshness or a behavioral cause.
- `/profile facts` browses readable saved notes, with detail, prefilled editing,
  and confirmed forgetting through the existing profile-fact owner. IDs remain
  available to exact typed commands but are not required for menu navigation.
  `/schedule list` similarly selects a task for details, edit, pause/resume,
  next-tick execution, or confirmed removal through the scheduler owner.
  Result actions show their position/count; Tab or Left/Right chooses an action,
  Up/Down scrolls the report, and Enter selects. Confirmation opens on Cancel.
  Read-only note/task details remain available while MO works; mutations queue.
- The learning menu keeps pending review, accepted suggestions and status in its
  main view. **More actions** contains optional scanning, consolidation and skill
  imports through the same registry and handlers; exact typed commands still work.
  Automatic learning does not require opening this menu. `/profile mine` shares
  the effective learning review instead of presenting already-active candidates
  as new approvals. Active learning lists accepted suggestions and promoted
  workflow habits from their physical skills, including after restart. Undo
  recoverably retires the exact generated packs; changed guidance requires a
  fresh review and failed retirement stays visibly active. Other profile rules
  and authored skills retain their existing `/profile` and `/skills` views.
- Ctrl+E prompt enhancement must replace the input buffer only (Esc reverts to the original). Its instant preview only corrects and shapes the operator's text; provider refinement must use the currently selected provider/model and may use bounded operator-profile, approved workflow, MO work-pattern, and canonical current-project guidance for a genuine rewrite, but must not add generic boilerplate, send, create taskboards, or mark progress.
- Ordinary busy input queues and appears once as a normal user message, with
  transient notices for the available controls and live pending counts in the
  existing footer. A second Enter offers that exact message to the running turn
  as a saved user correction at its next safe checkpoint on the same model;
  a transient notice confirms submission without a permanent pending-status row.
  Starting the queued turn, including an unconsumed late steer, suppresses a
  second user echo while preserving the existing input-routing owner.
  Alt+Up, or plain Up on an empty editor, removes and restores an unconsumed
  queue/steer for editing; if MO already consumed the steer, it copies the text
  into the editor as a corrective follow-up instead of claiming an undo. The
  in-turn buffer holds at most three 12,000-character updates; a promotion beyond
  that boundary fails closed and preserves the exact message in the normal
  pending queue.
  There is no third-Enter stop shortcut. First Esc cancels the latest still-pending
  queued input or steer with an exact notice; three Esc presses stop MO. Incoming
  queued or steered text does not reset that stop count. Ctrl+C or
  a direct `wait`, `stop`, `pause`, or `cancel` message (including polite/emphatic
  wording such as `just stop`, the common `jsut stop` typo, and trailing punctuation)
  immediately cancels the current turn, preserving queued follow-ups. Requests
  such as `don't stop` or `stop Blender playback` remain ordinary work. When no
  turn is running, the same direct message returns `Nothing to stop` locally
  instead of starting a provider request. Live Control relays the same Esc notice.
  The compatibility workspace child's explicit Cancel command uses that same
  immediate interrupt, not the staged Esc gesture.
  Ordinary input, retry, and slash-launched turns share one admission path that
  claims busy state and cancellation before starting the worker. Exit requests
  cancellation and waits up to five seconds for that owned turn before teardown;
  queued follow-ups do not start during teardown. Session checkpoint custody
  remains with the existing terminal loop.
- Typed and selected commands use one busy boundary. Registry-admitted inspection
  commands (such as `/status`, `/projects`, and `/settings`) can run during a turn
  and never enter the chat queue. Other state-changing commands stay unavailable
  until idle, apart from their existing explicit goal/PRT controls. A `/model` choice made
  during an active turn replaces the one pending model choice and applies after
  that turn, before the next queued input; it never mutates the in-flight request.
- MO Desktop launches separately and cannot own terminal task state.

## Game Collaboration mode

Game Collaboration is an explicit, Terminal-only project context for users building
software games. It is inactive by default and does not activate from words such as
"game", "level", or "character".

Use `/game start` to bind the current project, `/game status` to inspect its
user-owned record, `/game review` for a read-only consistency summary, and
`/game stop` to leave the mode without deleting its record. `/game pause` preserves
the record while disabling context; `/game resume [project]` explicitly reloads a
saved record in a later Terminal session. This is separate from `/resume`, which
restores a conversation transcript. The active or paused project binding is saved
as bounded metadata with that Terminal conversation and is restored only by an
explicit conversation switch/resume; sibling history projection never adopts it.

Questions and proposals are explicit records: `/game ask <id> <question>`,
`/game decide <id> <answer>`, and `/game propose <id> <summary>`. Approval is a
separate exact action: `/game approve <proposal-id>` or `/game reject <proposal-id>`.
Answers and proposals never execute tools by themselves. Concurrent Terminal
sessions use record revisions and reject stale writes instead of overwriting a
newer decision.

The record is private runtime state under `memory/work/game-collaboration` and is
not a second transcript, taskboard, skill pack, or project-local instruction file.
Its bounded context is injected only into active Terminal provider turns. Desktop,
Board, Design, API, Telegram, scheduler, and ordinary non-game turns do not receive
this context or change their existing behavior.

## Current ownership map

Confirmed remaining in `main_terminal.py` after the latest extraction:

- Import/composition surface for `MoTui` mixins.
- `MoTui.__init__` state initialization.
- Entrypoints and session recording live directly in `terminal_loop.py` and
  `native_terminal.py`; `main_terminal.py` owns only `MoTui` composition/state.

Do not split further just to chase line count unless the state initialization
has clear tests and a clear owner.
# Browser Dashboard connection

`/dashboard show` (or startup `mo --dashboard`) opens the native WebView presentation
attached to this TUI. `input_dispatch._dashboard_action` schedules allowlisted
navigation on the existing application loop and focuses the exact terminal identity.
Commands and prepared requests select the main MO pane, preserving other drafts;
requests still require Enter in the normal composer. The TUI closes the adapter
on exit. Other local instances receive typed controls through the existing
PID/instance-bound handoff and recheck the project on the UI loop before acting.
An image Share request may also bind the current conversation slot; the TUI
rejects a changed slot before it touches the composer and leaves existing text
in place, appending the image path once. This path waits for Enter.
Design context remains an ordinary owner-bound turn, even if it starts with `/`;
it is never reinterpreted as a Dashboard control. No second terminal emulator,
provider or Agent is created. See the
[Dashboard guide](../core/dashboard/README.md) for source editing and connection boundaries.
