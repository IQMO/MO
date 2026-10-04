# MO Core Maintenance Contract

This file owns detailed runtime, state, graph, multi-instance, procedure, and
public/private-boundary invariants for changes under `core/`. The root
`AGENTS.md` owns repository-wide working rules and routes maintainers here.

## Runtime truth

- Task-phase chronology checks require an explicit phase or row claim. Ordinary
  explanations such as reporting blockers must not trigger corrective requests.
- `show_viz` owns structured diagrams and terminal-native data visuals. ANSI
  payloads use the registered operator-visual marker and are consumed before
  model-context capping; raw Mermaid DSL remains markup rather than being parsed
  as Markdown structure. Image delivery keeps its separate file-marker path.

- `core/prompts/system.md` is the authoritative MO runtime behavior prompt. Repository instructions do not assign MO's runtime identity.
- `core/mail/README.md` records Agent-chat Gmail and Connected Tab Outlook,
  their distinct account owners, model-visible explicit reads, approval
  flow, on-demand Dashboard setup, bounded local review, surface reuse, and
  verification limits. Start there before extending
  mail behavior; tests do not imply public Gmail OAuth release readiness,
  Outlook delivery, or a rendered Desktop acceptance.
- `core/life/README.md` records separate private operator-confirmed commitments,
  cases with dated updates, and recorded money owners, optional payment-plan links, local mail wording
  hints, Dashboard and Agent
  adapters, profile-learning boundaries, and reminder separation.
- The Agent turn loop reminds an interactive provider about overdue progress
  prose through the existing trailing request context. It never schedules a
  request, persists a reminder in conversation history, or changes continuation
  eligibility. Delivered interim prose resets the reminder interval; a single
  long provider/tool call still relies on the surface's activity display.
- `core/local_extensions.py` is the neutral bridge for profile-owned local extensions. Empty profiles load no private commands, hooks, context, board rows, Desktop apps, or closeout machinery. An admitted extension may supply bounded ambient context on simple turns without activating one of its workflows. Private Desktop app metadata is bounded and value-free; implementation loads only after an explicit tray open and receives presentation seams rather than Agent/Gateway/model authority.
- This checkout is the active product source; the product name is **MO Agent**. A checkout folder name is never user-facing identity. Private extension lineage/context lives in the local profile (`~/.mo`, including `~/.mo/operator` or `MO_LOCAL_EXTENSION_ROOT`) and never in tracked product docs.
- Do not duplicate private extension internals here; product code owns only the bridge.
- Extension `blocked_text` is a terminal gate rejection: return it to the caller
  without running later gates, board-close hooks, or learning. An instruction
  requests another provider pass; a replacement `final_text` continues through
  the remaining pipeline. Extensions own their correction-attempt budgets and
  must distinguish unfinished work from a completion claim.
- `core/browser_bridge.py` is the sole Connected Chrome native-host lifecycle,
  authentication, transport, and live-status owner. MO Desktop Settings may
  call its existing `install()`, `status()`, and `uninstall()` functions as a
  presentation adapter but must not persist a second enabled flag, registration
  record, or service. `clients/chrome/README.md` owns the operator setup and
  extension behavior guide; `tools/browser.py` remains the browser action owner.
  A cached exact-tab reference is usable only while the native browser target
  lease still belongs to that action owner; rebind through the existing catalog
  after a lease expires.
- Connected browser input uses the existing exact-tab transport: bounded
  `Input.insertText` replaces editable content in bulk after page-local
  selection, and `Input.dispatchKeyEvent` supplies page shortcuts. Do not replace
  rich-editor documents by setting their temporary textarea value or fall back
  to native input for a Connected Tab request. Background tabs must remain
  usable without changing the operator's active tab, OS focus, or clipboard.
  Native typing and key sequences remain a separate path and check the existing
  turn-cancel event and observed foreground window between input units.
- `core/runtime/capability_routing.py` owns the shared lexical signal for a
  natural computer action or control demonstration. Turn-intent work routing
  and `DesktopActionAdmission` consume that signal; surfaces must not add a
  phrase-specific Connected/browser test classifier or treat the signal as
  action-time authority.
- Operator paths, servers, project names, identity, deployment knowledge, and terminology live in the per-user `~/.mo` profile, never product code or tracked docs. The optional `mo_control.*` external bridge resolves only from private config or environment and is disabled by default.
- State is private by default under `~/.mo` or `MO_STATE_HOME`, from any cwd. Every runtime-state path (`memory/...`, `logs/...`) must resolve through `core.state.paths.resolve_state_path()`; never default a writer to a bare cwd-relative path.
- Per-project caches reuse that state-path owner too. Explicit project-local
  mode resolves them relative to the working directory, not MO's installation.
- The generated `~/.mo/README.md` refreshes from content, not from events: startup compares the rendered registry with the file and rewrites only on drift, so editing `STATE_LAYOUT` reaches the human map without waiting for a migration. The rewrite is best-effort — a locked or read-only profile must never block startup — and the map is a description, never a second configuration source. Declare a path at the granularity a human acts on: only top-level roots and `memory/*` domains are rendered, so a nested entry warns nobody unless its parent's purpose says so.
- `core/state/layout.py` owns private-home initialization, `/doctor layout`, and the generated `~/.mo/README.md`; Durable state is grouped under `memory/{profile,learning,sessions,work,surfaces}`; diagnostics use `logs/`, liveness/scratch uses `run/`, generated UI and project indexes use `cache/`, and user artifacts use `media/`. The six declared profile Markdown files are the only automatically curated prose files. `personal/` is opaque: never auto-index, migrate, synchronize, inspect, or delete it.
- `core/diagnostics/source_inventory.py` owns deterministic public-source discovery, and `core/diagnostics/redundancy_check.py` is the sole product exact/near-duplication detector over that inventory. It covers whole files, normalized Python/Kotlin functions, exact and renamed token windows, normalized text blocks, and reviewed allowances; do not add a parallel clone scanner.
  Reviewed token allowances describe current sampled findings: removing a stale
  fingerprint does not prove the underlying semantic overlap disappeared.
- `core/systemcare/` owns calibration, read-only Safe/Advanced scans, selected machine/MO/server/project inspection, exact plans, native action revalidation, receipts, typed recovery originals and cancellation. Its private ledger is `memory/systemcare.sqlite`, diagnostics use `logs/systemcare.jsonl`, and cross-process work uses `run/systemcare/`. Public projections omit raw paths/originals. Registry inspection is bounded to declared families; repair removes only eligible exact missing-target values, never arbitrary key trees. Selected service/startup/Game Mode changes, fixed Windows repairs, registered app actions, native updates and unused driver-package removal stay in the same catalog/service; originals precede reversible mutations and post-check receipts retain partial failures. Browser history categories, exact Windows Recycle Bin items and verified-uninstall leftovers reuse the same plans and receipts; protected data and unknown owners remain excluded. Recorded Windows boot durations are separate from registration presence and predicted impact. Plans are immutable and expire; cleanup revalidates every candidate. Protected Desktop operations reuse that service through one requested Windows permission worker; Agent tools retain normal confirmation gates. `mo_desktop.systemcare` owns preferences and optional automation reuses the existing scheduler. MO/host/project maintenance review uses existing canonical owners rather than a second mutation system. Opaque `personal/` and curated learning remain outside this owner. See `mo_desktop/systemcare/README.md` for coverage limits.
- `core/transfer/` owns the only cross-surface file-cargo protocol and stores
  its ledger/outbox in `memory/transfers.sqlite` and durable in-flight custody
  under `memory/transfers/`. Keep its imports lazy on startup paths. A sender
  retains a private staged copy and stable idempotency key until hub completion; the hub
  retains spool custody until the target records receipt. Delivery into
  `core.state.attachments` reuses that existing catalog and stable attachment
  ID, never creates a second catalog or duplicates a Desktop drop. The
  `turn_context` compatibility purpose remains catalog-only, keeps its fixed
  20 MiB attachment ceiling, and completes independently of the cargo
  `auto_accept` preference so a turn cannot strand input that has no cargo
  acceptance UI. Chunk download, target acceptance/receipt, expiry, and
  cancellation share one per-transfer cleanup lock; the API serves an
  already-verified bounded byte chunk, never a spool pathname that cleanup can
  remove while the response is streaming.

## Personalization and project boundaries

- Personalization is operator-scoped by default. `Profile` owns structured identity/preferences/project-history metadata plus the six curated Markdown files; `learning.md` is an accepted-learning ledger and `behavior.md` is its compact categorized mirror. They are not separate project profiles. Accepted behavior rules receive one bounded, query- and purpose-ranked early slot in normal profile context so current rules survive the capsule budget; the event ledger is not a second prompt-injection path.
- `core.learning.operator_messages` is the single accepted-turn capture router. It writes high-confidence project/privacy facts, explicit corrections, and terms through existing profile owners; keeps workflow promotion approval-gated; stages product requirements under `memory/work/product-intent` for source-backed project-history consolidation; and records bounded message-hash receipts. Retained conversation snapshots reconcile incrementally by mtime so interrupted messages are not lost and normal turns do not rescan unchanged history. The provider-facing capture nudge is retired. Automatic extraction does not cover every durable fact, so the existing profile writer remains available for provider-understood knowledge without forcing a second completion pass.
- `Profile.projects` is recent-working-directory navigation metadata only. A project entry stores path/name/last-opened/session-count/notes, but the runtime currently writes no learned project rules to `notes`; do not present this list as a complete project inventory or a per-project learning store.
- Project-status and continuity questions retain bounded query-matched profile context. Durable work-record locations belong in existing profile facts; changing task, deployment and publishing status stays in its canonical work record. `Profile.referenced_work_path()` admits an exact existing file explicitly linked in the six curated profile files under `memory/work`; it grants no directory, write, credential or `personal/` access and builds no report index. Ordinary `read_file` dispatch combines that permission with the existing sandbox guards. Authorized reads of canonical saved-conversation snapshots reuse `core/session/session.py:project_messages`, independently of recall selection, to omit message-level provider replay and presentation fields. The default conversation view retains user/assistant text without tool calls/results for bounded recall; `view=evidence` retains original tool arguments and outputs for verification. Both retain source identity and label historical conversation as orientation, not current proof. Each view owns its displayed line numbers; restart pagination when switching views, and never reuse raw shell-search offsets. This formatting grants no access: selected-source permissions and full-access policy keep their existing owners. The persisted snapshot and its recovery state remain unchanged. Other files retain literal reads. A recorded release checklist is not live store/device verification, and a status question does not authorize continuing it.
- Lookup/profile capsules prioritize requested operational facts ahead of general
  preferences and the explanatory index. Fact selection reuses the existing
  relevance ranker so specific subjects survive older rows sharing only a product
  name; generic status wording is not an entity. The context budget is unchanged.
  External coding-session transcripts are not automatically ingested as profile
  truth: reconcile verified durable locations through the existing fact lifecycle,
  keep task progress in its work record, and retain credential custody with its
  established owner.
