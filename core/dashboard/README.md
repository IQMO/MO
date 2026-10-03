# MO Dashboard

`core.dashboard` is MO's canonical bounded dashboard synthesis and delegated-control
contract. It is not a task, memory, profile, learning, graph, presence, file,
integration, or runtime database. Those owners remain authoritative: the
Dashboard reads bounded summaries and emits presentation plus allowlisted action
descriptors that adapters route back to the existing owners.

Local Communication status follows the same projection: Gmail shows its sync
state and unread count, and Outlook.com identifies its Connected Tab route. The
connected Email tab reads up to ten messages on demand, searches through the
existing account owner, and opens selected content in a local reader. Workspace
keeps its full structural map. Gmail's Inbox total is labeled as an API estimate;
Outlook has no browser-independent total or unread count. Sender, subject, and
body text stay in the authenticated local view and are absent from snapshots,
projections, exports, logs, and saved conversations. An explicit Agent mail
request sends its retrieved result to the configured model for the live reply.
The compact Desktop Home
shows up to two recent Gmail subjects beside current work; You shows Gmail
unread status. Its Outlook and Gmail links submit an inbox request to Agent chat.
Everywhere and Android retain Gmail status only. See `core/mail/README.md`.

Life presents two distinct local records: confirmed commitments from
`core.life.items`, and operator-recorded income/outgoings from
`core.life.money`. The Money section selects one month, displays exact totals
separately by currency and outgoing category, and edits entries through the
authenticated Life route. Life's top overview reuses that same read for a
six-month income/outgoing timeline, grouped by currency; an empty record shows
an unvalued zero baseline. It does not infer entries from mail, connect to a
bank, convert currencies, or claim an account balance. Neither record is a
Dashboard-owned store; profile learning and reminders keep their own owners.

## Owners

- `snapshot.py` collects and redacts a bounded source snapshot.
- `projection.py` converts a supplied snapshot into
  `mo-dashboard-projection-v1`. It caps metrics, sections, items, and actions,
  carries the MO Cube brand, and exposes only allowlisted destinations.
- `registry.py` and `providers.py` own the richer generated-dashboard section
  registry.
- `render.py` owns text/export assembly; `dashboard.html`, `dashboard.css` and
  `dashboard.js` separate markup, shared-skin styling and local interactions.
  Connected and read-only HTML use the same skin-aware presentation, with richer
  registry and graph-quality evidence collapsed under System.
- `server.py` is the loopback adapter attached to one existing terminal or Desktop Agent.
  It delegates controls to terminals, rule reads/writes to MO Files, knowledge
  queries to `core.knowledge`, and maps to the native structural graph renderer.
  It creates no second Agent, provider configuration, or business state store.
  Desktop retains its established Gateway-turn, command, Files and destination routes.

## Open and use

Run `mo --dashboard`, or `/dashboard show` in an existing MO terminal. Desktop's
cube launcher **Dashboard** opens or focuses one native window on the resident Agent with
its current project and configuration, without launching a terminal or a second
Agent. The cube-attached compact panel remains a separate gesture. The connected
workspace opens in MO's existing optional native WebView renderer, with frameless
window controls and the normal MO configuration. Its shared native adapter keeps
Windows' system backdrop disabled so the configured rounded corners remain clear.
Stopping its owning MO instance closes it; reopen through
MO rather than bookmarking the capability URL. `/dashboard` remains the bounded
terminal summary; `/dashboard html` writes a **read-only** export, and
`/dashboard map` opens the structural map. An export is not a connected controller.

