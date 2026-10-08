# MO Agent FAQ

This is the practical boundary guide for MO Agent. It answers questions that
change how you install, trust, operate, or extend the system. Command reference
lives in `/help`; implementation ownership lives in [`MAP.md`](MAP.md).

## Which MO surface should I use?

| Capability | Terminal | MO Desktop | Android app | Telegram |
| --- | --- | --- | --- | --- |
| Full repository coding workflow | **Yes** — primary surface | Companion files and deliverables stay here; explicit software implementation goes to Terminal or an active Project Architect | Through paired hub jobs or an existing remote terminal, not a phone-side checkout | Bounded hub turns |
| Gateway/taskboard truth | Renders the canonical board | Reuses Gateway but does not expose or own terminal taskboard UI | Reuses hub Gateway jobs; no second taskboard | Reuses hub Gateway jobs |
| Full native tool catalog | Capability- and sandbox-filtered | Request hints prime discovery; current scope and runtime guards govern execution | Hub mode uses hub tools; **This phone** is direct chat only | Capability-scoped subset |
| Local screen observation/control | **Available directly for an explicit request; not continuous** | **Separate resident desktop-control surface** | Can remotely control an already-running Desktop host | No |
| Existing terminal Live Control | Host can opt in | Can host a screen lane | **Native controller**, including multi-terminal workspaces | No |
| Conversations and continuity | Local sessions; explicitly share one named conversation when wanted | Isolated Desktop session plus explicit terminal following | Private phone thread, explicit portable named conversations, and bounded continuity selection | Isolated Telegram session plus bounded continuity |
| Voice | Terminal accepts ordinary text/paste | Optional Double-Alt hold-to-talk and local TTS/Voice Chat | Android system speech recognition places text in the draft | Client-dependent |
| Provider credentials | Device-local runtime only | Same local credential broker | Hub keys are never copied; This phone uses a separately entered encrypted key | Stay on the serving runtime |
| Offline use | Yes with a configured local provider | Yes for locally available capabilities/provider | No hub-backed conversation while disconnected; local draft persists | No |
| Background/resident operation | Interactive process | Optional singleton resident process | Optional resident Cube/service | Telegram poller |

The surfaces are deliberately unequal. The terminal is the primary engineering
agent and can directly use the existing target-owned computer tools for an
explicit screen request. Desktop is a separate resident process and isolated
conversation that reuses the same Agent/Gateway; it is not required for Terminal
to discover or observe a local window. Android and remote clients add reach
without moving provider keys, task truth, or a second agent runtime onto the
client.

For example, Terminal handles “review this window screen” on its own route:
`computer_observe` can read direct pixels or useful DOM/UIA evidence for the
task. An unknown window or tab is resolved once with `computer_targets` and
retained; reviewing the visible screen can use a direct screen capture without
that discovery step. Desktop is not involved unless the operator explicitly
asks for that separate surface.

## Is MO a model?

No. MO is the runtime around a provider model.

The provider proposes reasoning, tool calls, and an answer. MO supplies tools,
sandbox policy, sessions, memory, task evidence, verification gates, and
surface adapters. Changing providers does not change those runtime contracts.

## Why do I see only some of MO's tools?

MO currently has 71 unique built-in provider schemas in one source catalog.
The default `capability_routed` view sends only the task-relevant subset to the
selected provider; `tool_search` can activate another built-in schema for the
next request. `tool_search action=list` returns the complete runtime inventory
and count without activating more schemas. Configured MCP servers may add separately named
`mcp__<server>__<tool>` schemas, so a configured runtime can exceed 71.

Related operations intentionally share one provider owner instead of competing
schemas:

- `git_status` uses `status` or `check_ignore`;
- `web_fetch` uses `raw` or `readable` mode;
- `show_viz` owns structured diagrams and terminal-native data visuals;
- `file_transfer` owns send, list, accept, cancel, and retry;
- `migrate` owns inspect, plan, and approved apply.

`map_project` and advanced graph diagnostics remain discoverable but stay out
of ordinary code requests. `perceive` reads a local image or embedded-text PDF;
`computer_observe` exclusively owns live screen, window, and shared-tab
observation. Catalog exposure never grants execution permission—the same
sandbox, lane, confirmation, and task-evidence checks still apply per action.

## What does “evidence-backed” mean?

For work that needs a task board, Gateway owns the rows. A row completes only
after the required runtime evidence exists—for example, an inspected file, a
successful edit, or a relevant test result. The model's sentence “done” is not
evidence.

Final-answer gates also distinguish:

- verified completion;
- blocked or failed verification;
- work that still needs explicit approval;
- claims about current state that were not observed during the turn.

The interface renders this truth; it does not create it.

## How does MO use project rules?

For project-work turns such as edits, builds, reviews, and execution, MO reads the applicable `AGENTS.md` chain from ancestors to the effective working directory before provider work. Complete rules are supplied when they fit; larger files have explicit read-before-work references. The provider checks the requested work against those rules and assesses whether an authorized update is needed before reporting.

If no applicable contract exists, MO previews a generic starter. The first permitted project edit or execution can create only `AGENTS.md`, subject to the role and sandbox's write permissions. Its destination is the Git project root, or the configured project directory outside Git; launching inside a Git project's `tmp/` directory does not put the starter there. Existing rules are preserved, and inspection alone creates no files. This starter is baseline guidance, not an inferred set of project-specific rules or a full documentation bundle.

