"""Self-maintenance preflight context for MO's own codebase work."""
from __future__ import annotations

import json
from pathlib import Path
import re

from ..owner_protocols import (
    is_owner_maintenance_activation,
    is_owner_integrity_audit_activation,
    is_owner_interface_audit_activation,
    is_owner_comparison_activation,
    is_owner_dedup_activation,
)
from ..path_defaults import mo_home, operator_pack_root

_SELF_ACTION_WORDS = {
    "audit",
    "auditing",
    "capability",
    "capabilities",
    "debug",
    "diagnose",
    "diagnosis",
    "diagnostic",
    "forensic",
    "learn",
    "learning",
    "reasoning",
    "rootcause",
    "root-cause",
    "root cause",
    "self",
    "skip",
    "skipped",
    "trace",
    "workflow",
    # Self-diagnosis verbs (corrective/forensic phrasing). Kept to terms that rarely
    # describe ordinary feature work, so they only fire the heavy preflight when
    # paired with a self scope marker (MO / your … ) — not on generic requests.
    "guess",
    "guessing",
    "guessed",
    "drift",
    "drifting",
    "drifted",
    "investigate",
    "investigating",
    "misbehave",
    "misbehaving",
    "misbehavior",
    "misbehaviour",
}

_SELF_SCOPE_MARKERS = {
    "devmode",
    "owner_maintenance",
    "mo",
    "owner_comparison",
    "owner_integrity_audit",
    "expert audit",
    "versus mode",
    "versus-mode",
    "your behavior",
    "your capabilities",
    "your capability",
    "your codebase",
    "your own",
    "your reasoning",
    "your source",
    "your workflow",
    "your profile",
    "your behaviour",
    "your gating",
    "your drift",
    "your context",
    "yourself",
}

_RELEVANT_COMMANDS = {"/structural-graph", "/learning", "/profile", "/status", "/prt"}

# Keep this list small, explicit, and non-overlapping. It is the operator-auditable
# discovery contract for MO self/OWNER_MAINTENANCE preflight.
REQUIRED_DISCOVERY_AREAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("slash commands", ("interface/command_registry.py", "/structural-graph", "/learning", "/profile")),
    ("turn/runtime hooks", ("core/agent/agent_turn.py", "core/agent/agent.py", "_record_turn_memory_and_learning", "_maybe_handle_workflow_control_turn")),
    ("graph/code map", ("core/graph/code_graph.py", "core/graph/structural_graph.py", "core/graph/search.py", "core/graph/callgraph.py", "graph_status()")),
    ("learning/profile/workflow", ("core/learning/proactive_learning.py", "core/learning/workflow_learning.py", "learning_suggestions.jsonl", "workflow_candidates.jsonl")),
    ("trace/session logs", ("memory/sessions", "session_closeouts", "heartbeats.jsonl", "tool_audit.jsonl", "provider_audit.jsonl", "logs/monitor/backend_monitor-*.jsonl", "~/.mo/operator/mo_trace.py")),
    ("taskboard/evidence", ("core/tasking/agent_taskboard.py", "complete_task", "core/tasking/task_evidence.py")),
    ("tests/docs", ("tests/", "~/.mo/memory/devmode", "~/.mo/operator/devmode/OWNER_MAINTENANCE.md", "~/.mo/operator/devmode/OWNER_MAINTENANCE/")),
    ("duplication/stale/legacy", ("git grep", "rg", "dead code", "duplicate paths", "retention proof")),
)