The main views are Workspace, Email, Life, Learning and System. Project selection
stays in the top-right header on every view; rules, knowledge, work records,
Checks & LSP, and Other commands live within Workspace. The command list
retains registered terminal destinations without duplicate alias entries. It contains neither a terminal emulator nor
voice controls. Open terminal focuses the existing normal MO terminal;
Stop uses the selected terminal's existing cancellation owner. Selected terminals
are revalidated against their instance/PID and project, including at receipt.
Cross-terminal controls use the existing exact-terminal handoff with a distinct
typed control; ordinary Design context still remains a normal conversation turn.
Command menus open there; `/now`, `/status`, `/knowledge` and allowlisted detailed
navigation run through the existing command owner. Other bare commands and natural
management requests are prepared as drafts, preserving existing text and requiring
Enter in that terminal. A different project opens its existing live terminal,
offers an explicit choice when several match, or launches normal MO in that folder.
After an explicit control launches a terminal, the Dashboard waits for that exact
instance's heartbeat before delivering the requested control once. Opening the
Dashboard itself never launches a terminal. Idle terminals need no saved conversation
to be found. Historical boards are not live bindings. Open task titles and actual
blockers remain visible; prior-session resumable work is labeled as not running.

Workspace keeps the live terminal, current work and recent taskboards at the top before the structural map. A compact **Needs attention** row reuses existing sources: blocked-task counts only when the current-folder host terminal is selected or is its sole live terminal, pending learning-review counts, and non-clear provider/interrupted-work/SystemCare notices. Unknown counts are omitted, and blocked counts are not carried over to another project or an unselected remote terminal. The review and notice shortcuts open their existing Learning and System views. Major section dividers use a modest skin-derived tint rather than a hardcoded color.
Email uses the same active skin with Gmail/Outlook icon account buttons in the
single compact control row, a ten-row list,
search, local reader, and a session-only last-action line. Gmail exposes its
unread count, estimated Inbox/search count, and encrypted notice on/off setting.
When an account is not ready, Email shows its setup controls on demand. Gmail
can be enabled through the existing private runtime preferences, accepts an
operator-owned Desktop OAuth client into the canonical private credential file,
and starts browser consent and the first sync. Its setup view links Google's
project/client steps and keeps the credential fields private to Dashboard.
Agent chat's explicit `mail action=connect` delegates to the same preference,
Gmail, and Connected Tab owners and shows the remaining sign-in step locally.
Outlook can prepare the existing
native bridge and shows the Chrome extension folder and Outlook sign-in link.
Chrome still requires the operator to load the current unpacked extension; this
is not a published one-click connector. No setup poller or second credential
store runs in the background.
Outlook opens its Mail URL through MO's existing desktop-open action if no tab
is present, then queries the one signed-in MO Connected Tab; no background
count is inferred. A hidden Outlook tab can leave its list unrendered; the
Dashboard reports that failure and does not treat zero rendered rows as an
empty mailbox. Open/read, archive, and move call the existing mail owner
directly. Move shows a skin-styled dropdown of existing Gmail user labels or
visible Outlook folders from those owners; it does not create a destination.
The Outlook row digest is rechecked before a numbered browser action,
so a changed visible list fails closed. Send and delete enter only this
Dashboard host's live MO conversation for the established later-turn approval;
delete asks the operator to choose the exact message again in that conversation,
because the Dashboard does not pass a fetched message ID to the model.
These requests never select an idle terminal. The Dashboard passes no fetched message
content in these shortcut requests; Agent mail reads use the mail tool. No
local mail index or action-history database is added.
Email also shows literal-wording groups for its currently visible ten rows,
including case/paperwork wording from Gmail subjects or short snippets and
Outlook previews. These local hints do not summarize message bodies or assign
importance. Gmail's own Important, Unread, and Spam labels appear beside its
sender when present. Gmail rows can show a confirmed Life link by durable message ID; Outlook cannot
make that claim from its current browser list. The Life view delegates private
confirmed commitments to `core.life.items`: title, category, optional date
and notes, open/done status, and revisioned edits. Payment/subscription plans
can include confirmed amount, currency, interval, and installment count. A
case can hold a custom area, reference, and dated operator-confirmed
conversation, paperwork, step, or note updates in the same Life record.
Case history is collapsed until opened; Resolve is an explicit user action.
The compact Email controls sit above the inbox and reader, and the Life
overview and Money scope note leave more room for the actual records.
A recorded outgoing can link to a payment plan, with progress derived from existing
money entries. Neither field group acts as a payment, reminder, or automatic
due-date advance. Life also shows possible
groups from the existing bounded Email glance when opened, with a route to
the matching Email filter. These wording hints are not saved commitments.
Existing scheduler jobs appear separately with name, type, enabled state, next
run and last status; their prompts remain with the scheduler. Life can create a
plain reminder or an Agent task through that owner, queue a run, pause, resume,
and remove it. A plain reminder uses the existing Desktop notice source and no
model; an Agent task uses the configured model. Resident Desktop starts the
existing scheduler when enabled, sharing its singleton lock with other MO runtimes.
A commitment date alone does
not create a schedule. Profile facts and work
taskboards keep their own owners. Snapshot and compact Dashboard receive
confirmed-item counts only; the connected view retrieves details through its
authenticated local route. See
`core/life/README.md` for source and privacy behavior.
When confirmed items are open, Desktop's existing compact Personal projection
shows only their open/overdue/due-soon counts; it reads the same snapshot and
does not open a new store or poller.
Recorded activity lives in Inspect work alongside the retained taskboard records,
with larger calendar cells and responsive totals, not an always-open home strip.
It reads provider-usage receipts through
`SessionManager.usage_activity()`, deduplicating saved aliases. It covers retained
local terminal/Desktop sessions over the displayed date window, not lifetime usage,
session duration or active work time. Snapshot limits and missing coverage are
reported explicitly; unavailable usage is not presented as a measured zero. The
numeric projection caches unchanged receipts and uses the existing refresh loop.
Usage is read only while Inspect work is open, at most once per minute. Returning
to that view reuses the current observation; ordinary Workspace polling does not
rescan session receipts. The calendar aligns weekdays, shows its exact retained
date window, and exposes daily receipt totals. It is keyboard navigable without
making every day a Tab stop.
Empty history occupies no row; Inspect work reveals taskboard records and their
session/turn identities instead of sending a generic work-management prompt.
Task completion still belongs to the runtime evidence gates, not editable checkboxes.

