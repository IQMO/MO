# `.modesign` artifact format

MO Design stores each visual concept privately as a declarative `design/v2` YAML
document with the `.modesign` suffix. Downloads with Board images use a bounded
ZIP bundle under the same suffix. The suffix is intentionally not `.mo`: it cannot be
confused with MO's `~/.mo` private profile and runtime directory.
Existing `design/v1` artifacts remain readable as Board-less documents; every
new artifact and every artifact that first saves Board state uses `design/v2`.

## What standalone means

A downloaded `.modesign` carries HTML, CSS, optional bounded script, manual edits,
window preferences, visual brief, shared Board, and source-map hints needed to
reopen the concept independently in MO Design:

```powershell
python -m mo_desktop.mo_renderer path/to/concept.modesign
```

It is self-contained as a design artifact, not an installed application or an
MO profile file. It does
not contain the Studio conversation, credentials, private profile prose, Python
code, shell commands, dependencies, or a packaged native app.

Downloads without Board images remain ordinary YAML. Image-bearing downloads
contain `design.modesign`, `images.json` (opaque attachment ID to member name),
and the referenced image bytes under `images/`. `archive.py` owns this transport:
the document is bounded to 2,000,000 bytes, each image to the existing attachment
limit, and the bundle to 160 MiB uncompressed. Duplicate, missing, unexpected,
encrypted, or unsafe member names are rejected. Images are read as bytes, never
extracted through archive paths. Download fails if a referenced image is missing.
Private working YAML references the existing attachment catalog; copying that
internal file alone does not package its images. Use Download to move it between
profiles. Opening an external file imports a fresh editable private Design and
its images without overwriting an existing Design with the same ID. Conversation,
terminal bindings, and other private sidecars are excluded.

Visual preferences are profile-owned and advisory, not portable artifact state.
MO Design may consult approved operator preferences for an explicit refinement or
new concept, but the current request and verified project evidence always win.
Current-state reconstruction ignores those preferences, and profile prose is
never copied into the artifact. Legacy `dna` input remains readable only for
migration and is discarded on the next write.

## Document shape

```yaml
mo: design/v2
meta:
  id: account-overview-a1b2c3d4
  title: Account overview
  summary: A compact account workspace.
  revision: 3
  created_at: '2026-08-07T18:00:00Z'
  updated_at: '2026-08-07T18:04:00Z'
window:
  width: 1200
  height: 760
  min_width: 920
  min_height: 620
  resizable: true
runtime:
  allow_scripts: false
design:
  html: '<main class="account">...</main>'
  css: '.account { min-height: 100vh; }'
  script: ''
handoff:
  objective: Implement the approved account overview.
  acceptance: []
  decisions: []
  constraints: []
  project_root: ''
  context_query: ''
  files: []
  symbols: []
board:
  version: board/v1
  revision: 0
  elements: []
  decisions: []
  questions: []
```

`core.design.schema` rejects unknown keys, invalid identifiers, wrong types,
oversized fields, excessive list items, and `design/v2` documents above 2 MiB.
Legacy `design/v1` retains its original 512 KiB limit.
`core.design.service` owns atomic creation and revision updates. The renderer
never treats artifact content as executable Python or as implementation
authority.

`meta.revision` is the saved visual generation: `r6` means the sixth committed
state of that design, not an application release. Studio keeps up to 40 private
snapshots in the design folder. Clicking the revision badge opens that history;
selecting `r3` previews that exact snapshot without writing the artifact or
creating `r7`. The selected version drives Source, Download, Handoff, and the
visual source for the next Design request. A completed refinement still writes
only the normal next revision from the latest head. The current revision is
protected; the user may explicitly delete a confirmed older snapshot without
affecting the active artifact or any other saved version. Designs created before
snapshot history was introduced can select only revisions saved afterward.

## Manual Preview edits

Optional `design.edits` stores at most 512 declarative element overrides alongside
the original HTML/CSS/script. Each entry has a `selector`, lowercase HTML `tag`,
`identity`, a bounded `style` mapping, and optional leaf `text`. `edits.py` owns
the property allowlist and limits; script, event handlers, arbitrary attributes,
and URL-valued styles cannot be introduced through this field.

