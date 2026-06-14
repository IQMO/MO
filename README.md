# MO Agent

**A local-first AI coding agent that refuses to fake progress.**
By [IQMO](https://github.com/IQMO).

The coding agent you point at your own projects — like Cursor, Claude, or
GPT — but local-first, and built so it never fakes progress, never forgets your
session, and never pads the answer.

> **Status: under active development.** A private pre-release ("underdog"
> build) — used daily and already capable, but evolving fast: expect rough
> edges, changing internals, and features still landing. Not a finished product.

---

## Why MO Agent

A few things set MO apart from the agents you already know:

- **It stays focused across a long session.** Most agents *compact* when the
  chat gets long — they summarize the past, quietly forget it, and drift off
  task. MO doesn't. It manages context **dynamically in the background** —
  progressively compressing as pressure builds, then doing a clean **handoff**
  (a continuity capsule: live task board, files touched, goals, code map) only
  when needed — so the agent keeps the thread and stays on the task, and a
  two-hour run still remembers what mattered at minute five.
- **Recoverable dumpzone, zero token tax.** Before any handoff, MO
  deterministically folds *old, completed* work into compact summaries — with
  **no extra model call and no token cost**. The full originals are archived to
  a recoverable dumpzone (`logs/compacted_chains/`), so exact past output is one
  `read_file` away whenever you need it.
- **It navigates your code instead of grinding through it.** MO keeps a local
  **structural code graph** — fuzzy symbol search, a caller/callee walker, and a
  community map — and is built to reach for it *before* grep sweeps or reading
  whole files. One graph lookup routinely replaces 5–20 blind grep/read calls.
  Edits are targeted exact-text patches (never whole-file rewrites). The model
  spends its budget on your problem, not on rediscovering your repo every turn.
- **Concise by default.** MO leads with the answer, not the setup, and
  losslessly structure-compresses tool output before it ever costs you
  tokens — so you pay for substance, not noise.

These are the difference between an agent that *uses* a model and one that
*works it well*: MO's tooling keeps the provider model fast, cheap, and on
target instead of drowning it in grep dumps, whole-file reads, and lost context.

And the foundation underneath them:

- **Evidence-backed task truth** — the runtime Gateway owns the task board.
  Model prose cannot mark work complete; tasks close only on real tool/runtime
  evidence, and the final answer cannot claim done while tasks are open.
- **Local-first** — your code, keys, profile, and memory stay on your machine
  under `~/.mo`. No server, no telemetry, no cloud account.
- **Provider-agnostic, with failover** — any OpenAI-compatible endpoint is just
  the engine; MO automatically fails over to a backup provider on
  rate/balance/empty-response errors and keeps going. Its identity and behavior
  stay MO's.
- **Learns only with your permission** — MO mines recurring patterns from your
  sessions into reviewable suggestions; nothing influences future behavior
  until you confirm it (`/learning pending` → `confirm`).

## Quickstart (10 minutes)

Requirements: Python 3.10+, Windows / Linux / macOS.

```bash
git clone https://github.com/IQMO/rMO.git
cd rMO
python -m pip install -r requirements.txt
python mo.py --init
```

**Verify:** `--init` prints your private home layout (`~/.mo`), created
profile templates, and an honest provider-key report (present/missing).

Add a provider key to `~/.mo/.env` (created by init, never tracked):

```env
OPENCODE_API_KEY=your_key_here
```

Run it:

```bash
python mo.py
```

**Verify:** MO greets you by your profile name (set it with
`/profile name <you>`), and the footer shows tokens · model · reasoning.

Give it real work from any project folder:

```text
find issues in this project
```

**Verify:** a task checklist appears (`N tasks (X done, Y open)`), advances
only as tools actually run, and the report separates confirmed findings from
suspicions.

Optional global command — add `~/.mo/bin` to PATH, then run `mo` from any
project directory:

```bash
# Linux/macOS
export PATH="$HOME/.mo/bin:$PATH"
# Windows PowerShell
[Environment]::SetEnvironmentVariable('Path', "$env:USERPROFILE\.mo\bin;$env:Path", 'User')
```

## What you get

| Capability | What it means |
|---|---|
| Context handoff | Long sessions continue via a continuity capsule, not destructive compaction — nothing important is forgotten |
| Recoverable dumpzone | Old completed work is compacted with no token cost; full originals archived under `logs/compacted_chains/`, recoverable with read |
| Concise output | Answer-first replies + lossless structural compression of tool output |
| Evidence task board | Compact checklist (`√ → □ !`) owned by the runtime, not the model |
| Project audit mindset | "Find issues in my codebase" gets disciplined diagnosis: orientation → catalog → fix → verify |
| Reference comparison | "Compare this against X" stays read-only, classifies per dimension, adopts only proven-better |
| `/goal` | Autonomous multi-step runs with a deterministic completion auditor |
| Ghost (Alt+G) | Fast side-check/planning lane that proposes but never owns task truth |
| PRT (`/prt`) | Post-commit review pipeline with structural impact scoring |
| Code graph | Local community map + BM25 fuzzy search + caller/callee walker (`/structural-graph`) |
| Learning loop | Pattern mining → confidence-ranked clusters → your confirmation → relevance-gated injection |
| Profile portability | `/profile export` / `import` moves your learned state between MO installs |
| Telegram (optional) | Remote surface with the same task truth in replies |
| Hooks (optional) | `~/.mo/hooks.yaml` maps runtime events to your shell commands |
| Session trace | `python mo_trace.py serve` records a session and validates MO's behavior afterwards |

Run `/help` inside MO for the full command list, or press `F4` for the palette.

## Personalization and privacy

Everything personal lives in your private runtime home — never in this repo:

| Yours | Where |
|---|---|
| Profile, terms, learned behavior | `~/.mo/memory/profile/` |
| API keys and secrets | `~/.mo/.env` (gitignored everywhere, never printed) |
| Sessions, memory, task history | `~/.mo/memory/` |
| Config | `~/.mo/config.yaml` |

A fresh `--init` gives every user the same machinery, empty. MO builds your
profile from working with you.

## Run it your way

- **Headless / always-on:** run MO as a background service and reach it
  remotely (e.g. Telegram) with
  `python mo_service.py --config ~/.mo/config.yaml --surface server`.
- **Backend diagnostics:** a best-effort JSONL monitor writes under
  `~/.mo/logs/monitor/` for when you want to see what the runtime did. It is
  never runtime authority — the task board is.
- **Upgrading an older install:** `python mo.py --migrate-state` (dry-run
  first; apply with `--confirm`).

## Design principles

- **Provider-first:** the model decides; the sandbox enforces.
- **One source of truth:** Gateway owns boards; helpers own templates; the UI
  renders truth only.
- **Anti-duplication, anti-over-engineering:** the simplest working solution,
  consolidated, with nothing dead left behind.
- **Honest reporting:** what changed, what verified, what failed, what's
  unknown — never a generic AI dump.

## Security

- Sandbox gates every tool call: path boundaries, shell safety, network
  policy, secret redaction.
- A secrets-only critic gate scans answers; a turn-end security check scans
  modified files.
- Keys/config never committed; supervisor lock prevents duplicate processes.
- No inbound network surface — MO never opens a listening port; the optional
  Telegram surface is outbound polling only.
- Outbound tools (`web_fetch`/`web_search`, shell network) are sandbox-gated and
  default to a **local-trust** posture; harden them in `config.yaml`
  (`web_fetch_allowed_hosts`, or disable network) — see `config.example.yaml`.

## License

Private. © IQMO 2024–2026.
