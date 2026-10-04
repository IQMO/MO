# MO Design Studio runtime

**Runtime contract:** `design/v2` plus `board/v1`, including the Current terminal,
Background, and New terminal Handoff routes described here.

MO Design turns a visual conversation into a self-contained concept or interactive
prototype whose saved context carries into the eventual Handoff. Studio can load
an observed current surface, refine only what the user asks to change, and hand
the finished concept to an implementing MO. It does not claim to build the final app.
The `.modesign` suffix identifies a validated Design document or portable image
bundle, never Python, a shell script, or a packaged application. Its visual can run in the standalone desktop renderer;
its explicit name keeps it distinct from MO's `~/.mo` private profile/runtime
directory. The canonical field-by-field contract is documented in
[`core/design/README.md`](../../core/design/README.md). The trusted chrome's
visual hierarchy, responsive behavior, accessibility bar, and frontend ownership
are governed by [`VISUAL_SYSTEM.md`](VISUAL_SYSTEM.md).

## Artifact boundary

The [artifact guide](../../core/design/README.md) is the single documentation
owner for the `design/v2` fields, Board validation,
portability, and storage boundary.
Studio consumes that contract and never adds a second file format.

The sandboxed preview receives the resolved Studio's active `--mo-*`,
`--panel-*`, `--button-*` and `--cube-*` variables, including typography,
status colors and control geometry. Existing `--mo-preview-*` aliases remain
available. App previews can consume the current Desktop theme without copying
a palette or inheriting unrelated Studio variables.

Each design is an isolated session folder at
`media/designs/<id>/` with distinct artifact and private-state responsibilities:

- `<id>.modesign` is the visual and implementation brief with private catalog image references; Download packages its referenced images.
- `<id>.board-working.json` is immediate user ink, one optional MO proposal,
  and an optional exact originating-terminal identity. It is private working
  state, not another portable format or transcript.
- `session.json` is bounded private UI state: recent conversation, truthful
  current activity phase, pending
  Design request kind, current-head completion baseline, exact selected source
  revision, the last completed revision, and a bounded attention result when an
  accepted turn finished without changing the visual. A historical request reads
  and inherits that selected snapshot and excludes later session conversation
  from its model prompt.
- `revisions/rNNNNNN.modesign` holds at most 40 private snapshots. The revision
  badge opens history. Selection previews an exact snapshot without creating a
  revision; Source, native Download, Handoff, and the next Design prompt use the
  selected version. Confirmed Delete applies only to a non-current snapshot.
  The current artifact and every unselected revision remain unchanged.

The Desktop tray opens or focuses its **Design workspace**, initially the
Welcome page, leaving other standalone and terminal-linked Design windows
untouched. Welcome introduces Explore, Edit, and Handoff in three short steps.
**Start a design** creates a clean session in that same window; **Open saved
designs** loads the chosen artifact and saved conversation there. Returning via
the tray focuses that workspace without discarding its active design. Welcome
itself creates no artifact or model request.

The Studio's design switcher lists bounded safe metadata only and swaps the
active document and matching private conversation inside that renderer.
Recent conversation is injected only as
bounded private prompt context and is never serialized into the Design artifact.
The compact **+** action creates a clean isolated design/conversation while
keeping the current optional read-only project.

Project context is optional per design. **Choose project** uses the native
folder picker and stores that folder only in the current artifact's handoff.
**Use no project** clears it without affecting any other design session.
An attached project stays read-only throughout all Design work. MO maps relevant
routes, components, theme, layout, files, and symbols from current source into
the visual and brief; graph hints are orientation only and must be verified.

Approved operator visual preferences remain in the private profile rather than
the artifact. They may guide explicit refinements or new concepts, but they never
override the current request, project evidence, or accessibility and are not
serialized into the portable artifact.

## Runtime flow

1. The agent calls `mo_design` with `action: open`. The service writes a valid
   starter document and launches or focuses its renderer. A terminal-launched
   Preview hides the entire conversation/project/brief/Handoff rail and library
   controls, including Handoff in revision history. Manual editing, Board,
   history, Download, and activity details remain available; conversation stays
   in the originating terminal. This presentation belongs to the open request,
   not the saved artifact: reopening from Welcome shows standalone Studio in
   that same window; the actual invoking surface determines terminal mode,
   independently of the executable that launched the terminal.
   `view: board` retains its existing exact-terminal binding and Board-only
   presentation; normal Preview remains the default.
2. Provider argument deltas for later `mo_design` updates publish bounded
   HTML/CSS/script fields to that design's private live-preview file. Writes are
   throttled while tokens arrive, the final snapshot is always flushed, and
   other tool payloads are ignored.
