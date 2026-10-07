"""Single source of truth for MO's private-home (``~/.mo``) layout.

Declares every path MO's PRODUCT code owns under the state home: purpose, owner
class, lifecycle, sync class, and whether first-run init creates it. One
registry so the initializer (creates from it), ``/doctor`` (validates against
it), dashboard (can surface it), and optional state coordinator stop drifting —
historically each knew paths independently and fell out of sync (e.g. init
created ``memory/structural_graph`` while the live graph moved to ``cache/``).

Boundary: PRODUCT paths only. Undeclared profile-private data is not enumerated
here (no operator specifics in product code). The layout check reports
anything undeclared as ``undeclared`` for the operator to classify — never an
error. Creation is limited to the declared first-run directories and generated
README.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import (
    PROFILE_PROSE_ROLES,
    RUNTIME_PREFERENCES_LOCK_PATH,
    RUNTIME_PREFERENCES_PATH,
    mo_home,
)
from ..utils.atomic_write import atomic_write_text


@dataclass(frozen=True)
class StatePath:
    relpath: str                # relative to ~/.mo, forward slashes
    kind: str                   # "dir" | "file"
    owner: str                  # product | cache | config | secret | tooling | extension
    lifecycle: str              # durable | ephemeral | cache | config
    purpose: str
    sync: str = "never"         # never | device | hub | replicate | snapshot
    create_at_init: bool = False
    deprecated: bool = False    # declared-but-legacy (live copy lives elsewhere)


STATE_LAYOUT: tuple[StatePath, ...] = (
    # --- root config / secrets ---
    StatePath("README.md", "file", "product", "config", "generated private-home structure map", create_at_init=True),
    StatePath("config.yaml", "file", "config", "config", "authored runtime defaults", sync="device", create_at_init=True),
    StatePath(RUNTIME_PREFERENCES_PATH, "file", "config", "config", "interactive Terminal defaults saved by personalization commands", sync="replicate"),
    StatePath(RUNTIME_PREFERENCES_LOCK_PATH, "file", "product", "ephemeral", "runtime-preference writer lock"),
    StatePath("answer_rules.md", "file", "config", "config", "optional user-authored answer block and warning phrases", sync="device"),
    StatePath("hints.txt", "file", "config", "config", "optional user-authored replacement idle hints", sync="device"),
    StatePath("skin", "file", "config", "config", "UI skin selector", sync="replicate"),
    StatePath("skins.json", "file", "config", "config", "validated user-created UI skin registry", sync="replicate"),
    StatePath("device.json", "file", "config", "config", "stable local device identity", sync="device"),
    StatePath("credentials", "dir", "secret", "config", "device-local private credentials; never synchronized", create_at_init=True),
    StatePath("credentials/providers.env", "file", "secret", "config", "provider/image/embedding credential variables", create_at_init=True),
    StatePath("credentials/telegram.env", "file", "secret", "config", "Telegram bot credential variables"),
    StatePath("credentials/gmail.env", "file", "secret", "config", "operator-owned Gmail OAuth client ID"),
    StatePath("credentials/gmail-token.bin", "file", "secret", "durable", "encrypted device-local Gmail OAuth token"),
    StatePath("credentials/mcp", "dir", "secret", "config", "per-MCP credential files"),
    StatePath("credentials/legacy", "dir", "secret", "config", "inactive credential archives from an earlier upgrade; no longer written"),
    StatePath("credentials/everywhere.json", "file", "secret", "config", "device-local Everywhere hub token bundle"),
    StatePath("credentials/everywhere-live-host.json", "file", "secret", "config", "device-local Everywhere Live Control host token bundle"),
    StatePath("credentials/everywhere-transfer.json", "file", "secret", "config", "device-local Everywhere cargo token bundle"),
    StatePath("credentials/everywhere-files.json", "file", "secret", "config", "device-local Everywhere controller token bundle for MO Files and MO-host workspace"),
    # --- root dirs ---
    StatePath("docs", "dir", "tooling", "durable", "LEGACY private documentation root; archive under memory/archive/legacy-docs", deprecated=True),
    StatePath("tmp", "dir", "tooling", "ephemeral", "LEGACY private scratch root; archive under memory/archive/legacy-tmp", deprecated=True),
    StatePath("bin", "dir", "product", "durable", "mo / mo.cmd launcher shims", sync="device", create_at_init=True),
    StatePath("memory", "dir", "product", "durable", "durable state grouped by meaning", create_at_init=True),
    StatePath("memory/transfers.sqlite", "file", "product", "durable", "resumable cross-surface file-transfer state; names and paths remain private", sync="never"),
    StatePath("memory/transfers", "dir", "product", "durable", "bounded sender and hub file-transfer custody until acceptance or expiry", sync="never"),
    StatePath("memory/systemcare.sqlite", "file", "product", "durable", "device-local SystemCare calibration, scans, plans, receipts, and exact private target evidence", sync="never"),
    StatePath("logs", "dir", "product", "ephemeral", "audit + monitor logs", create_at_init=True),
    StatePath("logs/monitor", "dir", "product", "ephemeral", "backend monitor run logs"),
    StatePath("logs/compacted_chains", "dir", "product", "ephemeral", "exact recovery archives for compacted tool chains"),
    StatePath("logs/traces", "dir", "product", "ephemeral", "bounded owner diagnostics traces"),
    StatePath("logs/file_operations.jsonl", "file", "product", "ephemeral", "bounded cross-session file-operation summary"),
    StatePath("logs/desktop_issue_reports", "dir", "product", "ephemeral", "user-approved Desktop issue-report prompts"),
    StatePath("logs/mo_desktop.log", "file", "product", "ephemeral", "bounded MO Desktop lifecycle diagnostics"),
    StatePath("logs/systemcare.jsonl", "file", "product", "ephemeral", "bounded path-free SystemCare lifecycle diagnostics"),
    StatePath("logs/provider_audit.jsonl", "file", "product", "ephemeral", "bounded provider request audit"),
    StatePath("logs/review_audit.jsonl", "file", "product", "ephemeral", "bounded review audit"),
    StatePath("logs/tool_audit.jsonl", "file", "product", "ephemeral", "bounded tool execution audit"),
    StatePath("cache", "dir", "cache", "cache", "per-project graph/index caches", create_at_init=True),
    StatePath("cache/structural_graph", "dir", "cache", "cache", "LIVE structural graph (per-project digest)"),
    StatePath("cache/project_history", "dir", "cache", "cache", "derived per-project Git history and consolidation search indexes"),
    StatePath("cache/code_graph", "dir", "cache", "cache", "LEGACY duplicate graph cache; retired after canonical graph build", deprecated=True),
    StatePath("cache/pycache", "dir", "cache", "cache", "shared Python bytecode cache outside product checkouts"),
    StatePath("media", "dir", "product", "durable", "generated and attached user artifacts", sync="device"),
    StatePath("media/generated", "dir", "product", "durable", "tool-generated images and validated cloud music/video results", sync="device"),
    StatePath("memory/media/jobs", "dir", "product", "durable", "private session-bound cloud media requests, task IDs and local result receipts; no reference URLs", sync="never"),
    StatePath("run/media", "dir", "product", "ephemeral", "task-owned prepared references and continuation frames; expiring public access; never original files", sync="never"),
    StatePath("bin/media", "dir", "tooling", "durable", "optional verified media-transfer helper installed by explicit operator action", sync="never"),
    StatePath("media/designs", "dir", "product", "durable", "portable MO Design documents", sync="device"),
    StatePath("media/explainers", "dir", "product", "durable", "evidence, scenes, narration, and rendered explainer videos", sync="device"),
    StatePath("media/attachments", "dir", "product", "durable", "categorized cross-surface attachments", sync="device"),
    StatePath("media/attachments/gallery", "dir", "product", "durable", "image attachments", sync="device"),
    StatePath("media/attachments/audio-video", "dir", "product", "durable", "audio and video attachments", sync="device"),
    StatePath("media/attachments/documents", "dir", "product", "durable", "document attachments", sync="device"),
    StatePath("media/attachments/files", "dir", "product", "durable", "other attachments", sync="device"),
    StatePath("media/attachments/index.jsonl", "file", "product", "durable", "private attachment provenance index", sync="device"),
    StatePath("personal", "dir", "config", "durable", "user-owned private files; never auto-indexed or synchronized"),
    StatePath("skills", "dir", "product", "durable", "local skill packs (SKILL.md) + seeds (created lazily by seed_profile_skills)", sync="replicate"),
    StatePath("operator", "dir", "extension", "durable", "reserved private profile data"),
    StatePath("mcp", "dir", "tooling", "cache", "installed MCP servers"),
    StatePath("models", "dir", "tooling", "cache", "local model blobs; check subpaths before clearing — not all are disposable"),
    StatePath("models/voice", "dir", "tooling", "durable", "optional Desktop voice runtime: installed venv, downloaded model, and install/licence record"),
    StatePath("models/voice/cache", "dir", "cache", "cache", "voice download/build caches; safe to clear, a reinstall refills them"),
    StatePath("run", "dir", "product", "ephemeral", "profile-scoped runtime scratch"),
    StatePath("run/skills.lock", "file", "product", "ephemeral", "cross-process profile skill-tree writer lock"),
    StatePath("run/local_embeddings", "dir", "cache", "ephemeral", "temporary local embedding runtime state"),
    StatePath("run/everywhere.disabled", "file", "product", "ephemeral", "immediate local Everywhere disable gate"),
    StatePath("run/everywhere-remote.disabled", "file", "product", "ephemeral", "immediate remote-control disable gate"),
    StatePath("run/systemcare", "dir", "product", "ephemeral", "SystemCare operation lock, cancellation request, and bounded active marker"),
    StatePath("run/design-preview", "dir", "product", "ephemeral", "MO Design live-preview and renderer coordination state"),
    StatePath("run/design-handoffs", "dir", "product", "ephemeral", "bounded prompts explicitly routed from MO Design"),
    StatePath("run/browser-control", "dir", "product", "ephemeral", "authenticated loopback descriptor for Connected Chrome tabs"),
    StatePath("run/heartbeat", "dir", "product", "ephemeral", "bounded multi-instance heartbeat ledger"),
    *(StatePath(f"run/state-layout-v{version}", "file", "product", "ephemeral", "obsolete layout marker; no longer written or read", deprecated=True) for version in range(2, 6)),
    StatePath("sync", "dir", "product", "durable", "device-local state-sync git metadata and status", sync="device"),
    StatePath("sync/repo.git", "dir", "product", "durable", "private curated-profile synchronization metadata", sync="device"),
    StatePath("sync/everywhere-status.json", "file", "product", "ephemeral", "latest Everywhere coordinator state"),
    StatePath("sync/last-status.json", "file", "product", "ephemeral", "latest private profile synchronization state"),
    StatePath("sync/validation-cache.json", "file", "product", "cache", "bounded profile synchronization validation cache"),
    # --- memory/ files ---
    StatePath("memory/mo.db", "file", "product", "durable", "structured identity, recent-workspace, preference, and usage metadata", sync="snapshot"),
    StatePath("memory/mo.db.lock", "file", "product", "ephemeral", "profile db lock"),
    StatePath("memory/learning/episodes.sqlite", "file", "product", "durable", "episodic turns + vectors + knowledge entries", sync="snapshot"),
    StatePath("memory/learning/suggestions.jsonl", "file", "product", "durable", "mined and confirmed learning", sync="device"),
    StatePath("memory/learning/workflows/candidates.jsonl", "file", "product", "durable", "operator-staged workflow candidates", sync="device"),
    StatePath("memory/learning/operator-message-receipts.jsonl", "file", "product", "durable", "idempotent automatic operator-message routing receipts", sync="device"),
    StatePath("memory/learning/operator-message-reconcile.json", "file", "product", "durable", "per-snapshot checkpoint for incremental operator-message reconciliation", sync="device"),
    StatePath("memory/work/product-intent", "dir", "product", "durable", "source-hashed product requirements awaiting source-backed project-history consolidation", sync="device"),
    StatePath("memory/work/product-intent/candidates.jsonl", "file", "product", "durable", "inert product-intent candidates routed from operator messages", sync="device"),
    StatePath("memory/surfaces/telegram.sqlite", "file", "product", "durable", "Telegram authorization and chat/session mapping", sync="hub"),
    StatePath("memory/surfaces/mail-state.bin", "file", "product", "durable", "encrypted device-local Gmail sync cursor and notice state", sync="device"),
    StatePath("memory/surfaces/everywhere.sqlite", "file", "product", "durable", "Everywhere hub auth, consent, jobs, events, and audit", sync="hub"),
    StatePath("memory/surfaces/publisher-reports.sqlite", "file", "product", "durable", "optional publisher report queue; dedicated publisher home, 30-day expiry"),
    StatePath("memory/surfaces/everywhere-device.sqlite", "file", "product", "durable", "device-local continuity outbox, inbox, cursors, and bindings", sync="device"),
    StatePath("memory/surfaces/browser", "dir", "product", "durable", "device-local connected-Chrome native host manifest, launcher, and install record"),
    # --- memory/ dirs created at first-run init ---
    StatePath("memory/profile", "dir", "product", "durable", "curated operator prose, terms, facts, and learned preferences", create_at_init=True),
    StatePath("memory/profile/operator.md", "file", "product", "durable", "curated operator identity, projects, and operating context", sync="replicate"),
    StatePath("memory/profile/thinking_model.md", "file", "product", "durable", "operator reasoning and decision model", sync="replicate"),
    StatePath("memory/profile/terms.md", "file", "product", "durable", "operator-defined vocabulary and command terms", sync="replicate"),
    StatePath("memory/profile/learning.md", "file", "product", "durable", "historical learning-event ledger", sync="replicate"),
    StatePath("memory/profile/behavior.md", "file", "product", "durable", "active accepted rules projected from learning", sync="replicate"),
    StatePath("memory/profile/facts.md", "file", "product", "durable", "bounded operational facts and credential locations", sync="replicate"),
    StatePath("memory/learning", "dir", "product", "durable", "episodic and learned behavior state", sync="device", create_at_init=True),
    StatePath("memory/learning/workflows", "dir", "product", "durable", "learned workflow state", sync="device"),
    StatePath("memory/learning/bundles", "dir", "product", "durable", "reviewable learning bundle exports and imports", sync="device"),
    StatePath("memory/learning/bundles/exports", "dir", "product", "durable", "learning bundle exports", sync="device"),
    StatePath("memory/learning/bundles/imports", "dir", "product", "durable", "staged profile prose from learning bundle imports", sync="device"),
    StatePath("memory/surfaces", "dir", "product", "durable", "surface and cross-device databases", sync="device", create_at_init=True),
    StatePath("memory/sessions", "dir", "product", "durable", "conversation state grouped by lifecycle", sync="device", create_at_init=True),
    StatePath("memory/sessions/conversations", "dir", "product", "durable", "user-visible saved conversation snapshots", sync="device", create_at_init=True),
    StatePath("memory/sessions/history", "dir", "product", "durable", "internal session history", sync="device"),
    StatePath("memory/sessions/history/handoffs", "dir", "product", "durable", "automatic pre-handoff recovery snapshots", sync="device"),
    StatePath("memory/sessions/closeouts", "dir", "product", "durable", "bounded closeout summaries", sync="device"),
    StatePath("memory/work", "dir", "product", "durable", "task, goal, and review state", sync="device", create_at_init=True),
    StatePath("memory/work/taskboards", "dir", "product", "durable", "taskboard ledger", sync="device"),
    StatePath("memory/work/goals", "dir", "product", "durable", "/goal run state", sync="device"),
    StatePath("memory/work/reviews", "dir", "product", "durable", "private work-review records, including PRT history and source-linked per-project consolidation findings", sync="device"),
    StatePath("memory/work/game-collaboration", "dir", "product", "durable", "user-owned Terminal Game Collaboration project records", sync="device"),
    StatePath("memory/work/reviews/maintainer.jsonl", "file", "product", "durable", "PRT review history ledger", sync="device"),
    StatePath("memory/work/reviews/patterns.json", "file", "product", "durable", "verified review finding patterns", sync="device"),
    StatePath("memory/life", "dir", "product", "durable", "operator-confirmed commitments and money entries", sync="device"),
    StatePath("memory/life/items.json", "file", "product", "durable", "private commitments and their source references", sync="device"),
    StatePath("memory/life/items.lock", "file", "product", "ephemeral", "cross-process life-item writer lock"),
    StatePath("memory/life/money.json", "file", "product", "durable", "private operator-recorded income and outgoings", sync="device"),
    StatePath("memory/life/money.lock", "file", "product", "ephemeral", "cross-process money-entry writer lock"),
    StatePath("memory/reviews", "dir", "product", "durable", "LEGACY review output root; migrate to memory/work/reviews", deprecated=True),
    StatePath("memory/maintainer", "dir", "product", "durable", "LEGACY maintainer review output; migrate to memory/work/reviews/maintainer", deprecated=True),
    StatePath("memory/perception", "dir", "product", "durable", "RETIRED face-candidate output; archive under memory/archive/retired-perception", deprecated=True),
    StatePath("memory/visualizations", "dir", "product", "durable", "LEGACY generated visuals; archive under memory/archive/legacy-visualizations", deprecated=True),
    StatePath("memory/handoffs", "dir", "product", "durable", "LEGACY completed-turn handoff store; no longer read, safe to delete", sync="device", deprecated=True),
    StatePath("memory/handoffs/latest.json", "file", "product", "durable", "LEGACY global handoff; never synchronized, no longer read", deprecated=True),
    StatePath("memory/scheduler", "dir", "product", "durable", "scheduler definitions and run ledger", sync="hub"),
    StatePath("memory/scheduler/jobs.json", "file", "product", "durable", "approved scheduler job definitions", sync="hub"),
    StatePath("memory/scheduler/runs.jsonl", "file", "product", "durable", "bounded scheduler execution ledger", sync="hub"),
    StatePath("memory/scheduler/tick.lock", "file", "product", "ephemeral", "scheduler singleton lock"),
    StatePath("memory/scheduler/scripts", "dir", "product", "durable", "operator-approved scheduler compute scripts", sync="hub"),
    # --- memory/ dirs created lazily on first write ---
    StatePath("memory/learning/imports", "dir", "product", "durable", "inspected external-repo skill bundles", sync="device"),
    StatePath("memory/profile/reconcile", "dir", "product", "durable", "dialectic profile reconciliation proposals", sync="device"),
    StatePath(
        "memory/archive",
        "dir",
        "product",
        "durable",
        "inert migration archive; never injected, indexed, or treated as current runtime truth",
    ),
    # --- deprecated: legacy project-local-only; live graphs are under cache/ ---
    StatePath("memory/structural_graph", "dir", "product", "cache", "LEGACY — live copy is cache/structural_graph", deprecated=True),
    StatePath("memory/code_graph", "dir", "product", "cache", "LEGACY duplicate graph cache; retired", deprecated=True),
)

_BY_RELPATH: dict[str, StatePath] = {e.relpath: e for e in STATE_LAYOUT}

SYNC_POLICIES = frozenset({"never", "device", "hub", "replicate", "snapshot"})


def sync_entries(*policies: str) -> list[StatePath]:
    """Return exact manifest entries for the requested default-deny sync classes."""
    wanted = {str(policy or "").strip().lower() for policy in policies}
    invalid = wanted - SYNC_POLICIES
    if invalid:
        raise ValueError("unknown sync policy: " + ", ".join(sorted(invalid)))
    return [entry for entry in STATE_LAYOUT if entry.sync in wanted and not entry.deprecated]


def init_dirs() -> list[str]:
    """Relative dirs first-run init should create (non-deprecated, create_at_init, dir).

    This is the single source the initializer iterates — replacing its old inline
    list so new-user creation can never drift from the declared layout again.
    """
    return [e.relpath for e in STATE_LAYOUT if e.kind == "dir" and e.create_at_init and not e.deprecated]


def declared(relpath: str) -> StatePath | None:
    return _BY_RELPATH.get(str(relpath).replace("\\", "/").strip("/"))


@dataclass
class LayoutFinding:
    category: str   # missing | undeclared | deprecated_present | empty_declared
    relpath: str
    owner: str
    detail: str


def _immediate_count(path: Path) -> int:
    """Number of direct entries under a dir (fast — never recurses into huge trees)."""
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return -1


def _classify_hint(path: Path) -> str:
    try:
        if path.is_dir():
            n = _immediate_count(path)
            return f"dir, {n} entr{'y' if n == 1 else 'ies'} (top level)"
        size = path.stat().st_size
        low = path.name.lower()
        note = ""
        if ".bak" in low or low.endswith("~") or "backup" in low:
            note = " — looks like a backup stray"
        elif low.endswith((".log", ".txt")):
            note = " — looks like a stray log/txt"
        return f"file, {size}B{note}"
    except OSError:
        return "unreadable"


def check_state_layout(home: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """REPORT-ONLY comparison of on-disk ``~/.mo`` against ``STATE_LAYOUT``.

    Walks the home root and ``memory/`` one level deep, classifies each entry, and
    returns structured findings. Creates / moves / deletes NOTHING. ``undeclared``
    entries (typically user-owned or profile-private data) are surfaced for the
    operator to classify — never flagged as errors.
    """
    root = Path(home).expanduser() if home else mo_home(config)
    findings: list[LayoutFinding] = []
    counts = {"ok": 0, "missing": 0, "undeclared": 0, "deprecated_present": 0, "empty_declared": 0}

    # 1. Declared paths — present? populated? deprecated-but-populated?
    for e in STATE_LAYOUT:
        p = root / e.relpath
        if not p.exists():
            if e.create_at_init and not e.deprecated:
                findings.append(LayoutFinding("missing", e.relpath, e.owner, f"declared, created at init, absent — {e.purpose}"))
                counts["missing"] += 1
            continue
        if e.deprecated:
            if e.kind == "dir":
                n = _immediate_count(p)
                detail = f"deprecated dir present with {max(0, n)} top-level entr{'y' if n == 1 else 'ies'} — {e.purpose}"
            else:
                detail = f"deprecated file present — {e.purpose}"
            findings.append(LayoutFinding("deprecated_present", e.relpath, e.owner, detail))
            counts["deprecated_present"] += 1
            continue
        if e.kind == "dir" and e.lifecycle == "durable" and _immediate_count(p) == 0:
            findings.append(LayoutFinding("empty_declared", e.relpath, e.owner, "declared durable dir is empty (informational)"))
            counts["empty_declared"] += 1
            continue
        counts["ok"] += 1

    # 2. On-disk entries not declared (root, memory domains, and canonical
    # profile files). Dynamic session/log/media contents are owned by their
    # declared containers and intentionally are not walked.
    declared_top = {e.relpath.split("/")[0] for e in STATE_LAYOUT}
    for child in _safe_iterdir(root):
        if child.name in declared_top:
            if child.name == "memory":
                for sub in _safe_iterdir(child):
                    rel = f"memory/{sub.name}"
                    if rel not in _BY_RELPATH and not _is_declared_sqlite_sidecar(rel):
                        findings.append(LayoutFinding("undeclared", rel, "?", _classify_hint(sub)))
                        counts["undeclared"] += 1
                    elif rel == "memory/profile" and sub.is_dir():
                        for profile_child in _safe_iterdir(sub):
                            profile_rel = f"memory/profile/{profile_child.name}"
                            if profile_rel not in _BY_RELPATH:
                                findings.append(LayoutFinding("undeclared", profile_rel, "?", _classify_hint(profile_child)))
                                counts["undeclared"] += 1
            continue
        findings.append(LayoutFinding("undeclared", child.name, "?", _classify_hint(child)))
        counts["undeclared"] += 1

    return {"home": str(root), "counts": counts, "findings": [f.__dict__ for f in findings]}


def _is_declared_sqlite_sidecar(relpath: str) -> bool:
    """True for SQLite ``-wal``/``-shm`` companions of a declared DB file."""
    value = str(relpath or "").replace("\\", "/")
    for suffix in ("-wal", "-shm"):
        if value.endswith(suffix):
            base = _BY_RELPATH.get(value[: -len(suffix)])
            return bool(base and base.kind == "file" and base.relpath.endswith((".sqlite", ".db")))
    return False


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []


def render_layout_report(result: dict[str, Any]) -> str:
    counts = result.get("counts", {})
    lines = [
        f"State layout check — {result.get('home', '?')}",
        f"  ok:{counts.get('ok', 0)}  missing:{counts.get('missing', 0)}  "
        f"undeclared:{counts.get('undeclared', 0)}  legacy:{counts.get('deprecated_present', 0)}  "
        f"empty:{counts.get('empty_declared', 0)}",
    ]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for f in result.get("findings", []):
        by_cat.setdefault(f["category"], []).append(f)
    labels = {
        "missing": "MISSING (declared, created-at-init, absent)",
        "deprecated_present": "LEGACY present (live copy lives elsewhere)",
        "undeclared": "UNDECLARED (classify: product? owner? tooling? stray?)",
        "empty_declared": "EMPTY declared dirs (informational)",
    }
    for cat in ("missing", "deprecated_present", "undeclared", "empty_declared"):
        items = by_cat.get(cat) or []
        if not items:
            continue
        lines.append(f"\n{labels[cat]}:")
        for f in items:
            owner = f.get("owner") or "?"
            lines.append(f"  - {f['relpath']}  [{owner}]  {f['detail']}")
    return "\n".join(lines)


def render_state_home_readme() -> str:
    """Render the human map from the same registry used by init and doctor."""
    top = [entry for entry in STATE_LAYOUT if "/" not in entry.relpath and entry.kind == "dir" and not entry.deprecated]
    memory_domains = [
        entry for entry in STATE_LAYOUT
        if entry.relpath.startswith("memory/")
        and entry.relpath.count("/") == 1
        and entry.kind == "dir"
        and not entry.deprecated
    ]
    retired_locations = [
        entry for entry in STATE_LAYOUT
        if entry.kind == "dir"
        and entry.deprecated
        and ("/" not in entry.relpath or (entry.relpath.startswith("memory/") and entry.relpath.count("/") == 1))
    ]
    lines = [
        "# MO Private Home",
        "",
        "This directory is MO Agent's private runtime profile. Its structure is generated from",
        "`core/state/layout.py`; `/doctor layout` validates the same contract. Do not treat this",
        "README as a second configuration source.",
        "",
        "## Owned roots",
        "",
    ]
    for entry in top:
        lines.append(f"- `{entry.relpath}/` — {entry.purpose} ({entry.lifecycle})")
    lines.extend(["", "## Durable memory domains", ""])
    for entry in memory_domains:
        lines.append(f"- `{entry.relpath}/` — {entry.purpose}")
    lines.extend([
        "- `memory/mo.db` — structured runtime metadata; curated prose stays in `memory/profile/`.",
        "",
        "### Canonical profile sources",
        "",
    ])
    for name, role in PROFILE_PROSE_ROLES.items():
        lines.append(f"- `memory/profile/{name}` — {role}.")
    lines.extend([
        "",
        "Session snapshots are separated by lifecycle: user-visible saved conversations are under",
        "`memory/sessions/conversations/`; automatic recovery copies are under",
        "`memory/sessions/history/handoffs/`; closeout summaries are under",
        "`memory/sessions/closeouts/`.",
        "",
        "## Retired locations",
        "",
        "These compatibility-only paths are recognized so `/doctor layout` can flag them and",
        "new state must use the destination in each description.",
        "",
    ])
    for entry in retired_locations:
        lines.append(f"- `{entry.relpath}/` — {entry.purpose}")
    lines.extend([
        "",
        "## Indexing boundary",
        "",
        "MO injects a bounded, query-ranked capsule from the active files under `memory/profile/`;",
        "the historical `learning.md` ledger is included only for explicit learning questions. MO stores",
        "episodic turn recall in `memory/learning/episodes.sqlite` and keeps one per-project structural graph in",
        "`cache/`. It does not automatically index `personal/`, `credentials/`, arbitrary media,",
        "or the whole private home. Provider claims must still be verified with the owning source.",
        "",
        "## Ownership boundary",
        "",
        "- `personal/` is user-owned and opaque to automatic indexing or migration.",
        "- `credentials/` is device-local secret state and is never synchronized.",
        "- `operator/` is reserved private profile data, not product source.",
        "- `logs/`, `run/`, and `cache/` are bounded/generated state, not durable memory.",
        "- `media/` is the one home for generated and attached user artifacts.",
        "",
    ])
    return "\n".join(lines)


def refresh_state_home_readme(home: str | Path) -> Path | None:
    """Rewrite the generated map only when it no longer matches the registry.

    Registry edits could otherwise reach the map only when the file was missing,
    so a changed `STATE_LAYOUT` could stay invisible on disk indefinitely. The comparison is cheap (sub-millisecond render plus one
    small read) and it runs on the startup path.

    Best-effort by design: the map is informational, and `atomic_write_text`
    replaces the target, which fails while another process holds the file open on
    Windows. Refreshing a description must never stop MO from starting.

    Returns the path when it was rewritten, ``None`` when it was already current
    or could not be written.
    """
    path = Path(home) / "README.md"
    current = render_state_home_readme()
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == current:
            return None
        atomic_write_text(path, current, encoding="utf-8")
    except OSError:
        return None
    return path


def write_state_home_readme(home: str | Path) -> Path:
    path = Path(home) / "README.md"
    atomic_write_text(path, render_state_home_readme(), encoding="utf-8")
    return path