Before the final report, MO reads and fingerprints the rule chain again. Changed sources are supplied to the provider for reconciliation; repeated changes receive bounded retries and an explicit incomplete-review result if they never settle. Created starters, changed rules, and read/write failures are disclosed. Unchanged rules stay silent. Fingerprints detect changes; they do not certify that a model followed every natural-language rule or automatically enforce rules for arbitrary targets outside the active project scope.

Project rules belong to `core/context/project_context.py`. Learned conventions remain in the existing private skills owner, the structural graph remains orientation-only, and the Dashboard delegates to these owners rather than creating a second policy store.

## Why does the terminal use native scrollback?

The normal TUI keeps prompt-toolkit styling but commits finalized transcript
lines to the terminal's main screen buffer. This gives the whole session normal
mouse-wheel scrolling, drag selection, copy, and
Shift+PageUp/Shift+PageDown. Up/Down and PageUp/PageDown remain editor/history
keys instead of fighting the transcript. Finalized lines continue to appear
while a draft is in the composer; only the visible split workspace batches them
until that surface closes.

MO still keeps a bounded canonical transcript for Live Control snapshots and
session presentation. Set `runtime.scrollback_transcript: false` only when a
terminal needs the compatibility managed viewport. Interactive Terminal always
uses the styled TUI so commands keep one Command Center owner; it fails closed
instead of starting a second plain command loop.

See [`interface/README.md`](interface/README.md).

## How do queue and steer work while MO is busy?

A message sent during an active turn is queued and appears once in the
transcript as your message, without a permanent pending label. The existing
footer shows live queue/steer counts, and transient notices explain the controls.
Press Enter again to steer that exact message into the running turn at its next
safe provider checkpoint; steering does not stop the turn or switch its selected
model. A provider call already in flight cannot be interrupted, so the visual
acknowledgement is immediate but application waits for the next checkpoint.
Starting a queued message or preserving a late steer does not echo its text again.

At most three not-yet-consumed steer updates of 12,000 characters each are held
for one running turn, matching the existing terminal paste/remote-input boundary.
If that steer buffer is full or a message is larger, the exact message remains in
the ordinary queue and the terminal says so; it is never truncated or discarded.

On an empty editor, Alt+Up—or plain Up—removes and restores the latest
unconsumed queued message or steer for editing. If MO already consumed a steer,
the text is copied into the editor as a corrective follow-up because an applied
instruction cannot honestly be undone. Esc first cancels a still-pending input;
three Esc presses remain the explicit current-turn stop escalation. There is no
third-Enter stop shortcut.

## Where does MO keep my data?

The default private home is `~/.mo` (or the explicit `MO_STATE_HOME`). It
contains:

- `memory/` for profile, learning, sessions, work, and cross-surface state;
- `credentials/` for scoped device-local secrets;
- `logs/` for diagnostics and audits;
- `run/` for liveness, locks, and temporary runtime coordination;
- `cache/` for generated dashboards, graphs, and derived UI;
- `media/` for generated or attached artifacts;
- `personal/` for opaque user-owned private storage.

`core/state/layout.py` owns this structure and generates `~/.mo/README.md`.
Project source stays in the project; MO does not create project-local runtime
state by default.

## What does the Dashboard show, and can I edit MO from it?

The Dashboard is one bounded, read-only semantic projection with a user view
and an operations view. Terminal/generated HTML, Desktop, and Android adapt
that same versioned contract to their available space. It shows counts, status,
limited detail, provenance, and delegated controls from the existing taskboard,
profile, learning, runtime, presence, and structural-graph owners. It does not
reveal profile prose, memory text, learning text, prompts, credentials, or raw
tool data.