- `Profile.project_locations()` projects curated project declarations for the terminal rail: named `- **Name** — ...` entries under an `operator.md` Projects section, plus `project`/`repo` facts. Facts may use the same `**Name** — ...` form to declare a display name. Explicit native absolute directories are deduplicated; incidental launch history never becomes ownership. Named projects without a usable folder remain visible as **no folder**, and cannot launch a terminal. An explicitly named personal location is displayed as opaque profile metadata without filesystem inspection; unnamed personal references are not discovered as projects. Other directory candidates require existence. The host supervisor advertises only entries with a local path, while both surfaces retain their current project and open panes. No second persisted catalog is maintained; omitted names or locations must be corrected through the existing private profile owner, never inferred from a model answer.
- `core/context/project_context.py` owns project rules through the ancestor `AGENTS.md` chain for the active checkout and each newly targeted project. It delivers complete instruction files or explicit read-before-work references, never apparently complete head/tail excerpts. The context bridge prioritizes that contract and preserves policy text; an oversized lower-priority source does not suppress later sources that fit. That read-only context, live source/tests, and current Git/runtime evidence are the project authority; do not infer project rules from operator-wide learning. The history index below retains sourced analysis only, never a second project-policy authority.
- Project-work turns read and fingerprint the active rule chain before provider work. During a live provider turn, canonical file/root/workdir tools resolve each newly targeted project and defer the entire first batch until its applicable rules have been supplied for review; later calls reuse the per-turn rule ledger while the chain is unchanged. Pathless tools and shell command text are not guessed as project selectors, so shell/test/Git calls use their explicit `workdir`. Context preparation is read-only; missing rules preview the existing minimal starter. The tool dispatcher creates it only for an admitted project edit/execution after checking the starter's own role and sandbox write permissions. Reuse the existing Git-root resolver for its destination, never a nested scratch directory; preserve each effective target scope for ancestor-rule discovery. Final reporting reads every reviewed chain afresh and supplies changed sources to the provider, with bounded retries and explicit unreadable/unsettled outcomes. Unchanged rules stay silent. `project_bridge` remains the manual read-only bridge, and targeting a directory neither promotes it into the profile project catalog nor creates another rules owner. The Dashboard's existing Rules view is the sole visual projection; `interface/` remains a renderer and must not acquire a second rule catalog, resolver, or badge. This is source reconciliation, not automatic semantic-compliance proof. `core/skills/` learned conventions, `core/graph/` orientation, and Dashboard projections must not become alternate project-policy owners.
- `TurnIntent` separates action authority from optional project orientation. Direct execution and simple native computer actions retain tools, project rules, task/evidence gates and action-time permissions without automatically preparing episodic recall, repository workspace summaries, or graph/knowledge/history context. A `run`/`execute` request does not prove a shell target; the provider selects the existing tool. Build, repair, investigation, managed work and resumed project tasks retain their orientation; explicit references to past conversation retain the existing recall policy even during direct actions. The weak build word `new` does not turn a computer action into a build; explicit build, repair and review requests still take precedence. Turn classification reuses its selected work pattern for procedure lookup. Do not repair launch latency with per-window phrase exceptions or by bypassing execution safeguards.
- A question asking what prior work already fixed or whether MO should report instead of repeat it is a boardless runtime-status turn. A restored compact handoff reconciles its recorded provider accounting and bounded current Git history before answering. The outbound status projection keeps the compact handoff and recent human dialogue while leaving exact tool payloads in persisted evidence; an old open board does not authorize replay or execution. Only an explicit execution or taskboard-resume request resumes that work.
- `core/learning/memory.py` owns one operator-wide episodic turn index. Recall is query-ranked and bounded, but turns have no dedicated project identity, so current behavior does not provide per-project recall isolation. Project-work context recalls past user requests, omitting old MO answers that can repeat stale verdicts; explicit memory questions retain response excerpts.
- `AgentTurn._context_query_for()` reuses the adopted task objective or the latest user subject in the current conversation for bounded relative follow-ups. Profile-rule ranking, task skills, location conventions and episodic recall consume that query. It grants no execution authority, does not select another session, and stores no second task anchor. Relative approvals and documentation follow-ups put that preceding subject before the new instruction so bounded lexical recall does not spend its term budget on acknowledgement words. An explicit new subject uses its own query; persisted conversation messages provide the same selection after reload.
- The active context bridge admits ranked recalled excerpts individually within the existing source and global budgets, retaining their turn IDs and orientation-only label. Higher-priority contracts remain intact; retrieval does not guarantee delivery when no record fits. `turn_context` schema version 2 reports delivered `flags`, per-source `*_chars` and `context_bridge_source_chars`; `prepared_source_chars` reports pre-admission material, while `memory_records_retrieved` / `memory_records_delivered` distinguish retrieval from delivery. Older unversioned trace flags/counts describe prepared material. The same admitted-skill decision stores a bounded skill-name and turn-number receipt in the existing session owner. Selected skill names stay internal; the next provider turn receives the same runtime fact through the existing work/learning context. Terminal retains one compact receipt of the admitted context categories through its existing activity callback and notice rail. Native runtime phases use the same activity pulse as native tools, only while the phase executes. Natural-language follow-ups stay with the provider rather than a phrase-matched answer. Keep context delivery distinct from episodic-recall disclosure and `◈` learning/mining lifecycle events. Neither traces nor the receipt prove that a provider followed the guidance.
- Profile learning, reviewed suggestions, workflow candidates, and skills are distinct lifecycle stages, not interchangeable ledgers. One normalized suggestion cluster has at most one active confirmed authority: admission suppresses candidates already covered by that authority, and manual or automatic confirmation reconciles older recurrence rows. A materialized `SKILL.md` suppresses its equivalent confirmed-suggestion adapter; candidate rows never activate behavior. A generated pack renamed to `*.retired` is a durable tombstone: every suggestion consumer must exclude its candidate ID, maintenance mirrors decay retirement into the source ledger when possible, and import/export must not reactivate it. Materialized metadata records whether approval was `explicit` or `automatic-safe`. Profile prose and a generated skill can still describe the same correction, so consolidation work must prove prompt-level duplication before removing either representation.
- Unclassified explicit corrections enter the existing pending suggestion ledger
  as `operator_correction`, with source evidence and manual review only. Capture
  records staging only after the candidate is present; reconciliation retries
  older receipts that claimed staging without a saved candidate.
- Active learning review includes promoted workflow habits projected from the existing profile-owned physical skills, not from stale candidate rows. Reviewed actions bind to current guidance, triggers, scope and source identity; usage counters alone do not invalidate them. Undo delegates to recoverable generated-pack retirement, verifies that authority is inactive, and preserves its tombstone so the inert workflow candidate cannot reappear as pending. Retirement failure remains active and reports failure; authored skills and profile prose are separate controls.
- `core/profile/curation.py` owns preview-first, explicit-confirmation archival of stale `operator.md` sections into inert device-local `memory/archive/profile-sections`; it creates the archive before replacing current profile truth. Move still-active procedures into their canonical owner before archiving a mixed procedure/run ledger.
- Generated/profile task skills are global by default; project-local skill roots remain explicit config opt-ins. New `record_convention` writes reuse the private skill root and persist the runtime's canonical `project_root` in the existing pack. Context selection, discovery and PRT require that project match before applying its file globs or explicit-name trigger. The tool receives profile, configuration and effective project from the Agent, not provider-supplied scope metadata. Older unbound conventions retain their cross-project behavior and are labeled unbound; never guess an owner or silently migrate them. Skill writing updates an existing candidate by ID or an authored pack by its unique case-insensitive name and project binding within the selected root, preserving its original folder and supporting files. Ambiguous names or a destination owned by another identity require exact-source reconciliation, never another silently shadowed pack. This explicit tool does not establish autonomous project-convention mining.
- Eligible turns also expose a compact directory from the same loaded physical
  skills: name, purpose, and full read path. It uses the existing context budget
  without selecting a workflow or earning delivery/mastery receipts. The model
  chooses by the request and conversation, reading full guidance when absent;
  already included guidance needs no repeat read. Full selected skill bodies
  retain admission priority over directory metadata. The active work method
  precedes the directory; the directory still precedes optional work-status and
  code-orientation context at the same priority. Explicitly requested and
  query-relevant metadata ranks first; each admitted row retains its complete
  read path. The sandbox permits read-only access to the profile-owned skill
  root, including under project access; writes and unrelated private state keep
  their existing boundaries. Directory admission never activates a skill by itself.
- A `learned-convention` with file-glob scope activates through the existing location selector or an explicit request naming it, within its project binding when present. Arbitrary rule-body words in older generated trigger lists do not widen that scope. New convention writes retain the name as their text trigger; ordinary authored and approved task-skill triggers match complete terms or phrases rather than substrings inside unrelated words. Updating private task-trigger coverage belongs in the existing pack, not a product-specific keyword workaround.
- The structural graph is a per-project code-orientation index, not a learning or personalization database. Profile `important_paths` and the active project may boost ranking; the Dashboard brain layer adds only sanitized count nodes and synthetic explanatory edges. Neither path writes learned project knowledge.
- Post-provider learning runs only after a response clears the normal gates and only on eligible direct-operator surfaces. It indexes episodic turns, captures explicit terms/corrections/workflow candidates, mines suggestions, and may auto-materialize the narrow safe class; `learning.md` is therefore event-driven after initialization, not a manually maintained-only file. Desktop commits exact/FTS turn recall before returning the accepted answer, then runs profile reconciliation, operator capture, and suggestion mining on one bounded serial worker so those scans do not delay speech. Terminal retains synchronous learning notes.
- Suggestion mining reuses `direct_operator_directives` to exclude quoted, fenced and attributed text. Concise-reply signals must concern answers, replies, responses or explanations; task terms such as short circuits or compact JSON are not communication preferences. Existing recurrence/confidence and approval boundaries remain in force. `/profile mine` delegates to the same effective review as `/learning suggestions`; both review and post-turn learning use the existing suggestion-path resolver, including explicit profile locations. Re-scanning does not present confirmed or retired suggestions as pending.
- Post-turn suggestion mining first classifies the newly indexed operator message and rescans only the pattern families that message can affect; a turn with no suggestion signal does not rescan episodic history. Saved-session learning reconciliation reads settled receipt digests once per pass and routes only new or retryable messages through the existing locked owner. These are incremental fast paths, not permission to skip current-turn memory, explicit feedback capture, review notices, or the full operator-invoked learning review.
- Terminal owns the actionable `/profile`, `/learning`, `/skills`, and `/dashboard` commands. Dashboard projections remain bounded read-only data. The local WebView adapter in `core/dashboard/server.py` is tied to that existing TUI and delegates registered commands, draft requests and cancellation to it; rule edits reuse MO Files revision checks and project knowledge reuses `core.knowledge`. It is not another Agent, provider/configuration source or business-state owner. Standalone HTML remains read-only; `/dashboard show` opens the authenticated loopback connection. The full Dashboard contains no voice UI or embedded terminal; its optional native host reuses MO Design window visuals and Desktop per-pixel-alpha entry. Desktop retains its existing request/surface routes; Everywhere and Android remain companion projections. See `dashboard/README.md` for launch, routing and privacy boundaries. Headline learning counts report effective confirmed and review clusters; raw ledger buckets remain audit data, and skill counts use the post-deduplication physical-plus-virtual inventory.
- `core/diagnostics/personalization.py` owns the one official read-only personalization-maintenance report. `/doctor personalization` and `system_health(scope="personalization")` are presentation adapters over that same report, not separate audits or stores. It composes structural profile checks, accepted-learning duplication, effective learning/skill authority, episodic-recall availability, session/closeout retention, bounded file/graph health, and project-file recurrence. Its pending-learning count matches the existing suggestion/workflow review owner; staged product-intent candidates remain a separate project-history review signal and are never labeled as learning items. It never reads session message bodies, mutates state, infers semantic profile freshness or behavioral causes, or grants apply authority. Existing profile, learning, graph, and session owners perform any separately requested maintenance; established retention and exact deduplication remain mechanical, while pending learning choices, semantic profile changes, and named-session deletion outside automatic retention remain operator decisions.

## Graph/index authority

- `core/knowledge/` owns the private per-project knowledge manifest and the
  `/knowledge` command's basic read-only status/query. Collection reuses the
  existing project inventory and graph status; document content hashes and
  inventory identify the saved source revision independently of graph freshness.
  The existing project-index worker maintains the manifest automatically under
  the shared refresh lease, while one manifest byte lock serializes normal writers
  across processes. Status, query, Dashboard, and native search recollect metadata
  without writing or building a graph. Changed saved references are withheld while
  automatic maintenance catches up; no manual refresh is required. Current graph
  availability, counts, and freshness always come from its owner. Queries retain
  matching document headings and source lines, ranked by whole query terms and
  the best matching heading rather than repeated headings or substrings.
  Automatic orientation composes knowledge, history, and structural context
  through `build_project_orientation` within one shared preparation budget.
  The existing context bridge admits each component separately, so trimming
  graph context cannot silently erase documentation/history or report them
  delivered under a graph-only receipt. A failure in one owner does not suppress
  the others. Native `code_search` supplies its
  existing graph hits to this query owner, avoiding a second graph search and
  repeated exact source references. Missing/stale/broken knowledge does not
  suppress graph/history output; graph freshness labels and source-check
  requirements remain unchanged. `core/utils/markdown.py` supplies prose lines
  and headings to knowledge, structural extraction, and the docs checker,
  excluding fenced examples and HTML comments. Test filenames are inventory only;
  the knowledge layer does not infer test coverage or publish generated Wiki pages.