3. The Studio bridge checks the committed file and live preview. It returns a
   new payload only when the artifact, session sidecar, or live source changed,
   while the shell keeps the last finished visual visible until the new revision
   commits. Token-incomplete markup is never promoted into the user-facing
   preview; the compact refresh action safely reloads the last finished visual
   during work or the current visual when idle. The bridge never completes work
   merely because a revision appeared. Provider/worker completion alone is not
   visual completion: after the worker callback, the resident broker requires a
   newer committed revision whose HTML/CSS/script/manual-edit signature differs from the
   starting visual. The one deliberate exception is an explicit brief-only turn:
   it must return the `Design brief updated` result contract and change real
   Handoff fields, so arbitrary metadata still cannot masquerade as a visual.
   A turn without either form of evidence clears
   pending work and renders a compact retry/attention state instead of leaving
   Studio loading or announcing a false update.
   **Stop** in the bottom activity line cancels only this request through the
   worker's existing turn cancellation signal. Studio keeps the request pending
   while its current operation unwinds; saved artifacts and manual edits remain.
   Queued requests can be stopped before they start. Requests older than 15
   minutes request the same cancellation instead of merely hiding a live worker.
   A disconnected standalone viewer can only release its own waiting display.
   A failed worker may leave a committed partial revision, but that revision is
   explicitly unaccepted. Studio retains the last accepted preview when one
   exists and states when a first-turn failure has no completed preview to retain.
4. Ordinary chat is dispatched as one two-way visual conversation with the
   user's full words preserved. MO may answer a question, explain a tradeoff,
   suggest options, or ask for clarification without changing the artifact; an
   explicit response contract lets the broker finish that turn without inventing
   a revision. A requested load or visual change still requires a newer changed
   visual, and the worker's concise delivery explanation remains in chat instead
   of being replaced by a generic completion line. The locked contract makes current-surface work
   observation-first, permits only changes explicitly requested now, and treats
   future/deferred refinement language as context rather than present authority.
   It verifies source and tests, avoids guessed live values and proposal panels,
   and labels source-only reconstruction honestly. A new app, product, or showcase
   request defaults to a decision-useful interactive prototype unless the user
   explicitly asks for a static concept: primary controls work in the sandbox and
   the preview includes the proportionate screens/states, keyboard/focus,
   responsive, reduced-motion, loading, success, empty, and failure behavior.
   The same update records a meaningful objective, acceptance outcomes, visual
   decisions, and evidence limits. Important screen/flow/behavior descriptions
   stay in the brief; stable file/symbol mapping is recorded only from verified
   current source. Each `action: update` sends only changed fields; the save owner
   retains omitted fields from the selected revision and commits the complete
   artifact atomically. After the commit, the same Design worker uses
   the existing bounded computer/vision observer for one visual QA pass on one
   exact rendered Studio target. On Windows, a non-minimized Studio window may
   render while covered without activation; unsupported background rendering
   is reported unavailable rather than stealing focus or widening capture. The
   worker may commit at most one further complete
   correction followed by one re-observation. The broker requires the actual
   `computer_targets` and observation-producing `computer_observe` callbacks;
   activity text alone is not evidence. It must say when visual QA was
   unavailable rather than claim success or continue a capture/update loop.
   Verified files and symbols already recorded by the artifact are read first;
   the worker does not repeat broad discovery unless a specific fact is missing.
   A user message may also carry up to eight native-picker attachments of at most
   20 MiB each, including no-text attachment-only input. The bridge imports each
   file into the canonical private catalog and exposes only safe metadata and
   opaque IDs to Studio JavaScript and the session sidecar. The broker resolves
   the exact saved files for the current turn; source paths stay Python-only and
   attachment contents are untrusted evidence, never embedded instructions.
5. If testing an enabled artifact script throws, the iframe reports a bounded
   runtime error and the last control descriptor to the trusted shell. It never
   reads input/select/textarea values. Studio deduplicates the failure per
   design revision and routes one diagnostic repair inside Studio;
   the repaired committed revision resolves the same pending work. Terminal-linked
   Preview shows the error in activity details and leaves repair to that terminal;
   it never starts an invisible Desktop worker. Without a
   resident Desktop host, Studio records the issue but does not open a terminal.
