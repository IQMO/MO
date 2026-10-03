# MO Agent — Repository Contract

This file governs work in the MO Agent repository. It does not assign runtime
identity: `core/prompts/system.md` owns MO's identity and behavior. Codex,
Claude Code, and similar coding agents are not MO and must not role-play it.

## Core contract

- Work evidence-first. Verify product/runtime/code claims with current files, Git, logs, tests, or runtime observations before reporting them.
- Understand the current implementation before changing it. Default to production maintenance: diagnose, fix, verify, and consolidate; do not introduce unrequested features, redesigns, or dependencies.
- Use targeted edits for existing files. New small files may be written directly. Never print secrets, tokens, keys, credential values, private keys, or `.env` contents.
- Self-knowledge about MO comes from this checkout's source, especially `core/prompts/system.md`, never generic agent assumptions.
- Prefer simple code that preserves behavior. Treat the operator's explicitly described interaction as the acceptance criterion; do not silently substitute a different UX, workflow, or implementation goal. Start with the smallest direct change in the existing owner, and add complexity only when current evidence proves it is required; state that reason before expanding scope. Remove proven stale duplication; do not delete real features without evidence.
- A replacement may not merge with the old path silently retained: delete the old path in the same change, or register a `# COMPAT(<id>)` marker with a removal condition in the same change (enforced by `core/diagnostics/compat_debt.py`).

## Reporting and fixing discipline

- Lead with the evidence-backed verdict. Do not append defensive tails that merely transfer risk back to the operator.
- Fix root causes. If the same area needs a second patch, stop stacking guards and remove the state that makes the defect recur.
- Diagnose and fix in one pass when authorization and evidence allow. Never call work done, clean, current, deployed, or broken from a stale summary.
- Attribute a refusal to the component evidenced by the result. A host rejection before process creation does not establish a child-process error or an MO guard failure. Name a reviewer, matched policy, or confirmed false positive only when diagnostic evidence supports that attribution.

## Project and verification rules

- This is a local Python project. Node.js is unavailable; do not suggest npm/Node solutions. Add no dependency without operator approval.
- Playwright and separate browser/profile automation are not part of MO verification. Browser acceptance uses MO Connected Tab against an operator-open target when explicitly required; Desktop and Design acceptance uses the real running surface. Keep deterministic protocol and state checks below those UI boundaries.
- The ignored `tests/` tree is a maintainer-local overlay. It is verification evidence, never public product source and must never be tracked.
- After an intentional ignored-overlay edit, refresh its source baseline and content digest with `python -m core.diagnostics.test_preflight --write-overlay-manifest`.
- When you change, rename, or remove a symbol, function, or behavior, find its tests in the ignored `tests/` overlay in the same change and update or remove them. A test that fails against current source is either a real bug or a stale test — diagnose which before changing it. Never make a test pass by asserting removed or different behavior, and never treat a stale green test as correctness evidence. Keep the overlay manifest current when tests change.
- Scoped verification is the default, including ordinary commit, push, and deployment work. Reuse completed verification for an unchanged candidate instead of rerunning it as ceremony. Private delivery shorthand is interpreted only from the active operator profile; using it never expands test scope, cancels an earlier restriction, or bypasses normal authorization boundaries. If the operator says no full suite, do not start one. Run the complete gate only when the operator explicitly requests it, when an expressly defined release acceptance requires it, or when changing the test harness, collection, privacy, or publication gates themselves and no operator restriction forbids it. Tracked code changing by itself is not a reason to monopolize the machine with the full suite.
- Before a direct broad pytest sweep, run `python -m core.diagnostics.test_preflight --collect`. Do not run that standalone collection immediately before `python -m core.diagnostics.test_suite --workers 2`; the complete gate owns its boundary preflight and authoritative parallel/serial collection. Run the complete gate at most once for one stable delivery candidate and do not use `-n auto` by default. While iterating, repeat only scoped tests for the touched modules.
- The same proportional cadence governs every expensive verification lane. When the private local Android client is present, iterate with the narrowest Gradle check for the changed owner and run its full ladder or emulator/adb acceptance only when explicitly requested or required by its release boundary. Treat device/emulator verification as one intentional final acceptance pass, never a per-fix step. Boot at most one emulator per session and keep it running.
- Tests own their temporary state. Keep `MO_STATE_HOME` and pytest temp roots outside the checkout, remove them on teardown, and never bulk-clean shared checkout caches or another session's scratch. Use recorded creation paths for later cleanup; a later `TEMP` or `TMP` value may point elsewhere. Exclude artifacts the operator requested to retain.
- Do not run broad pytest for Markdown-only changes.
- Synthetic/manual E2E runs use an isolated temporary `MO_STATE_HOME`. Only real device-boundary checks may use the live profile, and their intentional synthetic continuity rows must be removed afterward.
- Keep startup imports light. In particular, do not import heavy SDKs at module top anywhere on the `core.agent.agent` import chain. Defer them behind a first-use loader and recheck import time after changing that chain.

## Shared-agent and Git coordination