_CAPABILITY_FILES = (
    ("code graph", "core/graph/code_graph.py", "build_code_graph_context() injects orientation for non-greeting work"),
    ("structural graph", "core/graph/structural_graph.py", "community graph selected by code_graph when present"),
    ("graph fuzzy search", "core/graph/search.py", "BM25 symbol/file search exposed as the `code_search` tool (plain-language query) — use before broad grep"),
    ("call graph", "core/graph/callgraph.py", "get_callers()/get_callees() exposed as the `find_callers`/`find_callees` tools — answer who-calls-X cheaply before manual reference hunting"),
    ("slash command registry", "interface/command_registry.py", "lists runtime commands such as /structural-graph and /learning"),
    ("learning mining", "core/learning/proactive_learning.py", "/learning suggestions and /profile mine review safe learning updates"),
    ("local skill learning", "core/learning/workflow_learning.py", "stages/promotes local skill candidates; never auto-executes them"),
    ("turn learning hook", "core/agent/agent.py", "_record_turn_memory_and_learning() records feedback/terms/workflow results"),
    ("turn workflow control", "core/agent/agent_turn_dispatch.py", "_maybe_handle_workflow_control_turn() handles explicit workflow adoption"),
    ("provider audit", "core/provider/provider_audit.py", "logs provider requests/responses for trace review"),
    ("tool audit", "core/agent/agent_turn_dispatch.py", "_write_tool_audit() writes redacted logs/tool_audit.jsonl"),
    ("session closeout", "core/session/session_closeout.py", "captures dirty workspace, taskboard state, logs, and unresolved work"),
    ("heartbeat", "core/heartbeat.py", "records live taskboard/git/session continuity"),
    ("taskboard truth", "core/tasking/agent_taskboard.py", "task rows advance only via explicit complete_task evidence"),
    ("live trace", "~/.mo/operator/mo_trace.py", "session recorder and behavior validator; run from `~/.mo/operator/mo_trace.py` to replay recent actions"),
    ("input behavior gates", "core/behavior_gates.py", "run_input_gates() — declarative pre-provider registry (threat scan + malicious-code refusal)"),
    ("content safety", "core/content_safety.py", "classify_harmful_coding_request() refuses malware/attack-tooling builds; dual-use-aware, operator-disableable"),
    ("write-time secret gate", "core/sandbox.py", "guard_tool_call blocks writing hardcoded secret literals into files (contains_hardcoded_secret_literal)"),
    ("skills", "core/skills.py", "select_skills_context() injects relevant authored, promoted, and confirmed local skill packs from the profile-owned ~/.mo/skills root"),
    ("semantic memory", "core/learning/embeddings.py", "optional embeddings backend (build_embedder) gives EpisodicMemory.recall meaning-based ranking; bm25 keyword fallback"),
    ("adaptive reasoning", "core/agent/agent.py", "_adaptive_reasoning_level() picks per-turn depth; per-provider reasoning_effort seam in core/provider/provider.py"),
)

def _marker_in_text(marker: str, text: str) -> bool:
    """Whole-word marker match.

    Substring matching let the 2-char scope marker ``mo`` fire on ordinary words
    like *re**mo**ve* / *me**mo**ry* / *modal*, injecting the self-preflight on
    unrelated work. Word-boundary matching keeps real mentions (``audit mo``,
    ``devmode``, ``your codebase``) while dropping incidental substrings.
    """
    return re.search(r"\b" + re.escape(marker) + r"\b", text) is not None


