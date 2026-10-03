# GitHub workflows

This directory owns two public automation lanes:

| Workflow | Purpose |
| --- | --- |
| `ci.yml` | Public-boundary, documentation, lint, complete Python compile/import, and optional local-overlay checks |
| `prt.yml` | Trusted-code PRT review for same-repository pull requests and authorized mentions |

## PRT

`prt.yml` is the production GitHub arm of PRT. Posting requires `prt.enabled`,
`prt.maintainer_github`, and an explicit `--post`; the CLI otherwise returns a
dry run. An omitted GitHub setting defaults to false, while
[`config.example.yaml`](../../config.example.yaml) enables it. This workflow
loads the runner's normal MO configuration; the setting alone starts no local
review daemon. The shared engine and evidence contract live in
[core maintenance](../../core/MAINTAINING.md#prt-review-contract).

Provision the repository Actions secret `MO_CONFIG_YAML` with a minimal normal
MO `config.yaml`: the selected `providers` entry, matching `model.default`, and
the PRT posting flags above. Use the effective selected model, including any
saved `/model` choice, rather than accidentally exporting an older baseline.
Do not copy unrelated profile data or embed credentials. The workflow writes
this file into its isolated private home and selects it through the existing
`MO_CONFIG` path; the normal Agent/config/model owners perform selection.
There is no PRT-specific provider/model selector or secret-presence priority.

For API-key providers, stage the configured logical credential with one of:

1. `DEEPSEEK_API_KEY`
2. `OPENCODE_API_KEY`
3. `ZAI_API_KEY`
4. `MO_PROVIDER_KEY` (generic key; reference that name in `api_key_env`)

GitHub writes use the built-in `${{ github.token }}`. The workflow materializes
these credential values into an isolated
`$RUNNER_TEMP/mo-prt-state/credentials/providers.env`, points MO at that
temporary profile, and removes it in an `always()` cleanup step. Credential
presence never selects a model. The profile is never
committed, cached, or uploaded.

This is explicitly provisioned configuration, not synchronization with a live
terminal: later local selections do not automatically update the runner. OAuth
authentication and local-only endpoints are not transported by these API-key
secrets. For those routes, run the trusted reviewer on an already authenticated
host with the same pinned-worktree boundary below; local execution can still
publish a GitHub review and status. It does not prove hosted Actions execution.
For Codex OAuth, [refreshed credentials must survive between runs](https://learn.chatgpt.com/docs/auth/ci-cd-auth);
this workflow supplies no auth synchronization or refresh-token persistence.
Missing configuration or authentication must not silently change provider,
model or account privacy settings. Local `/prt` uses the active Agent provider.

The workflow automatically reviews same-repository pull requests and accepts
`@mo` requests only from repository owners, members, or collaborators. On pull
requests, `@mo /prt`, `@mo /describe`, and questions are supported. On issues,
use `@mo /plan`.

The security boundary is deliberate:

- Reviewer code is checked out from the trusted base/default branch.
- PR content is fetched into a separate detached worktree and treated as data.
  The review engine consumes that pinned worktree directly, without creating
  another local snapshot. The review Agent's allowed workspace is restricted to
  that worktree, so graph/history checks use the same source boundary rather
  than the trusted reviewer's installation directory. Base/head identity is
  checked before posting.
- PRT explicitly builds structural graph evidence for that worktree.
- The same reviewer reuses or refreshes MO's existing Git history index for the
  worktree and retrieves bounded ancestry context for the pinned revision.
  Private recorded findings and linked conversations never enter GitHub review
  context. Coverage limits appear in the report; history does not prove a bug,
  dictate old versus new code, or authorize a repair.
- The isolated credential profile is available only to trusted reviewer code;
  the job does not execute PR tests, scripts, or project commands.
- The workflow never invokes `--fix`, `--propose`, or `--autofix-ci`.
- Fork pull requests do not enter the provider-secret job.

GitHub PRT remains review-only. Local terminal PRT is separate and target-aware:
worktree/path targets report only, while commit/range targets delegate confirmed
violations to the existing Agent correction worker. `/prt report` summarizes
target-aware local history; GitHub results remain on the pull request and its
`MO PRT` status. Local correction never changes GitHub review authority and does
not commit, push, deploy, or use credentials.
An incomplete review posts an `error` status with no meaningful code score;
a completed below-target review posts `failure`. Both fail the posting CLI/job.
Score and confidence are evidence-weighted heuristics, not correctness
probabilities; meeting the target also requires no unresolved findings. Validate
the exact reviewed SHA, review body, inline comments or fallback, and `MO PRT`
status together with the job conclusion. A green Actions badge alone is not
evidence that a complete review or correct delivery occurred. These PR/mention
triggers do not make local PRT automatic or cover every push outside a PR.
The workflow's 30-minute job timeout is separate from local provider continuation
and the bounded local affected-test runner.

## Public checkout CI

`ci.yml` is the dependency-light public checkout gate. It runs the
external-safe public/private preflight, checks local file targets and Markdown
heading/custom anchors outside fenced examples and comments, and checks literal
runtime-owner paths and command-registry coverage in the capability ledger with
`python -m core.diagnostics.docs_check`. The documentation check covers tracked
and newly added public Markdown, not ignored private notes. It is not a full
Markdown renderer and does not verify external URLs, prose accuracy, dotted
symbol ownership, runtime behavior, or installed builds. Those claims require
current source and acceptance evidence. It then
checks shared/surface prompt ownership and deterministic provider message,
tool-schema, call/result, and multimodal semantics, followed by the non-root
container/private-state/workspace boundary. Finally it enforces the configured
Ruff rules, compiles core, Terminal, tools, Desktop, Everywhere, and the root
`mo.py`/`mo_service.py` entrypoints, and imports the runtime boundaries.
The ignored maintainer test overlay is exercised only when it is
present; the public repository does not ship that overlay.
Sequential native commands stop on a failed exit status before running another
check, so a later successful command cannot mask a failed prerequisite. The
source redundancy check still fails for unapproved clones and stale reviewed
allowances. See the existing diagnostic contract in
[core maintenance](../../core/MAINTAINING.md). Android build and release
automation remains with the private local client, outside public GitHub Actions.