Studio captures the visible screen for editing, pauses its script, and records
changes without replacing the prototype source. On playback, matching elements
receive the overrides, including after script-driven DOM replacement. Stable
unique `data-mo-id` or `id` attributes are preferred. Structural selectors also
require an identity match; absent or mismatched elements are reported rather
than edited speculatively. The same component identity must not denote unrelated
elements across screens. This is a DOM element editor; canvas pixels and embedded
application internals are not separately editable components.

Manual saves check the exact current revision under the existing writer lock,
create ordinary historical snapshots, and preserve the Board and brief. MO reads
these edits in the same artifact. Updates retain omitted edits; when incorporating
their intent into source, MO explicitly supplies the remaining list, or `[]`.
The receiving implementation uses the exact approved revision, including edits.

## Shared Board

`board/v1` is the portable, declarative spatial layer inside `design/v2`.
It supports bounded strokes, lines, arrows, rectangles, ellipses, text, and
opaque-ID image blocks with semantic MO skin color roles. Selection exposes a
resize handle; arrows retain endpoint and bend handles. Double-click opens the
shared properties dialog, including existing-text editing and arrow controls.
These edits remain revisioned and undoable across the editor's own autosaves.
Geometry stays inside a 16,384-unit
logical canvas; element, point, operation, and text totals are capped. `actor`
records the last user or MO editor, and element revisions make stale update or
delete operations fail instead of overwriting newer work.

An explicit request to open, launch, run, or show the drawing Board is a
boardless work turn on every agent surface. That turn exposes only the existing
`mo_design` schema, and completion requires a successful `open` or `show` event
with `view: board`; prose, another drawing application, a shortcut, or a
terminal handoff is not launch evidence. Questions about how Board works and
negated launch instructions remain non-actuating conversation.

User gestures save immediately to the private
`<id>.board-working.json` sidecar and checkpoint as one normal artifact
revision after a settled drawing burst. MO can only write a validated proposal
layer through `mo_design board_propose`. `board_read` includes deterministic
content bounds, bounded occupied geometry, and clear placement candidates;
new MO elements that materially overlap existing content are rejected unless
the call explicitly declares an intentional annotation/overlay. A draft remains visually distinct until the
user chooses **Accept** or **Reject**. A pending proposal blocks further drawing
and Handoff. Accepted Board decisions and unresolved questions are saved
portably with the artifact.

## Session and storage boundary

The default private layout is `media/designs/<id>/<id>.modesign` under the
resolved MO state home. A sibling `session.json` stores bounded conversation,
pending request kind, current-head completion baseline, exact selected source
revision, completion revision, and attention state. `mo_design read` and the
next complete update inherit that selected source while the request is pending,
so refining history cannot silently fall back to the latest head. The sidecar is
private and non-portable; it is not part of the artifact contract.

The Board working sidecar is also private and non-portable. It carries immediate
ink, an optional MO proposal, and—only when opened from a terminal—the exact
PID-bound terminal origin used for linked Board targeting, decision-only draft
notification, and Current-terminal Handoff. A separate `board-link/v1` runtime
row reserves at most one standalone Board renderer for that exact terminal and
is removed by the same private launch token when the renderer exits. Neither
runtime binding enters the artifact.

The Studio **+** action starts a clean design and conversation while preserving
the current optional read-only project binding. The switcher moves
freely among those isolated design folders and their matching sessions.
The starter title identifies the visual but does not masquerade as an
implementation objective; without a real summary or Design update, the brief is
empty and Studio keeps it as one collapsed summary row.