Projects come from curated declarations plus the actual current folder, not recent
launch history. Rules exposes applicable `AGENTS.md` sources, parent before child,
with a project-relative scope. Authorized edits use Files' revision-checked write
and reread; conflicts preserve the draft. Late rule/knowledge responses cannot
rebind to a newly selected project, and typing during a save keeps the newer draft.
Inherited sources outside authorized
locations remain read-only. No replacement rule catalog is created.

Project knowledge reads the automatically maintained `/knowledge` manifest and
freshness-gated query owner. It is source-linked orientation, **not generated Wiki
pages or proof of behavior**. Dashboard status and search remain read-only; the
existing project lifecycle refreshes changed sources without a manual action.
Learning separates saved profile counts, the revision-checked review queue, and the existing skill inventory. Workspace moves the pending-review count into **Needs attention**. Approve, Dismiss and Undo still call the same review owner; changed items cannot use an old approval, and suggestion text cannot be rewritten in place. **Edit saved notes in MO** routes to the existing `/profile facts` owner for add, update and confirmed forget; profile prose stays with its existing owner.

Skills & conventions searches the current profile plus enabled project-local
roots without seeding, mining or retirement. Project-bound packs from other
projects are excluded; older unbound conventions are explicitly labeled as
cross-project. Inspect source reads current guidance, activation, provenance and
recorded outcomes directly, not a terminal placeholder; the Dashboard has no
skill-pack body write path. Inventory is read on opening Learning, changing
project, reviewing an item or explicit Find / reload, not by every status poll.
Full bodies are fetched only on inspection; lists are bounded with their total
and a search control. Late reads cannot cross a project switch. Project rules
remain editable AGENTS.md sources through the revision-checked Files owner. No
project rule file is created automatically.