6. Studio has no model control or last-used model state. Before each worker
   starts, the broker reads Terminal's single persisted `/model` preference and
   revalidates it through the same shared activation owner; with no saved choice,
   the resident Agent's configured model remains active. No model value crosses
   the renderer channel or enters the artifact, sidecar, environment, command
   line, or credential store. A configured fallback remains reactive only after
   a real provider call fails and cannot replace the saved preference for the
   next request.
7. **Send** routes the full request in the hard `mo-design` sandbox lane.
   It permits validated `mo_design` artifact actions but rejects all project
   mutation and actuation, including with operator override. MO must update the
   same `.modesign` artifact; the send does not grant implementation approval. The
   single enlarged bottom activity line shows bounded real worker phases such as
   preparing context, considering, inspecting source, observing pixels, working
   with the saved Design, and checking the
   result. It is the only animated in-work indicator and resolves truthfully to
   an answer, `Static concept`, `Interactive prototype`, `Visual QA unavailable`,
   `Brief updated`, or attention state; it never labels every revision “ready.”
   **Details** expands the full activity text. Tool failures are retained in chat
   while MO continues, and failures before worker startup resolve the pending
   request visibly. A Design read is never labelled as a saved visual update.
   Ordinary Design chat uses the Agent's existing context preparation; its
   broker does not perform an additional graph scan before launching the worker.
   Feedback on manual edits starts with the saved revision and relevant preview,
   with project source consulted only when the requested claim needs it.
   For Board work the worker reads bounded `board/v1` state and may submit only
   declarative `board_propose` operations. The broker recognizes a Board result
   only when that proposal sidecar exists; Studio renders additions/updates as
   a distinct brand draft and deletions as a distinct error-colored draft.
   The user alone can Accept or Reject it.
8. Ordinary **Send** continues the iterative Design conversation. Each project-
   bound visual request carries explicit current-state, refinement, or new-concept
   intent; current-state evidence checks therefore apply beyond the first revision.
   **Handoff** is enabled when no request or attention item remains. A saved
   Terminal or historical revision needs no Studio-worker completion marker;
   this explicit transfer is not a visual-QA attestation. Its compact sheet recaps
   the actual saved title, summary, revision, Static/Interactive state, optional
   implementation brief, and selected project context. Those saved fields carry
   what Design already built; they are not a fresh intake form or blocking
   checklist. The only decision in the sheet is which MO recipient receives the
   finished work. Current terminal continues its existing conversation;
   Background and New terminal start implementation goals.
9. Final handoff builds fresh context from the document. When a project is pinned,
   bounded structural-graph hints are included only if its persisted graph is
   fresh, and the prompt still requires verification against live source and
   focused tests.
