# MO Design Studio

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
[`core/design/README.md`](../../core/design/README.md).

## Artifact boundary

The [artifact guide](../../core/design/README.md) documents `design/v2` fields,
Board validation, portability and storage. Studio uses that same file format.

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

## Conversation, progress and recovery

Open a design from the launcher or ask MO to create one. A Terminal-opened
Preview keeps conversation in that Terminal and hides Studio's conversation,
project, brief, Handoff and library controls. Manual editing, Board, history,
Download and activity details remain available. Reopening a saved design from
Welcome restores standalone Studio; the saved file does not lock it to Terminal
presentation. An explicit Board request can instead open the Board-only view.

While MO works, Studio keeps the last finished visual visible. Refresh reloads
that finished visual, or the current saved revision when idle. Partial incoming
markup is never presented as a finished update. The bottom activity line reports
the current work; **Details** expands it. A read or metadata-only change is not
reported as a visual update. Advice and explanations can finish without changing
the design; a requested brief-only change is reported as **Brief updated**.

**Stop** cancels only the current request. Studio waits while an active operation
unwinds, preserves saved artifacts and manual edits, and can stop queued work
before it starts. A request older than 15 minutes requests cancellation and
shows attention. A disconnected standalone viewer can release only its own
waiting display. Failed work may leave a saved partial revision, explicitly
unaccepted: Studio retains the last accepted preview when available and says
when a first-turn failure has no completed preview to retain.

Your full request stays in the visual conversation. A question may receive an
answer, options or clarification without a revision. A current-surface request
uses observed pixels and verified source, changes only what you ask to change
now, and labels an unobserved reconstruction honestly. New app or product
requests default to interactive prototypes unless you ask for a static concept.
Primary controls and relevant loading, empty, success and failure states belong
to that prototype, including keyboard, responsive and reduced-motion behavior.
The same saved revision carries the objective, acceptance outcomes, decisions
and evidence limits. It remains a prototype, not an implemented application.

After an update, MO performs a bounded visual check of the exact Studio preview
when observation is available, with at most one correction and re-observation.
A supported Windows window can be observed while covered without activation;
unsupported background observation is reported unavailable. Activity text alone
does not establish visual verification.

You may attach up to eight non-empty files, each at most 20 MiB, using the native
picker; an attachment-only message is allowed. Files are copied to the private
attachment catalog when sent. The Studio page receives safe names and metadata,
not original source paths. Attachment content is evidence, not instructions.

Studio follows Terminal's saved `/model` choice for each new request, or the
configured model when none is saved. It has no separate model picker or saved
model setting. Provider fallback applies only after a real request fails and
does not replace the saved choice for the next request.

Ordinary **Send** can change the same private Design document, but never grants
permission to change a selected project. The activity line distinguishes an
answer, static concept, interactive prototype, unavailable visual QA, updated
brief and attention state. Tool and startup failures remain visible. Board
proposals appear as distinct drafts; only you can Accept or Reject them.

If an enabled preview script fails, Studio records bounded error text and the
last control's description, never form-field values. Desktop-owned Studio can
request one design-only repair for that revision. Repeating the same failure
does not multiply repair requests. Terminal-linked Preview leaves repair with
its Terminal; a standalone viewer without Desktop records the issue locally
and does not silently open a Terminal.

**Handoff** is available when no request, attention item or Board proposal remains
unresolved. Its sheet recaps the selected saved revision and asks only which MO
recipient should implement it. A historical revision or Terminal-created design
does not need a Studio completion marker, and Handoff does not attest visual QA.
Verified implementation marks that exact revision **Resolved · rN** and closes
its window. Reopening resumes the conversation and retains that marker; a newer
revision remains unmarked until separately implemented and verified. See the
[artifact handoff contract](../../core/design/README.md#handoff-boundary).

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
It opens this same artifact in a Board-only presentation: no
conversation rail, Preview switcher, library/new controls, minimize, or Handoff.
It retains skin-native pin, close, resize, Board tools, Download, autosave, and
revision checkpointing. Each exact Terminal can own one live standalone Board.
Showing that design focuses it; another design waits until the first closes.
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
user's next drawing follow-up in that terminal, MO reads the current Board.
The badge does not claim automatic synchronization or send a separate content
message. Accept or Reject sends only the draft decision back to that exact live
Terminal so the conversation can continue. **Handoff** remains separate and is blocked while an
MO proposal awaits review.

Drawing follow-ups use that exact linked Board. Ordinary diagrams do not
capture the screen; a question about the visible interface may need observation.
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

Opening an already-open design in the same presentation focuses its window.
Current terminal and Background require the resident Desktop host. New terminal
remains available when a target folder is selected. Desktop hosts these routes
but is not itself an implementation destination.

## Rendering and security

Generated HTML/CSS/script is isolated in an iframe with `sandbox="allow-scripts"` but no
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

## Visibility and resource use

Idle checks keep appearance and Terminal connection status current without
resending Board content or rebuilding chat. Welcome loads the saved-design list
on demand and does not start a Preview. Its short introduction plays once and
respects reduced motion.
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