Checks & LSP reads the selected project's policy, not the hosting project's toggle.
On, Off and Default are edited in native Settings → Projects & checks through
`LspManager.set_project_selection()` and the normal private preference overlay.
Dashboard keeps read-only status and evidence; its former LSP save route is removed.
No extra provider, configuration file or language server is installed. Settings
stops affected clients in its host on a change; other MO processes observe the
policy on their next LSP use.
Recorded checks are scoped to the chosen live conversation; no host conversation
substitutes for an unbound project. Checks refresh with the existing Dashboard poll
while visible. `/dashboard checks` and this view share recorded turn identity and
time, supplied and omitted context sources, memory entries delivered, tool outcomes
and verification checks. Available tools and a current index do not prove use;
supplied context does not prove the model acted on it. Missing sampled evidence is
labeled as missing. Reload sources also requests fresh graph/check evidence.

The map starts with package cubes aggregated from actual graph relationships.
Explore opens a compact Source / Work / Graph insights menu. Source follows the
current package into its files; file details expose its symbols. Menu, cube,
search, source and relationship selections use the same navigation path, with
Back and All packages for return. Exact file-name searches precede symbols that
merely share that path. Files, Files & symbols and Symbols only retain the
detailed graph; Touched only is disabled in the package overview.

Details open below the map, never as a permanent sidebar or over the menu.
Incoming and outgoing relationships retain their kind, confidence and source
identity, including multiple kinds between the same nodes. Long lists have
explicit remaining counts and Show more controls. Work selection shows indexed
evidence files across packages and reports unindexed paths; an old package filter
cannot silently hide them. All of these are snapshot orientation, not live work
or proof of correctness.

Package drill-down centres and fits its visible files, not the full-project
symbol layout. Rotation and resizing refit visible geometry; deliberate zoom
remains available with Fit graph to restore the view. Short navigation transitions
stop after settling and are skipped for reduced motion. Shaded cube faces rotate
with the graph and retain the same identities and dependencies. Curved,
weighted connections and graded focus highlights (without decorative arrowheads) describe structural relationships, not
live execution. Inferred detailed edges remain dashed. Graph colours and control
radii and padding follow the shared skin and Desktop settings, including live native updates.
The native
window keeps its size across tabs; long content scrolls inside its view using slim
skin-colored scrollbars, rather than clipping or growing the window. Idle maps do
not continuously redraw, including during a stationary pointer hold. Hidden views
pause the graph even when drift was enabled. On Windows the finite, larger
four-cube entrance reuses Desktop's per-pixel-alpha surface and shared mark,
with skin-derived light strokes and no interior shade line, revealing the WebView only afterward.
The native entrance does not depend on CSS or color-key transparency in WebView2.
**Replay entrance** replays the existing in-window HTML animation in both the
native window and export; it does not send a page-readiness callback or request
a second native launch.
The read-only HTML export uses the same four-cube mark with per-cube glow; it has
no circular entrance backdrop or ring overlay.
Reduced-motion skips the animation. The entrance and initial window are centred on the selected
monitor; the four cubes hold still until the first state response arrives.
Rounded regions, skin edges and effects reuse the same native
visual controller as MO Design, with the same resolved radius and border colour
also painted by the HTML shell so WebView repaints preserve the curved edge.
The existing state refresh reloads the canonical visual settings and skin, so an
open native window follows saved Settings changes without a separate theme timer.
Native clipping is applied before the entrance fade. Both the logo and empty header space drag the
window; tabs and window buttons retain their own actions. Machine CPU/RAM observations,
runtime availability and project verification are distinct signals, not one green
health verdict. The compact Desktop Dashboard is a separate companion surface;
Desktop voice keeps its own four-cube feedback and project context.
System keeps the selected provider and CPU/memory meters in a compact strip.
Needs attention counts only non-clear provider, interrupted-work and SystemCare
notices; their details remain in System. Goal, worker and scheduler diagnostics are
status summaries, not scheduled-item or due-time lists. Connections routes to the
existing `/everywhere status` owner; this Shell snapshot has no remote surface
inventory. There is no aggregate green health verdict: meters show utilization only
and do not classify machine or project health.

## Local connection boundary