- Before changing Git state, inspect the live status, branches, worktrees, and relevant remote/PR state only far enough to establish the exact target and detect overlap. Existing changes belong to another session unless ownership is established.
- Unrelated unstaged paths and non-overlapping concurrent work are not blockers. Preserve them, stage and commit only intentional files, and continue without waiting, isolating the checkout, or asking the operator. Pause only for overlapping edits, foreign staged entries, non-fast-forward history, a failing required guard, or an unproven destructive target.
- Shared `main` is commit-ready history: every commit placed on it is considered publishable, and pushing its tip necessarily publishes any missing ancestor commits while never including unstaged files. Work that is not publication-ready must stay uncommitted or off `main`. Never reset, rebase, force-push, merge over, delete, or revert another agent's work without exact operator authorization.
- Plain `/prt` reports uncommitted work in the current terminal when present; otherwise it reviews HEAD. Worktree/path reviews never edit. Commit/range reviews delegate confirmed corrections to the existing Agent owner and reassess the original change plus repairs until the target is met or progress stops with the actual score, remaining findings, and reason. Trusted GitHub review remains review-only and never edits PR code.
- MO Agent is broader than PRT; GitHub review must not displace the operator's active product task.

## Operator profile boundary

- Operator identity and preferences are private profile data, not product defaults. Never hardcode local names, accounts, paths, servers, projects, or personal preferences into product code or tracked docs.

## Source and authority routing

- `core/` — agent logic, providers, Gateway, task/evidence systems, state, graph, and runtime. Read `core/MAINTAINING.md` before changing runtime/state/graph/session boundaries.
- `interface/` — prompt_toolkit terminal UI.
- `mo_desktop/` — optional domain-neutral Desktop companion with profile-owned apps. Read `mo_desktop/MAINTAINING.md` before Desktop, Live Control, role, profile-app, or cross-surface UI changes.
- `mo_everywhere/` — Everywhere hub/client behavior. Read `mo_everywhere/README.md` for its current protocol and topology authority.
- `mo_publisher/` — optional public publisher pages and AI-report custody. Its `README.md` owns isolated deployment, review and retention; it never shares a Hub profile or supplies customer/Hub authorization.
- `clients/chrome/` — MO Connected Tab's Chrome boundary and branded assets. Its `README.md` owns setup and current extension behavior; `core/browser_bridge.py` remains the sole native lifecycle/transport owner and `tools/browser.py` remains the browser action owner.
- `ANDROID.md` — public Android behavior, requirements, pairing, privacy, and Google Play availability. The complete native client, tests, signing inputs, device evidence, and release automation live only in the ignored local `clients/android/` tree; preserve that private source and never force-add it. The public Hub protocol remains under `mo_everywhere/`.
- `tests/` — ignored maintainer-local verification overlay; never push it.
- `docs/` and `tmp/` are ignored local-only locations, not durable authority. Product-safe guidance belongs in tracked documentation; private plans and history belong under `~/.mo/memory/...`. If scratch is necessary, isolate it in a task/session-unique subdirectory of `tmp/` (subagent-unique when agents run concurrently); write real product files directly to their owned paths, move durable private output to its profile authority, and remove only your own scratch when the task closes. Never bulk-clean shared `tmp/` while another session may be active.

## Cross-cutting runtime invariants

- Runtime state is private by default under `~/.mo` or `MO_STATE_HOME`; writers resolve every `memory/...` and `logs/...` path through `core.state.paths.resolve_state_path()`.
- `core/state/layout.py` owns the private-home structure and generated README. `personal/` is opaque and is never auto-indexed, migrated, synchronized, inspected, or deleted.
- `core/graph/structural_graph.py` owns the only native persisted structural graph and its public queries. `core/graph/code_graph.py` is its in-memory source extractor, not a second output. SQLite/FTS5/vector recall, in-memory BM25 graph ranking, and the human project map are distinct. On large projects consume bounded graph slices and never load/dump the whole graph into model context.
- Multiple terminal instances are supported through stable `MO_INSTANCE_ID` values and default `main-<instance>` session slots. Singleton services use a resource lock.
- Gateway/Agent and taskboard evidence gates own task truth. Procedures seed rows but never bypass evidence or allow provider prose to complete work.
- Profile extension state lives only under `~/.mo/operator` or explicit profile overrides. The checkout never supplies a repo-local operator pack. The pre-push privacy guard blocks private identity, secrets, private paths, and tracked tests; the tracked external-safe preflight separately blocks canonical credential-source drift without inspecting or executing the owner guard.
- Native **MO Live Control** requires exact `control` plus `remote_control` device scopes and a distinct `remote_host` identity. Public Hub protocol authority lives in `mo_everywhere/README.md`; Desktop invariants live in `mo_desktop/MAINTAINING.md`; client-side Android evidence remains with the private local client.

## Public/private publication boundary

- This repository is the public product: push means publish. Private profile state, extension internals, credentials, deployment knowledge, local plans/reports, and maintainer QA never enter tracked files.
- Private extension commands come only from the profile bridge and remain absent with an empty profile.
- Ignored checkout docs never steer product claims. Promote product-safe truth to tracked authorities or move private records under the profile before relying on them.