Each design has one artifact folder and optional read-only project binding.
Switching designs swaps both the artifact and its matching session inside that
Studio window. The Desktop tray opens Welcome independently of active artifact
windows; [Studio's runtime guide](../../mo_desktop/design_studio/README.md) owns
the launch and terminal-linked presentation contract. Until Handoff, the selected project is evidence only and
the `mo-design` sandbox permits durable writes only through validated
`mo_design` actions to the same artifact.

## Design conversation contract

Ordinary Design chat keeps the user's full message intact and sends one locked
visual-work contract to the selected model. It does not classify the turn from
keywords before the model reads it. Loading, mapping, comparing, or reproducing
an existing surface is observation-first; an explicit change in the current
request authorizes only that change. Future or deferred wording such as “later I
will refine it” does not authorize a refinement now. Without current pixels, the
result must say `Source reconstruction — live visual not observed`. When the
reference surface is a web page, MO Design may observe the requested exact Chrome tab
through the canonical computer tools, connecting automatically. That is
read-only Design evidence: the Design lane does not receive browser actuation,
and its project/artifact boundaries remain unchanged.

The direct Terminal `mo_design open` path persists the preview's origin intent for
its first complete revision. When a read-only project is selected and no explicit
intent is supplied, it fails closed to `current_state`; only an operator-requested
change may select `refinement` or `new_concept`. Updates retain omitted visual
and brief fields from the selected source revision through the existing save
owner. The adapter does not reject content based on evidence labels, acceptance
lists, or source-map availability. MO verifies references and states evidence
limits through the conversation; brief metadata never grants project access.

A request for a new app, product, or showcase creates a Design prototype, not an
implemented or packaged application. Unless the user explicitly asks for a static
concept, the prototype enables its sandboxed script and demonstrates the primary
screens/states and controls needed to judge the flow. The same atomic update
records a meaningful objective, acceptance outcomes, visual decisions, and
evidence limits. Screen/flow/behavior descriptions may be recorded independently;
project files and symbols are recorded only after a project is selected and the
mapping is verified against current source.

Automatic **Repair** remains a separate deterministic request because it carries
trusted runtime evidence for one reproducible artifact-script failure. It repairs
the same design without creating implementation authority.

An explicit drawing, diagram, or annotation request opens Board alone in a
pin-or-close renderer rather than inventing HTML/CSS or opening the full Design
conversation. This Board-only private-artifact route remains available from
Terminal clarification/review lanes. MO Desktop exposes the same tool: it binds
the selected live terminal when one is proven and otherwise opens a saved
unbound Board without terminal linkage. A linked Board resolves
`board_read` and `board_propose` through that exact terminal binding, so the
provider never guesses another artifact id; `board_read` returns the validator's
bounded operation contract with spatial placement evidence. MO proposes only
those declarative operations, and the broker accepts a result only when the
matching private draft actually exists. A runtime-only badge identifies the
exact live origin. The badge is status only: on the user's next drawing follow-up
in that terminal, MO reads the current Board through `board_read`. Board chrome
does not push a second content summary or start another terminal turn. Accept or
Reject queues only the draft decision so the waiting conversation can continue;
it is not `/goal` and does not authorize project work.

Trusted Board chrome exposes its keyboard map directly: V/P/E select the main
pointer tools, L/A/R/O/T select shapes or text, Ctrl/Cmd+Z undoes and
Ctrl/Cmd+Y or Ctrl/Cmd+Shift+Z redoes history. Delete removes the selected element,
Escape cancels, and 0 resets the
view. These shortcuts never run while focus is in a text control or dialog.

After each update, the same Design worker binds one exact Studio target and
performs one bounded visual QA observation on the rendered preview through MO's
existing computer observer. On Windows that exact non-minimized Studio target
can render while covered without being activated; unsupported rendering is
reported as unavailable rather than changing focus or capturing a broader
display. A
text-only home model delegates only pixel reading to the bounded vision observer
and keeps ownership of the turn. If the preview cannot be observed, MO must say
so rather than claim visual verification. No second Design worker or continuous
screen watcher is created. A material finding allows at most one complete
correction and one re-observation, never an open-ended capture/update loop.

Provider completion and a revision number are not enough: the resident broker
accepts visual completion only after the worker finishes, the same artifact has
a newer committed visual signature, and the worker callback carries actual
`computer_targets` plus an observation-producing `computer_observe` event.
Activity text mentioning those tools is not evidence. If the preview is closed
or otherwise unavailable, the worker may state `Visual QA: unavailable:
<reason>`; the saved artifact remains reopenable and the limitation is shown in
the result. An explicitly requested brief-only turn
may complete without changing that signature only when real Handoff fields change
and the worker returns the `Design brief updated` result contract; arbitrary
metadata remains insufficient. `mo_design update` accepts only changed fields;
the save owner merges them with the selected revision and atomically saves the
complete artifact. A bounded post-commit visual QA finding may produce one
further correction. Partial arguments for an update stream through
the bounded live-preview owner, but the shell keeps the last finished visual on
screen until the new revision commits. This avoids displaying malformed
token-incomplete markup. Refresh reloads that safe preview even while MO is
working. `mo_design action=read` returns a bounded source chunk with an offset
and next-offset marker; continue from that marker when the complete HTML/CSS is
needed, rather than flooding the turn context with the whole artifact. An explicit
`revision` reads that exact saved source and must remain on every chunk; the
continuation includes it so a newer head cannot replace an in-progress read.
Terminal Handoff supplies this canonical read action for its selected revision,
without requiring direct filesystem access to private snapshots. With no explicit
revision, read retains the pending Studio selection or current head behavior.
After 15 minutes without accepted completion evidence, Studio clears the pending
state and offers retry or a new design. A failed worker may leave a committed
partial revision, but Studio marks it unaccepted and never calls it finished. It
retains the last accepted preview when one exists; a first-turn failure states
plainly that no completed preview exists.

Each user Design message may include up to eight files of at most 20 MiB each,
including an attachment-only message. The native picker imports them into the
canonical private attachment catalog and sends only opaque IDs through the
Studio command/session boundary; the original source path never reaches Studio
JavaScript. The broker resolves the exact saved catalog files for that turn, and
the Design prompt labels their contents as untrusted evidence to inspect—not
instructions to execute or follow.

MO Design has no independent model control or last-used model state. Before each
worker starts, the resident broker reads Terminal's single saved `/model`
preference and revalidates it through the shared catalog activation owner; when
no preference exists, the Agent's configured baseline remains active. Credentials
and model choices never enter the renderer channel, `.modesign` artifact, or
conversation sidecar. Configured fallback is reactive to a real call failure and
never replaces the saved primary for the next request.

## Handoff boundary

The artifact never edits or builds its selected project. Ordinary **Send** keeps
the user and MO in the same iterative visual conversation. Every project-bound
visual update carries an explicit `current_state`, `refinement`, or `new_concept`
intent; omission fails closed, so later current-surface loads cannot bypass the
evidence-preserving baseline checks. **Handoff** stays disabled while work is
pending or attention is unresolved. A saved revision from Terminal or history
does not require a Studio-worker completion marker: Handoff is the user's explicit
transfer decision, not proof of visual QA. Its compact sheet recaps the saved title,
summary, revision, visual type, optional brief, and selected project context,
then asks only which MO recipient receives that accepted work. The action creates an
implementation handoff; it is not another Design refinement or a fresh concept-
intake form. Current terminal returns it as a normal interactive turn to the
exact originating live MO when that binding is still valid, without entering
`/goal`. Background requires a selected safe project and uses MO's normal goal
completion notification, and New terminal opens a visible goal. Without a
selected project, an interactive receiving MO must resolve the target before
writing rather than treating its startup directory as authority. The receiver
implements the exact accepted revision, verifies behavior and visual alignment,
and only then calls the existing completion action for that revision. Studio
shows **Resolved · rN** whenever that exact revision is selected. Reopening
reactivates the conversation without erasing that resolved-revision record; a
newer revision remains unmarked until separately verified and completed. MO
Desktop hosts the tray, Studio, broker, and notification fallback; it is not an
implementation-agent destination.
Any unresolved MO Board proposal must be accepted or rejected first. In Studio,
the separate Handoff action transfers work to an implementation recipient.
An ordinary Terminal conversation already discussing that exact design can
proceed on the user's explicit implementation instruction without another
Studio transfer or approval. Terminal reads the accepted revision and brief,
resolves the intended project from that conversation, implements and verifies,
then marks that revision complete. A connection badge or Board draft decision
alone does not authorize project changes. Studio workers remain read-only to
the project; this does not relax their sandbox or visual-completion evidence.

Runtime, security, theming, streaming, and focused verification details live in
the [MO Design Studio guide](../../mo_desktop/design_studio/README.md).