- Terminal Agent startup and ordinary non-Desktop turn entry queue project index
  maintenance in the existing structural refresh worker, using the effective
  workspace and its allowed roots. MO Desktop skips that unconditional startup
  work so companion chat and voice models do not compete with a project crawl;
  project edits and Terminal/Role work retain the existing maintenance path. No
  prompt keyword or additional skill is required. The worker
  checks freshness, reuses current artifacts, refreshes the structural graph for
  source changes, and delegates local Git ancestry to `history.py` when a Git
  HEAD exists. It never initializes Git, records findings, reads old chats, or
  audits/completes tasks automatically. Non-Git source folders can use the graph
  alone. Scoped workspaces remain separate; a restricted subdirectory cannot
  authorize indexing its parent repository. Home/system/private-state roots and
  out-of-project source links are not automatic indexing targets.
  Maintenance is best-effort and nonblocking; graph reads still do not build.
  When a project-work turn starts without graph, project-knowledge, or history
  context, the provider loop keeps those exact sources pending. A newly fresh
  graph is admitted immediately as one bounded query-matched slice; knowledge
  and history remain pending until the shared refresh lease closes, then their
  current bounded slices are admitted at the next provider checkpoint. Each
  source clears after its completed lookup and records a context receipt. The
  provider loop never waits, retries the worker, or appends a whole index.
  Python file summaries, symbol nodes, import targets, alias bindings and call
  relationships share one parsed source set during extraction. Test sources
  remain file owners for navigation and impact; their calls to production are
  aggregated at the file boundary instead of persisting test function/class
  nodes. Identifier-like string keys in top-level Python dictionary registries
  contribute at most 18 terms/160 characters to their file node's search
  vocabulary; keys do not become graph nodes. Natural-language code search
  spends its bounded result rows on distinct source owners and retains the
  highest-ranked symbol as focus. Bounded query
  extraction admits distinct scope terms across a long request before adding
  spelling/stem variants, so later interface, test, and documentation scope is
  not displaced by variants of earlier prose. Narrow direct prohibitions such as
  `do not use MCP` remove that target from positive term and symbol ranking while
  leaving supporting scope statements intact. Public tool atoms may match an
  executor suffix (`code_search` -> `execute_code_search`), and an explicitly
  named non-generic top-level product surface receives a bounded path boost.
  Exact words in an owning source path receive a rarity-weighted bounded boost,
  so a request naming `voice` reaches Voice owners before files that mention
  generic project or reporting prose.
  Two-letter all-uppercase product acronyms and trailing sentence punctuation
  never create symbol atoms. Requested product, verification, and documentation
  seeds plus the next ranked distinct owner render before graph-neighbor
  expansion under the same character cap. The internal role-candidate window is
  at least 64 nodes while the delivered slice remains bounded; connected
  neighbors expand by query score before stable node-name tie-breaking.
  Test-role classification comes from test directories, root-level conventional
  test filenames, or `conftest.py`; a nested product module named `test_*` is not
  test evidence by filename alone.
  Symbol-level
  caller/callee queries remain available. Incremental updates preserve edges for
  unchanged languages; do not add another whole-project import parse or cache.
  Native builds run in the existing isolated, time-bounded helper and cover
  every indexable project file by default. A positive
  `MO_CODE_GRAPH_MAX_FILES` value is an operator-selected
  local cap; zero means whole-project coverage and must not fall back to a hidden
  file-count limit.
  The existing project refresh lease prevents duplicate workers and status must
  report it as active whether another process or the current process owns it. A successful
  project edit during an in-process refresh schedules one coalesced recheck;
  overlapping lifecycle checks do not manufacture another worker.
  Read-only lanes and role tool restrictions skip automatic maintenance.
  `MO_STRUCTURAL_GRAPH=0` or `MO_STRUCTURAL_GRAPH_AUTO_UPDATE=0` disables it;
  `MO_STRUCTURAL_GRAPH_AUTOBUILD=0` prevents missing-index initialization.
  Existing timeout, file/size ceilings, custom graph refresh command, and private
  cache ownership remain in force. Missing/stale/limited coverage remains visible
  through graph/history status and is not evidence that work is correct.
- `core/graph/history.py` owns the derived, project-scoped Git evidence index in
  `cache/project_history/<project-digest>/history.sqlite`. Explicit `python -m
  core.graph.history build` indexes local HEAD ancestry incrementally; status
  distinguishes discovered/indexed commits, shallow history, clipped patches,
  excluded path changes, and absent GitHub discussion coverage. Current graph
  and history admission include extensionless `Dockerfile` build sources.
  Widening shared source admission bumps its policy version; history status
  reports older coverage as stale, and the existing builder revisits commits
  with excluded paths while retaining unaffected commits and durable findings.
  A completed build removes commit/search rows outside current HEAD ancestry;
  this cache is not a path-keyed archive, and abandoned or replaced repository
  lineages must not compete in full-text ranking. Source-linked findings remain
  durable private records, but the search projection admits only findings whose
  checked HEAD is on current ancestry. Switching back to that ancestry, or
  merging it, restores the finding on the next build without rewriting it.
  Current graph queries remain owned by `structural_graph.py`.
  Native code search, MCP search,
  and provider graph context may retrieve bounded history without rebuilding it.
  Provider project-knowledge context does not echo the user's query. Explicit
  document-path matches receive stronger direction, leaving the bounded block
  for verified document/capability rows instead of a header-only receipt.
  Native `code_search` carries only a compact history lead alongside owner hits;
  `project_history` owns detailed inspection.
  Native/MCP search and fresh provider context reuse graph-matched owner paths
  for retained findings as well as commits, even when query wording differs. A shared
  file is a retrieval lead, not proof that two symbols have the same purpose.
  `record --input FILE` retains source-linked analyst findings privately under
  `memory/work/reviews/project-history/<project-digest>/`; the cache is only a
  rebuildable search projection. Records identify owners, observed terminology,
  source hashes, cited commits, and recorded intent with its explicit source.
  Source or inventory changes require reconsideration. Unchanged source hashes
  allow reuse of orientation, never a test pass, completion receipt, automatic
  consolidation verdict, or proof that unknown callers cannot exist. Existing
  task/evidence gates still require current claim-specific evidence. The helper
  excludes ignored/private source paths and the maintainer test overlay, and
  never substitutes a second code graph, clone scanner, or transcript owner.
  Findings may retain explicit private `source_refs`, not transcript copies.
  The native `project_history` tool delegates status, record, inspect and trace
  to this owner; it never creates a second ledger. Recording is a durable private
  mutation and remains blocked in enforced read-only lanes. Inspection reports
  exact changed evidence without opening private sources. CLI `trace ID` has the
  same inspection behavior; `trace ID --source N` resolves one cited user message
  through the session owner or one correlated task through the existing taskboard
  ledger reader. Session
  name/ID plus message-content hash detect overwritten slots and removed or
  ambiguous messages; taskboard references retain session/turn/board/task IDs.
  Search never opens those private sources implicitly. Recorded intent remains
  interpretation; unresolved source conflicts require the user's verdict.

  Consolidation combines distinct evidence, not competing verdict engines:
  `redundancy_check.py` supplies present-code similarity candidates, the
  structural graph supplies current relationships, and history supplies past
  changes and retained reasoning. A clean clone scan does not exclude semantic
  duplication or conflicting ownership; repeated patches or high churn do not
  prove repeated implementation work. Check Git ancestry and current consumers
  before counting duplicate fixes or proposing a removal. Reuse, improve, or
  replace by required outcomes and current evidence, not implementation age.
  None of these indexes imposes a new mandatory work gate or certifies intent.
  Retain reviewed findings through the existing history record owner instead of
  creating another scanner, verdict ledger, or consolidation layer.

  For regression work, inspect the existing tests' setup, assertions and covered
  behavior before adding a case. Similar setup does not establish duplicate
  coverage, and two passing tests do not establish distinct coverage. Use current
  tests, applicable Git history and retained verification receipts together: the
  ignored maintainer QA overlay can inform graph discovery, but its source is
  intentionally absent from the Git history index. None of these tools proves
  that a regression fails on the relevant earlier implementation; verify that
  claim against that implementation when making it.

- `core/graph/structural_graph.py` owns the sole native persisted code graph and its co-located compact status manifest, analysis, labels, and `code_map.html`. Its default location is `~/.mo/cache/structural_graph/<project-digest>/`; project-local `memory/structural_graph/` requires explicit local-state opt-in. Status and health surfaces consume the compact manifest and live file fingerprints instead of parsing the full graph merely to report freshness/counts.
- `core/graph/code_graph.py` is the in-memory source extractor, not another output tree. Public graph context and query callers use `core/graph/structural_graph.py`; it reuses the extractor for native builds. `cache/code_graph` and `memory/code_graph` are retired duplicates. Checkout-local `graphify-out/graph.json` is a read-only compatibility input used only when the native graph is absent, never a native build destination.
- Python call extraction follows unambiguous top-level re-exports, including
  aliases and package entrypoints; cycles and conflicting assignments remain
  unresolved. An empty caller/callee result means no recorded static relationship,
  never proof that a symbol is a leaf or has no runtime callers.
- The native artifact keeps shared edge values in `edge_defaults`; individual edges retain overrides, including non-unit weights. Source files and spans live on source nodes; identical edge copies are omitted while distinct edge metadata is preserved. Export resolves an omitted span from its source node. Loading does not materialize shared values into tens of thousands of edge dictionaries. `structural_graph.py` owns their query semantics, and direct artifact consumers such as the generated human code map resolve the same defaults without mutating the loaded graph. A build serializes before atomically replacing the prior graph. File size alone never prevents a valid project graph from refreshing or loading; bounded query results, compact status reads, the existing worker lifecycle and cache lease own resource usage. Unavailable artifacts remain distinct from valid queries with no match.
- `core/graph/generate_code_map.py` embeds its adjacent `code_map.html` and
  `code_map.js` into the generated self-contained human map. These are presentation
  assets, not another graph owner. Source/work navigation shares one transient
  selection path; detailed adjacency preserves directed edges and distinct
  relationship kinds. Visible-scene framing, finite transitions and hidden-view
  suspension belong to this renderer. The shared skin remains in its existing
  stylesheet slot so live theme changes cannot override the local canvas layout.
- Graph discovery covers Git-tracked files and non-ignored untracked files so a new product owner is orientable before its first commit. Git-ignored content remains excluded except for the explicit bounded `tests/` QA overlay; private state, scratch, generated maps, secrets, and unsupported artifacts must not enter the graph. Freshness uses that same discovery authority, so adding a non-ignored source file makes an older graph stale instead of silently reporting complete coverage.
- Episodic SQLite/FTS5/vector recall, the graph's in-memory BM25 ranker, and human project maps are distinct derived systems. Search preserves explicit symbol atoms inside broad queries and indexes each distinct raw/concept term once from bounded symbol-owned vocabulary. The same tokenizer exposes snake_case, CamelCase and acronym word boundaries while retaining whole identifiers for exact matches; it does not add a second index or topology reranker. Search results remain metadata for navigation; use the existing bounded source-read tools to inspect implementations. Deterministically passed Python callables may form `passes_callback` edges; unresolved receivers remain unknown instead of fanning out.
- Diff impact retains conservative direct file dependants and adds edited
  post-image symbol detail from the same graph. Actual added lines are distinct
  from unchanged hunk context; Python spans include decorators and nested edits
  select their innermost symbol. Confirmed and inferred symbol dependants retain
  the existing resolution tiers. `symbol_coverage` explicitly marks removed
  lines, renames, malformed/incomplete hunks and module/unmapped lines as partial;
  a fresh post-image graph cannot recover a deleted owner's former callers.
  Review the captured diff/pre-image source for those changes. Regex-only
  languages may have declaration-only spans. No symbol result proves exhaustive
  regression coverage or transitive impact, and the supplied diff must describe
  the graph's source revision. Native `affected_tests`, ordinary Agent checks,
  PRT and MCP consume the existing `prt_impact_summary` owner. Changed tests come
  first, then confirmed symbol-linked tests, then other conservative file
  candidates; ordering never deletes candidates or expands the runner's limit.
  MCP diff impact uses that one summary and graph identity, with explicit list
  counts/truncation. Replacing the artifact during capture makes impact
  unavailable. The reviewer source-context window parser remains a separate
  consumer: its unchanged context is intentional, not duplicate edit evidence.
- Query, context, and callgraph reuse one compact adjacency cache for the loaded graph revision; BM25 uses compact posting lists, shares empty identity metadata, and retains only one exact-query result so context and location-scoped conventions do not repeat its scoring in the same warm burst. A long-lived process retains only one active project graph plus its derived indexes during that burst. Five idle minutes release the parsed graph and every derived index; the next read transparently reloads the same artifact. This is a resource lease only, never a turn/request/tool limit or semantic stopping rule. Cache replacement and every successful build invalidate all object-derived indexes together. On large projects, check status, build explicitly when missing/stale, consume bounded ranked slices, and verify against source/tests; never load the whole graph into model context. Interactive and server builds run in a below-normal-priority helper so extractor heaps die with the worker; a cross-process refresh lease prevents duplicate helpers, while the worker's per-project writer lock protects artifacts. Unrequested MO and every MCP `build_graph` call queue that helper instead of waiting; MO continues with bounded file evidence, while the external MCP may return bounded, explicitly stale orientation until status is fresh. An operator-requested foreground MO build may wait. Routed discovery and the focused MO Design catalog expose this same remedy alongside graph readers; read-only project lanes remain enforced by the dispatch sandbox. Agent, Design, and handoff navigation may reuse a bounded, explicitly stale graph snapshot while maintenance refreshes it; source checks still own current claims. Automated mapping, risk, and learning consumers still fail closed. PRT rejects stale graph evidence but can report source-based review results with graph coverage explicitly unavailable. The build ceiling and incremental delta path remain resource boundaries. `mapthis` owns partitioned whole-project orientation: every depth keeps the invoking interface's selected model, complexity adapts only request-local reasoning/token depth, provider transports own their request timeouts, and one cancellation event reaches mapper, remap, synthesis, and compatible transports. `map_project` is a synchronous barrier, so MO consumes relevant current-session findings and completes independent targeted inspection before calling it. Mapper progress uses the standard activity lane and worker registry; the tool has no dedicated panel or transcript visual.
- The repo-scoped graph MCP keeps read/query state in one supervised persistent
  worker. Its parent stdio loop owns the hard query deadline, terminates the
  worker on timeout, and starts a clean worker for the next query; a completed
  build also retires the old worker so no process serves pre-build indexes. The
  worker may remain alive after its graph cache lease expires, but its parsed
  graph, topology, and BM25 state do not remain resident merely to keep it warm.
  Status remains a direct compact-manifest read, and transport responses must
  remain safe under non-UTF-8 Windows console encodings. The process does not
  hot-reload source: after updating this server, restart its client connection
  and verify the loaded version, script timestamp, and query deadline in status
  before treating a live result as current-code evidence.