10. Design completion uses the existing private `session.json` lifecycle record,
    never a second artifact or task system. After the receiving MO verifies the
    approved implementation, it calls `mo_design action=complete` for that exact
    design and revision. Studio observes the existing session poll and closes the
    matching window. Reopening reactivates the conversation while retaining the
    resolved revision. Studio shows **Resolved · rN** whenever that exact revision
    is selected; a newer revision remains unmarked until separately verified and
    completed. See the [artifact handoff contract](../../core/design/README.md#handoff-boundary).

## Manual agreement in Preview

Choose **Edit design** to customize the currently visible screen. Click an HTML
element, drag to move it, or drag its corner to resize. The inspector provides
text, dimensions, spacing, corners, typography, colors, alignment, and opacity;
the element selector also reaches containing elements. Container text is kept
intact; select a leaf text element to change its copy.

**Undo**, **Redo**, and **Reset selected edits** remain available across autosaves.
Changes save after a 1.2-second settled burst, or immediately through **Save**
or Ctrl/Cmd+S. The status distinguishes unsaved work and save errors. **Done**
saves before returning to prototype interactions, which restart from their
initial state. Switching surface or Design, opening history, sending chat,
downloading, closing through Studio chrome, and opening Handoff finish the
current edit first. An abrupt process exit can lose changes still marked unsaved.
Historical revisions remain read-only.

An external revision change cannot silently overwrite local edits. A failed
stale save keeps them in the open editor; **Reload saved design** asks before
discarding unsaved work. Saved overrides whose elements are absent from the
current prototype screen are reported. The [artifact contract](../../core/design/README.md#manual-preview-edits)
describes element matching, source preservation, and the MO refinement boundary.
Manual Save does not claim that a worker performed visual QA or completed an
implementation. Handoff captures the reviewed saved revision and checks it
again before dispatch; queued commands carry that exact revision.

## Board clarification and terminal linkage

Board and Preview are two views of the same Design artifact, not separate apps.
The trusted Canvas2D surface supports select/move, pen with coalesced pointer
samples and pressure, eraser, line, arrow, rectangle, ellipse, text, undo/redo,
semantic color, stroke width, pan, zoom, resize, and shared element properties.
Existing text can be changed through its properties dialog. Each gesture is saved immediately;
a 1.2-second settled burst checkpoints one ordinary Design revision. Historical
revisions are read-only. The current editing session retains undo/redo after its
own checkpoints; selecting another saved revision establishes a fresh history.
Whiteboard paper and ink derive from the active shared skin and keep contrast
when switching skins.

The terminal opens Board directly for explicit draw/diagram/annotation requests.
Terminal and Desktop route that explicit launch through the same narrow
`mo_design` catalog. The cross-surface completion gate accepts only a successful
`open` or `show` call with `view: board`, so Desktop must not substitute Paint or
generic screen actuation and Terminal must not expose raw tool syntax as prose.
That route reuses this renderer and artifact in a Board-only presentation: no
conversation rail, Preview switcher, library/new controls, minimize, or Handoff.
It retains skin-native pin, close, resize, Board tools, Download, autosave, and
revision checkpointing. One private `board-link/v1` reservation permits only one
live standalone Board per exact terminal; showing the same design focuses it,
another design is refused until the first closes, and renderer exit releases the
token-bound row.
The same Board-only open/show/read/propose route is available during ordinary
clarification-safe investigate/review work even when full Preview mutation is
not. Desktop binds the selected live terminal when one is provable; without one,
it opens the saved Board unbound without terminal linkage rather than
inventing a terminal shortcut or claiming the feature is unavailable.
For genuinely spatial architecture, layout, or interaction work it may offer
Board once, and it may offer once after two clarification attempts leave the
same material ambiguity unresolved. Simple/non-spatial tasks are not interrupted,
and a declined offer is not repeated.

A compact runtime-only badge shows **Connected · MO terminal** plus a bounded
workspace/instance label while that exact origin remains live; it is never
serialized into the `.modesign` artifact. The badge is status only. On the
user's next drawing follow-up in that terminal, the bound `mo_design` route
resolves the artifact and `board_read` supplies the current Board; trusted chrome
does not queue a second Board-content turn or claim synchronization. Accept or
Reject queues only the draft decision to the exact live PID/instance so the
conversation can continue. **Handoff** remains separate and is blocked while an
MO proposal awaits review.

After an exact Board is linked, a drawing follow-up exposes only `mo_design` and
resolves its target from that binding. `board_read` supplies the complete
operation contract, so providers do not inspect product source to discover
element fields or limits. A request that explicitly depends on the current
visible terminal/screen/interface may also receive one bounded
`computer_observe`; ordinary diagrams do not capture the screen.
The visible keyboard reference documents V/P/E, L/A/R/O/T, undo/redo,
Delete/Backspace, Escape, and viewport reset. A bounded spatial read supplies
occupied geometry and clear placements to MO; a new proposal that materially
covers existing work is rejected unless it explicitly declares intentional
annotation overlap.

## Handoff routing

Handoff routes work originating in Studio. An explicit instruction to implement
an identified design in its existing Terminal conversation needs no return trip
through this sheet; that Terminal verifies the exact revision and target before
acting. Board approval and connectivity alone remain insufficient authority.

- **Current terminal** revalidates the exact terminal that opened the Design, or
  an existing safe unambiguous terminal when no origin was recorded. Studio queues
  one PID-bound normal input turn for that exact TUI; it never prefixes `/goal`,
  guesses another recorded origin, or sends work to the Desktop conversation.
- **Background** launches the canonical GoalRunner headlessly in the selected
  project. It alone sends a completion notice through the existing cube-first,
  tray/platform-fallback owner. The notice action focuses the exact saved design.
- **New terminal** launches one visible interactive MO terminal in the selected
  project and injects the handoff as `/goal`. Its work remains visible, so it
  does not produce a background-completion notice.

The separate renderer authenticates requests to the resident Desktop host with
a process-local HMAC secret and short-lived command spool. The host brokers the
Welcome and artifact windows, final routes, and notification fallback; it is not
an agent target. The same secret protects renderer-PID-addressed, one-shot focus
requests. Opening an already tracked artifact in the same presentation reuses
its window; another window cannot consume that focus request. Current and Background require the resident
host; New terminal remains available when a target folder is selected.

## Rendering and security

The trusted Studio chrome can call only the narrow `StudioBridge` API. Generated
HTML/CSS/script is isolated in an iframe with `sandbox="allow-scripts"` but no
same-origin capability. Its CSP sets `default-src 'none'`, denies connections,
forms, and base navigation, and permits only inline styles plus data/blob media.
Generated markup is sanitized to remove scripts, nested browsing/plugin elements,
event-handler attributes, `srcdoc`, and JavaScript URLs. The explicit `script`
field is evaluated only when `runtime.allow_scripts` is true.

Board is trusted Studio chrome outside the generated iframe. Its declarative
scene is validated before every private save and artifact checkpoint; generated
Preview content cannot mutate it. Board controls, draft review, and the native
title reuse the active MO skin. While Board is selected, the native title
provides the fail-closed foreground signal used by phone pen injection:
`MO Design Board — …` inside full Studio and `MO Board — …` in the standalone
presentation.

Successful clicks are not logged. On a script error, only the capped error text
and semantic control descriptor cross into the trusted bridge; field values do
not. Diagnostic evidence is explicitly treated as data, not prompt instructions,
and one pending Design request prevents an error loop from multiplying workers.

The Studio consumes the shared MO skin tokens, Desktop panel geometry, and
character cube preferences. The single enlarged activity line is below the preview;
its cube is the loading and active-work signature, stays still when idle,
stops while hidden, honors reduced motion, and has no parallel
palette or character setting. Starter previews receive those same shared
tokens as CSS variables. The skin-aware mark is supplied to the native title bar so
the window never inherits the Python executable icon. The tray re-focuses Welcome;
it never redirects or closes an artifact window.

## Verification

Idle polling reuses the cached payload and theme projection. Board terminal
presence checks return only changed connection metadata, without resending the
Board or rebuilding chat; terminal-linked Preview uses the same bounded check.
Welcome checks only shared visual settings while idle, loads library metadata
on demand, and never starts a Preview iframe. Its short staggered introduction
plays once, respects reduced motion, and adds no animation timer or worker.
Preview is unloaded while Board is selected and is
never started in standalone Board. When the WebView reports a hidden document,
Preview unloads, polling backs off, and chrome animation pauses; returning
restarts the prototype. An active editor retains its frozen screen and unfinished
fields; its script and CSS animations stay paused. Shared theme changes repaint
chrome immediately and reach that frozen Preview when editing ends.
The native host's visibility reporting determines when
that hidden-document policy applies.

Board paints only visible geometry, decodes images sequentially into scaled
canvas previews, and drops obsolete image caches. The preview pixel allowance
scales with the Board's image count; full source images still need a transient
browser decode and remain intact for Download. Undo history is bounded by count
and serialized size. These bounds are not a fixed process-memory guarantee.

Focused acceptance covers schema, persistence, provider delta streaming, the
design-only workspace lane, optional verified project mapping, CSP/sandbox rules, shared
theme/character reuse, tray integration, authenticated routing, exact terminal
normal-turn pickup, visible startup-goal delivery, headless GoalRunner completion,
and cube-to-tray notification
fallback. It also proves that a completed Design request without a newer changed
visual clears into attention, that arbitrary metadata-only revisions cannot
complete chat while an explicit changed brief can,
that first-turn failure does not claim a completed preview and later failure
distinguishes an unaccepted partial revision from the last accepted one, that each
Design dispatch reuses the saved Terminal model through the shared provider owner
and exposes only failure-triggered fallbacks, that bounded opaque attachment IDs
survive picker/import/session/broker
routing without exposing source paths, that advice can complete
without creating a revision, that real worker activity reaches the bottom line,
that only Background
notifies, and that an
actionable notice focuses the exact design. The broker and TUI reuse bounded
existing loops rather than creating a watcher per design.
Board-focused acceptance additionally covers v1-to-v2 compatibility, geometry
and size bounds, immediate working saves, atomic checkpoints, stale operation
conflicts, MO draft evidence and review, draft-blocked Handoff, linked Board
targeting, decision-only terminal notification, one-Board-per-terminal
binding/release, private launch-token
transport, Board-only chrome, active-skin markup, and both full-Studio and
standalone Board-stage phone coordinate mapping.
Native acceptance launches a real
pywebview window, waits for its ready marker, updates the same document while
the process remains open, and verifies that the visible revision and preview
change without a renderer restart. Windows movement acceptance keeps the shared
outside effect enabled while repeatedly moving the real window and requires the
renderer to remain responsive without a native crash; effect HWND creation,
movement, resizing, and teardown stay on WinForms' persistent UI thread rather
than pywebview's short-lived event workers. Interaction acceptance clicks a
deliberately failing control, verifies the visible pending-diagnosis state and
authenticated Studio repair, then repeats the click to prove per-revision
deduplication.