Terminal and generated HTML expose allowlisted commands or copyable requests;
Desktop sends the same controls through its existing command, Gateway-turn,
Files, and destination routes. **Profile** delegates to `/profile`, the
supported place to review curated facts, preferences, and authored profile
material with the existing revision and authorization checks. Profile is no
longer a browsable MO Files location on any surface, and the Android dashboard
routes to Chat or Control instead.
The Dashboard does not provide a raw editor for
episodic memory, learning databases, suggestions, system prompts, task
evidence, or credentials. Learning suggestions use the existing
[automatic adoption and review flow](#does-learning-need-my-approval-every-time),
and profile-authored roles or skills retain
their own approval and tool/lane boundaries.

The six Profile Markdown files are not MO's whole memory and their modification
age is not a learning-health score. `operator.md` and `thinking_model.md` are
mainly curated; `terms.md` updates from explicit definitions; `facts.md`
updates when MO's validated durable-fact owner records, changes, or forgets a
fact; `learning.md` updates for deduplicated explicit corrections or approved
profile learning; and `behavior.md` mirrors the accepted behavioral rules. The
Dashboard therefore labels them **Curated profile files** and separately shows
exact facts, terms, learning events, active rules, pending review, effective
confirmed suggestions, generated skills, and workflow candidates. A retired
learned-skill pack remains a tombstone and is excluded from active/review counts;
the raw ledger can retain its historical row for audit. Ordinary conversation is
still indexed in episodic memory without rewriting all six files.

## Can MO read or synchronize everything under `~/.mo`?

No. `personal/`, credentials, mutable databases, raw logs, device sessions,
caches, and extension internals are excluded from automatic indexing and
profile synchronization.

The optional profile replication lane has an exact allowlist: curated profile
Markdown, local skills, and the active skin. Conversation continuity is a
separate bounded event ledger and never enters Git.

## How are credentials handled?

Provider, Telegram, image, embedding, and MCP credentials live in canonical
scoped files under `~/.mo/credentials/`. Configuration names the logical key;
it does not contain the secret or authorize arbitrary environment reads.

Use `/credentials` to inspect readiness without printing values. Raw credential
files are outside normal model-tool access even when a broad project sandbox is
enabled.

For a workspace **MO host** pane, the workstation's Everywhere controller
credential authorizes the hub terminal start and Live Control lease only. The
provider credential remains on the serving hub. Each new hub terminal receives
that hub's resolved non-secret profile and Codex auth-file routing explicitly,
so a long-lived multiplexer cannot redirect it to stale profile state.

## Which providers work?

MO's provider abstraction supports:

- official DeepSeek;
- official Z.ai/GLM;
- OpenCode catalogs;
- OpenAI/Codex OAuth through the local Codex auth file; a machine without the Codex
  CLI (a server) gets its own sign-in with `python -m core.provider.codex_login start`
  (open the link, enter the code) and then `finish`. Give each machine its own
  sign-in: a copied auth file breaks once another machine refreshes the session.
  When a session ends anyway, MO starts that sign-in itself: its reply shows only
  the link and code, and MO finishes signing in by itself once you approve;
- Ollama and other local OpenAI-compatible servers;
- custom OpenAI-compatible chat endpoints.

`/model` changes and saves the Terminal source/model/thinking default in the
private `~/.mo/preferences.json` overlay. Startup and `/reload` revalidate that
choice against the current provider catalog; an unavailable saved choice falls
back to the authored `config.yaml` default. MO Desktop follows that provider and
uses its configured `mo_desktop_model` for lightweight assistance, without
changing the Terminal model or thinking setting. `/show`, `/hints`,
and `/activity` save their Terminal display defaults in the same overlay.

## How does `mapthis` choose its model and stopping boundary?

`mapthis` keeps the provider and model selected in the interface that started the
run. Project complexity may adapt each slice's token and reasoning depth, but it
does not silently substitute a legacy mapper model. Interactive runs have no
shorter mapper-wide wall-clock cutoff: the selected provider owns its request
timeout, and cancellation is forwarded through mapper, re-audit, synthesis, and
compatible provider transport calls. An embedding or test may still request an
explicit dispatcher deadline.

## What is `/knowledge`?

The existing project-index worker automatically maintains a private project manifest from the file inventory, Markdown headings, capability and command ledger, test filenames, and structural-graph metadata. It runs at project start, turn boundaries, and after project edits; the shared project refresh lease coalesces workers and a manifest byte lock serializes writers across MO instances. `/knowledge query <text>` searches headings, capability/command records, and current graph nodes. Document matches include the matching heading's source line. Test filenames are inventory, not verified behavior coverage or inferred capability-to-test relationships.

`/knowledge status`, queries, Dashboard, and native search remain read-only. They check the saved manifest's source fingerprint separately from the current structural graph and never become another writer or bypass a read-only lane. If maintenance is disabled, still running, or failed, changed saved references are withheld and the surface reports that automatic maintenance will retry; no manual refresh is required. Graph counts and freshness come from the graph owner at query time, and stale or unavailable graphs contribute no search results.

The existing native `code_search` tool also consults a current manifest. It reuses its graph hits and adds document/capability/command references not already displayed by the current graph, without searching the graph twice. Normal project-work turns additionally receive a bounded query-matched knowledge slice. That fast context path rechecks the exact backing document bytes for every selected row and omits changed rows rather than running a second graph search or serving them as current. Missing, stale, or unreadable knowledge does not block graph/history results; their existing freshness labels still apply. Markdown headings and links inside fenced examples or HTML comments are excluded through the same source-line parser used by the structural graph and documentation checker.

This is the source-linked manifest, automatic bounded project-turn retrieval, and basic query surface. It does not yet generate explanatory Wiki prose, provide an interactive Wiki, or publish pages. Existing source, tests, and maintenance documents remain the authorities.

## Can repeated tool calls change my selected model?

No. An exact repeated tool batch is a tool-loop condition, not a provider
failure. MO reuses the completed result, keeps one in-flight background
verification instead of launching duplicates, and blocks truthfully if the
provider keeps repeating the same completed work. Automatic provider fallback
is reserved for eligible provider-request failures; tool repetition never
selects a replacement provider.

## Why did MO Design leave the model I selected?

It should not leave it pre-emptively. The selected provider/model is the primary
for that request. Design may try a configured fallback only after an actual call
fails—for example through timeout, rate/capacity, permission, or provider
response failure. Studio reports the failed source/model, replacement
source/model, and reason in activity and failure detail. Each new Design request
reads Terminal's saved `/model` selection; Studio has no independent chooser.
The [model ownership contract](core/MAINTAINING.md#presentation-adapter-boundary)
owns that route. Capacity evidence is
kept per provider/model route, so a limit on one configured model does not mark
sibling models on the same provider unavailable.

If Studio shows **Needs attention**, worker completion was not accepted as
Design completion. A changed visual needs a completed worker, a newer committed
visual signature, and real post-render observation evidence. A failed worker can
leave a useful partial revision, but Studio labels it unaccepted and never calls
it finished. It keeps the last accepted preview when one exists and says plainly
when a first-turn failure has no completed preview.

## Can MO work fully offline?

Yes, when every capability used by the turn is local. Configure an Ollama or
other local compatible provider and avoid cloud-routed perception, web, remote
MCP, Telegram, and Everywhere features.

“Local-first” does not mean a cloud model becomes local. MO reports and enforces
the configured route; it does not disguise it.

## What can MO Desktop do?

MO Desktop is an opt-in auto-intent companion on Windows. It runs as a
singleton, console-less process with its own `mo-desktop` session and the same
Agent/Gateway backend.

It can:

- answer ordinary questions in a cube-attached conversation card;
- discover installed applications and top-level windows without guessing a
  command line, then bind one exact owner-scoped target;
- inspect the target through bounded whole-window or target-relative-region
  screenshots or through UI Automation. On Windows, MO first asks the exact
  native target to render without activation, so a covered window can be
  inspected without taking over the active window. If that is unsupported, the
  visible fallback is allowed only when the same target is foreground;
  minimized, missing, degenerate, or stale targets fail closed;
- discover ordinary Chrome tabs through **MO Connected Tab**, connect automatically, retain
  the selected tab, and read its DOM/text or viewport before acting. Unshared
  tabs, protected pages, and pages held by another debugger are unavailable. If needed, Desktop may use the exact native
  window without claiming DOM access or adding another browser transport;
- point, click, type, semantically or physically scroll, drag, invoke controls,
  and manage windows after fresh target evidence;
- create files and other explicit deliverables through Gateway;
- accept optional double-Alt voice input and local speech output;
- follow one explicitly selected terminal;
- host native Android Live Control;
- show one fixed Cube-branded mini Dashboard (Now, You, System) that links to the full Dashboard, and present
  bounded structured choices and images.

Explanation or pointing alone does not authorize state-changing actions.
Request classification primes tool discovery, not an execution lane or a veto
over the model's reading of the current conversation. State-changing actions
must stay within the authorized current request, consume a fresh observation, and must
be verified by a newer observation on the same target. Three unchanged verified
action/observation cycles stop further action instead of looping. Discover an unknown target once, observe it, act, and inspect the fresh result
returned by the action. Terminal and Desktop share these owners and evidence
rules. Image-capable models receive screenshots directly; text-only models use
the configured bounded observer without switching their home provider. DOM/UIA
controls remain useful for exact input. Explicit captures always return current
pixels. The first observation attaches automatically to the requested ordinary
Chrome tab. The toolbar action can connect directly or stop access to that exact
tab. The extension initializes one native channel on install/reload and Chrome
profile startup so discovery stays ready even with an empty catalog; that idle
channel has no page access. An explicitly stopped tab remains excluded until the
operator re-enables it or Chrome clears the extension's session storage. A
browser viewport is not desktop screen geometry; visible walkthrough pointing
uses a native screen/window observation. Page DevTools can detach MO; after it
closes, a later observation may attach again unless the tab was explicitly
stopped. Inspecting the extension's service worker is separate.
Background-safe observation does not make raw input background-safe: pointer and
keyboard actions still require the exact target in the foreground.
Desktop does not own terminal task truth.

See [`mo_desktop/README.md`](mo_desktop/README.md).

## How do I set up or remove MO Connected Tab?

MO browser tools automatically verify and repair this checkout's user-scoped
native-messaging registration on first use. **Settings → System → Connected
Chrome** reports a valid registration as neutral, disabled **Bridge ready** and
retains **Repair bridge** only for explicit recovery. **Copy extension folder**
provides the folder for Chrome's one-time **Load unpacked** step. Chrome owns its
extension list, so MO Settings cannot silently install or remove that unpacked
record.

Ask MO to work on the intended tab; observation connects automatically, with no
extension click. The Chrome action can stop access. After an extension update,
reload it to load the changes.
Request deadlines preserve compatibility with the existing protocol. For removal, disconnect attached tabs, choose **Remove
bridge**, then remove the unpacked extension from Chrome. See the
[MO Connected Tab guide](clients/chrome/README.md) for details.

## How do Email and Life work together?

In Desktop's connected Dashboard, **Email** has Gmail and Outlook account
buttons, search, a local reader, and controls for existing mail destinations.
If Gmail is disconnected, its setup starts there but requires your own Google
Desktop OAuth client and browser consent. Outlook uses a signed-in Chrome Mail
tab with MO Connected Tab; it does not require a Microsoft app registration.
You can also ask MO in Agent chat to connect Gmail or Outlook. MO enables the
existing local Gmail preference or prepares the Outlook browser bridge and
shows the next account-owner step directly in that conversation; Gmail's
client ID and secret are entered in Dashboard, not chat.
MO can open Outlook Mail when no tab exists, but Chrome's extension must be
loaded and Outlook must render the requested list. A hidden tab can leave its
rows unavailable, so MO reports that failure instead of calling the inbox empty.

**Life** holds matters you explicitly confirm: payments and installment plans,
subscriptions, appointments, cases and their progress, plus income and
outgoings you record. Email offers bounded wording hints to help review visible
messages; it does not import a mailbox into Life or treat sender claims as
personal facts. A date on a Life item is not an alert. Add an explicit reminder
or Agent task with the existing scheduler. On an explicit Agent mail request,
MO uses the configured model to summarize the messages it retrieves. It does
not record a sender's claim as an obligation without your confirmation. See
[mail behavior and setup](core/mail/README.md) and
[Life ownership](core/life/README.md).

## Does Android run MO or hold my provider key?

The app does not run MO's Python Agent/Gateway. It has two explicitly selected
chat modes, documented in the [Android guide](ANDROID.md#install-and-pair).

In **Hub** mode, its resident destinations are Dashboard, Chat, Control, and Files. Dashboard
uses the hub's validated canonical projection, adds only local phone capability
indicators, and routes actions to those existing destinations; it does not add
a phone-side MO runtime or a second hub-state owner.

Hub conversation turns run through isolated `api-<device>` sessions. The phone
keeps an encrypted pairing credential; the hub's provider credentials stay on
the serving runtime. In **This phone** mode, the user separately configures an
encrypted phone-local provider profile/key for direct chat. It does not use
MO's tools, profile, taskboard, or portable conversations, and never silently
falls back to Hub or mixes credentials.

In Hub mode the app can ask the hub to start one bounded worker, schedule exact-device work,
or launch a fixed MO terminal on the hub. Those operations reuse existing
server owners and exact returned leases.

## What is Android Live Control?

It is an opt-in, memory-only relay to an already-running MO terminal or MO
Desktop host.

- The controller needs exact `control` and `remote_control` scopes.
- Each host connects outbound with a distinct `remote_host` identity.
- One short controller lease owns a host at a time.
- Terminal control relays bounded text/activity events.
- Desktop control relays changed JPEG frames and bounded input.
- The hub does not persist pixels/input, expose a raw shell, or open an inbound
  workstation port.

Android terminal workspaces group existing `mo_session` hosts for
presentation. Closing a view releases its lease; it does not kill the terminal
process. A hub-started terminal can be stopped only by its exact server lease.

## Can MO control the Android phone itself?

No public Store capability semantically automates the phone UI or exposes
privileged Android system operations. The Store app can control an authorized
Desktop or terminal, share one explicitly selected phone folder read-only with
authorized Hub devices; those are separate capabilities and do not grant
control over Android itself.

Owner-device builds and their additional capabilities remain private local
custody. They are not distributed, documented as Store availability, or exposed
as public source in this repository.

## What is Consistent Everywhere?

Three independent systems:

1. **Conversation continuity** sends finite-TTL, thread-scoped orientation
   events through an authenticated hub. Events contain intent, outcome, next
   step, lifecycle, and opaque project evidence—not raw reasoning, tool output,
   credentials, or full transcripts.
2. **Portable named conversations** expose only a conversation explicitly
   created on a scoped device or marked with `/session share <name>`. The core
   session store remains the only transcript/catalog owner; clients receive a
   bounded user/assistant text projection and must send the current revision
   when continuing, renaming, unsharing, or deleting it.
3. **Profile replication** optionally uses private Git/OpenSSH for an exact
   allowlist of curated profile Markdown, skills, skin state, and saved Terminal
   preferences. The [state layout](core/state/layout.py) owns that manifest.

None automatically merges or publishes all conversations. Portable selection
is explicit, continuity selection is explicit when ambiguous, and equivalent
terminal prompts remain separate by stable thread identity.

See [`mo_everywhere/README.md`](mo_everywhere/README.md).

## Can I attach files in MO Design?

Yes. The native paperclip picker accepts up to eight files per user message,
with a 20 MiB limit per file, and the message may contain attachments without
text. Studio shows removable composer chips and safe attachment metadata in the
conversation.

The native bridge imports selected files into MO's private attachment catalog.
Only opaque IDs cross the Studio command/session boundary; original source paths
do not reach Studio JavaScript. The Design worker receives access to the exact
saved catalog files for that turn and is instructed to treat their contents as
untrusted evidence, never as commands.

## How do file transfers differ from chat attachments?

Chat attachments are bounded input for one conversation turn. File transfer is
optional cargo between stable paired devices and uses one shared resumable
spine across Terminal, Desktop, Telegram, and Android. Turn attachments remain
catalog-only with their fixed 20 MiB limit and do not depend on the cargo
auto-accept preference.

With `file_transfer.enabled: true`, files are split into verified chunks up to
the configured 2 GiB ceiling. The sender retains its source until the hub
accepts the complete file; the hub retains its spool until the target records
receipt. The default destination is the receiver's categorized attachment/file
catalog. A requested named path is a stronger action and is never auto-accepted
unless `auto_accept_named_paths` is explicitly enabled. The receiver, not the
sender, resolves that path and confines it to the receiver's current allowed
filesystem roots.

Transfer routes require `control` plus exact `file_transfer` scope. Device
labels are presentation only: a unique stable device ID owns delivery, and an
ambiguous label must be resolved explicitly. Android and Python receivers retain
verified partial chunks for resumable delivery.

## What can MO Files access?

MO Files is not a public or permanent remote disk share. It shows this computer
and discovers the authenticated sources that are actually available: the
serving hub/profile, connected Desktop and terminal hosts advertising
`files_v1`, and Android's explicitly shared phone folders. After selecting a
source, it exposes
opaque locations owned by that source: **MO home** (that host's own runtime
root), project roots allowed by its current filesystem policy, and
**Personal** roots only when explicitly configured. A host already
configured with `access.mode: full` advertises its available drive roots
instead of guarded project roots. APIs and clients still receive opaque
location IDs and relative paths—not absolute roots—and hidden/control/
credential/key files and symlinks remain excluded.

Remote MO-host browsing/reading requires `control` plus exact `file_browse`;
folder creation, rename, copy, move, edit, and recoverable file-or-folder Trash/restore
additionally require exact `file_manage`; sending to a selected paired device
additionally requires `file_transfer`. Writes use a selected SHA-256 revision and atomic replacement
so a stale editor cannot silently overwrite a changed file. Folder copy/move is
bounded and verified; Trash relocates the exact selected tree instead of
permanently erasing it, and restore fails rather than overwrite an occupied
original path. Windows source discovery uses its own revocable
`everywhere-files.json` controller credential, shared with MO-host workspace
panes when that identity also has exact `remote_control`, and reports missing
pairing, offline hub, or insufficient authority while keeping **This computer**
usable.
Connected MO hosts use Everywhere HTTPS pairing, identity, and revocation.
Desktop also offers an explicit five-minute browser Quick transfer by QR from
one selected local folder. The QR bearer link permits bounded uploads and
downloads on the chosen private Wi-Fi/LAN address without creating a device
pairing. Stop sharing, expiry, or closing MO Files invalidates that link; none
of these actions revokes an existing Hub pairing or retracts files already
transferred. Browser Quick transfer uses unencrypted HTTP, so use it only on a
trusted private network.

On Android, the compact Files destination shows the current source beside
connectivity. **This phone** is a separate source in both distributions: it can
browse the folder selected through Android's system picker. Private Direct can
also expose the broader phone boundary after a separate Android all-files grant.
Opening Files never silently opens a picker or expands that grant. Play's
explicitly enabled folder host offers read-only peer access while the phone is
unlocked and the resident service runs; Stop, Remove and revoked authority end
access. Existing Android pairing needs an explicitly reviewed `remote_host`
grant and a compatible Hub before hosting works. These restored Play features
belong to the unreleased candidate, not the older closed-test binary.
Incoming/Outgoing activity is
the existing transfer ledger; clearing it hides terminal history only and does
not cancel or delete active custody.

## Can I run more than one MO terminal?

Yes. Every process gets a stable `MO_INSTANCE_ID` and a distinct default
`main-<instance>` session. `/heartbeat instances` shows sibling terminals. Each
sibling summary may include only a bounded, redacted active task title, or its
next ready title when no row is active. On commit, push, and deploy turns,
workspace context tells MO to inspect current Git plus sibling task/session
evidence before changing shared state instead of asking you to reconstruct
evidence already available. This phase is untrusted coordination context, not
file ownership or action authority. Another active terminal does not require a
new branch: for non-overlapping work, MO stages only its task files and continues
on the current branch. Within one process, active `WorkerRegistry` path claims
can attribute files to registered workers; unmatched changes remain
unattributed. Desktop follows an explicit binding when several terminals are
live.

The headless service, Telegram poller, scheduler, Desktop resident, and
Everywhere coordinator use resource locks where only one owner is valid.

## How does MO remember things?

MO separates:

- exact session conversation and task state;
- curated profile facts;
- episodic SQLite/FTS5 recall;
- optional semantic embeddings fused with keyword results;
- approved workflow/feedback learning;
- materialized local skills;
- source structure in the project graph.

These are not interchangeable. Profile facts outrank incidental navigation,
learning needs evidence and policy approval, and code graph results are
orientation that must be verified against current source.

Within the same loaded conversation, a relative follow-up such as “fix please”
uses its latest substantive user subject to select relevant profile guidance,
skills, and episodic recall. A named new subject uses its own query. Selection
and retrieval do not guarantee delivery: the context budget may omit material.
Current `turn_context` telemetry distinguishes prepared from delivered sources
and retrieved from delivered memory records; delivery itself does not prove the
model followed them. See the [personalization contract](core/MAINTAINING.md#personalization-and-project-boundaries)
for selection, adoption, and evidence ownership.

A new terminal deliberately starts a separate session so concurrent terminals
cannot overwrite one conversation. Its startup hint offers `/session` to reopen
the full saved conversation. Independently of how a follow-up is worded, MO may
receive an inactive same-surface conversation's substantive user request as
secret-redacted JSON historical data from the canonical session store. A
conversation owned by another live Terminal process is excluded before its
record is read; active siblings expose only bounded heartbeat/taskboard
coordination metadata. The stored transcript remains unchanged; redaction
markers in provider context are never reconstructed. This does not auto-resume
old work: the explicit current request wins, and old project/runtime claims
still need current evidence. A process-wide MO Trace can aggregate bounded
activity from several running Terminals for observation; that does not make
their transcript or taskboard part of another Terminal's conversation.

That narrow projection has no phrase list, turn window, or source-local word
cap. The normal bounded provider-context envelope still applies, but it is not a
behavioral turn/request budget. Profile Markdown, episodic recall, and the code
graph remain separate owners and are not transcript substitutes.

## Does learning need my approval every time?

No. After eligible answered turns, MO records and checks recurring guidance.
With `learning.auto_promote` enabled (the default), sufficiently repeated,
recent, high-confidence patterns about verification, clean completion and
concise routine replies can activate automatically. Other suggestions,
including visual preferences and workflow habits, remain inactive until
approved. Quoted examples are filtered from recurrence evidence; concise-reply
signals must concern replies or explanations, not isolated task words.

`/learning` shows the items that need a decision. **Active learning** identifies
accepted suggestions, including those learned automatically, and offers
**Undo learning**. You do not need to scan or open the menu for automatic
learning to run. **More actions** holds optional scans, consolidation and skill
imports. `/profile mine` uses the same effective review as
`/learning suggestions`, so rescanning an active item does not request approval
again. `/learning status` explains the current automatic-learning setting.

Pending counts describe effective review items, not every historical ledger
row. Profile facts, accepted behavior rules, workflow candidates and skill
packs remain distinct; their counts should not be added into an approval
backlog. The [personalization contract](core/MAINTAINING.md#personalization-and-project-boundaries)
owns these boundaries. Existing learning stays active if automatic promotion
is later disabled; use the review controls to undo a specific item.

## What is the structural graph?

`core/graph/structural_graph.py` owns one private persisted graph per project.
It supports bounded code search, callers/callees, neighborhoods, paths,
explanations, stats, and generated maps. `core/graph/code_graph.py` extracts
and provides compatibility APIs; it is not another graph store.

The repo-scoped `mo-graph` MCP is an external adapter over that same artifact,
loader, topology, and in-memory BM25 ranker; it does not maintain a parallel
index. Loose issue wording receives a small deterministic code-vocabulary
bridge (for example permission/authorization and approval/confirmation), not a
second embedding store or permission to treat a graph match as proof.
The persisted machine form may omit an edge's `source_file` when it is exactly
the path already owned by that edge's source node, and stores shared semantic
values once in `edge_defaults`. Query and map consumers resolve those values
from the source node/defaults without expanding every edge dictionary. The
parsed graph and its derived indexes stay warm during a query burst, then leave
memory after five idle minutes; the next query reloads the same artifact.

The graph never replaces source reads or tests. Missing graphs fail closed;
stale graphs fail closed for automated consumers but remain explicitly labeled
orientation in the external MCP, where agents must verify selected results.
Startup and turn maintenance run outside the provider request. If a missing
graph becomes fresh while a project-work turn is still active, the next provider
checkpoint receives one bounded query-matched direction slice and the terminal
shows the normal `context supplied` receipt. MO does not wait for the worker,
retry the build tool, or inject the whole graph.
The same active turn keeps initially missing project documentation and Git
history pending while that shared refresh lease is active, then supplies their
current bounded slices at the next checkpoint after the worker finishes.
Long work requests keep later interface, verification, and documentation scope
in that bounded query. Search returns distinct file owners, recognizes public
tool names such as `code_search` as executor ownership, and favors an explicitly
named product surface. Direct instructions not to use a target do not rank that
target as requested work. Small top-level Python registries add bounded search
vocabulary to their owning file without creating a node per key. Explicitly
selected product, verification, and documentation seeds are rendered before
their expanded graph neighbors, and neighbor expansion follows query relevance
rather than node-name order. The internal candidate window is wider than the
delivered slice so an explicitly requested document is not discarded before
selection. Project-documentation context omits the already-visible query and
uses that bounded space for verified source rows.

## What are goal mode and PRT?

`/goal <objective>` runs a persistent autonomous goal with explicit progress,
auditing, pause/resume, and evidence requirements.

`/prt` reviews a diff, commit, range, or path for correctness, regressions,
security, duplication, maintainability, and verification quality. Local behavior
is target-aware: worktree/path targets report findings without editing; commit
and range targets delegate confirmed violations to MO's normal Agent worker for
source re-check, minimal correction, and focused verification.

| Command | Behavior |
| --- | --- |
| `/prt` | Manually start review: report uncommitted work; otherwise review `HEAD`, correct confirmed violations, and reassess |
| `/prt .` | Report the captured final worktree compared with its current commit |
| `/prt <commit-or-range>` | Review pinned committed source, then delegate confirmed violations |
| `/prt <path>` | Report worktree changes within that repository path |
| `/prt report` | Show target-aware recorded review history; start no review or correction |

Local reviews use independent snapshots and may run while other terminals work.
Reports identify the captured source, elapsed duration, origin, graph/history
coverage, and test execution or skips. Later edits are outside that review
evidence; a skipped test run or empty affected-test list does not establish
regression coverage. Worktree/path reviews stop after that report. Commit/range
reviews with confirmed unresolved findings start an ordinary Agent worker, which
re-checks each finding against current source/contracts before making minimal
changes and running focused verification. PRT then reassesses the original change
together with the corrections, repeating while confirmed issues remain and fixes
make progress. It stops at the configured target with no unresolved findings, or
reports the actual score, remaining findings, and blocker when further progress
cannot be established. A perfect 5.0 is not required. Corrections require HEAD to
remain at the reviewed commit and cannot overlap pre-existing uncommitted work.
Historical targets still receive their pinned review; correction waits for the
matching checkout. It preserves unrelated work and never
commits, pushes, deploys, or uses credentials. A completed review can be reused
for unchanged source; PRT is not a mandatory commit gate and never commits merely
to unblock another review.

The [GitHub workflow](.github/workflows/README.md#prt) remains review-only: it
runs trusted base reviewer code against an isolated PR worktree and never
executes untrusted PR code inside the provider-secret job or edits the PR. In the
active local agent, a routed worktree report—or the final result of a delegated
committed-target correction—is supplied once to MO's next provider turn,
independent of follow-up wording or terminal rendering; a failed provider call
leaves that exact result available for retry.

Neither mechanism can turn missing evidence into success. Implementation and
maintenance boundaries live in the
[PRT review contract](core/MAINTAINING.md#prt-review-contract).

Local PRT never runs merely because ordinary work finished or a commit was
created. Its pre-commit focus is the proposed worktree change; its post-commit
focus is the pinned change and any confirmed corrections. GitHub PR events and
authorized mentions retain their separate triggers; an arbitrary push is not a
universal post-push review. Score/confidence are evidence-weighted heuristics,
not correctness probabilities. Even a rounded 5.0 does not meet the target with
unresolved findings. Bounded provider summaries prioritize unresolved findings
and disclose omissions; delivery of a summary is not delivery of every detail.

## How do schedules and background roles work?

One private scheduler owns one-time, interval, and daily work. It can run MO
turns, goals, role-governed work, or approved no-model scripts already stored in
the scheduler's private script directory. Terminal, Desktop, and Android are
clients of this same owner.

Persistent roles come from profile skill packs and can govern an interactive Terminal/Desktop conversation as well as background or scheduled work. Terminal supports `/role activate <role-name>`, `/role status`, and `/role off`; `/role <name> <objective>` remains an explicit direct worker request. Active roles are saved with their conversation, not applied globally to every session.

In Terminal, open `/role` and select **Book Writer** or **Project Architect**; selecting it activates the role and returns to the conversation. You can also type `/role activate book-writer` or `/role activate project-architect`. In Desktop, use **Settings → Voice → Conversation role → Choose**, or ask in text/voice to activate the named role. Choosing a role applies its workflow and tools to both text and voice; Default leaves it.

`Project Architect` keeps specialists as project-bound role packs and dispatches them through MO's existing worker runtime. Ask MO to activate the role, or use `/role activate project-architect` in Terminal. Activation and orientation do not start project work. When assigned a task or asked to resume one, the role compares saved calibration references with current project evidence; worker reports remain untrusted until verified. Worker processes do not resume after shutdown; saved session history and Terminal taskboards preserve work context. Use `/role show` in Terminal, or ask MO to show Project Architect's workspace in Terminal or Desktop: Mologrthim, MO's operations floor, opens at this MO's bay with its real specialists, their checked work and the other running MOs, and the conversation stays where it was. Opening it changes no role, starts no workers and creates no Design prototype.

The floor uses MO's shared theme and window controls; drag its title bar to move it and resize it from its edges.

`Book Writer` writes a book on any subject the user picks. The user chooses one books folder; `LIBRARY.md` there lists every book (title, folder, subject, format, status and where reading stopped), and each book's `BOOK-STATE.md` is its canonical resume checklist for approved material, pages, next steps, and production checks. Asked to read, the role answers with the book's text only, one passage at a time, so MO Desktop's voice reads exactly the book; `voice.spoken_max_chars` sets how much of a reply is spoken. The user picks PDF, EPUB, or print-ready production. When useful or requested, the role can use MO Design's existing artifact owner for an interactive page-flipping prototype; it is not the manuscript or a final export. Normal sandbox, user-authority, confirmation, and any explicitly declared role scopes still apply.

## How does MCP access work?

Configured MCP servers start lazily when the turn needs the tool catalog. MO
exposes tools as `mcp__<server>__<tool>`, passes them through the sandbox, and
can restrict each server with an exact `allow_tools` list. A malformed
allowlist exposes nothing.

MCP child processes inherit a safe environment plus explicitly selected scoped
secrets for that server—not the parent process's ambient credentials.

## What is public and what is private?

The Git repository is product source. It must not contain operator identity,
profile state, credentials, private server/deployment knowledge, session
memory, ignored maintainer tests, or private extension internals.

Public CI runs value-free boundary checks, documentation-link validation,
compilation, and entrypoint imports. The ignored `tests/` tree is a
maintainer-local verification overlay and is never published.

## How do I update MO?

For the Python checkout, use `/update`, `mo --update`, or
`python -m core.update.apply` from an eligible clean checkout.
The [update and recovery guide](README.md#update-and-recover) explains dependency
refreshes, local edits, moved installations, and setup diagnostics. The
[removal guide](README.md#remove-mo) distinguishes deleting the application from
erasing your private MO home.

Android installs and updates come from Google Play for users eligible for the
available track. The [Android guide](ANDROID.md#availability) records the public
baseline and distinguishes closed testing from Production availability. Private
owner-device builds are maintained and distributed outside the public repository.

## Can I control language-server checks per project?

Yes. Configure installed server commands under `lsp.servers` in your normal MO
config, then `/reload`. `/dashboard lsp on`, `off`, or `default` saves a choice
for the current project in the existing private preferences overlay; `default`
restores the config baseline. Other projects and provider settings are unchanged.
Servers start lazily. Turning a project off stops its clients; reload stops old
clients before applying config changes. No servers are installed automatically.

Desktop Dashboard Home opens **Project checks**; Work offers the current
project's LSP control. Terminal uses `/dashboard checks`. The detail shows
configuration, graph freshness and sampled check events for this conversation's
latest recorded model turn. Missing events are not passing results; security
events currently record findings, not every clean scan. No permanent footer
badge or extra model request is added.

LSP diagnostics supplement tests and review; they do not prove user intent,
correctness or improved accuracy for every task. A new edit invalidates the old
diagnostic receipt and rearms the gate. Versioned stale notifications are ignored;
servers that omit diagnostic versions cannot provide exact version correlation.
Errors or unavailable evidence can prompt the same selected model to revise its
answer. Off, unsupported, timed-out and missing-server states are never “clean.”

## What should I read next?

- Setup and project overview: [`README.md`](README.md)
- Source ownership: [`MAP.md`](MAP.md)
- Terminal behavior: [`interface/README.md`](interface/README.md)
- Desktop behavior: [`mo_desktop/README.md`](mo_desktop/README.md)
- Everywhere hub and protocol: [`mo_everywhere/README.md`](mo_everywhere/README.md)
- Android availability, setup, and privacy: [`ANDROID.md`](ANDROID.md)
- Maintainer contract: [`AGENTS.md`](AGENTS.md)
## How do I open the full Dashboard?

Run `mo --dashboard`, or use `/dashboard show` in MO. It opens automatically in
the existing optional native WebView renderer and stays attached to that normal terminal. Open terminal
return there; there is no embedded terminal or browser voice panel. Project rules
edit their authorized `AGENTS.md` source through MO Files, while Project knowledge
queries the existing `/knowledge` index. Learning details, Approve and Undo run
in place through the established revision-checked owner. Checks & LSP applies
normal per-project preferences to the selected project. `/dashboard html` is only a read-only export.
The cube-attached Desktop Dashboard remains a separate compact companion.
See the [Dashboard guide](core/dashboard/README.md) for the complete boundaries.
