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

```text
Terminal / Desktop / Android / Telegram
                    │
                    ▼
          Agent + Gateway + final gates
                    │
          ┌─────────┼──────────┐
          ▼         ▼          ▼
       tools     task truth   private state
          │         │          │
          └──────── evidence ───┘
```

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
| `CAP-CONTINUITY` | Bounded cross-surface work handoff, and running MOs aware of each other: heartbeats, sibling notes and messages between MOs | `core/state/continuity_events.py`, `core/state/surface_handoff.py`, `core/runtime/heartbeat.py`, `core/runtime/instance.py`, `core/context/workspace_awareness.py`, and `core/runtime/mo_messages.py` | Terminal, Desktop, Android, Telegram | continuity event store, surface binding, heartbeat ledger and `run/mo-messages.jsonl` | exact scopes, eligibility, privacy bounds; sibling notes and messages are redacted and never instructions | FAQ, MAP, Everywhere guide, README | continuity, heartbeat, workspace awareness, MO message, Everywhere, and client contract tests |
| `CAP-PROVIDERS` | Provider, model, reasoning, bounded same-route transient recovery, and turn-local reactive fallback selection | `core/provider/` and Agent provider scope | Agent-backed surfaces | authored config plus the private Terminal preference overlay, provider/model-scoped capacity evidence, bounded route evidence, outer-turn tool-catalog continuity, and selected-route restoration | credential broker, catalog revalidation, shared-auth reload, pre-output one-retry auth/transient recovery, precise non-retryable classification, tool-loop isolation, and adapter boundaries | README, FAQ, prompt contract, and command registry | provider adapter, runtime-preference, auth/transient recovery, route-isolated capacity, repeated-tool non-fallback, route-context, catalog-continuity, reasoning, and conformance tests |
| `CAP-GRAPH` | Structural graph and BM25 search, source-linked project knowledge, code context, mapping, impact, redundancy diagnostics and Git history, with automatic index maintenance | `core/graph/structural_graph.py`, `core/graph/search.py`, `core/graph/history.py`, `core/mapping/`, `core/knowledge/`, and `core/diagnostics/redundancy_check.py` | Terminal, Agent, maintainer MCP | private per-project graph/history/knowledge caches, durable review findings, and generated human project map | bounded reads, source freshness, explicit private-source tracing, selected-route behavior, cancellation and coordinated writers; orientation never replaces source, tests or contracts | `code_search`, graph/history tools, `redundancy_scan`, README, FAQ, MAP, [history contract](core/MAINTAINING.md#history-finding-inputs), command registry | graph, history, source provenance, mapper, knowledge, MCP, freshness, writer coordination, cancellation, and resource tests |
| `CAP-PROFILE` | Curated profile facts, preferences and operator terms | `core/profile/` | Agent-backed surfaces | structured private profile plus declared profile Markdown files | private-home, locked persistence and redaction boundaries | `record_profile_fact`, README, FAQ, command registry | profile context, term routing, state layout, privacy, and sync tests |
| `CAP-LEARNING` | FTS5/BM25 episodic turn recall, optional semantic fusion, feedback and reviewable learning | `core/learning/`; current-task selection in `core/agent/agent_turn.py` and delivery in `core/context/context_bridge.py` | Agent-backed surfaces | private turn index, learning stores and suggestions | bounded retrieval/admission, prepared-versus-delivered evidence and policy-owned adoption; recalled entries are turn excerpts, not complete sessions | [personalization contract](core/MAINTAINING.md#personalization-and-project-boundaries), FAQ, command registry, Dashboard Learning and Checks | recall, follow-up/reload context, delivery budgets, learning, closeout, and owned-skill tests |
| `CAP-SKILLS` | Selected skills, discoverable skill catalog, scoped conventions and authored roles | `core/skills/` with delivery capture in `core/agent/agent_turn.py` and Terminal projection in `interface/turn_runner.py` | Task skills on non-Desktop Agent surfaces; explicit authored roles on Terminal and Desktop; local Dashboard source inspection | private profile skill roots, reconciled shipped seeds, opt-in project-local skills and bounded per-session delivered-name receipts | declared lanes/tools, approval, project-bound new conventions, explicitly unbound older conventions, delivered-context accounting, current-turn-only Terminal projection, and no inference that delivery proves provider adherence | [personalization contract](core/MAINTAINING.md#personalization-and-project-boundaries), FAQ, command registry, Dashboard Learning | skills, roles, literal/semantic/location selection, seed reconciliation, next-turn delivery context, Terminal receipt, persistence, and ownership tests |
| `CAP-GOAL` | Autonomous goal mode | `core/goal/` | Terminal and service-backed turns | goal store and taskboard | evidence, pause, budget, and completion gates | README, FAQ, command registry | goal lifecycle, auditor, and resume tests |
| `CAP-PRT` | Target-aware Project Review Team reporting and committed correction handoff | `core/review/` plus the existing Agent worker runtime | Explicit Terminal `/prt` and trusted GitHub workflow | durable review audit/worker state | target distinction, trust, diff, provider, normal sandbox/verification, exact-event consumption, and publication boundaries | [PRT contract](core/MAINTAINING.md#prt-review-contract), FAQ, interface notes, workflow README | PRT, review, committed-correction routing, worker, terminal, GitHub delivery, and privacy tests |
| `CAP-ROLES` | Persistent skill-backed conversation roles, project-bound specialist orchestration, and role-governed background/scheduled work | `core/skills/roles.py`, `core/agent/agent.py`, `core/agent/agent_slash.py`, `core/agent/agent_turn_dispatch.py`, `core/worker/`, and `mo_desktop/mologrthim/` | Terminal, Desktop, scheduler, and background workers | profile role packs own role contracts and project rosters; session metadata restores active roles; user-selected `BOOK-STATE.md` owns book progress; worker records are process-local while saved sessions/taskboards retain work context | exact project binding, untrusted worker reports, declared role scopes plus ordinary sandbox/current-turn authority/confirmation | README, FAQ, `/role`, `role_work`, and shipped role packs | role activation, project isolation, specialist registration/dispatch/report verification, session persistence, Desktop status projection, scheduler, book format/state, and command-help tests; native Desktop acceptance |
| `CAP-AUTOMATION` | Persistent plain reminders, scheduled turns, goals, roles, and scripts | `core/runtime/scheduler.py` | Terminal, headless service, resident Desktop scheduler and notices, and connected Dashboard Life | private schedule store and run logs shared through one singleton lock | explicit scheduling; plain reminders use no model, Agent tasks use the configured model, and mail tasks require explicit mail requests | natural requests through `schedule_job`, Dashboard Life, FAQ and `/schedule` | scheduler, service, Desktop startup/stop, Dashboard route, native save/run, and recovery checks |
| `CAP-VISUALIZE` | On-demand structured diagrams, terminal-native data visuals, native image display, optional image generation, Generate (images, video and music through Kie.ai), and local image transforms | `core/visualize/`, `core/imagegen.py`, `core/imageedit.py`, `core/media/`, and `tools/media.py` | Terminal, Desktop, and Telegram through surface-owned display adapters | private generated media plus explicit user-selected image files | exact producer-marker provenance, registered ANSI artifacts, sandboxed image paths, read-only-lane exclusion for producers, bounded chart data, and cooperative generation cancellation | prompt contract and command registry | visualization, marker-channel, sandbox, routing, generation, editing, and surface-delivery tests |
| `CAP-EXPLAINER` | Evidence-backed narrated explainer and focused product-video production | `core/explainer/`, the installed `mo --explainer` launcher route, and the shipped explainer-video skill in `core/skills/seeds/` | Agent-backed surfaces with local shell and artifact access | private `media/explainers` projects, project-owned hashed media, saved resolved style/status, and derived video artifacts | source-link validation plus provider factual review, narration/audio/asset hashes, bounded muted clips, private-state output, canonical saved MO styling with project overrides, lazy optional render/voice imports, deterministic frames/callouts/pan-zoom/crossfades, an optional ducked music bed with loudness normalization, SRT subtitles, recoverable staged pair publication, mandatory FFprobe checks, measured progress, and explicit silent-versus-narrated reporting | README and [explainer guide](core/explainer/README.md) | explainer schema, quality, storage, media integrity, styling/templates, narration failure, rendering/FFprobe/cancellation, non-checkout launcher, skill-selection, and visual-inspection tests |
| `CAP-DESKTOP` | Persistent Desktop assistant, conversation, machine/screen help, same-project Terminal handoff for explicit software implementation, four-cube app launcher/running-window switcher with independent Shell instances, tray-activated session Focus mode with a freely movable lower-right cube expansion, a collapsible window list, native hover previews, Explorer tray access and reversible taskbar hiding, capability knowledge, read-only project checks, native Settings for project LSP/graph preferences, surface/instance models, declared Agent preferences and shared visuals, native Phone workspace with setup checks and one-use companion QR, and the Inventory basket of everything MO did with you | `mo_desktop/` over Gateway plus `interface.desktop_ui`/`desktop_widgets`; exact implementation handoff reuses `core.design.terminal_handoff` | Desktop | isolated `mo-desktop` session plus one immutable active visual state; project implementation remains owned by a live/new Terminal or explicitly active Project Architect; project LSP uses canonical private preferences | desktop intent, observation, approval, matching lock/ready generation, exact same-project heartbeat target, strict palette/geometry roles and in-place window lifecycle; hover never launches apps | README, FAQ, Desktop guides, launcher/tray controls and `mo_desktop/capabilities.py` | Desktop runtime, implementation routing, capability knowledge, conversation, visual source-guard, lifecycle, native UI and persistence tests |
| `CAP-SYSTEMCARE` | Scoped machine/MO/host/project inspection, MO Care's model-free watch of MO's own background work, Windows maintenance plans, compact Game Session, global Game Mode and verified recovery | `core/systemcare/` plus `tools/systemcare.py`; `mo_desktop/systemcare/` is the native presentation adapter and the existing cube renderer supplies quick access/active state | Deferred Agent tools and MO Desktop/Dashboard | private device-local `memory/systemcare.sqlite`, bounded logs, cross-process operation state and the canonical Game Mode recovery journal | read-only scans, bounded declared registry inspection, exact fresh selection/calibration/digest, typed originals before reversible changes, verified receipts, no-force native updates/removal, selected Windows permission and normal confirmation gates; Game Session adds bounded resource snapshots and reuses the visible refresh rather than adding a telemetry worker; optional automation uses the existing scheduler | Desktop SystemCare guide, core/Desktop maintenance contracts and MAP | service/state/tools, native host lifecycle, Game Session projection, launcher shortcut/HUD, Desktop persistence/routing, scheduler pause/resume, stale evidence, partial failures and restoration tests |
| `CAP-DESIGN` | Self-contained visual concepts, project-aware refinement, bounded per-message attachments, shared user/MO Board clarification, Board-only exact-terminal or saved-unbound Desktop windows, discoverable Board keyboard controls, canonical saved Terminal provider/model selection with visible configured failure-triggered fallback, live native preview, non-mutating revision selection/download/delete, exact-terminal Board reads and draft-decision return, actionable exact-design completion notices, and explicit implementation handoff | `core/design/`, `tools/mo_design.py`, and `mo_desktop/design_studio/` | MO terminal and Desktop, autonomous background turns, and new visible terminal turns | private `media/designs`, portable `board/v1`, bounded snapshots, immediate Board working/draft sidecar, live preview, attachment metadata/session sidecar, authenticated command/completion state, exact terminal-context queue, one-Board-per-terminal runtime binding, and one-shot renderer focus under MO state | strict declarative schemas, bounded geometry/operations, deterministic spatial context plus default accidental-overlap rejection, explicit MO-draft review, atomic complete-visual/Board updates, exact non-goal terminal return, private launch token, current-revision delete protection, worker-finish plus changed-artifact and visual-observation completion evidence, truthful unaccepted-partial state, Board-only clarification exceptions within read-only project lanes, opaque attachment IDs and untrusted-content handling, credential-free shared model activation, reactive configured-selector fallback evidence, iframe CSP/sandbox, opt-in scripts, pending/draft Handoff guards, and advisory private visual preferences | README and Desktop Design guide | schema/service/Board/tool/provider/Desktop routing, standalone bound/unbound launch and release, spatial guard, keyboard controls, revision selection/export/delete, draft review/Handoff block, exact decision return, attachment import/routing, model selection/fallback boundary, partial and metadata-only false-completion, actionable-notice, exact-focus, and notification tests plus native renderer, interaction, pen, and hot-reload acceptance |
| `CAP-COMPUTER` | Target-owned desktop perception, optional Connected Chrome-tab diagnosis and actuation | `core/desktop/runtime.py`, `core/desktop/apps.py`, `tools/computer.py`, `tools/screen.py`, `tools/browser.py`, `core/browser_bridge.py`, and `clients/chrome/`; compatibility adapters route through the canonical computer façade | Terminal and Desktop through explicit agent tools; MO Design receives observation only | application/window refs plus exact Chrome-tab refs, target observations, bounded native-window/region renders, visible viewport evidence, stable signature, revision and lease state | ordinary Chrome tabs are discovered and the selected tab attaches on demand; user stop is respected, private/protected/other-debugger tabs are excluded; authenticated local bridge, bounded CDP allowlist, native-window fallback without DOM authority, no second browser/profile transport, best-effort occluded-window rendering without activation, minimized-window rejection, exact current-turn action authority, actions that return their own fresh evidence (no forced verification round; an unknown result is stated), stable window refs, raw-act stale/foreground rejection, strict key input, bounded no-progress stop, inline result observation, cancellation through transport and high-impact approval | `computer_targets`, `computer_observe`, `computer_act`, README, FAQ, Connected Tab guide and Desktop guides | canonical façade, admission, runtime, completion, UIA, window-render/region/pointer/drag/scroll, connected-browser, extension/native-host, and native acceptance corpus |
| `CAP-VOICE` | Optional local Desktop speech and Voice Chat with transcription-to-answer and first-audio latency evidence | `mo_desktop/voice/` and companion controller | Desktop | private optional voice runtime and settings | explicit activation, local process, accepted-final-only speech, first-PCM speaking state, redacted diagnostics | Desktop guide | voice protocol/state/timing unit tests and native audio/release acceptance |
| `CAP-REMOTE` | Everywhere hub, pairing, overview, jobs, and native clients | `mo_everywhere/` | service, Android, Desktop coordinator | hub registry and private device credentials | exact device scopes, revocation, TLS, and role checks | README, FAQ, Everywhere guide | Everywhere API, registry, Android, pairing, and deployment tests |
| `CAP-LIVE-CONTROL` | Native remote Desktop and terminal presentation | Everywhere broker plus Desktop/terminal host publishers | Android controlling Desktop or terminal | stable host key, live connection, session lease | `control`, `remote_control`, distinct `remote_host`, local consent | FAQ, MAP, Desktop, Everywhere, Android guides | broker, host, Android JVM, reconnect, and one final device pass |
| `CAP-ANDROID` | Hub requests, resident controls, phone capabilities available in the installed release, and separate This phone provider chat | `mo_everywhere/` plus the privately maintained Google Play client | Android | Keystore-protected separate Hub/phone credentials, app-private state, Android services | exact scopes, local consent, keyguard, capability and confirmation gates | README, FAQ, and [`ANDROID.md`](ANDROID.md) | public Hub/API contracts plus private client, release, and recorded-device acceptance |
| `CAP-PUBLISHER` | Optional public product/support/privacy/deletion pages and Android AI-report admission | `mo_publisher/` | public web pages, Android report submission and operator CLI | independent private publisher home and report store | separate from Hub/Agent authority, bounded consented reports, durable receipts and local-only review/deletion; retention also requires an independently operated purge timer | [Publisher guide](mo_publisher/README.md) and Android publication guide | publisher admission, custody, retention and page tests plus Android reporting and operated-deployment acceptance |
| `CAP-PHONE` | Optional phone semantic, file, system, and advanced operations when the installed Android release exposes them | shared Hub/tool gates plus the privately maintained Android client | Android and Agent through hub | phone capability ledger and local grants | default-off consent, fresh evidence returned by each action (an unconfirmed result is stated; no forced verification round), exact later-turn confirmation | FAQ and [`ANDROID.md`](ANDROID.md) | public Hub capability/approval contracts plus private client and device acceptance |
| `CAP-PHONE-INPUT` | Phone Trackpad: cursor, typing, window switching and pressure-aware Board pen input from the Android app | `mo_desktop/phone/` plus the privately maintained Android client | Google Play and Direct Android builds with Desktop over an authenticated ADB tunnel; foreground MO Design/standalone Board for pen input | adb-served pairing state, private input preferences, and one local bounded session | operator-initiated pairing, one-use Desktop nonce, loopback-only service, exact indexed stylus routing, finger/palm exclusion by default, bounded actions, exact foreground Board target, no credential exposure | Desktop guide and `mo_desktop/phone/README.md` | public host view-model/protocol checks plus private client routing/settings and recorded-device acceptance |
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

## Behavior reference

What each capability does in practice, area by area.

### Evidence-backed work

- Gateway-owned task lifecycle with procedure scaffolds or model-authored plans.
- Evidence-gated `complete_task`; failed checks stay blocked.
- Sandboxed file, shell, browser, desktop, phone, network, and MCP dispatch.
- Final-answer gates for completion, verification, safety, and unsupported
  current-state claims.
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
- `/knowledge` inspects and queries an automatically maintained private project
  manifest of document headings, capabilities, commands, and current graph nodes
  with source references. The existing project-index worker maintains it
  asynchronously under the shared project refresh lease; one manifest byte lock
  serializes writers across MO instances. Status, query, Dashboard, and native
  `code_search` remain read-only and report an automatic refresh in progress
  instead of requiring a manual rebuild. Normal project-work turns receive a
  bounded query-matched slice whose selected source documents are reverified
  before use; test filenames remain inventory.
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
  deterministic motion, optional Piper, staged FFmpeg encoding, mandatory FFprobe
  validation, and measured status through `mo --explainer` from any directory.
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
  A failing preview interaction is captured once per revision and sent through
  the existing background worker for diagnosis. Project implementation requires
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
  live state or treats deferred refinement as a present change; the same worker visually checks the
  rendered preview once before finishing, with at most one correction and one
  re-observation. Only the background route publishes a completion notice through MO's existing
  cube/tray/platform notification owner. Design reuses Terminal's saved `/model`
  selection through the [shared model owner](core/MAINTAINING.md#presentation-adapter-boundary),
  with no independent Studio chooser or artifact setting. Its native attachment picker can add bounded files, images,
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

- Saved sessions, explicit resume, retries, undo, project history, and
  structured closeouts. A fresh terminal remains isolated, while the latest
  same-surface saved user request is available as secret-redacted JSON
  historical context for natural follow-ups without silently reopening that
  conversation. Before a terminal turn's first provider request, MO checkpoints
  that active user request through the same atomic session snapshot; a clean
  turn-end save replaces the checkpoint, while a power/process loss leaves a
  bounded interrupted-request marker that continuity can distinguish from the
  older completed transcript.
- The Ctrl+B workspace rail loads profile projects, expands their local or host
  terminals, and switches live project groups without stopping their processes.
  New terminals use the selected project's directory; existing MO sessions keep
  their original project. The UI has no combined pane-count cap; host launches
  retain the serving supervisor's resource limit. Local PTYs own raw interactive
  keys; a focused host tile keeps the visible
  composer and sends one complete message on Enter. Host drafts remain separate,
  MO slash commands remain owned by the original main pane, and host panes reuse the existing
  Everywhere hub-terminal supervisor and `mo_session` Live Control owner. A new
  **MO host** pane always starts on the serving host; Desktop terminal actions
  remain separate for portable and external-control callers. The hub pins its
  resolved non-secret profile/project/auth-file routing into the new terminal,
  so a persistent multiplexer cannot reuse stale profile state; controller and
  provider credentials remain separate. `Alt+Up/Down`
  scrolls each pane's own bounded history or full-screen app navigation;
  `Alt+Home/End` jumps to that pane's oldest/newest available output, and
  `Alt+F` toggles the focused pane between the grid and the full workspace. The
  Health view shows a quiet snapshot of machine resources and attached terminal
  process trees, adding a local total only when multiple local trees make it
  useful. The selected provider stays visible while `/status` owns the detailed
  runtime report. Connected hosts supply bounded
  CPU/memory observations through the authenticated status/socket path without
  claiming a control lease; older hosts show unsupported readings. Each MO pane
  reuses the one terminal-title publisher for `MO · <first request> · tasks
  <current>/<total> · <instance> · <provider>/<model>` when a taskboard is active,
  omitting absent fields and retaining the existing working spinner. Titles stay
  bounded and redacted; legacy `MO · <instance> · <model>` titles remain attachable.
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
  confirmed violations through the existing Agent worker for correction and
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
  Trash, dynamic device send, and Incoming/Outgoing progress all reuse the
  existing file and transfer owners. Desktop's WebView board also offers an
  explicit, short-lived local browser QR transfer for devices without MO;
  that LAN HTTP connection requires a trusted private network.
- Windows Desktop Settings, tray, MO Files, MO Phone, MO Design, dialogs, and
  pointer surfaces consume one strict active visual state. The shared
  `interface/desktop_brand.py` owner supplies static four-cube geometry for
  Windows HTML shells and native icons; surfaces supply only scale and skin
  tokens. The Android app keeps one native live renderer for its mobile
  callers rather than adding a cross-runtime generation layer. Settings alone
  persists skin, panel/button geometry, and one shared window-effect type and
  intensity; changes repaint existing windows without replacing their companion,
  startup, file, transfer, phone, or Design owners.
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
- Confirmed learning has one active runtime authority: a materialized local skill
  pack suppresses equivalent virtual adapters, including repeated confirmed
  suggestions with different candidate IDs. Retired generated packs are durable
  tombstones: their source suggestions stay inactive across review, startup,
  reconciliation, bundle transfer, and materialization. Explicit workflow promotion
  always materializes its pack; candidate rows never activate behavior.
- Generated skill use is recorded only when that skill's guidance actually fits
  in the active context bridge. The same delivered-skill decision stores a
  bounded per-session name-and-turn receipt. Terminal projects that exact receipt
  once on the matching turn's neutral notice rail. The next provider turn receives
  the same fact as context for natural-language follow-ups. The receipt does
  not claim the provider followed the guidance. Later explicit correction or
  explicit positive feedback settles the pending outcome; an unrelated or neutral
  next turn does not invent success.
- Relative follow-ups reuse the current conversation's task for relevant profile
  rules, skills and recall, including after session reload. New subjects use their
  own query. New learned code conventions belong to the current project and apply
  to their declared files or an explicit name request within that project. The
  Dashboard labels older unbound conventions as cross-project. Recalled excerpts
  retain source IDs and fit individually within
  the context budget; traces distinguish prepared material from delivered guidance.
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
- One bounded, Cube-branded dashboard projection for user and operations
  perspectives across Terminal, browser/read-only HTML, Desktop, and Android.
  `mo --dashboard` or `/dashboard show` opens the connected frameless MO workspace
  through the existing optional native WebView renderer;
  work commands use the normal MO terminal, with no embedded terminal or voice UI.
  Rules edit their MO Files source; learning review and project LSP controls use
  their existing owners directly, and knowledge queries reuse `/knowledge`.
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
- Raw episodic memory, learning stores, prompts, and task evidence remain
  under their established review/approval owners rather than becoming
  dashboard-editable state.
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
- Observe → target → act → verify ownership; a mutation consumes the observation
  that authorized it.
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
