# MO Agent Map

The machine-checked [product and capability contract](CAPABILITIES.md) owns the compact product charter and maps each built-in capability to its runtime, surface, state, security, discovery, acceptance, and source-layout owners.

Compact orientation only. [`AGENTS.md`](AGENTS.md) owns repository work rules; component READMEs explain user behavior; maintenance contracts own invariants.

## Runtime authority

- `core/prompts/system.md` — MO's identity, behavior, tool use, and evidence contract.
- `core/agent/` and `core/gateway.py` — turn loop, slash commands, Agent/Gateway coordination, and surface routing.
- `core/gates/` — input/final/claim gates; provider prose cannot bypass task truth, verification, policy, or approval.
- `core/tasking/task_board.py` and `core/tasking/` — task rows, evidence, procedures, and completion authority.
- `core/runtime/turn_intent.py` and `core/runtime/continuity.py` — turn classification plus open-work/continuity orientation.
- `core/local_extensions.py` — neutral bridge to optional private profile extensions; an empty profile adds nothing.
- `core/session/` — session persistence, compact user-visible continuity, closeouts, and context handoff.
- `core/transfer/` — the single resumable file-cargo model, SQLite/spool service, target/presence routing, and sender outbox/retry custody. It feeds the existing attachment catalog after receipt; it is not a second catalog.

## Tools, context, and providers

- `core/tooling/` and `tools/` — native tool schemas, dispatch, sandboxing, and bounded results.
- `core/context/` — workspace, work patterns, and prompt-safe context builders.
  `core/context/project_context.py` owns the applicable `AGENTS.md` chain and read snapshots. The tool dispatcher admits root-level starter creation through `core/context/project_docs.py`; `core/gates/post_provider_pipeline.py` rechecks current rules before reporting. Skills and the graph do not own project policy.
- `core/provider/` — validated provider/model routing with lazy SDK/client initialization and the private credential broker.
- `core/mcp/` — MCP manager with exact fail-closed allowlists and standalone servers.
- `core/desktop/` — shared screen observation, targeting, actuation, and pixel-routing policy. `core/browser_bridge.py`, `tools/browser.py`, and [`clients/chrome/`](clients/chrome/README.md) form the single optional Connected Chrome-tab DOM/viewport transport and reuse that target/observation owner; Terminal and Desktop share it, Design observes only, and the Chrome action starts or ends an exact tab share; and no model detach action, isolated browser process, or profile remains. Without a shared tab, Terminal and Desktop may reuse the existing exact native-window path rather than adding another browser transport. `core/systemcare/` owns calibration, scoped read-only inspection, selected native maintenance, exact-plan revalidation, receipts, recovery originals, cancellation, and private device state; its tool/Desktop adapters add no policy or state owner.
- `core/perception/`, `core/imagegen.py`, `core/imageedit.py` — distinct read, generate, edit, and show image paths.
- `core/visualize/` — terminal-native trees, charts, panels, and images inserted as ordinary transcript entries; `core/explainer/` owns sourced scene projects, saved canonical/custom styling and quick layouts, project-owned hashed images/bounded clips, narration/audio freshness, deterministic motion/callouts/transitions, measured status, staged MP4 rendering, FFprobe validation, and the non-checkout `mo --explainer` route.

## State, memory, learning, and graph