def should_include_self_capability_preflight(user_input: str) -> bool:
    """Return True for MO self-work where capability discovery must precede action."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if is_owner_maintenance_activation(text) or is_owner_comparison_activation(text) or is_owner_interface_audit_activation(text) or is_owner_integrity_audit_activation(text) or is_owner_dedup_activation(text):
        return True
    scope_hit = any(_marker_in_text(marker, text) for marker in _SELF_SCOPE_MARKERS)
    action_hit = any(_marker_in_text(word, text) for word in _SELF_ACTION_WORDS)
    if scope_hit and action_hit:
        return True
    # Angry/corrective operator feedback often says "you" rather than "MO".
    # Require both self-action language and code/runtime nouns to avoid matching
    # ordinary "can you fix this bug" work.
    if "you" in text and action_hit and any(noun in text for noun in ("codebase", "feature", "graph", "skill", "tool", "trace", "workflow", "profile", "drift")):
        return True
    # MO runtime economy is self-work: "why do you cost so much per turn", "your
    # token spend". Require self scope (you/MO) + a cost noun + an economy phrase so
    # generic perf/cost talk ("reduce the cost of this query") does NOT fire.
    if ("you" in text or _marker_in_text("mo", text)) and \
            any(_marker_in_text(noun, text) for noun in ("cost", "costs", "token", "tokens", "spend", "expensive")) and \
            any(phrase in text for phrase in ("per turn", "per request", "each turn", "every turn", "per message", "so much", "so high")):
        return True
    return False

def _load_owner_preflight_rules() -> list[str]:
    """Load owner-only protocol preflight rules from profile-owned state.

    The detailed OWNER_MAINTENANCE/OWNER_COMPARISON protocol prose lives untracked in
    ``~/.mo/operator/devmode/preflight-rules.json`` (never shipped). A user clone has no
    such file, so the public code carries no protocol description — only a generic
    self-review reminder is emitted there.
    """
    try:
        path = operator_pack_root() / "devmode" / "preflight-rules.json"
        if not path.is_file():
            return []
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        return [str(item) for item in data if str(item).strip()]
    except Exception:
        return []


def build_self_capability_preflight_context(user_input: str, *, cwd: str | None = None) -> str:
    """Build the mandatory preflight context for MO self/OWNER_MAINTENANCE work.

    The detailed owner protocol rules are loaded from profile-owned state when
    present; absent that state (a user clone), only a generic self-review
    reminder plus live capability orientation is emitted.
    """
    if not should_include_self_capability_preflight(user_input):
        return ""
    root = Path(cwd or ".").resolve()
    lines = ["### MO Self-Capability Preflight — mandatory for this turn"]
    owner_rules = _load_owner_preflight_rules()
    if owner_rules:
        lines.extend(owner_rules)
        lines.append(
            "Required discovery areas: "
            + "; ".join(name for name, _anchors in REQUIRED_DISCOVERY_AREAS)
            + "."
        )
    else:
        lines.append(
            "This request is about MO's own behavior or capabilities. Before building or "
            "claiming completion, inventory the capabilities MO already has from live code "
            "evidence, prefer existing systems over new ones, and verify changes with the "
            "smallest sufficient tests."
        )
    lines.append("Relevant existing commands:")
    lines.extend(_relevant_command_lines())
    lines.append("Relevant code-backed capabilities to check:")
    lines.extend(_capability_file_lines(root))
    if owner_rules:
        lines.extend(_runtime_evidence_lines(root))
        if is_owner_maintenance_activation(user_input):
            lines.extend(_latest_blocked_devmode_lines())
    # OWNER_INTEGRITY_AUDIT audits arbitrary code, so it needs live-measured ground truth about the audit
    # target (line counts, function spans, symbol references) — not MO's self-capability
    # list. Append it so quantitative/exhaustiveness claims start from disk, not memory.
    if is_owner_integrity_audit_activation(user_input):
        from .owner_integrity_audit_ground_truth import build_owner_integrity_audit_ground_truth
        ground_truth = build_owner_integrity_audit_ground_truth(user_input, cwd=str(root))
        if ground_truth:
            lines.append("")
            lines.append(ground_truth)
    return "\n".join(lines)


def _latest_blocked_devmode_lines() -> list[str]:
    """Inject the latest blocked OWNER_MAINTENANCE run as mandatory startup evidence.

    A blocked run is not just historical telemetry: it is a self-maintenance failure
    mode that the next OWNER_MAINTENANCE pass must root-cause before declaring the
    codebase healthy. The block is emitted only when private owner rules are loaded,
    so user clones without the operator pack receive no owner-run history.
    """
    try:
        root = mo_home() / "memory" / "devmode"
        if not root.is_dir():
            return []
        sessions = sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        latest = None
        manifest = None
        for session in sessions[:20]:
            manifest_path = session / "manifest.json"
            if not manifest_path.is_file():
                continue
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                latest = session
                manifest = data
            break
        if latest is None or manifest is None:
            return []
        if str(manifest.get("status") or "").strip().lower() != "blocked":
            return []
        taskboard = manifest.get("taskboard") if isinstance(manifest.get("taskboard"), dict) else {}
        economy = manifest.get("economy") if isinstance(manifest.get("economy"), dict) else {}
        tasks = taskboard.get("tasks") if isinstance(taskboard.get("tasks"), list) else []
        open_titles = [
            str(task.get("title") or task.get("id") or "").strip()
            for task in tasks
            if isinstance(task, dict) and str(task.get("status") or "").strip().lower() != "completed"
        ]
        marker = _summary_terminal_marker(latest / "summary.md")
        lines = [
            "",
            "### Latest OWNER_MAINTENANCE Blocked Session - mandatory root-cause input",
            f"- latest_session: `{latest}`",
            "- manifest: "
            f"status=blocked, taskboard_state={taskboard.get('state', 'unknown')}, "
            f"open_count={taskboard.get('open_count', 'unknown')}",
            "- economy: "
            f"provider_requests={economy.get('provider_requests', 'unknown')}, "
            f"tool_calls={economy.get('tool_calls', 'unknown')}, "
            f"tool_errors={economy.get('tool_errors', 'unknown')}, "
            f"sandbox_blocked={economy.get('sandbox_blocked', 'unknown')}",
            f"- summary_marker: {marker or 'not found'}",
        ]
        if open_titles:
            lines.append("- first_open_rows: " + "; ".join(open_titles[:4]))
        lines.extend([
            "- Required action: before cataloging new work, explain why this session blocked, "
            "classify the blocker as already-fixed, product bug, protocol bug, or external boundary, "
            "and fix any product/protocol cause before [OWNER_MAINTENANCE COMPLETE].",
            "- Do not treat artifact wording such as `0 findings` or `healthy` as proof when "
            "manifest status/open_count says blocked.",
        ])
        return lines
    except Exception:
        return []


def _summary_terminal_marker(summary_path: Path) -> str:
    try:
        if not summary_path.is_file():
            return ""
        text = summary_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"\[OWNER_MAINTENANCE (?:COMPLETE|BLOCKED)\]", text)
        return match.group(0) if match else ""
    except Exception:
        return ""


def _runtime_evidence_lines(root: Path) -> list[str]:
    """Return sandbox-friendly runtime evidence locations to inspect."""
    home = mo_home()
    trace_dir = home / "memory" / "traces"
    trace_tool = "~/.mo/operator/mo_trace.py"
    lines = [
        "Runtime evidence paths to inspect when relevant:",
        f"- private runtime home: {home} (sessions, session_closeouts, heartbeat, tool/provider audit logs)",
        f"- live trace: {trace_dir}/trace_* (directory-based from {trace_tool} serve or .trace files; replay with `python {trace_tool} replay <path>`; list with `python {trace_tool} list`)",
        f"- backend monitor fallback: {home}/logs/monitor/backend_monitor-*.jsonl (when running mo.py directly)",
        f"- mo_trace.py: `python {trace_tool} serve <args>` (launches mo.py wrapped with auto-tracing; traces saved to {trace_dir}/)",
        "- if private profile-state paths are sandbox-blocked, state that explicitly and inspect source hooks instead of claiming trace coverage.",
    ]
    return lines


def _relevant_command_lines() -> list[str]:
    try:
        from interface.command_registry import COMMANDS
    except Exception:
        return ["- command registry unavailable; verify interface/command_registry.py with file tools"]
    lines: list[str] = []
    for spec in COMMANDS:
        if spec.name not in _RELEVANT_COMMANDS:
            continue
        subs = ", ".join(name for name, _desc in spec.subcommands) or "status/default"
        aliases = f" aliases={','.join(spec.aliases)}" if spec.aliases else ""
        lines.append(f"- {spec.name}{aliases}: {spec.description}; subcommands: {subs}")
    return lines or ["- no relevant slash commands discovered; verify command registry"]


def _capability_file_lines(root: Path) -> list[str]:
    lines: list[str] = []
    for name, rel, note in _CAPABILITY_FILES:
        if rel.startswith("~/.mo/operator/"):
            path = operator_pack_root() / rel.removeprefix("~/.mo/operator/")
            display = rel
        else:
            path = root / rel
            display = rel
        exists = path.exists()
        status = "exists" if exists else "missing"
        lines.append(f"- {name}: {display} ({status}) — {note}")
    return lines