The adapter binds only to loopback and requires the exact Host, an in-memory
capability and same-origin writes. **Connected · local** confirms only this local
Dashboard adapter; hub reachability stays behind the Connections `/everywhere status`
owner. The launch fragment is removed into session storage;
credentials/configuration are not copied to the browser. Responses are no-store,
URLs are not logged, and the map runs in an opaque sandboxed frame.
Only allowlisted registered destinations and Files-authorized sources are exposed;
there is no arbitrary command or filesystem endpoint. Closing the TUI shuts down
the adapter. Visible browser state refreshes every ten seconds; hidden tabs pause
refresh and requests cannot overlap. Unchanged lists retain their DOM, focus and
expanded details. Live work remains fresh on each poll; the expensive hosting-project
graph inspection is sampled at most once per minute with its observation time.
Explicit map/check reloads inspect their selected source. The rendered-map cache
retains one project, not every visited map. The local authenticated source editor can show authorized paths/content;
those and local learning-review summaries are not part of the portable redacted
projection or remote companion payload. Native window controls expose only window
operations, not a second data or command bridge.
Authenticated skill inspection is likewise local-only. It accepts opaque IDs from
the selected project's existing inventory, never an arbitrary source path.

Consumers may change layout and interaction for their platform, but must not
fork business truth. The Everywhere hub reuses a short-lived local snapshot,
then strips it to counts and fixed labels before projecting it; control devices
may also receive the remote-safe operations perspective. Android strictly
validates each optional `android` perspective and delegated action independently;
without the user projection it shows an unavailable state instead of creating a
local truth source. Desktop renders cached
snapshot-derived values through its existing asynchronous dashboard refresh;
the PIL renderer performs no disk, graph, profile, or network work.

## Privacy and personalization

The portable projection contract may show counts, lifecycle labels, bounded public surface names,
and redacted status detail. It must not include raw memories, learned prose,
prompts, transcripts, secrets, credential values, or absolute private paths.

Dashboard controls for Profile, Learning, Skills, Work, Goals, Projects,
scheduled tasks, Files, connections, servers, and the project map delegate to
the existing command, Gateway-turn, Files, or artifact owners. Profile is not a
browsable MO Files location on any surface. The curated-profile owner keeps
exact file policy, optimistic revision checks, and atomic writes. The six
visible files are not a complete memory store, and their age is not a
self-improvement health signal:

- `operator.md` and `thinking_model.md` are primarily operator-curated; an
  explicit self-introduction may also populate the operator name;
- `terms.md` receives safe, explicit term definitions from ordinary turns;
- `facts.md` receives validated durable facts through the profile-fact owner;
- `learning.md` receives deduplicated explicit corrections or approved profile
  learning; and
- `behavior.md` is the compact categorized mirror of those accepted learning
  entries.

Durable-fact detection also participates in first-call capability routing, so
`record_profile_fact` is available before the provider answers; the
post-provider capture gate is a bounded backstop, not the first opportunity to
persist a fact. Confirmed learning has one active execution surface: a
normalized suggestion cluster has one active confirmed authority, and a
physical `SKILL.md` pack wins over virtual suggestion adapters by candidate
identity or the skill loader's literal, bounded recommendation signature,
including legacy repeated confirmations. Broader diagnostic clustering does
not establish physical pack ownership. Retired generated packs remain tombstones
and cannot reappear as
virtual adapters, active review entries, or effective confirmed counts. Headline
status counts effective confirmed/review clusters and
the deduplicated physical-plus-virtual active skill set; raw ledger buckets
remain audit data rather than another runtime inventory.

The projection reports these curated files separately from exact structured
fact, term, learning-event, behavior-rule, review, skill, and workflow-candidate
counts. Raw episodic memory, suggestion databases, learning text, and MO's
system prompt are deliberately not dashboard-editable; learning confirmations
continue through the existing revision-checked review command owner.

## Compatibility

New projection fields must be optional or versioned. A native client must
reject an invalid nested projection without rejecting an otherwise valid
overview. The Android projection uses the exact `android` surface and carries
only its allowlisted management and Files actions. Android validates the full
action contract and never substitutes a local action or metric projection when
that contract is absent.