### History finding inputs

Use the native `project_history` tool with `action: "record"` after inspecting
the owner and its consumers/contracts; no scratch file is needed. The manual
CLI remains `python -m core.graph.history record --input FILE`. Fields are
`owner` (relative source path), optional `symbol`, `description`, `decision`,
`aliases`, at least two
`evidence_paths` including the owner, and optional full `commits` IDs. Re-record
the same owner/symbol to update it; use its returned `id` when the owner moves.
Reintroducing a former owner creates a distinct finding; an ID retained by a
moved finding is never implicitly reassigned to its former owner.
For manual CLI capture, remove task-owned input scratch after the durable record
is written. To add aliases or source references to an existing finding, supply its
`id` and those fields only: omitting `evidence_paths` preserves its original
source hashes, checked HEAD and inventory. A changed owner/description/decision
requires explicit inspected `evidence_paths`; metadata attachment cannot silently
revalidate old analysis. `sources_unchanged` means evidence files still match,
not that analysis has been independently proved. Recheck changed evidence and unknown
consumers before claiming a safe consolidation.

Attach `source_refs` only to explicit, available sources. A `user_message`
reference uses `kind: "user_message"`, `session_name`, `session_id`, and zero-based
`message_index` in the saved `messages` array. On initial capture only, the ID
may be omitted for an explicitly named saved slot: the session owner pins its
actual ID and content hash. Existing hashed references still require their ID
and are never rebound to another session. A `taskboard` reference uses
`kind: "taskboard"`, `session_id`, `turn_id`, `board_id`, and `task_id`. Recording
checks the source and retains its content hash, not its text. Taskboard references
also pin the snapshot timestamp. `python -m core.graph.history trace ID --source N`
opens one reference; ordinary search never opens private conversations.

Include `intent` only with its explicit `intent_source`; `"source_refs[0]"`
must cite an original user message, not a taskboard description or assistant
summary. Replacing that linked message requires an explicit intent/provenance
update; an unchanged intent must never silently point to a different request.
The current unanswered request is not yet a saved conversation message:
never invent a reference for it. Missing, changed, ambiguous or conflicting
sources remain unresolved. Retain an `open_question` when appropriate and ask
for the user's verdict if source inspection cannot settle the choice. Do not
search or copy the whole transcript store into the project index.

## Presentation adapter boundary

- `core/` owns runtime data, decisions, and persistence; `interface/` owns terminal layout, theme state, and interactive surface objects. A core module may cross into `interface/` only at a presentation or UI-command endpoint, and the import stays inside the function that renders or acts so startup remains light.
- `TaskBoard.render()` owns renderer-neutral board text. Structured board events carry that text without generating unused Rich markup or importing the UI. `TaskBoard.render_rich()` remains the explicit deferred adapter to the interface-owned Rich renderer. Board state and evidence never depend on either rendered string.
- `SessionManager.usage_activity()` projects numeric provider receipts from retained local terminal/Desktop conversations for the Dashboard activity strip. It deduplicates saved aliases, bounds reads and reports skipped snapshots; it is not a second ledger, lifetime counter, transcript export, or active-work timer. See `dashboard/README.md` for the visible coverage contract.
- Graph maps, dashboards, and terminal visual primitives may request current palette/CSS values from `interface.theming` at render time. The skin remains one authority: do not copy its palette into core or create a parallel core theme model. Headless-capable renderers retain an explicit neutral fallback where one already exists.
- Slash handlers may defer-import interface commands that directly control the live TUI (skin, help, or workspace presentation). They may not persist interface objects or move product/runtime decisions into the view layer.
- `core/state/preferences.py` owns the private interactive overlay: Terminal model/display defaults, optional Desktop model choice, graph preferences, mail opt-in and per-project LSP selection. Typed writers merge under the shared byte-lock/atomic-write pattern. Startup, `/reload`, and MO Design dispatch revalidate the saved Terminal model through `core.provider.model_catalog.activate_model_selection`. Design has no chooser or persisted model state. Desktop's explicit selection or followed Terminal provider variant is resolved by `runtime_model_selection` and applied only in Gateway's request-local scope.
- `core/state/configuration.py` owns surgical authored YAML updates, including Desktop persistence. It reads fresh disk state under a cross-process lock, validates the merged document and atomically replaces only touched top-level blocks. Settings' declared configuration editors save for reload/restart without mutating the current Agent. The private preference overlay and authored configuration retain distinct scopes; Settings owns neither defaults nor a browser store.
- Native Settings observes running Terminal model selections from existing instance heartbeats. An exact instance/PID/project/slot request uses the existing typed terminal handoff queue; the receiving idle Terminal validates and activates it, then publishes a correlated receipt. Queuing is not successful application. Draft text, focus and saved defaults remain unchanged; busy Terminals reject the request. No model-control daemon or second instance registry is added.
- Mologrthim uses that same typed Terminal handoff for an exact observation-only
  or talk view, with a correlated heartbeat receipt after the native room opens.
  Observation never activates a role, sends input or focuses the Terminal. The
  receiving Terminal owns its session, taskboard and workers; the view remains
  a projection. Its explicit new-conversation action reuses the normal Terminal
  launcher. Closing the room never stops the Terminal or its work. Resource
  totals use the native sampler's opt-in exclusive-root attribution, preserving
  existing workspace pane sampling semantics.
- Do not add top-level `core` → `interface` imports, let rendered text become task truth, or create a second renderer to avoid a bounded adapter call. Boundary changes re-run the import scan, the affected rendering/task-board tests, and structural-cycle verification.

## Multi-instance and work truth

- Multiple terminal MO instances are allowed. Each process gets a stable `MO_INSTANCE_ID` and its own default session slot (`main-<instance>`). Automatic prior-conversation projection excludes every conversation currently owned by another live Terminal process before reading its record; live siblings contribute only bounded heartbeat/taskboard coordination metadata. A cached closed conversation is hidden immediately if another Terminal reopens it. Explicit session restoration remains the only way to adopt that transcript or its taskboard.
- Desktop heartbeat snapshots omit repository status. Repository state belongs to active Terminal or Role project work; the resident companion must not spawn Git on every periodic liveness pulse or ordinary conversation turn.
- `core/runtime/resources.py` owns bounded Windows/Linux Health sampling for local
  process trees. Native probes retain process creation times and validate each
  parent/child edge before attribution: a child that predates its numeric parent
  PID belongs to an older process lineage and must not enter the new root's CPU,
  memory, or process totals. Windows also requires the parent edge to match in
  Toolhelp snapshots taken before and after handle sampling, preventing a PID
  replacement between enumeration and measurement from inheriting the stale
  edge. Incomplete enumeration or inaccessible timing stays unknown rather than
  being reported as a complete tree.
- `SessionManager.list_sessions()` owns saved-session catalog metadata, including
  relative age and the bounded first-user-message preview used by interactive
  selectors; interfaces consume those rows instead of reopening session files or
  reimplementing time/preview rules.