- `core/state/layout.py` — private state layout and generated `~/.mo/README.md`; all state paths resolve through `core.state.paths.resolve_state_path()`.
- `core/profile/` and `core/skills/` — bounded profile context, exact facts, roles, skill loading, and approval-owned skill changes.
- `core/learning/` — episodic SQLite/FTS5 recall, optional vector fusion, feedback/workflow learning, and evidence-grounded skill materialization.
- `core/graph/structural_graph.py` — the only native persisted structural graph and interactive code-map owner.
- `core/graph/code_graph.py` — extractor/compatibility API, not a second graph output or cache authority.
- `core/mapping/` — `mapthis` whole-project orientation using bounded source slices plus fresh graph evidence.
- `core/knowledge/` — automatically maintained private project manifest, bounded project-turn retrieval, and read-only `/knowledge` status/query over document headings, capability/command records, and current graph nodes. The existing project refresh lease coalesces workers and one manifest byte lock serializes writers across MO instances. Turn context verifies each selected backing document and omits changed rows; native `code_search` reuses existing graph hits and adds non-duplicate manifest references without a second graph search. Test filenames remain inventory; manifest-source freshness and live graph status are checked independently. It is orientation, not a second authority.
- Structural graph, episodic FTS5/vector recall, in-memory BM25 ranking, the knowledge manifest, and the human project map are complementary. [Project history](core/graph/history.py) adds Git evidence and source-linked findings, not policy or completion proof; see [its contract](core/MAINTAINING.md#history-finding-inputs). Never dump a whole large graph into model context.
- `core/dashboard/` — `snapshot.py`/`providers.py` collect bounded existing-owner values; `projection.py` supplies the versioned redacted contract to Terminal, browser, Desktop and Android. `registry.py`/`render.py` retain richer evidence; `dashboard.html`/`.css`/`.js` separate WebView/read-only markup, styling and interactions, `mo_desktop/mo_renderer.py` supplies existing native window chrome, and `server.py` ties its loopback connection to the existing terminal, Files, learning review, LSP, knowledge and graph owners. It creates no Agent or business-state authority. See its README for connected versus read-only operation.

## User surfaces

- `interface/` — production prompt_toolkit terminal. Styled finalized transcript lines use native terminal scrollback by default while the bounded canonical buffer still owns snapshots and Live Control; `interface/README.md` documents controls and compatibility mode.
- `mo_desktop/` — opt-in auto-intent companion with an isolated `mo-desktop` session, shared Gateway, one fixed Cube-branded four-view Dashboard panel, screen help/action, voice, files, MO Phone with the phone trackpad (`mo_desktop/phone/`), MO SystemCare, continuity, roles, and profile-owned apps through `core/local_extensions.py`. See `mo_desktop/README.md` and `mo_desktop/MAINTAINING.md`.
- `mo_shell/` — separate Windows native shell for one canonical MO terminal and one explicitly selected managed/embedded window, with an adjustable divider, compact four-cube handle, and canonical Desktop visual-state bridge. See [`mo_shell/README.md`](mo_shell/README.md) and [`mo_shell/MAINTAINING.md`](mo_shell/MAINTAINING.md).
- `mo_everywhere/` — disabled-by-default authenticated hub for pairing, jobs, continuity, Android APIs, and the memory-only MO Live Control relay. See `mo_everywhere/README.md`.
- `mo_publisher/` — optional public introduction/support/privacy/deletion pages and anonymous AI-report custody, in a separate process/state home. Its README owns operation; it supplies no customer accounts, purchase entitlements or private-Hub access.
- The Google Play Android client is distributed and maintained outside this repository. [`ANDROID.md`](ANDROID.md) owns public availability, requirements, setup, and privacy guidance; `mo_everywhere/` owns the tracked Hub protocol it uses.
- Telegram and headless service entry points reuse the same Agent/Gateway and private state; they are surfaces, not parallel brains.
- `Dockerfile`, `.dockerignore`, and `compose.yaml` package that same headless owner as a non-root, read-only-source container with explicit private-state and project mounts; they do not introduce a container-specific runtime.

## Cross-surface boundaries

- Every surface owns its own transcript/session by default. The only cross-surface transcript exception is a named conversation explicitly marked portable; `core/session/` remains its single owner, and scoped clients receive only its bounded user/assistant text projection. Bounded continuity events remain separate and contain lifecycle, intent, outcome, and next step—not messages, tool payloads, screenshots, secrets, or taskboard mutations.
- Multiple terminals use stable `MO_INSTANCE_ID` values and default `main-<instance>` sessions; opening another terminal cannot overwrite the first terminal's active slot. On nontrivial turns, workspace coordination reuses those live instance heartbeats to warn when another terminal, or MO Desktop, has active work in the same repository: its running turn (a short, redacted summary of what it was asked), its task rows, the files it just edited, a computer action it is driving, and what its registered workers, tests and goals are doing. That signal never assigns unclaimed dirty files to the sibling MO. Explicit PRT reviews capture independent source snapshots, so sibling activity does not block them or require a commit. See the [PRT review contract](core/MAINTAINING.md#prt-review-contract) for source, concurrency, evidence, and verification boundaries.
- Singleton services use a resource lock: headless service, Desktop resident, Telegram poller, scheduler, and one Everywhere coordinator per device.
- Native Live Control uses exact `remote_control` controller authority and separate `remote_host` identities, connects outbound from real terminal/Desktop hosts, and persists no frame or input stream.
- `core/transfer/` owns file cargo: exact `file_transfer` plus `control`, stable device IDs, idempotent requests, verified resumable chunks, bounded Hub custody and explicit target receipts. Surface adapters reuse its state.
- MO Files uses exact `file_browse` for opaque location/list/read access, independent `file_manage` for revision-checked mutation, guarded folder creation, manifest-backed Trash restore, and the existing cargo owner for send. Windows discovery and MO-host workspace panes reuse one dedicated workstation controller credential with exact per-feature scopes; notify, host, and cargo identities stay least-privilege.
- A hub-started terminal pins the serving hub's resolved non-secret profile routing across persistent `tmux`; its provider credential remains hub-local. Desktop and Android adapters never receive raw roots or create another persistent storage server.
- Desktop MO Files can explicitly open one five-minute local browser transfer listener for selected files or one allowed local folder; its QR bearer link grants no pairing or raw browsing and uses unencrypted HTTP on the chosen private LAN address. The hub and every enabled Desktop/terminal Live Control host publish one source through the existing authenticated `files_v1` lane; drive roots appear only for a host already in full-access mode. An authenticated Store client may present one explicitly selected phone folder as a separate read-only, locally consented source.
- Dashboard adapters read `core/dashboard/projection.py` and may navigate or emit allowlisted requests to existing owners, but never add a dashboard-specific mutation path for memory, learning, prompts, task evidence, or profile state. MO SystemCare tools/presentation likewise use one `core/systemcare/` owner; inspection remains read-only and selected native maintenance requires an exact fresh catalog plan, original-state/candidate revalidation, applicable permission, acknowledgment and confirmation. Files/SystemCare share the native app process/theme/source handshake through `mo_desktop/app_window.py`; feature policy and state remain with each canonical owner.
- The Store client supports explicitly selected, read-only phone-folder sharing
  with lock/Stop gates. It does not expose Android Accessibility automation,
  broad storage, or privileged system operations as public capabilities.

## Public/private boundary

- Runtime state is private by default under `~/.mo`, `MO_HOME`, or `MO_STATE_HOME`; explicit project-local opt-in is owned by `core/state/paths.py`.
- `memory/` holds profile, learning, sessions, work, and surface state; `logs/`, `run/`, `cache/`, and `media/` own their named generated data.
- `personal/` is opaque: MO does not index, migrate, synchronize, inspect, or delete it automatically.
- Private profile-extension records live under `~/.mo/operator` (or explicit profile overrides) and are never tracked or shipped.
- Credentials remain in canonical private stores. Public source, examples, QR payloads, device records, sync, and audit output contain no secret values.
- Profile Git sync uses a separate validated private repository and exact manifest; conversation continuity never enters Git.

## Verification

- Maintainer-local `tests/` is ignored and never published. Refresh its manifest after intentional test edits and after source commits, before scoped verification, with `python -m core.diagnostics.test_preflight --write-overlay-manifest`.
- Automated post-work review is GitHub-only and remains review-only: the trusted workflow reviews same-repository pull requests and publishes the review/status without editing PR code. Local `/prt` is explicit and target-aware: worktree/path targets report; commit/range targets delegate confirmed corrections and focused verification to the existing Agent worker. No local maintainer daemon or Git hook runs after commits. The [PRT review contract](core/MAINTAINING.md#prt-review-contract) maps execution, snapshot, graph/history, provider, reporting, and GitHub ownership.
- Use scoped tests while iterating. Before a direct broad pytest sweep run `python -m core.diagnostics.test_preflight --collect`.
- The complete Python gate is `python -m core.diagnostics.test_suite --workers 2`; it owns preflight and collection, then runs bounded parallel and required serial lanes once. Do not run standalone collection immediately before it. Use this gate only under the scope rules in [AGENTS.md](AGENTS.md).
- Android client build, release, and device acceptance remain in private local custody and are not represented by this repository's Python verification gate.
- CI runs the public/private boundary guard, documentation-link validation, compilation/import checks, and the optional maintainer overlay when present.
- This is a Python project. Do not add or require Node tooling.
- Episodic recall benchmark (dependency-free, reproducible with `python -m core.learning.recall_benchmark`): 30 paraphrased queries over 55 turns: BM25 recall@1 `0.667`, recall@3/5 `0.800`, MRR `0.722`; controlled fusion demonstration recall@3/5 `1.000`. Its "hybrid" column is a controlled concept-embedder demonstration, not a production embedding-model benchmark. Current Python suite evidence belongs in the CI run and changelog rather than an undated claim.
