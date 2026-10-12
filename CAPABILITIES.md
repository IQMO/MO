# MO Agent capabilities

This reference explains what MO can do, where each capability is available,
how it uses your data and permissions, and the limits to expect.
Optional integrations depend on your configuration and the installed release.
The capability list is not a claim that every integration is connected or
that an installer has been accepted for public distribution. See the
[current availability and installation guidance](README.md#installation-availability).

## Product charter

- **MO Terminal** is the primary agentic engineering workbench. It plans,
  inspects, maps, edits, runs tools, verifies outcomes, and maintains projects.
- **MO Desktop** is the optional persistent assistant for ordinary life and
  work. It has its own conversation and shares MO's tools, evidence, private
  profile and authorization boundaries.
- **MO Everywhere** connects authorized devices through a hub. Android's
  **Hub** mode uses that host without receiving its provider credentials.
  **This phone** is separate direct provider chat with a separately entered,
  encrypted key. See [Android modes and pairing](ANDROID.md#install-and-pair).
- Android's public channel is Google Play; access depends on the current
  release and testing channel. See [Android availability](ANDROID.md#availability).
- **Telegram** and the headless service provide additional ways to reach MO.
  Conversations stay isolated by default.
- You can explicitly share one named conversation with scoped Android clients.
  Other sessions are not listed or shared automatically.
- Cross-surface continuity shares bounded intent, outcome and next steps. It
  does not merge raw transcripts, tool payloads, screenshots, taskboards or secrets.
- Provider models supply reasoning. MO controls tool access, sandboxing,
  confirmation, task evidence, persistence and completion checks.
- Project work follows the applicable ancestor-to-project `AGENTS.md` rules.
  If rules are missing, MO previews a minimal starter and creates it only on a
  permitted project action. Reporting rechecks changed rules; detecting a changed
  file does not prove compliance with every instruction. Learned conventions and
  project orientation supplement those rules.
- Product state is private by default under `~/.mo` or `MO_STATE_HOME`.
  `personal/` is opaque and is never automatically inspected, synchronized,
  migrated, indexed or deleted.

## How capabilities reach MO's work

```text
Your request and permissions
             ↓
Relevant context and available tools
             ↓
Actions, results and completion checks
```

MO selects relevant context and tools for the request, surface and configuration.
This reference is searchable project knowledge; it is not loaded in full on
every turn and it is not the model's tool catalog.

- **Instructions and context:** your terms, profile, relevant prior requests,
  recall, continuity, skills, project rules, graph, history and documentation can
  inform a turn within a bounded context budget. A new project task can recall
  earlier user requests without replaying MO's old answers. An unrelated task
  from another session is not selected just because it is recent. Resume and
  history retain their own context. Corrections retain completed tool results.
  A labeled stale graph can help navigation; impact and verification still need
  current evidence. Asking about interface code alone does not imply screen access.
- **Tools:** MO normally presents a task-selected working set; configured
  deferred and full-catalog modes are also supported. `tool_search` lists known
  tools or activates matching tools for later use. Listing alone selects none;
  discovery may initialize a configured MCP integration. Available project
  knowledge can make its search tools available independently of your wording.
  Discovery grants no extra permissions, and surface, role and task limits apply.
  A bounded read-only review can use project orientation without creating a plan.
- **Background work:** where enabled, project graph, history and knowledge
  refresh asynchronously. Learning, saved closeouts, heartbeat, scheduled work
  and background roles follow their own settings; they do not all require a
  model tool call.
- **Completion:** MO checks relevant project rules, task evidence, claims,
  edits and surface results. LSP checks require a configured server enabled for
  that project. A disabled or unavailable check is not a clean result.
- **Controls:** commands, Desktop controls, Android grants and service setup
  activate their respective features. A listed capability does not establish
  that its window, service or integration is currently running.

`/dashboard checks` and Dashboard **Checks & LSP** show available records for
the selected conversation: delivered context and recall, tool outcomes and
verification. Terminal activity and context receipts add turn-local evidence;
selected skill names remain available for follow-up. Prepared context is not
necessarily delivered context. Delivered guidance does not prove the model
followed it, and a check entry does not prove the underlying check ran. Judge an
outcome by the actual result, saved artifact or visible surface.

## Capability ledger

Each stable capability ID identifies its product responsibility, surfaces,
private state, safeguards, discovery route and observable behavior or limits.

| ID | Capability | Product responsibility | Surfaces | State and storage | Permissions and safeguards | Find it | Behavior and limits |
| --- | --- | --- | --- | --- | --- | --- | --- |
<!-- mo-capabilities:ledger:start -->
| `CAP-AGENT` | Intent routing, automatic work context, agentic turns and planning | Conversation and planning | Terminal, Desktop, API, Telegram, service | private state for the selected surface conversation | sandbox, bounded context and completion checks | [Overview](README.md) and the context and tools explanation above | Plans and actions belong to the selected conversation; completion depends on recorded outcomes, not model wording alone. |
| `CAP-EVIDENCE` | Task truth, changed-file verification, configured LSP diagnostics and completion evidence | Task tracking and verification | all agent turns, subject to check eligibility and configuration | taskboard, task evidence and project LSP preferences | claim consistency and completion checks; disabled or unavailable diagnostics are not clean evidence | README, FAQ, and Dashboard Checks & LSP | Failed or unavailable checks remain visible; LSP runs only for a configured, enabled project server. |
| `CAP-TOOLS` | Filesystem, shell, web, repository access, local image/PDF perception and tool discovery/dispatch | Tool access and local perception | Agent-backed surfaces | tool audit and result state | sandbox, approval, secret, network and untrusted-content controls; bounded local perception with optional embedded-text PDF extraction | `tool_search`, `perceive`, README and FAQ | Available actions depend on the surface, role, configuration and current permission; reading an image or PDF grants no action authority. |
| `CAP-ORIENT` | Help, status, current work, canonical Dashboard, and usage | Help and Dashboard | Terminal, native WebView/read-only HTML, Desktop, Android | existing work, profile, learning, project and file records; no separate Dashboard copy | bounded redacted views; authenticated local controls act through the relevant feature | `/help`, `/status`, `/now`, `/dashboard`, `/usage` and [FAQ](FAQ.md) | Shows bounded current work, usage and source-linked status; a projection does not create or complete work. |
| `CAP-SETUP` | Native first-run setup with existing provider/model selection and Codex subscription sign-in, initialization, runtime and personalization diagnostics, credentials, updates, reload, and settings | Setup, credentials and updates | Native Settings, Terminal, Agent tools, and service setup | private application home and configuration; diagnostics do not apply changes | value-free read-only diagnostics, credential isolation, and separate apply/decision authority | README, FAQ, interface guide, and MO help and command palette | Diagnostics report without applying changes or exposing credential values; update behavior depends on the installation mode. |
| `CAP-SESSION` | Conversations, project history, natural prior-request orientation, compaction/handoff, save, switch, resume, retry and undo | Conversation and project history | Terminal and Desktop with separate conversations | private saved-conversation catalog and transcripts | surface isolation, retained user requests, redacted historical context, stable conversation selection and bounded saved data | README, FAQ, and MO help and command palette | Save, switch and resume the selected conversation; a fresh surface stays separate and prior requests are labeled historical context. |
| `CAP-GAME` | Explicit Game Collaboration project context, questions, decisions, proposals, and approvals | Game Collaboration | Terminal only | private `memory/work/game-collaboration` records plus bounded session binding metadata | explicit activation, bounded context, revision-conflict checks, and no tool authority from a recorded decision or approval | interface guide and MO help and command palette | Explicit project questions, decisions and proposals remain revision-aware; recording an approval does not itself authorize a tool action. |
| `CAP-PORTABLE` | Explicit portable named conversations | Shared conversations | Terminal and Android | private saved-conversation catalog and transcripts | opt-in share/create, exact scopes, revocation, bounded visible-text projection, optimistic revisions | Everywhere and Android guides plus MO help and command palette | Only explicitly shared or created portable conversations appear to scoped clients; revocation removes access. |
| `CAP-CONTINUITY` | Bounded cross-surface work handoff, and running MOs aware of each other: heartbeats, sibling notes and messages between MOs | Cross-surface continuity and MO coordination | Terminal, Desktop, Android, Telegram | private continuity events, surface bindings, heartbeat and MO-message records | exact scopes, eligibility, privacy bounds; sibling notes and messages are redacted and never instructions | `/heartbeat`, [FAQ](FAQ.md) and Everywhere controls | Shares bounded work context; sibling notes and MO messages are coordination data, not email or new instructions. |
| `CAP-PROVIDERS` | Provider, model, reasoning, bounded same-route transient recovery, and turn-local reactive fallback selection | Provider and model selection | Agent-backed surfaces | authored configuration, private Terminal preferences and provider/model-specific route and capacity records | isolated credentials, catalog revalidation, shared-auth reload, one pre-output auth/transient retry, precise non-retryable errors, isolated tool loops and restoration of the selected route | `/model`, [README](README.md) and [FAQ](FAQ.md) | Recovery and configured reactive fallback stay within the current turn; credentials, tool catalogs and capacity evidence remain route-specific. |
| `CAP-GRAPH` | Structural graph and BM25 search, source-linked project knowledge, code context, mapping, impact, redundancy diagnostics and Git history, with automatic index maintenance | Project graph, knowledge and history | Terminal, Agent and configured graph MCP clients | private per-project graph/history/knowledge caches, durable review findings, and generated human project map | bounded reads, freshness reporting, explicit private-source tracing, cancellation and coordinated refresh; orientation never replaces source, tests or contracts | `/structural-graph`, `/knowledge`, `code_search`, graph/history tools, `redundancy_scan` and [FAQ](FAQ.md) | Bounded source-linked search provides reusable project orientation; freshness is reported and navigation never replaces current source or verification. |
| `CAP-PROFILE` | Curated profile facts, preferences and operator terms | Private profile and preferences | Agent-backed surfaces | structured private profile plus declared profile Markdown files | private-home, locked persistence and redaction boundaries | `record_profile_fact`, README, FAQ, MO help and command palette | Curated facts and user-defined terms persist privately; product defaults do not include another operator's identity or preferences. |
| `CAP-LEARNING` | FTS5/BM25 episodic turn recall, optional semantic fusion, feedback and reviewable learning | Recall and learning review | Agent-backed surfaces | private turn index, learning stores and suggestions | bounded recall, distinction between prepared and delivered context, and configured adoption policy; recalled entries are excerpts, not complete sessions | `/learning`, [FAQ](FAQ.md), Dashboard Learning and Checks | Recall uses bounded excerpts, not entire sessions; review and adoption policy determine which learning becomes active. |
| `CAP-SKILLS` | Selected skills, discoverable skill catalog, scoped conventions and authored roles | Skills, conventions and role guidance | Task skills on non-Desktop Agent surfaces; explicit roles on Terminal and Desktop; local Dashboard guidance inspection | private skill packs, shipped starter skills, opt-in project skills and per-session delivery receipts | declared tools and scope, approval, project-bound new conventions, labeled older cross-project conventions and turn-specific delivery receipts; delivery is not proof of adherence | `/skills`, [FAQ](FAQ.md) and Dashboard Learning | Selection respects declared scope; a delivered-skill receipt records guidance supplied, not proof that the model followed it. |
| `CAP-GOAL` | Autonomous goal mode | Goal execution | Terminal and service-backed turns | goal store and taskboard | evidence, pause, budget, and completion gates | README, FAQ, MO help and command palette | Progress can replan, pause and resume; completion requires evidence and respects the goal's budget. |
| `CAP-PRT` | Target-aware Project Review Team reporting and committed correction handoff | Project Review Team | Explicit Terminal `/prt` and trusted GitHub workflow | private review reports and worker progress | exact review target, trusted input, current diff, provider permissions, normal sandbox and verification, exact-event handling and publication boundaries | `/prt` and [PRT guidance](FAQ.md#what-are-goal-mode-and-prt) | Plain /prt reviews uncommitted work or HEAD; path/worktree targets report only, while commit/range targets can route confirmed repairs. Trusted GitHub review stays review-only. |
| `CAP-ROLES` | Persistent skill-backed conversation roles, project-bound specialist orchestration, and role-governed background/scheduled work | Conversation roles and specialists | Terminal, Desktop, scheduler, and background workers | private role packs and project rosters; active roles saved with sessions; book progress in the user-selected BOOK-STATE.md; workers are process-local while sessions retain work context | exact project binding, untrusted worker reports, declared role scopes plus ordinary sandbox/current-turn authority/confirmation | README, FAQ, `/role`, `role_work`, and shipped role packs | Roles restore with the conversation and respect project boundaries; specialist reports require verification and do not grant new authority. |
| `CAP-AUTOMATION` | Persistent plain reminders, scheduled turns, goals, roles, and scripts | Reminders and scheduled work | Terminal, headless service, resident Desktop scheduler and notices, and connected Dashboard Life | private schedules and run logs with one active scheduler per shared store | explicit scheduling; plain reminders use no model, Agent tasks use the configured model, and mail tasks require explicit mail requests | natural requests through `schedule_job`, Dashboard Life, FAQ and `/schedule` | Plain reminders use no model; Agent jobs use the configured model. Scheduling and mail work require the corresponding explicit request. |
| `CAP-VISUALIZE` | On-demand structured diagrams, terminal-native data visuals, native image display, optional image generation, Generate (images, video and music through Kie.ai), and local image transforms | Visualizations and media generation | Terminal, Desktop and Telegram through their display controls | private generated media plus explicit user-selected image files | saved-output provenance, sandboxed image paths, no generation in read-only work, bounded charts and cancellable generation | `/visualize`, `/show`, the Generate role and configured media tools | Output follows the receiving surface; optional generation needs its configured provider, while saved images and transforms respect path permissions. |
| `CAP-EXPLAINER` | Evidence-backed narrated explainer and focused product-video production | Explainer and product-video production | Agent-backed surfaces with local shell and artifact access | private `media/explainers` projects, project-owned hashed media, saved resolved style/status, and derived video artifacts | source links and factual review; verified narration/audio/assets; bounded muted clips; private outputs; saved MO style with project overrides; optional rendering/voice; deterministic motion/callouts; optional ducked, loudness-normalized music; SRT subtitles; recoverable paired publication; FFprobe validation and measured silent/narrated status | README and [explainer guide](core/explainer/README.md) | Reports measured production status and distinguishes silent from narrated output; optional voice and video encoding require their components. |
| `CAP-DESKTOP` | Persistent Desktop assistant, conversation with native inline cards and More/Normal/Never frequency, cube-anchored folder menus with cascading hover submenus, machine/screen help, same-project Terminal handoff for explicit software implementation, four-cube app launcher/running-window switcher with independent Shell instances, tray-activated session Focus mode with a freely movable lower-right cube expansion, a collapsible window list, native hover previews, Explorer tray access and reversible taskbar hiding, capability knowledge, read-only project checks, native Settings for project LSP/graph preferences, surface/instance models, declared Agent preferences and shared visuals, native Phone workspace with setup checks and one-use companion QR, and the Inventory basket of everything MO did with you | Desktop companion | Desktop | separate Desktop conversation and shared active visual settings; software implementation belongs to a live/new Terminal or explicitly active Project Architect; private project LSP preferences | intent, observation and approval controls; exact same-project handoff; coordinated startup readiness; consistent visual settings and in-place window updates; hovering an app never launches it | `/desktop`, [README](README.md), [FAQ](FAQ.md), launcher and tray controls | Inline cards follow More/Normal/Never settings; folder menus anchor to the cube and open cascading submenus on hover. Hovering an app never launches it. |
| `CAP-SYSTEMCARE` | Scoped machine/MO/host/project inspection, MO Care's model-free watch of MO's own background work, Windows maintenance plans, compact Game Session, global Game Mode and verified recovery | MO Care and Game Session | Deferred Agent tools and MO Desktop/Dashboard | private device-local care records, bounded logs, coordinated operations and Game Mode recovery journal | read-only scans and limited declared registry inspection; exact fresh selection/calibration; originals saved before reversible changes; verified results; native updates/removal without force; Windows permissions and normal confirmation; resource snapshots on visible refresh; optional existing scheduler automation | Desktop MO Care and [FAQ](FAQ.md) | Scans are read-only. Changes require a fresh exact plan and confirmation; recovery reports actual restoration and partial failures. |
| `CAP-DESIGN` | Self-contained visual concepts, project-aware refinement, bounded per-message attachments, shared user/MO Board clarification, Board-only exact-terminal or saved-unbound Desktop windows, discoverable Board keyboard controls, canonical saved Terminal provider/model selection with visible configured failure-triggered fallback, live native preview, non-mutating revision selection/download/delete, exact-terminal Board reads and draft-decision return, actionable exact-design completion notices, and explicit implementation handoff | Design Studio and shared Board | MO terminal and Desktop, autonomous background turns, and new visible terminal turns | private saved designs and portable Board content, bounded revision snapshots, immediate working/draft state, previews, attachment metadata and Design conversations; one Board binding per terminal and exact-design focus | bounded declarative visuals and geometry; accidental-overlap rejection; explicit draft review; complete visual/Board updates; decisions return to the exact terminal without starting a goal; authenticated private launch; current-revision deletion protection; finished, changed and observed visual evidence; partial results labeled unaccepted; Board clarification can remain read-only; opaque untrusted attachments; shared credentials without exposing them; configured reactive fallback; sandboxed previews, opt-in scripts and guarded Handoff | README and Desktop Design guide | Ordinary Send continues the visual conversation. A visual update needs a changed, observed revision; Handoff explicitly authorizes implementation, and unaccepted drafts block it. |
| `CAP-COMPUTER` | Target-owned desktop perception, optional Connected Chrome-tab diagnosis and actuation | Computer controls and Connected Tab | Terminal and Desktop through explicit agent tools; MO Design receives observation only | exact application/window and Chrome-tab targets, bounded target images, viewport evidence and current observation/lease state | ordinary Chrome tabs attach on demand; user stop is respected; private/protected/other-debugger tabs are excluded; authenticated local connection and limited browser operations; native fallback grants no DOM access; no second browser/profile; best-effort covered-window capture without activation; minimized windows rejected; current-turn action authority; fresh action evidence or an explicitly unknown result; stale/foreground checks for raw input; strict key input; bounded no-progress stop, cancellation and high-impact approval | `computer_targets`, `computer_observe`, `computer_act`, README, FAQ, Connected Tab guide and Desktop guides | Actions target the observed window or tab and report their evidence or an unknown result. User stop and high-impact approval remain effective. |
| `CAP-VOICE` | Optional local Desktop speech and Voice Chat with transcription-to-answer and first-audio latency evidence | Desktop voice | Desktop | private optional voice runtime and settings | explicit activation, local processing, accepted-final speech for tool-backed work, separate Voice Chat reply stream, speaking state begins with actual audio and redacted diagnostics | Desktop guide | Optional local voice starts explicitly. Voice Chat streams its conversational replies; tool-backed MO work speaks the accepted final. Speaking reflects produced audio. |
| `CAP-REMOTE` | Everywhere hub, pairing, overview, jobs, native clients and an optional local ChatGPT Desktop MCP connection for MO work | Everywhere hub and paired devices | service, Android, Desktop coordinator and locally configured ChatGPT Desktop | hub registry and separate private device credentials | exact device scopes, revocation, TLS, role checks and existing work approvals | README, FAQ, [Everywhere guide](mo_everywhere/README.md#use-mo-from-a-local-chatgpt-desktop-mcp-connection) | Devices receive only granted scopes; pairing can be revoked and hub credentials are not copied to clients. ChatGPT Desktop uses dedicated local STDIO pairing, separately from Codex provider sign-in; actual ChatGPT UI acceptance remains pending. No public directory, web/mobile or conversation-sync integration is claimed. |
| `CAP-LIVE-CONTROL` | Native remote Desktop and terminal presentation | Remote Desktop and terminal control | Android controlling Desktop or terminal | stable host key, live connection, session lease | `control`, `remote_control`, distinct `remote_host`, local consent | [FAQ](FAQ.md), Desktop, Everywhere and [Android guide](ANDROID.md) | A live exact-host lease and local consent are required; reconnecting does not authorize another host or session. |
| `CAP-ANDROID` | Hub requests, resident controls, phone capabilities available in the installed release, and separate This phone provider chat | MO Everywhere for Android | Android | Keystore-protected separate Hub/phone credentials, app-private state, Android services | exact scopes, local consent, keyguard, capability and confirmation gates | README, FAQ, and [`ANDROID.md`](ANDROID.md) | Features depend on the installed release and Play channel; Hub and This phone modes keep their credentials and capabilities separate. |
| `CAP-PUBLISHER` | Optional public product/support/privacy/deletion pages and Android AI-report admission | Public support and report handling | public web pages and Android report submission | independent private publisher home and report store | separate Hub/Agent permissions, bounded consented reports, durable receipts and private review/deletion; retention depends on the operated purge schedule | Published support, privacy and deletion pages; [Android reporting](ANDROID.md) | Report submission is consented and separate from Hub access; availability, retention and deletion follow the operated service's published policy. |
| `CAP-PHONE` | Optional phone semantic, file, system, and advanced operations when the installed Android release exposes them | Consented phone operations | Android and Agent through hub | phone capability ledger and local grants | default-off consent, fresh evidence returned by each action (an unconfirmed result is stated; no forced verification round), exact later-turn confirmation | FAQ and [`ANDROID.md`](ANDROID.md) | Only exposed, locally granted phone capabilities are callable; consequential actions require exact later confirmation and unconfirmed outcomes are stated. |
| `CAP-PHONE-INPUT` | Phone Trackpad: cursor, typing, window switching and pressure-aware Board pen input from the Android app | Phone Trackpad and Board pen | Google Play and Direct Android builds with Desktop over an authenticated ADB tunnel; foreground MO Design/standalone Board for pen input | adb-served pairing state, private input preferences, and one local bounded session | operator-initiated pairing, one-use Desktop nonce, loopback-only service, exact indexed stylus routing, finger/palm exclusion by default, bounded actions, exact foreground Board target, no credential exposure | Desktop Phone setup and [Android guide](ANDROID.md) | Pair from Desktop for a bounded local session; pen input targets the foreground Board, with fingers and palms excluded by default. |
| `CAP-TELEGRAM` | Telegram remote conversation and approvals | Telegram conversations | Telegram | isolated Telegram sessions and queue | one poller, pairing, bounded approvals, no provider key | README, FAQ, MO help and command palette | Paired remote chat uses isolated sessions and bounded approvals without receiving the provider key. |
| `CAP-MAIL` | Gmail and Connected Tab Outlook connection guidance, inbox/search/read/draft/send and explicit move/archive through Agent chat; bounded review of one or both accounts and generic Gmail Desktop notices | Mail connections and actions | Terminal, Desktop chat, authorized Hub chat, connected Dashboard | Gmail encrypted private OAuth token and bounded sync state; Outlook signed-in Chrome tab; on-demand setup uses the canonical private credential file and existing bridge | explicit mail request, configured-model read/review, ephemeral saved turns, and later-turn exact send/trash approval | `/mail`, [mail setup](core/mail/README.md) and [Connected Tab guide](clients/chrome/README.md) | Outlook search may fail when its hidden-tab list is unrendered. Live move, send/delivery, public OAuth verification and published Chrome availability are not yet established. |
| `CAP-LIFE` | Confirmed commitments, personal cases with dated updates, optional payment plans, operator-recorded income/outgoings with optional commitment links, local visible-mail wording groups, and scheduled-task controls | Life records, money and schedules | Connected Desktop Dashboard and live Agent chat | private revisioned commitment/case and money records; Gmail source ID only when confirmed; exact decimal monthly totals per currency and derived installment counts; separate scheduler store | direct operator confirmation, case resolution only by operator, no automatic mail-to-profile learning, no bank-balance claim, and explicit separate reminders | [Life guide](core/life/README.md), connected Dashboard and Agent tools | Confirm a plan before saving; record linked outgoings and view charts/progress. Totals describe your records, not a bank balance; only you resolve a case. |
| `CAP-FILES` | Cross-machine file browsing and organization | MO Files | Desktop, Android, connected hosts | selected machine's filesystem and Trash | exact browse/manage scopes and path/privacy policy | README, FAQ, surface guides | Browse only exposed locations on the selected machine; management permissions, privacy bounds and recoverable Trash remain source-local. |
| `CAP-TRANSFER` | Resumable verified file cargo | File transfer | Terminal, Desktop, Android, Telegram | transfer ledger, sender custody, hub spool, target receipt | exact transfer scope, chunk/hash verification, bounded custody | README, FAQ, MO help and command palette | Interrupted transfers can resume; success depends on verified content and a target receipt, not merely a completed upload. |
| `CAP-ATTACHMENTS` | Bounded turn attachments and media catalog | Message attachments | Agent-backed surfaces including MO Design | private attachment catalog with reserved destination paths and coordinated provenance records | eight-file/20 MiB turn bounds, stable non-rebindable IDs, contained saved paths, opaque surface references, and untrusted-content handling | FAQ and surface guides | A turn accepts at most eight files and 20 MiB; attachments remain untrusted evidence with stable references. |
| `CAP-MCP` | External MCP tool integrations | Configured MCP integrations | Agent and separately configured controllers | configured integration processes managed by MO | server exposure policy, configured exact tool allowlists, sandbox and scoped child credential injection | configured server tools through `tool_search`, README and FAQ | Only permitted tools from configured servers are exposed; child processes receive scoped credentials rather than the entire credential store. |
| `CAP-HEADLESS` | Resident service composition | Resident MO service | headless service | private service state and explicitly mounted private home in containers | one service per resource, degraded-state reporting, non-root containers and read-only application files | [README](README.md) and service status | Component readiness and degraded state are reported. Container use requires separately configured private storage and the available deployment route. |
| `CAP-PRESENTATION` | Terminal stream, theme, hints, true-worker activity, and skin controls | Terminal presentation | Terminal | private presentation preferences and current runtime status | presentation controls do not change task evidence | interface guide and MO help and command palette | Stream, theme, hints and activity change presentation; they do not alter task completion evidence. |
| `CAP-TERMINAL` | Explicit internal terminal plus local and MO-host split-terminal workspace lanes | Terminal workspace | Terminal | exact local terminal process or request-bound remote control lease, plus pane/focus/draft state | explicit action; exact-process or exact-lease input/interrupt/close; bounded terminal output; fresh MO identity for new panes; intended host profile preserved through persistent tmux; existing Everywhere scopes; no duplicate starts from one request; failed cleanup is reported | interface guide and MO help and command palette | Input, interrupt and close target the exact local process or remote lease; remote drafts and local terminal keys remain separate. |
| `CAP-SHELL` | Windows floating cube shell for one canonical MO terminal and one explicitly selected window, keyboard and Windows accessibility controls, and live attachment-title context even in lightweight chat | MO Shell workspace | MO Shell | session layout and a reversible snapshot of the selected window | one external window, explicit managed/embedded mode, shared Desktop skin and temporary window binding; a window title is untrusted availability context, not observed content | [MO Shell guide](mo_shell/README.md) | One selected external window shares the adjustable workspace; group hide/restore and managed embedding preserve its restorable layout. |
| `CAP-EXIT` | Clean interactive shutdown | Interactive shutdown | Terminal | current process lifecycle | no remote lease may invoke local exit | MO help and command palette | Exit closes the local interactive process cleanly; a remote control lease cannot invoke local exit. |
| `CAP-MIGRATION` | Read-only-first migration from supported agent footprints | Agent-footprint migration | Terminal intent and tools | destination profile state and explicitly selected project rules after approved apply | credentials excluded, provenance-marked append, existing skill files preserved and imported skills inactive pending review/promotion | `migrate` inspection/plan/apply, README capability contract | Inspect and plan before approved apply; credentials and existing skills stay protected and imported skills remain inactive pending review. |
<!-- mo-capabilities:ledger:end -->

## Command coverage

These command roots map to the capabilities above. Public commands are
discoverable through MO's help and command palette; availability still depends
on the relevant surface and configuration.

| Command | Class | Capability |
| --- | --- | --- |
<!-- mo-capabilities:commands:start -->
| `/help` | public | `CAP-ORIENT` |
| `/init` | public | `CAP-SETUP` |
| `/doctor` | public | `CAP-SETUP` |
| `/credentials` | public | `CAP-SETUP` |
| `/mail` | public | `CAP-MAIL` |
| `/update` | public | `CAP-SETUP` |
| `/exit` | public | `CAP-EXIT` |
| `/clear` | public | `CAP-SESSION` |
| `/status` | public | `CAP-ORIENT` |
| `/now` | public | `CAP-ORIENT` |
| `/dashboard` | public | `CAP-ORIENT` |
| `/usage` | public | `CAP-ORIENT` |
| `/heartbeat` | public | `CAP-CONTINUITY` |
| `/everywhere` | public | `CAP-REMOTE` |
| `/send` | public | `CAP-TRANSFER` |
| `/transfers` | public | `CAP-TRANSFER` |
| `/telegram` | public | `CAP-TELEGRAM` |
| `/structural-graph` | public | `CAP-GRAPH` |
| `/knowledge` | public | `CAP-GRAPH` |
| `/model` | public | `CAP-PROVIDERS` |
| `/projects` | public | `CAP-SESSION` |
| `/new` | public | `CAP-SESSION` |
| `/profile` | public | `CAP-PROFILE` |
| `/learning` | public | `CAP-LEARNING` |
| `/goal` | public | `CAP-GOAL` |
| `/prt` | public | `CAP-PRT` |
| `/role` | public | `CAP-ROLES` |
| `/schedule` | public | `CAP-AUTOMATION` |
| `/visualize` | public | `CAP-VISUALIZE` |
| `/show` | public | `CAP-PRESENTATION` |
| `/desktop` | public | `CAP-DESKTOP` |
| `/hints` | public | `CAP-PRESENTATION` |
| `/activity` | public | `CAP-PRESENTATION` |
| `/workspace` | public | `CAP-TERMINAL` |
| `/terminal` | public | `CAP-TERMINAL` |
| `/undo` | public | `CAP-SESSION` |
| `/retry` | public | `CAP-SESSION` |
| `/session` | public | `CAP-SESSION` |
| `/resume` | public | `CAP-SESSION` |
| `/game` | public | `CAP-GAME` |
| `/reload` | public | `CAP-SETUP` |
| `/settings` | public | `CAP-SETUP` |
| `/skin` | public | `CAP-PRESENTATION` |
| `/skills` | public | `CAP-SKILLS` |
<!-- mo-capabilities:commands:end -->

## Behavior reference

What each capability does in practice, area by area.

### Evidence-backed work

- Tracked tasks with reusable procedures or model-authored plans.
- Evidence-gated `complete_task`; failed checks stay blocked.
- Sandboxed file, shell, browser, desktop, phone, network, and MCP dispatch.
- Completion, verification and safety checks before a final answer, including
  unsupported claims about current state.
- Exact confirmation boundaries for destructive, publication, payment-like, or
  high-impact actions.

### Coding and project intelligence

- Read, search, edit, shell, Git status, test execution, and project inspection.
- One persisted structural graph with bounded search, caller/callee,
  neighborhood, path, explanation, and impact views.
- `mapthis` partitioned whole-project orientation without loading every source
  file into one model context.
- Project-work turns resolve the applicable ancestor-to-project `AGENTS.md` rule
  chain before provider work. If none exists, MO previews a minimal starter and
  creates it at the project root on a permitted project edit or execution, subject
  to the existing write permissions. Inspection leaves the project unchanged.
  Before reporting, changed rules are supplied for review; unchanged rules stay silent.
- `/knowledge` inspects and queries private project knowledge: document headings,
  capabilities, commands and current graph nodes with source references. It
  refreshes automatically in the background, coordinating multiple MO instances.
  Status, query, Dashboard and `code_search` remain read-only and show refresh
  progress instead of requiring a manual rebuild. Project turns receive a bounded
  relevant slice with selected documents reverified before use. A listed test
  filename does not establish that the test ran.
  See [knowledge scope](FAQ.md#what-is-knowledge).
- Optional LSP diagnostics, code-structure compaction, and recoverable source
  skeletons for long sessions.
- Inline terminal tables, charts, trees, images, and generated visual assets.
- Optional [native Generate through Kie](core/media/README.md): Suno music,
  Seedream images and Seedance video, using the existing Desktop composer role
  and shared Terminal tools. Setup, temporary-reference privacy, saved results,
  continuation requirements and provider-verification limits are documented there.
- First-party explainer-video projects turn sourced research and explicit local
  product media into narrated explainers or focused product demos with saved
  MO-system/project styling, hashed images and bounded clips, readable callouts,
  deterministic motion, optional Piper voice, FFmpeg encoding, FFprobe output
  validation and measured status through `mo --explainer`. Those optional
  components must be available in the chosen setup.
- MO Design turns a conversation into a self-contained declarative `.modesign`
  concept or interactive prototype (the default for new app/product/showcase
  requests unless the user asks for a static concept),
  preserves the user's full request under one observation-first visual contract,
  keeps the last finished visual stable while a new revision is generated, and
  restores each design's private conversation and optional project binding when
  switching sessions. Its shared **Board** lets the user sketch with pen pressure
  and simple shapes while MO proposes visible declarative drafts that only the
  user can accept or reject. Explicit diagram requests open a Board-only,
  pin-or-close window bound one-to-one to the exact originating terminal, while
  Desktop can open the saved Board unbound when no terminal is available. Board
  tools expose keyboard shortcuts, and MO receives bounded clear-placement
  evidence with accidental overlap rejected before a draft is shown;
  useful spatial or repeatedly unresolved clarification can offer it once without
  nagging. A compact runtime-only badge shows the exact originating terminal;
  on the next drawing follow-up MO reads the current Board, while Accept/Reject
  returns only the draft decision. Board chrome does not start another turn or
  grant implementation approval. A selected project is mapped from current source into the
  preview, but the design lane can write only the private Design artifact—not the
  project. Its MO-style window chrome matches the other Desktop utility surfaces.
  A failing preview interaction is captured once per revision for background
  diagnosis. Project implementation requires
  explicit authority: Studio uses **Handoff**, while a Terminal already discussing
  that exact design may act on the user's implementation instruction after
  verifying the revision and target. Ordinary **Send** continues the
  visual conversation. The eventual Handoff sheet recaps the actual saved
  concept and asks only which MO recipient should receive it; that explicit
  action continues as a normal turn in the exact connected terminal, starts a
  headless background goal, or opens a goal in a new visible MO terminal.
  Background requires a selected safe project, while an interactive receiving
  terminal can confirm an otherwise unresolved target before writing.
  Current-surface work observes available pixels before updating and never invents
  live state or treats deferred refinement as a present change. MO visually checks
  the rendered preview before finishing, with at most one correction and one
  re-observation. Background work can publish a cube, tray or platform completion
  notice. Design reuses Terminal's saved `/model` selection without a separate
  Studio chooser or per-design model setting. Its native attachment picker can add bounded files, images,
  or documents to one Design message as untrusted evidence without exposing the
  original path to generated content. A requested visual update requires a newer
  changed visual after the worker finishes. Questions may finish with an explicit
  conversational response; an explicit brief-only update requires changed brief
  fields. Arbitrary metadata cannot prove visual completion; missing required
  evidence produces a retry state. Activating a completion notice opens the exact
  saved design in the existing Studio window instead of creating another renderer.
  Its revision badge opens bounded selectable history: choosing an older version
  previews it without creating another revision, and that exact version can be
  downloaded, handed off, or explicitly deleted when it is not current. The
  compact **+** starts a clean isolated Design session, and refresh safely reloads
  the finished preview.

### Durable workflows

- Saved sessions, explicit resume, retries, undo, project history and structured
  closeouts. A fresh terminal remains isolated. The latest saved user request from
  the same surface can provide redacted historical context for follow-ups without
  silently reopening that conversation. MO saves the active request before its
  first provider call; after a process or power loss, continuity can distinguish
  an interrupted request from an older completed transcript.
- The Ctrl+B workspace rail loads profile projects, expands their local or host
  terminals, and switches live project groups without stopping their processes.
  New terminals use the selected project's directory; existing MO sessions keep
  their original project. The UI has no combined pane-count cap; host launches
  retain the serving supervisor's resource limit. Local PTYs own raw interactive
  keys; a focused host tile keeps the visible
  composer and sends one complete message on Enter. Host drafts remain separate,
  MO slash commands remain owned by the original main pane, and host panes reuse the existing
  Everywhere terminal and Live Control connection. A new **MO host** pane always
  starts on the serving host; Desktop terminal actions remain separate for
  portable and external-control callers. New host terminals use the intended
  profile and project even with a persistent multiplexer; controller and provider
  credentials remain separate. `Alt+Up/Down`
  scrolls each pane's own bounded history or full-screen app navigation;
  `Alt+Home/End` jumps to that pane's oldest/newest available output, and
  `Alt+F` toggles the focused pane between the grid and the full workspace. The
  Health view shows a quiet snapshot of machine resources and attached terminal
  process trees, adding a local total only when multiple local trees make it
  useful. The selected provider stays visible while `/status` owns the detailed
  runtime report. Connected hosts supply bounded
  CPU/memory observations through the authenticated status/socket path without
  claiming a control lease; older hosts show unsupported readings. Each MO pane
  shows a bounded, redacted title with its first request, task progress, instance
  and provider/model where available, plus its working spinner. Older MO terminal
  titles remain attachable.
  The Ctrl+B rail prioritizes that session topic while the stable instance ID still
  owns routing, and each pane keeps one of six skin-owned title accents. The
  rail also keeps readable terminal roles, pane positions and compact state markers, with
  each project row labeled with its source; nested terminals inherit that source.
  In split mode, MO's status, hints, composer and footer stay inside pane 1;
  local PTYs keep their own terminal screen chrome without an added MO composer.
  Ctrl+B shows or hides the rail. Opening it refreshes terminal discovery and host
  observations once; leaving it open does not poll hosts or repaint on a timer.
  Opening Health requests one local snapshot.
- Explicit `/session share <name>` and `/session unshare <name>` control which
  named conversations may appear on scoped Everywhere clients; local sessions
  are never published automatically.
- Autonomous goal mode with progress, evidence, replanning, pause, and resume.
- Evidence-gated [PRT review](FAQ.md#what-are-goal-mode-and-prt) with deterministic local checks, bounded
  affected-test evidence, durable reports, and a trusted GitHub Actions review
  lane. Plain `/prt` selects uncommitted work when present, otherwise HEAD.
  Worktree/path targets report only; commit/range targets send
  confirmed violations to an Agent worker for correction and
  focused verification, followed by reassessment until the target is met or
  further progress is blocked with a reason and actual score.
- Persistent scheduled turns, goals, roles, and approved private scripts.
- Profile-authored skill-backed roles govern interactive conversations and background work. `Project Architect` coordinates project-bound specialist packs through MO's existing worker runtime; `Book Writer` keeps the user's library in `LIBRARY.md` in their chosen books folder, resumes each book from its `BOOK-STATE.md`, and follows the user's chosen production format. Role packs do not replace MO's sandbox, authority, or confirmation gates.
- Optional resumable cross-surface file cargo with stable device targeting,
  sender retry custody, chunk and whole-file verification, and target receipts.
- Bounded MO Files views on Desktop and Android discover the current hub, each
  connected Desktop or terminal host, and the separately
  consented phone as explicit sources. A selected machine exposes opaque
  MO-home/allowed-project or approved-Personal locations; it exposes
  drive roots only when that machine already runs with `access.mode: full`.
  Compact multi-selection, revision-safe edit/rename/copy/move, recoverable
  Trash, dynamic device send and Incoming/Outgoing progress remain attached to
  the selected source and transfer. Desktop's WebView board also offers an
  explicit, short-lived local browser QR transfer for devices without MO;
  that LAN HTTP connection requires a trusted private network.
- Windows Desktop Settings, tray, MO Files, MO Phone, MO Design, dialogs and
  pointer surfaces share the active skin, four-cube branding and visual settings.
  Android uses its native mobile presentation. Settings controls the skin,
  panel/button geometry, and shared window-effect type and intensity. Changes
  repaint open windows while their companion, startup, file, transfer, phone and
  Design work continues.
- MO SystemCare performs device-local Windows calibration and read-only Safe or
  Advanced health/startup/performance/gaming diagnostics on demand. Its compact
  Game Session extends the reversible global Game Mode journal with calibrated
  start/current resource context, verified restoration, a Care-cube shortcut and
  a dimmed single-cube active control; it adds no telemetry worker and does not
  stop services or close applications. Cleanup is limited to catalog-owned user
  targets and requires a fresh exact plan, revalidation, explicit non-undo
  acknowledgement, and later-turn confirmation; it has no generic registry
  cleaner and never inspects `personal/`.

### Private personalization

- Curated profile facts, approved learning, materialized local skill packs, and
  query-ranked episodic recall. Owned project, repository, and server inventories
  remain profile-owned even when phrased with temporal modifiers such as `now` or
  `today`; their bounded context prioritizes canonical query-matched facts without
  duplicating the full facts file. Operator-defined terms are profile-owned and
  are expanded before intent, approval, and first-call tool routing; they are not
  project-local commands.
- Durable operational facts trigger the profile-capture capability on the first
  provider call, while visible conversation is stripped from trusted surface
  wrappers before episodic indexing.
- Authorized private Telegram turns participate in the same memory and learning
  path as other direct operator conversations. Telegram group and supergroup
  turns remain isolated from owner learning.
- Confirmed learning has one active form: an installed local skill takes
  precedence over equivalent generated guidance, including duplicate accepted
  suggestions. Retired generated skills and their source suggestions remain
  inactive across restart, review and bundle transfer. Promoting a workflow
  creates its skill; a pending suggestion does not activate behavior.
- A skill receipt appears only when its guidance was actually delivered within
  the turn's context budget. The Terminal shows its name once on the matching turn,
  and that fact remains available for follow-up. Delivery does not prove the
  provider followed the guidance. Later explicit corrections or positive feedback
  settle the pending outcome; unrelated or neutral replies do not count as success.
- Relative follow-ups reuse the current conversation's task for relevant profile
  rules, skills and recall, including after session reload. New subjects use their
  own query. New learned code conventions belong to the current project and apply
  to their declared files or an explicit name request within that project. The
  Dashboard labels older unbound conventions as cross-project. Recalled excerpts
  retain source IDs and fit individually within
  the context budget; prepared material remains distinct from delivered guidance.
- `/learning` opens a selectable Terminal review queue across ordinary suggestions
  and workflow habits. Select an item to see what changes, why, and its example or
  source guidance; use Tab or Left/Right to choose **Approve**, **Dismiss**, or
  **Back**, then Enter. Up/Down scrolls details. Pending items remain inactive;
  **Active learning** explains accepted suggestions and offers **Undo learning**.
  Actions bind to the reviewed item, not its queue position; changed items require
  a fresh review. Existing numbered/text commands remain callable, but the menu
  needs no copied IDs. [Automatic learning and review](FAQ.md#does-learning-need-my-approval-every-time)
  explains which patterns can activate without another approval.
  `/learning status` shows the current setting and
  `/skills` lists materialized workflow skills. Optional scans, consolidation and
  skill imports sit under **More actions**; they are not routine learning steps.
  `/profile mine` uses the same effective active/pending review as `/learning`.
- Local SQLite/FTS5 memory with optional local or configured semantic fusion.
- One bounded, Cube-branded Dashboard provides user and operations views across
  Terminal, browser/read-only HTML, Desktop and Android.
  `mo --dashboard` or `/dashboard show` opens the connected frameless MO workspace
  through the optional native WebView interface. Work commands open in the normal
  MO terminal; the Dashboard has no embedded terminal or voice UI. Rules edit
  their MO Files source; learning review and project LSP controls act on their
  existing settings, and knowledge queries use `/knowledge`.
  See the [Dashboard guide](core/dashboard/README.md).
  It shows direct work/learning state, counts, provenance, and existing
  navigation actions without inventing a confidence percentage. Portable projections
  exclude profile prose, memory text, learning text, prompts and credentials;
  authenticated local learning review reveals the selected item's source details. In the
  Terminal, durable and actionable learning-lifecycle events appear in the
  persistent transcript activity lane; the compact footer remains reserved for
  short operational notifications and does not duplicate those learning
  messages.
- Local Dashboard Project checks exposes sampled verification evidence; its
  LSP control saves on/off per current project using normal private preferences.
  Configure installed servers in `lsp.servers`; no auto-install or extra model
  is involved. See the [LSP guide](FAQ.md#can-i-control-language-server-checks-per-project).
- The Dashboard does not provide unrestricted editing of raw memory, learning,
  prompts or task evidence; their normal review and approval controls apply.
- Interactive Terminal defaults are a separate, private personalization layer:
  `/model`, `/show`, `/hints`, and `/activity` update one `preferences.json`
  overlay without rewriting the operator-authored `config.yaml` baseline.
  `/hints` controls curated one-line discovery tips; non-comment lines in the
  optional MO-home `hints.txt` replace the built-ins after MO restarts.
- `personal/` remains opaque and is never automatically indexed, synchronized,
  migrated, or deleted.

### Perception and actuation

- Screen capture, local-image and embedded-text PDF perception, Connected
  Chrome-tab DOM/viewport control, and native Windows UI Automation.
- An exact owned Windows window can provide whole-window or target-relative
  pixel evidence while covered, without MO activating it. Unsupported renders
  fail closed unless that same target is already foreground for the visible
  fallback; minimized windows are not supported. Raw pointer and keyboard input
  remains foreground-bound and may interrupt the active desktop.
- Observe the exact target before acting. An action uses that observation once;
  fresh action evidence or an explicitly unknown result determines what follows.
- Once MO Connected Tab is installed, MO discovers ordinary Chrome tabs and
  connects to the requested tab automatically on observation—no per-tab click.
  Terminal and Desktop retain their separate ownership; Design receives
  observation only. One native channel stays ready, and Chrome's stop control
  remains effective. MO opens no second browser or profile. Native-window
  interaction remains available when it satisfies the requested workflow.
  See the [MO Connected Tab guide](clients/chrome/README.md) for setup, updating,
  status, DevTools conflicts, and removal.
- Image generation, safe image transforms, and cross-surface image delivery.
- MO Everywhere for Android provides Store-delivered Hub chat, Dashboard,
  background work, Files, Desktop/terminal control, selected-folder sharing,
  and phone-only provider chat. See
  [Android availability](ANDROID.md#availability) before treating a current
  capability as generally available from the Store.

### Extensibility

- Lazy MCP servers with per-server `allow_tools` filters and scoped credential
  injection.
- Native, HTTP-compatible, OAuth, and local model providers.
- Custom skills, role packs, and profile-owned Desktop apps.