- A provider-bound terminal turn checkpoints through the existing atomic `SessionManager` snapshot after deterministic local intercepts, before tool dispatch, and after each recorded tool result. The sanitizer retains full unanswered requests and tool evidence as explicitly interrupted history; missing tool results remain unknown. Bounded `pending_interrupted_work` is orientation, never the only copy of instructions. Snapshots use compact JSON through the same atomic writer, retaining provider replay and accounting. The normal clean turn-end autosave replaces the checkpoint with the completed exchange. A failed checkpoint pauses further provider/tool work and is an incomplete error result for Gateway and task closeout, not a successful answer. Never add a second transcript/journal owner, and never let a thread-local surface session write this terminal slot.
- Orderly shutdown attempts closeout, profile bookkeeping, and a final snapshot. Forced process or power loss cannot run those exit hooks: recovery uses only completed writes, and an action interrupted before its result was saved must be checked against live state before retrying. Atomic replacement prevents partial JSON from replacing a valid snapshot; it does not promise hardware-level power-loss durability. Completed episodic/profile/learning writes survive independently; interrupted post-answer learning is not replayed as a transaction, and optional queued embeddings may be lost while committed text recall remains available.
- Explicit session restoration can load that session's retained incomplete taskboard regardless of age, without activating it. Ambient board discovery keeps its 24-hour cutoff and all restoration keeps session isolation and evidence gates. Persisted goal reports are historical records, not restartable workers: `/goal resume` resumes a paused in-process goal. After restart, restore the conversation and continue explicitly from its saved evidence; do not automatically restart tools or background work.
- `core/agent/agent_turn.py` owns provider/text orchestration. `agent_turn_state.py` owns mutable per-turn state, while `agent_turn_tools.py` owns tool-batch validation and the single sandbox dispatch loop. Taskboards observe evidence and completion but do not authorize ordinary tools, impose tool order, or narrow the catalog to an active row. Tool-batch prefetch may execute only the leading consecutive read-family calls; once an action or other serial call appears, later reads execute in order so they observe the resulting state. Exact repeated-batch recovery may skip only the completed signature it observed; a different argument remains ordinary work, while an immediate exact repeat after recovery stops the stuck turn truthfully. A `read_file` File-not-found result also remembers its resolved path for this turn: changing view or page on that still-missing path gets one guide then stops, while another source or a file created later remains available. A successful `tool_search` that activates no schema tells the provider to call its already-active match; repeated no-op searches with varied wording stop at the existing doom-loop threshold until a real activation resets the count. This semantic catalog guard is not an automatic provider-request budget. Active-turn compaction removes recoverable completed read/search payloads before pressure forces a handoff. Final-gate continuations carry their owning gate and may declare an exact tool subset; their provider projection contains the current request, rejected candidate, gate instruction, and gate-local tool chain rather than the ordinary exploration transcript. Do not recreate advisory prompt injection, automatic request budgets, phase catalogs, forced planning retries, or another dispatch owner. Cancellation, exact-repeat recovery, explicit provider-request limits and completion gates remain authoritative.
- Changed-file verification and LSP diagnostics report that they fired only when the turn actually recorded modified files. A conversation or read-only turn does not run those project gates and must not emit telemetry implying that it did.
- An explicit operator-selected request ceiling is different: `agent.max_provider_requests_per_turn` (default 0, unlimited), or the `max_provider_requests` turn keyword, uses the existing provider request context to share one atomic count across the outer Gateway turn, direct Agent calls, auxiliary work, transport retries and spawned workers. Provider adapters count model completion attempts at SDK calls or Codex POST attempts, including model retries; HTTP redirect hops, credential refresh and separate embedding work are outside this count. They reserve immediately before the completion attempt; exhaustion emits `[REQUEST LIMIT REACHED]` with the actual count and elapsed time, preserves prior evidence, and never spends another request on finalization. Extension continuation inherits the same count; an autonomous goal pauses at this result instead of retrying or replanning. A later explicit user turn starts a fresh allowance. Natural-language numbers are instructions, not configured ceilings. This is a request count, not a token, price, or wall-time limit.
- Chat-completions requests to the exact `opencode.ai` host identify MO with its
  own User-Agent and a hashed `x-opencode-session` from the existing request-local
  session/surface seed. Both streamed and no-tools requests retain that identity;
  direct callers without an Agent seed use a private provider-instance fallback.
  Other hosts receive no OpenCode routing headers. This follows the provider's
  [coding-client requirements](https://opencode.ai/docs/go/#where-can-i-use-it)
  without exposing raw session names or impersonating another client.
- MO source work uses the same explicit current request, taskboard, sandbox, high-impact, credential, and verification boundaries as other project work. Do not add a source-root-specific approval parser or require the operator to repeat an authorization phrase on each tool call.
- Live terminal steering is owned by `Agent`: at most three exact 12,000-character updates may wait; the next safe provider checkpoint records their raw text as user messages in the canonical session and saves the terminal snapshot before further work, without changing the selected model. Corrections survive follow-ups and save/load without a duplicate system-context copy. Updates still waiting in the in-memory queue are not durable. Interfaces distinguish queued, pending, consumed, restored, and canceled updates; they must not claim a consumed correction was undone.
- The Codex OAuth Responses lane remains stateless (`store: false`). It requests encrypted reasoning output; `core/session/session.py` privately persists the provider's output items, and `CodexOAuthProvider` replays them only to the same model. Native argument bytes survive formatting-only normalization; semantic argument corrections remain reflected in replay so tool results retain their actual target. Chat-completions payloads exclude that metadata. Only the leading system block becomes Responses `instructions`, so the existing trailing dynamic-context message stays after the cacheable history. Requests use a hashed, session-and-surface-stable `prompt_cache_key` as a private accounting/isolation partition; OpenAI documents the key as unnecessary for cache routing on GPT-5.6+, so MO does not treat it as a universal token-saving control. Diagnostics may record only its short hash tag plus provider-reported cache hit/miss/write counts, never prompt content or the raw key seed. `agent.max_tokens` is sent as `max_tokens` by chat-completions adapters, but the private Codex request intentionally sends no output-limit field; status and monitor events must distinguish the configured value from the transmitted limit rather than implying enforcement. Do not add the public Responses `max_output_tokens` field to this private lane without matching first-party/backend evidence. A long-lived provider reloads a changed shared Codex auth file before requests; one server-declared HTTP or pre-output SSE authentication rejection may reload or refresh and retry exactly once before any eligible configured fallback. Authentication recovery never retries a response after any stream event has been emitted. The structured-review transport continuation described below is separate and replays only completed reasoning while its answer is still private.
- Automatic provider fallback is reactive and turn-local. Terminal and MO Design turns already inside the explicit `model.default`/`model.fallback` chain move only forward through its later selectors. A manual selection outside that chain stays selected and surfaces its own failure instead of entering another model route without operator consent; the chooser catalog itself is discovery, never fallback authority. Before fallback, one exact provider/model may receive one bounded retry for a classified temporary overload, rate/concurrency, server, or transport failure only when no provider output escaped. The shared classifier recognizes disconnects before HTTP response headers and Codex streams missing `response.completed`, so both reach this existing recovery path. That retry is per failed request episode: a successful provider response resets eligibility for a later independent request in the same long turn. A short provider `Retry-After` is a minimum and receives jitter; long waits, quota/billing/auth/permission failures, context overflow, and post-output failures do not use this replay path. Capacity headers and error cooldowns are keyed by exact provider/model route; never generalize one model's failure to sibling configured models. Action-required balance, quota, auth, permission, model, and policy failures stay unavailable for fallback selection until that exact route succeeds, is explicitly cleared, or the process restarts; a successful response clears stale state even without rate-limit headers. Fallback must preserve the tool-catalog mode already admitted for the outer turn, even when the fallback adapter normally uses a wider catalog. A failed call may use the configured route long enough to finish the admitted outer turn, but Gateway/direct-turn cleanup restores the exact operator-selected provider/model before later input. MO Design workers retain a distinct `mo_design` provider surface instead of collapsing into generic `worker` routing. Provider audit, monitor events, route-question context, and terminal notices distinguish same-route retry, fallback, and restoration; provider prose is never route evidence. A `finish_reason="length"` empty response (no visible text and no tool calls) is budget exhaustion, not a dead provider: the same provider/model is retried once with a continuation hint before any fallback, while a genuinely empty `stop` response still fails over immediately.
- Assistant history remains provider-neutral in the canonical session. When a provider declares or is known to require replayed assistant `reasoning_content` (including DeepSeek and Big Pickle compatibility lanes), `agent_turn.py` builds a per-call copy and supplies a non-empty compatibility marker only where it is missing. Never persist that synthetic field, mutate replay history, or apply it to providers that do not require it.
- Backend monitoring records completed `runtime_phase` spans for preparation,
  health/compaction, request projection, checkpoints, index scheduling and each
  existing post-provider stage. These spans contain timing and correlation,
  never operation inputs. Opt-in `MO_BACKEND_MONITOR_COVERAGE=1` checks the source
  emitter catalog and public source fingerprint at every turn boundary and at
  provider requests at most once per minute. Intermediate requests do not
  establish a fresh source fingerprint. Checks reread changed files only and
  parse Python emitter declarations; runtime event admission remains immediate.
  Turn end also verifies all source bytes, detecting edits that preserved file
  size and timestamps while reusing unchanged AST analysis.
  Catalog changes and unregistered event types emit `trace_coverage`; unknown
  payloads are never stored. A disk change does not prove that a running process
  loaded it. Coverage checks and sink failures never authorize or block work.
  Computer-use receipts persist through the same monitor as `computer_event`,
  retaining target bounds/revisions and hashed observation signatures without
  labels, screen contents or a second action owner.
  Chat Completions records preparation/transport spans, the first streamed
  chunk, request timeout and the SDK retry allowance. An allowance is not an
  observed retry count; automatic SDK retry activity may remain hidden.
- Codex Responses progress reuses the backend monitor's `provider_stream` event
  for ordinary and auxiliary calls. It records the effective reasoning setting,
  connection status, first activity phases, bounded event/item/text counts,
  elapsed time and completion/error/cancellation state, correlated to the
  existing session/surface context and one stream identity. Repeated activity
  is sampled at most once per 15 seconds; silence produces no invented progress.
  Actual Codex and chat-completions request payloads also provide keyed
  fingerprints of instructions, ordered schemas, options and bounded input-prefix
  checkpoints. Canonical response events retain a numeric-only `usage_reported`
  projection to distinguish absent cache fields from reported zero; existing
  usage accounting still owns totals. Native request options alone own reasoning
  effort; no frozen textual effort preference competes with them in turn context.
  Request-local adaptation keeps lightweight conversation and a direct configured-MCP
  lookup at low effort. A simple execution request may lower only an `xhigh`/`max`
  ceiling to `medium`; project investigation, active roles and open-board status retain
  their configured depth.
  No prompt, output, reasoning content, encrypted replay item or credential is
  copied into these records. Stream activity proves transport progress, never
  review correctness; source/test gates retain that authority. Monitor failures
  cannot change provider behavior, and progress creates no transcript messages.
  On an interactive Agent turn, those sampled phase events also update the
  existing transient activity line with the request number. They report only
  received connection, reasoning, text, or tool-call signals; they do not imply
  the provider finished or create transcript progress prose.
- Momentum compaction keeps older tool-chain summaries as runtime-owned orientation with exact results in atomic archives. At provider checkpoints, pressure may also age completed bulk read/search observations outside the recent window into labeled excerpts; their exact calls and archived results remain addressable. An admitted `complete_task` result makes its preceding current-turn chain eligible for the same archived compaction, with bounded assistant progress retained for orientation; the exact completion receipt and all later work stay verbatim. Rejected completions, user steers, unconsumed rounds, and unresolved active work never authorize that boundary. No completed or active evidence is replaced when its archive is unavailable. Retention follows transitive archive references from saved sessions and recent archives, including cycles; unreadable references abort pruning before deletion. Per-call turn health shares its current pressure measurement with this existing compactor and measures again after a change, before a pressure handoff; it never replaces active work with tool-name-only summaries. Low-pressure passes require at least another configured threshold of recoverable savings, so a recent compaction does not suppress a later full threshold accumulated in the same turn. `session_compact` reports actual replacements and serialized character reduction, including opaque replay metadata. This is not measured token or billing savings; result-cap text estimates and provider-reported cache accounting remain separate. Verification reuse still requires an unchanged candidate.
- Compacted tool archives use `read_file`'s existing session projection and
  numbered paging. Their default evidence view retains original tool arguments
  and outputs while omitting opaque provider replay and presentation fields.
  Formatting neither changes the archive nor grants access; canonical path and
  archive naming checks are supplied by the dispatcher. Other JSON stays literal.
- Live context estimates include the provider's textual message projection and
  the selected tool schemas, plus reported reasoning-token counts attached to
  retained, model-compatible native replay. The provider's effective reasoning
  context selects current-turn or all-turn replay. Counts leave with their
  messages on compaction; legacy rows without usage remain unmeasured. Never
  tokenize opaque replay/image bytes or carry an old input-token floor across
  compaction. These estimates are not billed token counts.
  Cumulative input replay is not current context pressure and never triggers a
  handoff by itself. The existing observation compactor owns recoverable replay
  reduction without replacing a low-pressure active session.
  A long turn may hand off again when its context refills; critical
  pressure can bypass the ordinary turn cooldown only when the prior seed fits
  the estimate AND the critical path is not itself cycling within the same turn
  (a wall-clock floor, `turn_health_critical_handoff_min_interval_seconds`,
  defaults to 60s). An oversized required seed honors that cooldown rather than
  looping or stopping useful work from an estimate alone. Provider overflow
  recovery remains separate. Failed archive writes preserve the live session
  and propagate failure, never successful-handoff telemetry.
- Handoff archives preserve the source messages before hydration cleanup. The
  compact seed links its exact archive and detailed capsule, preserves uncut
  user instructions and active steers, and refreshes dynamic context for the
  new session. Audit/file references are correlated to that session or turn;
  shared-worktree changes without correlated mutation evidence remain explicitly
  unattributed. Short Git status columns and paths remain intact, and assistant
  progress text survives when its message also starts a tool call. Archive paths
  are local recovery records for a specific missing passage, not routine sources
  to reread during continuation. Retained verification may be reused for an
  unchanged candidate, not repeated as handoff ceremony.
- Every provider request receives a bounded live projection of the current
  taskboard row. It supersedes older board references retained in the turn, so a
  completed row cannot cause a later provider round to repeat or complete the
  wrong task. The taskboard transition and evidence gates remain authoritative.
- Provider sanitization retains completed tool rounds across user steers and
  reuses interrupted-tail repair for missing results. Removing internal controls
  or orphan messages is semantic cleanup; only actual retention loss increments
  the trim counter used by automatic handoff.
- During an in-flight handoff, the active turn's request supplies the goal;
  later user corrections and restrictions remain in order and take precedence
  over recorded taskboard rows. The compact seed retains the original and recent
  user requests separately from its bounded assistant reports, and names the
  actual objective rather than a generic continuation placeholder. A handoff does not prescribe a fresh discovery
  pass. Background verification completion delivers its existing cached result
  directly to the provider; retrieving it never requires another test call.
- Prior-turn verification notices also retain their bounded diagnostic output, labeled as historical evidence. A successful ignored-overlay manifest repair removes only matching preflight-failure receipts, marks an in-flight verification stale, and re-arms the affected-test gate; unrelated passing proof and LSP evidence remain reusable. A missing test target is an error, never a passing run. Git task evidence distinguishes `status` from `check_ignore`.
- Dynamic context retains source priorities and proof labels without repeating authority boilerplate per source. Explicitly named skills take precedence over incidental matches and ambient profile hints, while project/safety contracts remain first. Authored skill bodies stay intact through parsing, selection and bridge admission under the existing budgets; a block that does not fit is omitted and earns no delivery or mastery receipt. Role overlays directly receive their authored contract outside those ordinary context budgets. Other optional guidance may use labeled excerpts. Neither context nor handoff guidance requires repeating unchanged source reads or passed verification.
- Session catalogs and mutations reuse continuity's metadata-first surface
  classification, including its documented legacy Desktop rule. Terminal
  selection/removal cannot consume Desktop or per-principal histories. Actual
  automatic names, including `mo-desktop`/`mo-desktop-*` and `scheduler-*`, are
  reserved against portable creation, sharing, and renaming. Conflicting
  existing surface ownership is rejected without overwriting the snapshot.
  Unsharing an explicitly portable conversation returns it to named Terminal
  history, regardless of which scoped device last wrote its shared revision.
- Provider tool projection and deferred discovery apply the same surface scope:
  process-local `desktop_sync` and Desktop-only `everywhere_pair_android` are
  absent outside Desktop. Terminal's independently owned local screen tools
  and slash-command pairing route remain available.
- Corrective gate controls are provider-only continuation messages, not
  conversation. `core/session/session.py` marks them transiently, removes the
  marker from the provider payload, drops the control after its corrective
  request, and excludes it from sanitization/persistence. A new continuation
  path must preserve that boundary rather than saving internal instructions as
  assistant speech.
- Claim correction has one provider retry per turn, but every replacement is
  checked against the current successful tool evidence. Shell chronology records
  mutating commit/push/deploy actions; for a deployment outcome, a later
  successful AND-linked remote assertion of both exact Git HEAD and active
  service also counts even when an earlier compound restart returned nonzero.
  That state evidence does not advance an execute task row as though it performed
  the deployment. A remaining detected claim gap returns a short user-safe
  failure while keeping its claim label and rejected draft internal, stays
  incomplete for Gateway/goals, and does not reach learning or extension
  closeout. The post-provider pipeline emits the actual continuation event,
  including its gate, for the shared trace counter.
  When final gates or post-processing change visible text, the Agent discards the
  original provider replay items and reasoning so the replaced draft cannot
  override the delivered answer on the next request.
- Workspace awareness renders the worker registry's existing active/recent
  summary once; completion notifications belong to the shell callback, not a
  second projection of completed history. Project instruction references allow
  reuse of already-read, unchanged files. Workflow preference extraction shares
  the capture gate's quotation/attribution boundary so pasted history does not
  become direct operator guidance. Learning materialization compares the same
  literal bounded recommendation signature as the physical skill loader;
  diagnostic clustering normalization does not identify physical ownership.
- Surface environment context may read the exact MO Shell host HWND inherited
  by that terminal and its live selected-window property. It exposes only a
  bounded, untrusted Windows title as availability orientation; it never treats
  the title as observed content or copies attachment state into heartbeat,
  memory, graph, taskboard, or another surface.
- Singleton resources—headless service, Telegram poller, scheduler—are resource-locked, not blocked by every MO terminal. `/heartbeat instances` is the lightweight sibling-process view. A sibling heartbeat is liveness/work context, not file ownership: in-process dirty-file attribution comes only from active `WorkerRegistry.claimed_paths`, and unmatched shared changes remain unattributed. Its bounded taskboard summary includes the redacted active task title, or the next ready title when no row is active, so workspace awareness can expose a sibling's current delivery phase without creating a second task or ownership registry. Before changing shared Git or runtime state during a reported commit/push/deploy phase, the provider checks current Git plus sibling task/session evidence and resolves sequencing from those sources instead of asking the operator when they are sufficient. Headless `service_started` health distinguishes disabled components from configured components that failed to acquire/start their resident worker: the latter report `stopped` and degrade the service, while thread-backed components report running only from observable liveness. Explicit `/resume` and named session switches restore the selected session's parked interrupted-work metadata with its transcript.
- `core/files/service.py` is the one local MO Files path boundary. Public
  surfaces use opaque location IDs and relative paths; `core/files/host.py`
  adapts that same owner to an authenticated Desktop/terminal `files_v1` lane
  without opening a listener or creating another queue. A host in project mode
  exposes configured project roots; only an already explicit
  `access.mode: full` host exposes drive roots. Hidden/private state, symlinks,
  secrets, and absolute paths remain unavailable in either mode. Cross-device
  send stays in `core/transfer/`, and terminal-history clearing never removes
  queued or active custody. Its bounded `import_stream` publishes one completed
  external file into an allowed writable folder with exclusive destination
  creation; Desktop's temporary browser QR session uses this same boundary.
- `core/state/attachments.py` owns the shared private attachment catalog. It resolves `media/attachments` through `core.state.paths.resolve_state_path()` from the active config; callers pass config and never infer a state root from sessions, `mo_home()`, path parents, or a surface-local wrapper. Imports reserve their destination atomically and append provenance under a cross-process lock, enforce the eight-file/20 MiB surface bounds, and never allow one stable ID to rebind to another source. Design JavaScript, command spools, and session sidecars receive only safe metadata and opaque IDs; the broker alone resolves contained catalog paths and presents attachment content to the model as untrusted evidence.
- Work patterns in `core/context/work_patterns.py` provide compact request-scoped guidance. Only stable, genuinely useful procedures crystallize in `core/tasking/procedure.py`; ordinary review/fix work does not receive a generic inventory/check/edit/recheck scaffold before the provider sees the request. `core/runtime/turn_intent.py` may choose whether to seed or resume a board, but a later current-turn instruction is admitted normally.
- Deterministic turn intent is a routing and context-cost hint, never semantic authority or tool authorization. It may omit `set_plan` for boardless chat, lookup, and status turns, but it must not reduce a normal Terminal turn to zero tools, prohibit mutation because of a chat/lookup label, or override a provider's conversation-aware reading of an authorized follow-up such as “yes please.” A first-person how-to or manual-action clarification does not become repository work merely because it contains `change` or `edit`; an external install request is simple execution and does not receive graph/history orientation. Direct remember/recall asks use the existing episodic-memory route; continuity and memory matching share one spelling-tolerant remember-word pattern so an observed typo cannot silently drop the recall index. Scoped work continuations and concrete corrective code/interface feedback admit optional model-owned planning; only a later `set_plan` creates the board, so ordinary chat and unrelated dislikes stay boardless. Dedicated design, profile, role, extension, and computer-actuation boundaries retain their exact catalogs. Ordinary delivery and service-lifecycle actions already named by the current task do not require a second approval; credential access and genuinely destructive actions retain exact current-turn boundaries. Explicit `confirm`/`confirmed` wording satisfies that boundary only when the same request names the matching destructive action family; a negated confirmation never does. `access.mode: full` widens filesystem roots only and supplies no action authority.
- Computer actuation authority is surface-neutral and request-local. Current-turn action hints prime the bounded computer family on Terminal and Desktop; they never remove tools discovered by the model or disable further discovery. Role, explicit lane, extension, sandbox, and execution guards remain authoritative. Screen/point/walkthrough intent may prime useful tools, but completion never forces actions based on wording. The shared gate checks actual mutation evidence; inline post-action observation satisfies it on Terminal and Desktop. A live operator steer supersedes the earlier request admission before an in-flight provider response may dispatch tools. Stale observation context or a prior turn never grants action. A resumable board may carry one bounded typed `capability`/`action`/`target` contract; Gateway may restore that contract after restart, but must never derive it from task titles, objectives, or conversation wording. Every act requires an exact target and a fresh same-target observation, and completion requires action plus a newer same-target observation. Three identical verified action/observation cycles with an unchanged stable observation signature stop further action as no progress. Compatibility adapters must preserve this contract rather than adding surface-specific bypasses.
- Joined surface diagnostics select one durable conversation and fail closed on provider, tool, heartbeat, or monitor rows that lack matching session/slot correlation. Desktop defaults to the latest saved reply's canonical turn metadata; an explicit older turn remains exact. Conversation excerpts retain available turn timestamps and instance IDs. Missing reply metadata is labeled unpinned history, never inferred from the snapshot save time or substituted from an older reply. Process-level recency or a matching surface name is never evidence that a row belongs to the current conversation.
- Provider tool schemas have one catalog in `tools/definitions.py`. `capability_routed` is the provider default; explicit `deferred` and `full_static` configuration remains available for compatibility/debugging. Closely related operations have one action-aware owner each (`git_status`, `web_fetch`, `show_viz`, `file_transfer`, and `migrate`); the retired Python entry points are deleted rather than kept as a second callable surface. Project audits and evidence reviews expose the native code-graph family regardless of complexity. Whole-project mapping stays explicit; advanced graph/duplication diagnostics stay lazy for routine or simple requests and are directly exposed for moderate or complex investigations. `redundancy_scan` is only a read-only provider adapter over `core/diagnostics/redundancy_check.py`; its similarity candidates never prove common behavior or safe removal. `computer_observe` exclusively owns live-screen observation while `perceive` remains local-file/PDF input. `core/agent/agent.py` selects the provider view, `core/agent/agent_turn_tools.py` owns the single safety/evidence loop, and `core/agent/agent_turn_dispatch.py` invokes stateless `tools.TOOL_EXECUTORS`. Computer schemas describe each backend's actual argument semantics: Desktop `context` is immediate observation, `wait` needs a non-empty element condition, and native `set_value` does not submit. The shared normalizer rejects unsupported `submit=true` before input; only browser `type` implements it. `computer_act action=open` navigates the observed Connected Chrome tab with `kind=browser`, or opens the default browser with `kind=desktop`; no separate URL-opening tool is registered. The stateful `set_plan` and `complete_task` controls bypass that stateless map and run only through `core/tasking/agent_taskboard.py`, which owns validation, mutation, persistence, and result text. Shared file/phone tool families live in `core/tooling/tool_constants.py`; safety, evidence, and UI projections may remain distinct when their semantics differ.
- Native project tools that actually ran remain schema-visible for one clearly referential follow-up, then return to normal capability routing unless that follow-up uses them again. The bounded carry-over excludes screen, phone, profile, discovery, and integration tools and never expands sandbox or action authority. `system_health` is the canonical provider-facing reader for current Goal, worker, taskboard, and exact-turn usage status; status questions do not require screen observation, transcript search, or catalog discovery.
- Naming a configured MCP server exposes only that server's family directly. After an MCP call, the same family remains available for related non-project follow-ups without another `tool_search`; naming another server or starting unrelated project work clears that continuity. This is an in-process routing hint, not permission, durable state, or a reason to expose every MCP server.
- Delivered graph, documentation or project-history context primes the existing code-evidence family independently
  of lexical or profile-expanded work categories; Desktop assistance retains its
  lazy engineering catalog. Exposure never forces a tool call.
  Bounded read-only reviews retain project orientation while remaining boardless;
  selecting a small planning policy must not discard their relevant project sources.
  Tool discovery prioritizes exact names and focused capability matches, counting
  each query term once. Detailed-description overlap is a fallback when no focused
  candidate matches; weak incidental matches do not pad the result limit. Existing
  project, role and capability filters remain authoritative. When discovery is
  available, a provider call to an omitted tool uses that same discovery path and
  exact filtered catalog before normal dispatch; a reduced schema working set is
  not a reason to end an otherwise valid continuation.
- Model/procedure plans use the existing `set_plan` owner for `pause` (a recorded blocker, followed by a question) and `cancel` (terminal cancelled rows). Neither transition grants approval or stops processes. Actual process cleanup remains with its execution owner. Explicit cancellation preserves completed rows and all evidence, clears pending action state, skips completion-only hooks/gates and is excluded from automatic resume. `ask` remains an approval row; missing information must not be turned into a new approval. Revision preserves IDs and evidence only for unchanged unfinished work contracts; changed work receives new IDs and no inherited row evidence.
- Procedure scaffolds may replay proven work structure, while each row must still clear its evidence gate. The ordinary assistant response is not a task row. Tools execute through the normal sandbox regardless of active row kind; only matching evidence advances a row. On an ordinary turn board, completion merges matching evidence already gathered across that board, including evidence observed before a plan revision, even when the active row has partial evidence; changed rows still receive new IDs and no inherited row evidence. Goal rows never borrow evidence from another row or iteration. Provider prose cannot finalize persisted work rows, and replay never bypasses verification.
  At turn finalization, an ordinary board with every row completed stays completed even if final response handling fails; an unfinished board may become abandoned. A zero-open board containing cancelled rows is not complete.
  Review plans contain established required work; repair rows follow confirmed
  in-scope defects. A no-change review closes from matching evidence. An ordinary
  board's new verify row may use valid same-turn proof for the unchanged candidate;
  row age alone does not invalidate it. Final reporting needs no additional row.
  An explicit empty revised tail may remove unnecessary ordinary-plan work when
  completed evidence remains; it cannot drop pending approval gates, goal
  obligations or required acceptance coverage.
- A `/goal` run owns one live model-planned taskboard for its whole lifecycle. It begins empty so only `set_plan` and tool discovery are available; the accepted plan must map every explicit goal AC ID to a concrete inspect/edit/execute row and each verification-required AC ID to a verify row before work tools are exposed. GoalPlan mirrors that board for durable display, never competes with it. A goal row may close only from evidence earned on that row, verify rows require a passing verification result, and the completion auditor checks delivered and verified AC coverage and reopens the exact deficient row. Provider/runtime failure markers and repeated-tool stops are unfinished outcomes, never completion evidence. Two consecutive plan-only revisions may refine the open tail; a third revision without an intervening evidence-producing action stops that Agent turn while preserving the board. Catalog discovery, whether separate or batched with a revision, does not reset this limit. Consecutive incomplete Goal turns re-plan once on the second result and pause on the third instead of retrying indefinitely. Explicit Goal stop signals the active Agent turn, records worker and taskboard as paused, and emits a paused `goal_finish`; resume clears that cancellation signal and reactivates the same board.
- Exact `test_runner` reuse is scoped to one Gateway runtime turn and its tested root/candidate. Reuse never authorizes a complete gate: follow the active project's proportional verification policy. Verification uses that candidate-aware result cache instead of also storing a batch-local copy that could retain an obsolete running marker. Row progress is not a mutation. A known file edit outside the tested root preserves its proof; project mutations invalidate completed proof but retain pending lifecycle markers as stale-candidate work. Stale markers are filtered before pending/failure/pass classification, so they neither prove nor block the current candidate. Terminal callbacks retain their bounded results independently of reuse, and stale or prior-turn receipts are not current proof. Fresh same-turn receipts re-enter the normal tool/evidence path without another subprocess. Requesting the same live verification again waits on its existing same-candidate job before the next provider request, including completed-batch replay; it does not launch another process or use provider requests as a polling loop. The wait yields for operator cancellation, a pending live steer, candidate invalidation, or the command timeout. Completion publishes terminal evidence before releasing waiters. Unrelated tools remain available after the initial background launch. Verification rows remain open while owned work is pending or current results fail or are inconclusive; scoped fresh verification may cover subsequent edits without rerunning an old broad suite. Only competing broad verification for the same root is held while its worker runs; scoped checks and unrelated diagnostics remain available. Shell and test_runner pass the execution owner's callback directly, never through a process-global mutable setter. Foreground and background commands share one bounded-output process lifecycle. An explicit positive timeout is the execution limit and is never extended by command classification. Omitted limits default to 60 seconds for shell, 420 for pytest/test_runner, 1800 for the canonical suite, and 3600 for explainer media. Preflight remains a separate bounded operation. Timeout output begins with its error status before retained partial evidence, so Agent monitoring and downstream validation cannot report a killed command as successful. Actual command invocation determines test scope; a quoted mention cannot create a suite worker or a pass claim. Canonical-suite lanes and shell-backed background work terminate the owned process tree on timeout, interrupt, explicit cancellation, or worker disconnect. Reuse `core.tooling.shell_processes.kill_process_tree()` instead of adding another watchdog or process registry.
  Report claim/citation/coverage checks use the existing `inspect` task kind;
  a custom executable assertion uses `test_runner` when a `verify` row is intended.
  Correct a mismatched unfinished plan through `set_plan` rather than running
  unrelated tests to close it. Private report edits do not by themselves require
  product retesting. A single literal removal of exclusively turn-attributed,
  Git-ignored scratch may preserve proof after the exact target disappears only
  when the cached tests' known inputs are outside that removal. Selected tests,
  broad or unknown verification inputs, compound commands, expansions and unowned
  shell mutations retain normal invalidation.
  Unknown scripts invalidate proof whether invoked through `shell` or
  `test_runner`, including when they write and then fail. Only actual direct
  verifier invocations or established inspections preserve it; quoted verifier
  names and verification mixed into a compound command cannot certify that the
  rest of the command left the candidate unchanged.
  A selected pytest invocation also retains its collection restriction for that
  project and Gateway turn, independently of proof invalidation. The automatic
  affected-test gate may reuse complete current proof, but otherwise discloses
  limited coverage instead of expanding selected verification into whole files.
- Windows cmd commands use an outer `/d /s /c` command wrapper so compound commands beginning with a quoted executable retain paths with spaces. This preserves cmd semantics: a final zero exit code does not certify earlier commands joined with `&`. Web search owns backend-error/fallback disclosure before external-content framing; a failed configured search is distinct from an unconfigured service, and fallback parse/network failures remain errors. Empty Instant Answer results disclose limited coverage and alternative research routes. Content-scan warnings are heuristics, not proof of injection; naming a credential setting alone is not a credential value.
- Direct Windows `python -c` execution repairs model-encoded literal newline
  separators only when the original program is invalid, the separators are
  outside Python string literals, and the repaired program compiles. Valid
  string escapes and still-invalid commands remain unchanged.
- The canonical-suite CLI configures UTF-8 stdio, streams unbuffered pytest progress through the caller's existing output handling, and returns the failing lane's actual exit code (124 for timeout, 130 for interruption). Run it directly: a `findstr` pipeline or a trailing successful command can mask its exit status. Timeout and interruption terminate only the lane's process tree through the existing shell-process owner.
- Post-provider memory and learning index only a final that has cleared the response gates. A provisional draft rejected by task, evidence, safety, or surface completion checks is never durable recall truth. `set_plan`, `complete_task`, and `tool_search` remain excluded from post-provider evidence/action counts because planning and catalog controls do not inspect or mutate the claimed subject. Ordinary execution does not require a taskboard.
- Shared memory and learning accept direct operator conversations, not every
  message carried by a user-facing transport. Terminal and authenticated
  one-operator companion surfaces are eligible by their established surface
  contract. Telegram grants eligibility only for an authorized private chat
  through a thread-local scope; group and supergroup turns remain excluded.
- Conversational role activation and `/role activate/off` use `role_work`'s
  existing Agent lifecycle; listing and discovery require no preselected role.
  Selection updates only the owning conversation, and Desktop persists through
  its own snapshot owner. Specialist operations retain their Project Architect
  prerequisite and ordinary lane guards. A role change returns its new contract
  to the current provider turn; subsequent turns resolve persisted metadata.
- Questions about previously implemented features remain bounded lookups unless
  they also request current work. `work_signals` checks that distinction before
  lexical inflection normalization can turn historical verbs into build work;
  such lookups do not prepare repository graph context or seed a taskboard.
- The active context bridge renders sources in priority order under one global
  character budget. It reserves room for later useful guidance or complete
  records before expanding earlier optional guidance; contracts and selected
  skill bodies remain whole. Recalled excerpts are admitted individually as described
  in [Personalization and project boundaries](#personalization-and-project-boundaries).
  Its `included_keys` and rendered-source accounting
  name only sources actually delivered to the provider. Generated skill mastery
  records opportunity only for an included skill source; a pending outcome is
  settled by an explicit correction or explicit unqualified positive feedback,
  never merely by the next accepted user turn. A task-triggered current skill is
  budgeted before ambient prior-conversation history; the canonical prior request
  remains uncapped on eligible turns when it fits, but unrelated history may
  not crowd the selected current-task route out of the provider turn.
- `core/runtime/continuity.py` renders prior conversations through one historical
  context source for conversational/history requests, retained across follow-ups.
  New project work uses current conversation and query-ranked recall without
  injecting the latest other session's task. Explicit continuity and resume keep
  their context; Desktop references stay isolated. Each record binds its own subject,
  recorded start/save times, and snapshot reference. Detached assistant answers
  are not session summaries: prior answers and actions are verified from the
  canonical transcript. Only an interrupted-turn checkpoint carries its preceding
  reply to interpret a resume request. Incomplete answers remain marked as such.
  The current-work snapshot reports runtime state without repeating a
  separate, undated history summary. Recent and subject-matched references stay
  bounded and scoped to the active conversation's surface and principal.
  Bounded lookups use that same subject selection even without a runtime-status
  snapshot; repeated queries and relative follow-ups reuse its cached selection.
  `referenced_conversation_path()` permits `read_file` for an exact selected
  transcript only after rechecking its saved ID and surface/principal ownership.
  It never grants the sessions directory or adopts another taskboard. Empty
  current-session rows and clean Git state do not imply no pending project work.
  The existing provider catalog includes `read_file` when profile or previous
  conversation context was actually delivered; role, lane and extension filters
  still apply. Tool exposure and permission to read a path remain separate.
- `core/learning/status.py` and `core/runtime/work_learning_status.py` are
  read-only count/state projections. They may report task, boundary, recall,
  and review facts but never combine them into a confidence, alignment,
  understanding, Sync, VIBE, or Momentum percentage. Trace validation is
  diagnostic evidence and does not create learning suggestions. Session
  closeout metadata is read only from its structured sidecar; Markdown remains
  presentation, not a parallel metadata store. Workspace dirtiness at closeout
  comes from staged and unstaged Git content diffs, not porcelain/stat metadata;
  untracked files remain an explicit opt-in. The session tool audit proves path
  touches, not ownership of remaining hunks after a selective commit or another
  session's edits. Dirty paths therefore remain continuity warnings, including
  paths touched by this session and paths with missing attribution. Open taskboard
  items, workers/goals, and task-owned scratch still produce unresolved closeouts;
  a clean closeout proves neither a clean Git tree nor task completion. Retained
  summaries remain orientation: reuse original matching verification receipts for
  an unchanged candidate, and refresh only missing or invalidated evidence rather
  than rerunning tests because a session closed or a commit was made.
- Audit findings require evidence relevant to the stated subject and scope,
  with uncertainty reported alongside supported findings. Caller/registration
  checks, competing owners, compatibility, tests, and runtime observation are
  selected as applicable, not a fixed checklist or minimum tool count. The
  existing system prompt and scoped work context own that reasoning discipline;
  final gates do not certify semantic ownership, duplication, or removability
  from answer wording or search keywords. Successful execution is not proof of
  a conclusion, and graph/history orientation is not current verification.
  Execution chronology, task-completion, verification, and permission boundaries
  remain enforced by their existing owners.
- Native code-search, caller/callee, and graph-read tools are taskboard-backed
  inspection evidence. Procedure rows must not ask for graph orientation while
  silently discarding the graph call from their own evidence.
- Workflow candidate status is inert staging/review evidence, never promotion
  truth. A successful explicit promotion writes one physical skill pack under a
  cross-process promotion lock; that pack is both approval record and runtime
  authority. Candidate rows, old promoted ledgers, and prompt-enhancer context
  are not parallel activation paths. Historical rows marked promoted without a
  matching pack remain pending for explicit review.
- Workflow source adoption uses the shared narrow control recognition in
  `core/context/gateway_helpers.py`, consumed by both template selection and
  pre-provider dispatch. The local path requires a leading adoption command
  with a source introducer; local promotion requires a standalone approval
  command. Applying an existing workflow, quoted/negated adoption, and ordinary
  tasks mentioning skills or workflows reach the provider unchanged. Neither a
  workflow keyword nor an unrelated colon, URL, or path makes a task into
  reusable learning. Post-provider promotion uses that same approval boundary;
  a suggestion ID mentioned in a task is not approval or dismissal authority.
  Source scanning, sandboxed reads, inert candidate storage,
  and explicit promotion remain owned by their existing learning/dispatch paths.
- Closeout conditions and structural-graph patterns remain diagnostics; they do
  not manufacture workflow candidates. Candidate staging requires operator text
  or an operator-requested external workflow source.
- Project `tmp/` is shared, ignored, non-authoritative scratch—not a staging copy of product source. A task that needs scratch uses a unique subdirectory and owns only the paths it creates. `agent_turn_tools.py` records successful write/edit paths for the current turn; tool-audit rows carry exact session/turn correlation, and session closeout refuses to attribute missing-correlation or foreign rows. The existing backend-monitor `tool_result` row additionally keeps one bounded, redacted excerpt only when a tool returns an error, and surface trace renders that diagnostic after conversation compaction; successful tool output and file content remain absent. The final scratch gate inspects only attributed paths, exempts exact operator-requested destinations, and may request one cleanup continuation; it never scans or deletes the shared tree. Durable output is written to its real product path or private-profile authority before completion.

## Ordinary project-change verification

Project LSP selection lives in `state/preferences.py`, alongside Terminal
preferences, keyed by the canonical project's digest. Normal config remains the
server/timeout baseline; the overlay stores only on/off, not another provider or
server configuration. `lsp/manager.py` owns lazy clients isolated by project and
language, project shutdown and typed diagnostics. `/reload` replaces the old
manager after shutdown. New document text clears previous diagnostic receipts;
versioned notifications must match the current version. Unversioned server
notifications do not establish exact version correlation. Successful edits rearm
the existing gate. Native Settings delegates project On/Off/Default to
`LspManager.set_project_selection`; server command/argument edits use the shared
authored configuration writer and take effect on reload. Dashboard exposes
read-only LSP status and recorded checks, with no preference mutation route. The
on-demand `/dashboard checks` projection uses existing monitor evidence and never
turns configuration, visited gates, absent events or graph orientation into
passing verification. No always-on status badge, polling service or model call.

The graph/history context and code-search paths described above support ordinary
project work. They provide orientation; they do not automatically certify project
intent, save every useful finding, or invoke the duplication scanner on every edit.
The existing post-provider pipeline owns the affected-test check below, separately
from an explicit PRT review.

- After a recorded Python editing batch, the next already-needed provider request receives a
  bounded structural-impact view from the same current-project diff/graph owner:
  changed symbols, confirmed/inferred dependants, test candidates, and partial or
  unavailable coverage. It is untrusted orientation, not verification or a PRT
  invocation. The existing turn state retains that view until mutation invalidates
  it; it is neither a persisted ledger nor an extra provider request. Final test
  selection reads current evidence independently and still reuses valid proof.
  Ordinary turns never join or start a synchronous graph refresh. The existing
  background maintainer owns refresh; stale impact returns bounded unavailable
  coverage without loading the known-stale graph. Explicit PRT retains its
  separate refresh policy.
  This path covers recorded Python edits, not arbitrary shell, non-Python or
  external changes. Interruption preserves recorded tool evidence through the
  session checkpoint, not this transient impact view or proof of completion.

- The ordinary Agent's post-provider affected-test check routes Git, graph and
  test work through `Agent._effective_project_cwd()` plus its effective allowed
  roots, not MO's installation checkout. Its existing path-diff reader includes
  staged and new files, including explicitly changed local QA. Successful editing
  batches also queue the existing coalesced, out-of-process index maintainer so
  dependency discovery can refresh while the next provider/test round proceeds;
  project startup and turn entry remain safety-net lifecycle checks, not separate
  index owners. Repairing the ignored test-overlay manifest re-arms only its
  failed preflight receipt without queuing project indexes because it changes no
  indexed source. Git add, commit, and push preserve successful verification only
  when before/after candidate snapshots confirm tracked, untracked, and ignored
  test-overlay files are unchanged; hook edits therefore invalidate reuse and
  queue index maintenance. Git staging and push skip maintenance only when that
  snapshot proves their hooks left working files unchanged. A successful local
  commit always queues maintenance because local HEAD history changed.
  Documentation-only turns skip project/test discovery.
  It consumes one `prt_impact_summary` and reuses the same bounded runner. Current-candidate same-root broad proof or
  complete scoped proof remains valid; partial scoped proof removes already
  covered targets from the selected test run. Graph discovery uncertainty never
  rewrites that proof. When passing current receipts cover every named target,
  graph limitations remain diagnostic without another generic report postscript.
  A real test failure gets one repair continuation. Any automatic post-provider
  gate recovery stops after 12 provider requests without an accepted result,
  preserves its edits and evidence, and records the unresolved interruption.
  Otherwise, missing/stale
  graph evidence, disabled execution, timeout, runner failure, or a missing
  runner result instead appends a bounded coverage disclosure to the accepted
  answer without another provider request. The existing turn state retains that
  disclosure across later gate continuations; memory indexes the same disclosed
  answer. Directly changed tests remain eligible when dependency expansion is
  unavailable. This check does not prove exhaustive test coverage.

## PRT review contract

This section owns PRT's shared maintenance contract. Compare these source owners,
current configuration, the exact reviewed candidate, and the active project's
instructions before changing behavior. Historical commits, changelog entries,
profile procedures and review scores are evidence to inspect, not substitutes
for the current implementation or operator authorization.

| Responsibility | Existing owner |
| --- | --- |
| Local command and per-Agent admission | [agent/agent_slash.py](agent/agent_slash.py), [review/lease.py](review/lease.py), [gateway.py](gateway.py) |
| Target identity and isolated source | [review/diff_review.py](review/diff_review.py), [review/snapshot.py](review/snapshot.py), [diagnostics/source_inventory.py](diagnostics/source_inventory.py) |
| Structural impact and Git history | [graph/structural_graph.py](graph/structural_graph.py), [graph/history.py](graph/history.py); see [graph/index authority](#graphindex-authority) |
| Review, exact-source admission, confirmation and scoring | [review/diff_review.py](review/diff_review.py), [review/review_scorer.py](review/review_scorer.py) |
| Selected provider and transport | [agent/agent.py](agent/agent.py), [provider/model_slots.py](provider/model_slots.py), [provider/provider.py](provider/provider.py) |
| Report, durable history and next-turn evidence | [review/prt_report.py](review/prt_report.py), [review/maintainer.py](review/maintainer.py), [agent/agent_turn.py](agent/agent_turn.py) |
| Confirmed local committed-target correction | [agent/agent_slash.py](agent/agent_slash.py), [worker/runtime.py](worker/runtime.py), normal Agent tool/sandbox/verification gates |
| Worker/activity and terminal presentation | [worker/runtime.py](worker/runtime.py), [interface notes](../interface/README.md#current-composition) |
| Trusted GitHub review and publication | [review/github_delivery.py](review/github_delivery.py), [workflow contract](../.github/workflows/README.md#prt) |

The [FAQ](../FAQ.md#what-are-goal-mode-and-prt) owns command examples. Local
Plain `/prt` selects uncommitted work when present, otherwise HEAD. Worktree/path
targets stop after the current-terminal report. Commit/range targets hand
confirmed unresolved findings to the normal Agent worker for investigation,
minimal correction and focused verification. The command then uses the same
review engine to reassess the original change plus corrections until its target
is met with no unresolved findings, or progress is blocked. It reports the
attained score and remaining findings; a perfect 5.0 is not mandatory. This uses
no separate repair provider, Git hook, amend/commit path, or acceptance ledger.
GitHub remains review-only.

Local PRT is manually invoked after work, before committing or on a committed
target. Pre-commit review focuses on the captured proposed work and reports only;
post-commit review pins the committed change and may correct/reassess it under the
existing worker contract. These are target-aware uses of one reviewer, not two
engines. A push alone does not invoke local PRT; configured GitHub PR events and
authorized mentions own hosted review.

- Structured PRT calls recover interrupted Codex streams in the provider's
  existing response accumulator. Only completed encrypted reasoning items are
  replayed with the original input, model and reasoning setting. Unfinished JSON
  remains private to that buffer and is discarded before resuming; it never
  becomes a finding. Every recovery requires newly completed reasoning since
  the previous replay. A repeated failure without that progress is terminal.
  Without an explicit outer request limit, continuation has no fixed attempt or wall-clock cutoff: elapsed time and
  reasoning-item counts do not establish that useful review work is exhausted.
  The progress check permits replay; it does not prove review correctness.
  Recovery is disabled with a visible token callback, tools or tool-call output,
  for ordinary turns, for explicit provider failures, and on cancellation.
  EOF without `response.completed` is a transport failure, never a successful
  partial answer. Each connection retains the existing one-attempt auth recovery.
  The existing stream trace records recovery and the actual connection attempts.
  Successful reports retain the recovery count and label token usage partial:
  usage for the disconnected request was not returned. `review_calls` counts
  structured completion calls; `transport_resumes` counts their continuations.
- PRT's no-tools JSON review has bounded input/output context and is an
  auxiliary request, not a normal conversational turn. Provider-native disabled-thinking control is applied
  request-locally only when the selected review provider is DeepSeek-backed,
  because DeepSeek's documented `max_tokens` budget includes hidden reasoning;
  other provider families retain their configured reasoning behavior. The
  operator's persistent model/thinking selection remains unchanged. An empty
  length-stop, malformed review JSON, or malformed confirmation JSON gets one
  compact retry and the provider reason remains in the review ledger if recovery
  still fails. `ReviewReport.token_usage.review_calls` counts every structured
  completion attempt, including JSON recovery, tier escalation and confirmation;
  provider transport continuations remain separate in `transport_resumes`.
  Confirmation may return only a proved subset; omitted, malformed, or locally
  unproved candidate entries are rejected rather than promoted or treated as a
  pipeline failure. Only invalid JSON or an invalid confirmation envelope uses
  the recovery attempt and can make the review incomplete.
- `/prt` is the explicit local PRT entry point. The Gateway turn scope protects
  this Agent's shared provider state and bounds the wait for its foreground turn.
  A changed target during that wait requires a fresh request. Other terminals,
  goals and workers do not block the isolated review phase, which claims no edit
  paths. After a complete commit/range review returns confirmed unresolved
  findings, `agent_slash.py` starts an ordinary background Agent worker and
  supplies the confirmed finding paths as explicit edit claims; the existing registry then enforces worker limits
  and edit conflicts. The PRT activity remains running through correction and
  reassessment; inline correction shares that activity's execution slot.
  Standalone worktree/path reviews never start that worker.
  Correction requires live HEAD to match the reviewed SHA and rejects overlap
  with pre-existing dirty paths. Its snapshot compares the original base with
  the current candidate, including supporting repairs and excluding unrelated
  paths already dirty at the initial review. Each fresh verdict feeds the next
  correction. An unchanged or repeated candidate, blocked worker, changed HEAD,
  or incomplete assessment stops with an explicit reason. The original commit
  can be fully undone by a valid correction: an empty combined diff then means
  the original base was restored, with affected tests selected from the original
  change. An empty standalone path review still has no reviewable evidence.
  The original commit verdict remains immutable; candidate rows carry `correction_of` in the same
  ledger and do not certify the unchanged commit.
  `core.review.snapshot` captures each local review in an independent temporary
  Git checkout without changing the operator's index, branches or commits.
  Path reviews copy staged/unstaged/untracked candidate source and ignored local
  QA through the existing source inventory and compare the final worktree once
  with its pinned base, excluding staging-only intermediate content.
  Commit/range reviews use the pinned committed source even when the operator checkout is dirty. Captured local QA
  is identified separately from committed product source. Its destination must
  resolve inside the snapshot even when the historical tree contains links.
  Capture is validated before review, with at most three attempts if files change while copying.
  Persistent capture changes produce an incomplete report, never a clean score.
  Graph/related-source/callgraph evidence and bounded affected tests use that
  same copy. Native graph nodes can seed the copy's existing incremental builder:
  only files whose bytes and original graph fingerprints match receive the new
  fingerprint. Changed, added and deleted files remain stale until the native
  builder refreshes them. Seeding is not a freshness verdict. No second extractor
  or persisted graph format is introduced.
  Its graph cache and source are removed after review. Each terminal
  can run its explicit review concurrently; requests are not silently queued,
  shared, or deduplicated across different evidence snapshots. The existing
  graph refresh owner and private ledger byte locks coordinate their resources.
  The repository must remain within allowed roots; a restricted subfolder does
  not authorize copying its parent. Reports identify the source snapshot, later
  edits excluded from it, graph coverage, test execution/skips and confirmation
  exclusions, elapsed duration and the originating terminal instance or GitHub
  surface. Worker phase updates reuse the existing activity display without
  adding transcript messages. A clean score is not E2E or delivery certification.
  Automated post-work PRT belongs only to the trusted GitHub workflow, whose
  separately pinned evidence worktree remains isolated from reviewer code,
  is passed directly to the review engine without a second source copy, and
  explicitly disables execution of PR-owned tests. GitHub remains review-only.
  Local Agent/Gateway work never commits merely to unblock PRT, and no ambient
  post-commit daemon or Git hook is installed; correction begins only from an
  explicit local commit/range target.
- PRT acquires bounded historical context through `core.graph.history`, using
  changed owners and graph-impacted paths. It reuses a fresh index or explicitly
  refreshes the derived Git index during review; no second scanner or ledger is
  created. The effective allowed roots apply to the resolved repository too;
  a restricted subfolder cannot authorize indexing its parent. Revision-scoped
  queries filter ancestry before ranking and never rewrite the shared HEAD
  index. Older-revision or unsafe-live-evidence reviews
  exclude current analyst findings; GitHub reviews exclude private history
  findings altogether. PRT never opens their linked chats or taskboard sources.
  History is sanitized, untrusted orientation in the existing no-tools prompt,
  never evidence sufficient to promote a finding. Local/GitHub reports disclose
  indexed ancestry versus retrieved candidates, freshness, shallow history,
  clipped patches and the bounded owner sample. Missing/partial history is a
  coverage limit, not a code defect.
  Judge conventions by applicable contracts and required outcomes; a justified
  simplification or replacement is not wrong merely because it differs from an
  older pattern. Ambient local post-commit review remains disabled; only an
  explicit local commit/range target can begin the correction lifecycle.
- Local affected tests run through `diff_review._run_affected_tests`: at most
  eight candidate Python paths, serially in one subprocess, with a 240-second
  default timeout and isolated state/temp roots. `prt.run_affected_tests` may
  disable execution. A test failure is a finding; timeout or runner failure makes
  the review incomplete. Graph-selected paths, collected tests and executed
  scenarios are different evidence: an empty list or successful subprocess
  does not prove exhaustive coverage. Captured local QA may differ from an older
  reviewed commit and is never represented as committed product source.
- [Ordinary project-change verification](#ordinary-project-change-verification)
  consumes the same impact summary and bounded runner without invoking PRT.
- Provider concerns require exact diff/source evidence and independent
  confirmation before scoring. A local hunk cannot prove a caller, validation
  path or test is absent. Check surrounding owners before implementing a
  finding; reject a disproved allegation explicitly rather than changing code
  to satisfy a score. Missing graph/history or skipped tests remain visible
  coverage limits, not proof of a defect or of correctness.
- `diff_review.append_review_audit` retains full reports in private
  `logs/review_audit.jsonl`; `maintainer.record_prt_review` keeps compact local
  history in `memory/work/reviews/maintainer.jsonl`. These serve different readers
  and share their existing writer/pruning locks, not a second review engine.
  `prt_report.route_prt_report` owns terminal/plain-text rendering. Worktree/path
  reports queue one bounded process-local event. If the review finishes during an
  active provider turn, `_call_provider` attaches that event at the next request
  checkpoint without interrupting the in-flight response; otherwise the next
  foreground turn receives it. The unread footer marker names the report state
  and never claims delivery readiness. Commit/range findings are instead
  serialized as bounded untrusted evidence in the normal correction worker's
  explicit objective; the final reassessment and correction outcome use the same
  provider event. `agent_turn.py` atomically consumes only the exact event
  included in a successful call; a failed call or newer event must not lose that
  evidence. Display or context delivery alone never starts a correction—the
  explicit committed-target command and worker owner do.
  The bounded provider summary lists unresolved findings by severity before
  positives or verbose coverage, identifies the reviewed target, and discloses
  how many finding summaries fit. A consumed event does not establish delivery
  of every finding; full details remain in the existing report/history owners.
- Preserve the distinction between a completed review, its delegated correction,
  inconclusive review infrastructure, source/test verification and delivery. A
  clean score is limited to reviewed evidence. A correction worker must re-check
  each finding, reject stale or false claims, use focused verification, preserve
  unrelated work, and leave commits/pushes/deployment to their existing owners.
  Reuse completed verification for unchanged source. Later edits need
  verification appropriate to that delta, not an automatic full-suite or
  repeated review ritual. Use the repository's normal verification contract.
- Scoring starts at five and subtracts evidence-weighted unresolved-finding
  penalties, adjusted by structural risk and established operator preferences.
  Score and confidence are heuristics, not correctness probabilities. Rounding
  can yield 5.0 with an unresolved informational finding; the target still
  requires zero unresolved findings. An incomplete review has no meaningful code
  score. Neither a high score nor a green job alone certifies E2E or delivery.

## Public/private boundary

- The local `~/.mo` profile, `~/.mo/operator` extension files, and private tooling/docs never ship. A plain `git push` publishes the repository.
- Profile extension state at `~/.mo/operator` is not a nested repo or submodule. There is exactly one product repo (`IQMO/MO`). `core/state/paths.py` never treats a repo-local `operator/` as a source.
- Ignored local docs are not product authority. Promote product-safe guidance into tracked docs or move private/local records under `~/.mo/memory/...` before relying on it.
- The privacy guard at `~/.mo/operator/privacy_guard.py`, installed as `.git/hooks/pre-push`, blocks operator identity, secrets, private paths, and tracked `tests/` content. Private extension commands come only from the profile hook; an empty profile exposes none.
- Repository test preflight is publication-safe: its normal and canonical-suite paths run only tracked product checks and validate the ignored test-overlay manifest. The `--guards-only` publication boundary deliberately skips that local manifest, so it is not a substitute for overlay closeout. Because the manifest pins both test content and product HEAD, review compatibility and refresh it after either changes before relying on a later preflight or suite result. Test preflight also fails closed on canonical credential-source drift in product Python: secret-shaped process-environment reads, alternate credential-file configuration, raw env-file parsing outside the broker/explicit migration, an incomplete config rejection, or a changed service map. It never discovers, imports, or executes profile-owned executable hooks.
