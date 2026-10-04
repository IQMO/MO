# MO Agent product and capability contract

This is MO Agent's compact public product contract. It answers what MO is,
which surface owns each capability, where state and authorization live, and
what evidence must remain true when the capability changes.

The source files named here are runtime owners, not a second implementation.
[`MAP.md`](MAP.md) maps the repository in more detail, while the surface guides
explain user workflows and limitations.

## Product charter

- **MO Terminal** is the primary agentic engineering workbench. It plans,
  inspects, maps, edits, runs tools, verifies outcomes, and maintains projects.
- **MO Desktop** is the optional persistent assistant for ordinary life and
  work. It has its own conversation while reusing MO's Agent, Gateway, tools,
  evidence, profile, and authorization boundaries.
- **MO Everywhere** is the authenticated coordination hub. The native Android
  app is its mobile client and does not contain a second host runtime or receive
  hub-side provider credentials. Its separate **This phone** mode is direct
  provider chat with a separately entered encrypted key, not the MO host runtime;
  [the Android guide](ANDROID.md#install-and-pair)
  owns the mode boundaries.
- Android's public channel is Google Play. The native client, its tests, signing
  inputs, release automation, and owner-device builds remain in private local
  custody. This repository publishes the Hub protocol and user-facing
  [availability contract](ANDROID.md#availability), not Android source packages.
- **Telegram** and the headless service are separate reachability surfaces over
  the same runtime owners. Conversational surfaces stay isolated by default.
- A user may explicitly make one named conversation portable for Android.
  `core/session/` remains its only catalog and transcript owner; clients never
  receive an automatic list of other sessions.
- Cross-surface continuity separately shares bounded intent, outcome, and next
  step. It does not merge raw transcripts, tool payloads, screenshots,
  taskboards, or secrets into any conversation.
- Provider models are replaceable reasoning engines. Runtime code owns tool
  eligibility, sandboxing, confirmation, task evidence, result classification,
  persistence, and final-answer gates.
- Project-work turns also resolve the effective `AGENTS.md` contract through
  `core/context/project_context.py`. Missing rules are previewed; the tool
  dispatcher creates only a minimal starter at the project root on a permitted
  project action, respecting role and sandbox write permissions. Final reporting
  rechecks the rule chain and supplies changed sources for review. This detects
  file changes, not semantic compliance with every instruction. Learned conventions,
  graph orientation, and Dashboard projections remain separate owners.
- Product state is private by default under `~/.mo` or `MO_STATE_HOME`.
  `personal/` is opaque and is never automatically inspected, synchronized,
  migrated, indexed, or deleted.
- A capability is complete only when its owner, supported surfaces, state,
  security, discovery, and acceptance evidence agree. Compatibility paths need
  a documented migration reason and removal condition.

## How capabilities reach MO's work

This ledger describes the product; it is not the provider's tool catalog.
`core/knowledge/index.py` indexes its capability and command references alongside
project documentation. Relevant references can reach a turn through project
orientation or `code_search`; the complete file is not injected into every turn.

- **Instructions and routing:** `core/prompts/system.md` owns MO's behavior.
  Gateway resolves operator terms before intent classification;
  `core/runtime/turn_intent.py` and `core/runtime/capability_routing.py` select
  work/context policies and capability hints. A bare interface/UI investigation
  does not select screen observation; explicit visible targets remain supported.
  Project rules have the separate
  lifecycle described above.
- **Automatic context:** `core/agent/agent_turn.py` prepares relevant profile,
  prior conversation, recall, continuity, work-pattern, skill/catalog/convention,
  graph/history/knowledge, and surface/workspace context. Selection depends on
  the request, surface and configuration; `core/context/context_bridge.py`
  admits bounded content, retaining separate graph, documentation and history
  delivery receipts. Prepared content and delivered content are distinct.
  New project tasks recall past user requests without replaying MO's old answers;
  another session's task is not injected by recency. History and resume retain their context.
  Bounded graph orientation can use a labeled stale snapshot for navigation;
  impact and verification still require current evidence.
  User corrections retain completed tool calls/results; provider-history cleanup
  does not count as context pressure or trigger a retention handoff.
- **Model-selected tools:** `tools/definitions.py` declares built-in schemas;
  `core/tooling/tool_registry.py` exposes a task-selected catalog by default,
  with configured deferred and full-static modes also supported. `tool_search`
  lists the known catalog or searches and activates schemas for the next
  provider request. Listing itself selects no schemas; discovery can initialize
  configured MCP catalogs. Delivered graph, documentation or project-history context exposes its
  native search/history readers on engineering surfaces, independently of
  wording or profile-term classification. Agent surface, role,
  extension and taskboard filters still apply; dispatch owns authorization.
  Bounded read-only reviews retain project orientation without creating a plan.
- **Lifecycle work:** the existing project-index worker maintains graph,
  history and knowledge asynchronously when project, configuration and lane
  permit. Learning capture and turn closeout use their existing stores;
  heartbeat, scheduled work and background roles use their configured runtime
  owners. These are not all provider tool calls.
- **Verification:** `core/gates/post_provider_pipeline.py` applies the relevant
  project-rule, task-evidence, claim, edit and surface checks before completion.
  LSP checks edited files only when a server is configured and enabled for the
  project. Visiting a gate does not prove its underlying check ran.
- **Explicit controls:** slash commands, Desktop controls, Android grants and
  service setup activate their respective owners. Desktop turns receive the
  feature manifest in `mo_desktop/capabilities.py` through the existing surface
  policy in `mo_desktop/companion.py`; it is not the full product inventory or
  evidence of a running window.

For visible evidence, `/dashboard checks` and Dashboard **Checks & LSP** project
available monitor records for the selected conversation: context admission,
delivered recall entries, tool outcomes and verification. Terminal activity and
the compact admitted-context receipt provide additional turn-local evidence;
selected skill names stay in provider continuity. Establish use
from the exact turn's delivered context, provider catalog, actual tool/check
results and resulting artifact or surface; availability alone proves none of
these, and supplied instructions do not prove model adherence.

## Capability ledger

The rows between the markers are machine-checked. Keep every field non-empty
and route detail to the named authority instead of expanding this file into a
changelog.

| ID | Capability | Runtime owner | Surfaces | State owner | Security owner | Public discovery | Acceptance owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
<!-- mo-capabilities:ledger:start -->
| `CAP-AGENT` | Intent routing, automatic work context, agentic turns and planning | `core/agent/`, `core/gateway.py`, `core/runtime/turn_intent.py`, and `core/context/` | Terminal, Desktop, API, Telegram, service | surface session through `core/session/` | sandbox, context admission and post-provider gates | README, MAP, and the activation paths above | Agent, Gateway, context-delivery, task-evidence, and surface integration tests |
| `CAP-EVIDENCE` | Task truth, changed-file verification, configured LSP diagnostics and completion evidence | `core/tasking/`, `core/gates/`, `core/lsp/`, and Gateway finalization | all agent turns, subject to check eligibility and configuration | taskboard, task evidence and project LSP preferences | claim, consistency and final gates; disabled or unavailable diagnostics are not clean evidence | README, FAQ, and Dashboard Checks & LSP | taskboard, evidence, changed-file, LSP, claim, and final-gate tests |
| `CAP-TOOLS` | Filesystem, shell, web, repository access, local image/PDF perception and tool discovery/dispatch | `tools/`, `core/tooling/`, and `core/perception/` | Agent-backed surfaces | tool audit and result state | sandbox, approval, secret, network and injection gates; bounded local perception with optional embedded-text PDF extraction | `tool_search`, `perceive`, README and FAQ | tooling, catalog routing, perception, sandbox, result, and safety tests |
| `CAP-ORIENT` | Help, status, current work, canonical Dashboard, and usage | command registry plus `core/dashboard/` snapshot, projection, render and connected adapter | Terminal, native WebView/read-only HTML, Desktop, Android | existing taskboard/profile/learning/runtime/graph/Files/knowledge owners; Dashboard owns no business state | bounded redacted projection; authenticated local controls delegate to existing owners | README, MAP, FAQ, dashboard/surface guides and command registry | projection/render, connected HTTP/Files/knowledge, real browser/terminal, Desktop and Android boundary checks |
| `CAP-SETUP` | Initialization, official runtime and personalization diagnostics, credentials, updates, reload, and settings | CLI, `core/diagnostics/doctor.py`, `core/diagnostics/personalization.py`, credential broker, updater | Terminal, Agent tools, and service setup | private runtime home and config; diagnostics own no state | value-free read-only diagnostics, credential isolation, and separate apply/decision authority | README, FAQ, interface guide, and command registry | initializer, doctor/personalization, credentials, update, settings, and non-mutation tests |
| `CAP-SESSION` | Conversations, project history, natural prior-request orientation, compaction/handoff, save, switch, resume, retry and undo | `core/session/` plus its `core/runtime/continuity.py` projection | Terminal and Desktop with isolated owners | private session store; `SessionManager` remains the only catalog/transcript owner | surface isolation, retained user requests, secret-redacted historical-data handling, stable per-conversation selection and bounded persistence | README, FAQ, and command registry | session, handoff, continuity, agent-turn, project-history, persistence, and isolation tests |
| `CAP-GAME` | Explicit Game Collaboration project context, questions, decisions, proposals, and approvals | `core/game_collaboration/` plus the command handler in `core/agent/agent_slash.py` | Terminal only | private `memory/work/game-collaboration` records plus bounded session binding metadata | explicit activation, bounded context, revision-conflict checks, and no tool authority from a recorded decision or approval | interface guide and command registry | Game Collaboration model, store, service, context, command, and session tests |
| `CAP-PORTABLE` | Explicit portable named conversations | `core/session/sessions.py` plus Everywhere API adapters | Terminal and Android | private saved-conversation store; `SessionManager` is the only catalog/transcript owner | opt-in share/create, exact scopes, revocation, bounded visible-text projection, optimistic revisions | Everywhere and Android guides plus command registry | session, Everywhere, Android contract, and device tests |
| `CAP-CONTINUITY` | Bounded cross-surface work handoff | `core/state/continuity_events.py` and `core/state/surface_handoff.py` | Terminal, Desktop, Android, Telegram | continuity event store and surface binding | exact scopes, eligibility, privacy bounds | FAQ, MAP, Everywhere guide | continuity, heartbeat, Everywhere, and client contract tests |
| `CAP-PROVIDERS` | Provider, model, reasoning, bounded same-route transient recovery, and turn-local reactive fallback selection | `core/provider/` and Agent provider scope | Agent-backed surfaces | authored config plus the private Terminal preference overlay, provider/model-scoped capacity evidence, bounded route evidence, outer-turn tool-catalog continuity, and selected-route restoration | credential broker, catalog revalidation, shared-auth reload, pre-output one-retry auth/transient recovery, precise non-retryable classification, tool-loop isolation, and adapter boundaries | README, FAQ, prompt contract, and command registry | provider adapter, runtime-preference, auth/transient recovery, route-isolated capacity, repeated-tool non-fallback, route-context, catalog-continuity, reasoning, and conformance tests |
| `CAP-GRAPH` | Structural graph and BM25 search, source-linked project knowledge, code context, mapping, impact, redundancy diagnostics and Git history, with automatic index maintenance | `core/graph/structural_graph.py`, `core/graph/search.py`, `core/graph/history.py`, `core/mapping/`, `core/knowledge/`, and `core/diagnostics/redundancy_check.py` | Terminal, Agent, maintainer MCP | private per-project graph/history/knowledge caches, durable review findings, and generated human project map | bounded reads, source freshness, explicit private-source tracing, selected-route behavior, cancellation and coordinated writers; orientation never replaces source, tests or contracts | `code_search`, graph/history tools, `redundancy_scan`, README, FAQ, MAP, [history contract](core/MAINTAINING.md#history-finding-inputs), command registry | graph, history, source provenance, mapper, knowledge, MCP, freshness, writer coordination, cancellation, and resource tests |
| `CAP-PROFILE` | Curated profile facts, preferences and operator terms | `core/profile/` | Agent-backed surfaces | structured private profile plus declared profile Markdown files | private-home, locked persistence and redaction boundaries | `record_profile_fact`, README, FAQ, command registry | profile context, term routing, state layout, privacy, and sync tests |
| `CAP-LEARNING` | FTS5/BM25 episodic turn recall, optional semantic fusion, feedback and reviewable learning | `core/learning/`; current-task selection in `core/agent/agent_turn.py` and delivery in `core/context/context_bridge.py` | Agent-backed surfaces | private turn index, learning stores and suggestions | bounded retrieval/admission, prepared-versus-delivered evidence and policy-owned adoption; recalled entries are turn excerpts, not complete sessions | [personalization contract](core/MAINTAINING.md#personalization-and-project-boundaries), FAQ, command registry, Dashboard Learning and Checks | recall, follow-up/reload context, delivery budgets, learning, closeout, and owned-skill tests |
| `CAP-SKILLS` | Selected skills, discoverable skill catalog, scoped conventions and authored roles | `core/skills/` with delivery capture in `core/agent/agent_turn.py` and Terminal projection in `interface/turn_runner.py` | Task skills on non-Desktop Agent surfaces; explicit authored roles on Terminal and Desktop; local Dashboard source inspection | private profile skill roots, reconciled shipped seeds, opt-in project-local skills and bounded per-session delivered-name receipts | declared lanes/tools, approval, project-bound new conventions, explicitly unbound older conventions, delivered-context accounting, current-turn-only Terminal projection, and no inference that delivery proves provider adherence | [personalization contract](core/MAINTAINING.md#personalization-and-project-boundaries), FAQ, command registry, Dashboard Learning | skills, roles, literal/semantic/location selection, seed reconciliation, next-turn delivery context, Terminal receipt, persistence, and ownership tests |
| `CAP-GOAL` | Autonomous goal mode | `core/goal/` | Terminal and service-backed turns | goal store and taskboard | evidence, pause, budget, and completion gates | README, FAQ, command registry | goal lifecycle, auditor, and resume tests |
| `CAP-PRT` | Target-aware Project Review Team reporting and committed correction handoff | `core/review/` plus the existing Agent worker runtime | Explicit Terminal `/prt` and trusted GitHub workflow | durable review audit/worker state | target distinction, trust, diff, provider, normal sandbox/verification, exact-event consumption, and publication boundaries | [PRT contract](core/MAINTAINING.md#prt-review-contract), FAQ, interface notes, workflow README | PRT, review, committed-correction routing, worker, terminal, GitHub delivery, and privacy tests |
| `CAP-ROLES` | Persistent skill-backed conversation roles, project-bound specialist orchestration, and role-governed background/scheduled work | `core/skills/roles.py`, `core/agent/agent.py`, `core/agent/agent_slash.py`, `core/agent/agent_turn_dispatch.py`, `core/worker/`, and `mo_desktop/mologrthim/` | Terminal, Desktop, scheduler, and background workers | profile role packs own role contracts and project rosters; session metadata restores active roles; user-selected `BOOK-STATE.md` owns book progress; worker records are process-local while saved sessions/taskboards retain work context | exact project binding, untrusted worker reports, declared role scopes plus ordinary sandbox/current-turn authority/confirmation | README, FAQ, `/role`, `role_work`, and shipped role packs | role activation, project isolation, specialist registration/dispatch/report verification, session persistence, Desktop status projection, scheduler, book format/state, and command-help tests; native Desktop acceptance |
| `CAP-AUTOMATION` | Persistent plain reminders, scheduled turns, goals, roles, and scripts | `core/runtime/scheduler.py` | Terminal, headless service, resident Desktop scheduler and notices, and connected Dashboard Life | private schedule store and run logs shared through one singleton lock | explicit scheduling; plain reminders use no model, Agent tasks use the configured model, and mail tasks require explicit mail requests | natural requests through `schedule_job`, Dashboard Life, FAQ and `/schedule` | scheduler, service, Desktop startup/stop, Dashboard route, native save/run, and recovery checks |
| `CAP-VISUALIZE` | On-demand structured diagrams, terminal-native data visuals, native image display, optional image generation, and local image transforms | `core/visualize/`, `core/imagegen.py`, and `core/imageedit.py` | Terminal, Desktop, and Telegram through surface-owned display adapters | private generated media plus explicit user-selected image files | exact producer-marker provenance, registered ANSI artifacts, sandboxed image paths, read-only-lane exclusion for producers, bounded chart data, and cooperative generation cancellation | prompt contract and command registry | visualization, marker-channel, sandbox, routing, generation, editing, and surface-delivery tests |
| `CAP-EXPLAINER` | Evidence-backed narrated explainer and focused product-video production | `core/explainer/`, the installed `mo --explainer` launcher route, and the shipped explainer-video skill in `core/skills/seeds/` | Agent-backed surfaces with local shell and artifact access | private `media/explainers` projects, project-owned hashed media, saved resolved style/status, and derived video artifacts | source-link validation plus provider factual review, narration/audio/asset hashes, bounded muted clips, private-state output, canonical saved MO styling with project overrides, lazy optional render/voice imports, deterministic frames/callouts/pan-zoom/crossfades, an optional ducked music bed with loudness normalization, SRT subtitles, recoverable staged pair publication, mandatory FFprobe checks, measured progress, and explicit silent-versus-narrated reporting | README and [explainer guide](core/explainer/README.md) | explainer schema, quality, storage, media integrity, styling/templates, narration failure, rendering/FFprobe/cancellation, non-checkout launcher, skill-selection, and visual-inspection tests |
| `CAP-DESKTOP` | Persistent Desktop assistant, conversation, machine/screen help, same-project Terminal handoff for explicit software implementation, four-cube app launcher/running-window switcher with independent Shell instances, tray-activated session Focus mode with a freely movable lower-right cube expansion, a collapsible window list, native hover previews, Explorer tray access and reversible taskbar hiding, capability knowledge, read-only project checks, native Settings for project LSP/graph preferences, surface/instance models, declared Agent preferences and shared visuals, native Phone workspace with setup checks and one-use companion QR | `mo_desktop/` over Gateway plus `interface.desktop_ui`/`desktop_widgets`; exact implementation handoff reuses `core.design.terminal_handoff` | Desktop | isolated `mo-desktop` session plus one immutable active visual state; project implementation remains owned by a live/new Terminal or explicitly active Project Architect; project LSP uses canonical private preferences | desktop intent, observation, approval, matching lock/ready generation, exact same-project heartbeat target, strict palette/geometry roles and in-place window lifecycle; hover never launches apps | README, FAQ, Desktop guides, launcher/tray controls and `mo_desktop/capabilities.py` | Desktop runtime, implementation routing, capability knowledge, conversation, visual source-guard, lifecycle, native UI and persistence tests |
| `CAP-SYSTEMCARE` | Scoped machine/MO/host/project inspection, Windows maintenance plans, compact Game Session, global Game Mode and verified recovery | `core/systemcare/` plus `tools/systemcare.py`; `mo_desktop/systemcare/` is the native presentation adapter and the existing cube renderer supplies quick access/active state | Deferred Agent tools and MO Desktop/Dashboard | private device-local `memory/systemcare.sqlite`, bounded logs, cross-process operation state and the canonical Game Mode recovery journal | read-only scans, bounded declared registry inspection, exact fresh selection/calibration/digest, typed originals before reversible changes, verified receipts, no-force native updates/removal, selected Windows permission and normal confirmation gates; Game Session adds bounded resource snapshots and reuses the visible refresh rather than adding a telemetry worker; optional automation uses the existing scheduler | Desktop SystemCare guide, core/Desktop maintenance contracts and MAP | service/state/tools, native host lifecycle, Game Session projection, launcher shortcut/HUD, Desktop persistence/routing, scheduler pause/resume, stale evidence, partial failures and restoration tests |
| `CAP-DESIGN` | Self-contained visual concepts, project-aware refinement, bounded per-message attachments, shared user/MO Board clarification, Board-only exact-terminal or saved-unbound Desktop windows, discoverable Board keyboard controls, canonical saved Terminal provider/model selection with visible configured failure-triggered fallback, live native preview, non-mutating revision selection/download/delete, exact-terminal Board reads and draft-decision return, actionable exact-design completion notices, and explicit implementation handoff | `core/design/`, `tools/mo_design.py`, and `mo_desktop/design_studio/` | MO terminal and Desktop, autonomous background turns, and new visible terminal turns | private `media/designs`, portable `board/v1`, bounded snapshots, immediate Board working/draft sidecar, live preview, attachment metadata/session sidecar, authenticated command/completion state, exact terminal-context queue, one-Board-per-terminal runtime binding, and one-shot renderer focus under MO state | strict declarative schemas, bounded geometry/operations, deterministic spatial context plus default accidental-overlap rejection, explicit MO-draft review, atomic complete-visual/Board updates, exact non-goal terminal return, private launch token, current-revision delete protection, worker-finish plus changed-artifact and visual-observation completion evidence, truthful unaccepted-partial state, Board-only clarification exceptions within read-only project lanes, opaque attachment IDs and untrusted-content handling, credential-free shared model activation, reactive configured-selector fallback evidence, iframe CSP/sandbox, opt-in scripts, pending/draft Handoff guards, and advisory private visual preferences | README and Desktop Design guide | schema/service/Board/tool/provider/Desktop routing, standalone bound/unbound launch and release, spatial guard, keyboard controls, revision selection/export/delete, draft review/Handoff block, exact decision return, attachment import/routing, model selection/fallback boundary, partial and metadata-only false-completion, actionable-notice, exact-focus, and notification tests plus native renderer, interaction, pen, and hot-reload acceptance |
| `CAP-COMPUTER` | Target-owned desktop perception, optional Connected Chrome-tab diagnosis and actuation | `core/desktop/runtime.py`, `core/desktop/apps.py`, `tools/computer.py`, `tools/screen.py`, `tools/browser.py`, `core/browser_bridge.py`, and `clients/chrome/`; compatibility adapters route through the canonical computer façade | Terminal and Desktop through explicit agent tools; MO Design receives observation only | application/window refs plus exact Chrome-tab refs, target observations, bounded native-window/region renders, visible viewport evidence, stable signature, revision and lease state | ordinary Chrome tabs are discovered and the selected tab attaches on demand; user stop is respected, private/protected/other-debugger tabs are excluded; authenticated local bridge, bounded CDP allowlist, native-window fallback without DOM authority, no second browser/profile transport, best-effort occluded-window rendering without activation, minimized-window rejection, exact current-turn action authority, actions that return their own fresh evidence (no forced verification round; an unknown result is stated), stable window refs, raw-act stale/foreground rejection, strict key input, bounded no-progress stop, inline result observation, cancellation through transport and high-impact approval | `computer_targets`, `computer_observe`, `computer_act`, README, FAQ, Connected Tab guide and Desktop guides | canonical façade, admission, runtime, completion, UIA, window-render/region/pointer/drag/scroll, connected-browser, extension/native-host, and native acceptance corpus |
| `CAP-VOICE` | Optional local Desktop speech and Voice Chat with transcription-to-answer and first-audio latency evidence | `mo_desktop/voice/` and companion controller | Desktop | private optional voice runtime and settings | explicit activation, local process, accepted-final-only speech, first-PCM speaking state, redacted diagnostics | Desktop guide | voice protocol/state/timing unit tests and native audio/release acceptance |
| `CAP-REMOTE` | Everywhere hub, pairing, overview, jobs, and native clients | `mo_everywhere/` | service, Android, Desktop coordinator | hub registry and private device credentials | exact device scopes, revocation, TLS, and role checks | README, FAQ, Everywhere guide | Everywhere API, registry, Android, pairing, and deployment tests |
| `CAP-LIVE-CONTROL` | Native remote Desktop and terminal presentation | Everywhere broker plus Desktop/terminal host publishers | Android controlling Desktop or terminal | stable host key, live connection, session lease | `control`, `remote_control`, distinct `remote_host`, local consent | FAQ, MAP, Desktop, Everywhere, Android guides | broker, host, Android JVM, reconnect, and one final device pass |
| `CAP-ANDROID` | Hub requests, resident controls, phone capabilities available in the installed release, and separate This phone provider chat | `mo_everywhere/` plus the privately maintained Google Play client | Android | Keystore-protected separate Hub/phone credentials, app-private state, Android services | exact scopes, local consent, keyguard, capability and confirmation gates | README, FAQ, and [`ANDROID.md`](ANDROID.md) | public Hub/API contracts plus private client, release, and recorded-device acceptance |
| `CAP-PUBLISHER` | Optional public product/support/privacy/deletion pages and Android AI-report admission | `mo_publisher/` | public web pages, Android report submission and operator CLI | independent private publisher home and report store | separate from Hub/Agent authority, bounded consented reports, durable receipts and local-only review/deletion; retention also requires an independently operated purge timer | [Publisher guide](mo_publisher/README.md) and Android publication guide | publisher admission, custody, retention and page tests plus Android reporting and operated-deployment acceptance |
| `CAP-PHONE` | Optional phone semantic, file, system, and advanced operations when the installed Android release exposes them | shared Hub/tool gates plus the privately maintained Android client | Android and Agent through hub | phone capability ledger and local grants | default-off consent, fresh evidence, exact later-turn confirmation | FAQ and [`ANDROID.md`](ANDROID.md) | public Hub capability/approval contracts plus private client and device acceptance |
| `CAP-PHONE-INPUT` | Maintainer-local MO Phone cursor and pressure-aware Board input retained outside the current public Store client | `mo_desktop/phone/` plus the privately maintained Android client | Compatible private local client with Desktop and foreground MO Design/standalone Board | adb-served pairing state, private input preferences, and one local bounded session | operator-initiated pairing, loopback-only service, exact indexed stylus routing, finger/palm exclusion by default, bounded actions, exact foreground Board target, no credential exposure | Desktop guide; explicitly unavailable in the current Google Play client | public host view-model/protocol checks plus private client routing/settings and recorded-device acceptance |
| `CAP-TELEGRAM` | Telegram remote conversation and approvals | `core/telegram/` | Telegram | isolated Telegram sessions and queue | one poller, pairing, bounded approvals, no provider key | README, FAQ, command registry | Telegram Gateway, session, approval, and service tests |
| `CAP-MAIL` | Gmail and Connected Tab Outlook connection guidance, inbox/search/read/draft/send and explicit move/archive through Agent chat; bounded review of one or both accounts and generic Gmail Desktop notices | `core/mail/`, `core/agent/agent_turn_dispatch.py`, `tools/browser.py`, and `mo_desktop/companion_dashboard.py` | Terminal, Desktop chat, authorized Hub chat, connected Dashboard | Gmail encrypted private OAuth token and bounded sync state; Outlook signed-in Chrome tab; on-demand setup uses the canonical private credential file and existing bridge | explicit mail request, configured-model read/review, ephemeral saved turns, and later-turn exact send/trash approval | `core/mail/README.md`, Chrome guide, and command registry | focused mail checks and isolated normal-provider new-user Gmail connection chat; live natural-chat Gmail list/read/search/draft and Outlook list/read; current Dashboard Gmail/Outlook lists rendered natively; Outlook hidden-tab search can fail when its list is unrendered; move, send/delivery, public OAuth verification, and published Chrome distribution unverified |
| `CAP-LIFE` | Confirmed commitments, personal cases with dated updates, optional payment plans, operator-recorded income/outgoings with optional commitment links, local visible-mail wording groups, and scheduled-task controls | `core/life/`, `core/dashboard/`, `life_item`, `life_money`, and the existing scheduler | Connected Desktop Dashboard and live Agent chat | private revisioned commitment/case and money records; Gmail source ID only when confirmed; exact decimal monthly totals per currency and derived installment counts; separate scheduler store | direct operator confirmation, case resolution only by operator, no automatic mail-to-profile learning, no bank-balance claim, and explicit separate reminders | [Life guide](core/life/README.md), connected Dashboard and Agent tools | Focused owner, route, Agent, and native Desktop checks; normal-provider isolated mail-review → confirmed plan → linked-outgoing conversation; native Desktop plan and linked-outgoing form/save, chart, progress, exact synthetic cleanup and real-item preservation |
| `CAP-FILES` | Cross-machine file browsing and organization | `core/files/` plus surface adapters | Desktop, Android, connected hosts | source-local filesystem and Trash owner | exact browse/manage scopes and path/privacy policy | README, FAQ, surface guides | file service, source, UI, protocol, and platform tests |
| `CAP-TRANSFER` | Resumable verified file cargo | `core/transfer/` | Terminal, Desktop, Android, Telegram | transfer ledger, sender custody, hub spool, target receipt | exact transfer scope, chunk/hash verification, bounded custody | README, FAQ, command registry | transfer, attachment, Everywhere, and surface acceptance tests |
| `CAP-ATTACHMENTS` | Bounded turn attachments and media catalog | `core/state/attachments.py` | Agent-backed surfaces including MO Design | private categorized attachment catalog with atomic destination reservation and a cross-process-locked provenance index | eight-file/20 MiB turn bounds, stable non-rebindable IDs, contained saved paths, opaque surface references, and untrusted-content handling | FAQ and surface guides | attachment, Design bridge/session/broker, transfer, Telegram, and Android tests |
| `CAP-MCP` | External MCP tool integrations | `core/mcp/` and Agent lazy catalog loading | Agent and separately configured controllers | Agent-owned configured MCP processes | server exposure policy, configured exact tool allowlists, sandbox and scoped child credential injection | configured server tools through `tool_search`, README and FAQ | MCP manager, sandbox, credential, and integration tests |
| `CAP-HEADLESS` | Resident service composition | `core/runtime/service.py`, `mo_service.py`, and root Docker packaging | headless service | component readiness and reverse-stop owner; mounted private home in containers | strict resource lock, degraded-state reporting, non-root container, read-only image source | README and MAP | service, lock, component, startup, tracked packaging diagnostic, and container build/smoke gates |
| `CAP-PRESENTATION` | Terminal stream, theme, hints, true-worker activity, and skin controls | `interface/` | Terminal | interface-local presentation state over canonical runtime projections | rendering only; no task-truth ownership | interface guide and command registry | registry, activity, keybinding, layout, theme, and transcript tests |
| `CAP-TERMINAL` | Explicit internal terminal plus local and MO-host split-terminal workspace lanes | `interface/internal_terminal.py` plus `interface/workspace_model.py`, `interface/workspace_pty.py`, `interface/workspace_conpty.py`, `interface/workspace_screen.py`, `interface/workspace_remote.py`, and `interface/workspace.py` | Terminal | exact local PTY lifecycle or request-owned hub-terminal/Live Control lease lifecycle plus in-memory pane/focus/draft state | explicit user action, exact-process or exact-lease input/interrupt/close, bounded VT/text projection, inherited MO routing-identity removal, serving-hub profile routing pinned across persistent `tmux`, existing Everywhere authentication/scopes, idempotent request identity, and fail-closed cleanup | interface guide and command registry | input dispatch, workspace model/controller/panel/PTY/screen/remote-protocol, ambiguous-start cleanup, terminal, Everywhere, lifecycle, native Windows, and configured-hub acceptance tests |
| `CAP-SHELL` | Windows floating cube shell for one canonical MO terminal and one explicitly selected window, keyboard and Windows accessibility controls, and live attachment-title context even in lightweight chat | `mo_shell/bridge.py`, `mo_shell/native/`, `core/runtime/heartbeat.py`, and `core/agent/agent_turn.py` | MO Shell | session-bound native layout and reversible selected-HWND snapshot | one external window, adjustable divider, explicit managed/embedded mode, compact group hide/restore, canonical Desktop skin payload, no persisted HWND or Agent/Gateway/taskboard duplication; window title is untrusted availability context, not observed content | `mo_shell/README.md` and `mo_shell/MAINTAINING.md` | scoped bridge and turn-context tests, native geometry/input/accessibility checks, native build, and one final Windows interaction acceptance |
| `CAP-EXIT` | Clean interactive shutdown | terminal application owner | Terminal | current process lifecycle | no remote lease may invoke local exit | command registry | terminal loop and Live Control input-boundary tests |
| `CAP-MIGRATION` | Read-only-first migration from supported agent footprints | `core/migrate/` | Terminal intent and tools | destination profile state and explicitly selected project rules after approved apply | credentials excluded, provenance-marked append, existing skill files preserved and imported skills inactive pending review/promotion | `migrate` inspection/plan/apply, README capability contract | migration inspection, plan, apply, and dispatch tests |
<!-- mo-capabilities:ledger:end -->

## Command coverage

`interface/command_registry.py` remains the command metadata owner. This table
maps every built-in public or internal root to a capability.

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

## Change rule

When a capability changes, update its current runtime owner first, then its
surface/state/security contracts, focused tests, this row, and user-facing
guide. A new public command must map to one existing or new capability row.
Removing or reclassifying a command must update the registry and this table in
the same change. Run:

```bash
python -m core.diagnostics.docs_check
```

The check rejects missing, extra, duplicated, reordered, or misclassified
command rows; unknown capability references; incomplete ledger rows; missing or
case-mismatched literal repository paths in runtime-owner cells; broken local
file/Markdown section links; and loss of the public route to this contract.
It does not prove prose accuracy or runtime acceptance. Keep behavioral claims
in their component authority and link to it; dated changelog evidence describes
the recorded candidate, not every later checkout or installed build. Use the
[source-linked history workflow](core/MAINTAINING.md#history-finding-inputs) for
reusable review findings, and recheck changed sources before treating them as
current guidance. Historical intent and current implementation are evidence for
the requested outcome, not a rule that either must always win.
