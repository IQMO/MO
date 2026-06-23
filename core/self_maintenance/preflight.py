"""Self-maintenance preflight context for MO's own codebase work."""
from __future__ import annotations

from pathlib import Path
import re

from ..owner_protocols import (
    is_devmode05_activation,
    is_iam05_activation,
    is_ifdev05_activation,
    is_vs05_activation,
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
    "devmode05",
    "mo",
    "vs05",
    "iam05",
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
# discovery contract for MO self/DEVMODE05 preflight.
REQUIRED_DISCOVERY_AREAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("slash commands", ("interface/command_registry.py", "/structural-graph", "/learning", "/profile")),
    ("turn/runtime hooks", ("core/agent/agent_turn.py", "core/agent/agent.py", "_record_turn_memory_and_learning", "_maybe_handle_workflow_control_turn")),
    ("graph/code map", ("core/graph/code_graph.py", "core/graph/structural_graph.py", "core/graph/search.py", "core/graph/callgraph.py", "graph_status()")),
    ("learning/profile/workflow", ("core/learning/proactive_learning.py", "core/learning/workflow_learning.py", "learning_suggestions.jsonl", "workflow_candidates.jsonl")),
    ("trace/session logs", ("memory/sessions", "session_closeouts", "heartbeats.jsonl", "tool_audit.jsonl", "provider_audit.jsonl", "logs/monitor/backend_monitor-*.jsonl", "~/.mo/operator/mo_trace.py")),
    ("taskboard/evidence", ("core/tasking/agent_taskboard.py", "complete_task", "core/tasking/task_evidence.py")),
    ("tests/docs", ("tests/", "~/.mo/memory/devmode", "~/.mo/operator/devmode/DEVMODE05.md", "~/.mo/operator/devmode/DEVMODE05/")),
    ("duplication/stale/legacy", ("git grep", "rg", "dead code", "duplicate paths", "retention proof")),
)

_CAPABILITY_FILES = (
    ("code graph", "core/graph/code_graph.py", "build_code_graph_context() injects orientation for non-greeting work"),
    ("structural graph", "core/graph/structural_graph.py", "community graph selected by code_graph when present"),
    ("graph fuzzy search", "core/graph/search.py", "BM25 symbol/file search exposed as the `code_search` tool (plain-language query) — use before broad grep"),
    ("call graph", "core/graph/callgraph.py", "get_callers()/get_callees() exposed as the `find_callers`/`find_callees` tools — answer who-calls-X cheaply before manual reference hunting"),
    ("slash command registry", "interface/command_registry.py", "lists runtime commands such as /structural-graph and /learning"),
    ("learning mining", "core/learning/proactive_learning.py", "/learning suggestions and /profile mine review safe learning updates"),
    ("workflow learning", "core/learning/workflow_learning.py", "stages/promotes workflow candidates; never auto-executes them"),
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
    ("skills", "core/skills.py", "select_skills_context() injects relevant read-before-acting best-practice packs from skills/ and ~/.mo/skills"),
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
    if is_devmode05_activation(text) or is_vs05_activation(text) or is_ifdev05_activation(text) or is_iam05_activation(text):
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
    """Load the owner-only protocol preflight rules from the operator pack.

    The detailed DEVMODE05/VS05 protocol prose lives untracked in
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
    """Build the mandatory preflight context for MO self/DEVMODE05 work.

    The detailed owner protocol rules are loaded from the operator pack when
    present; absent the pack (a user clone), only a generic self-review reminder
    plus live capability orientation is emitted.
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
    return "\n".join(lines)


def _runtime_evidence_lines(root: Path) -> list[str]:
    """Return sandbox-friendly runtime evidence locations to inspect."""
    home = mo_home()
    repo_memory = root / "memory"
    trace_dir = home / "memory" / "traces"
    trace_tool = "~/.mo/operator/mo_trace.py"
    lines = [
        "Runtime evidence paths to inspect when relevant:",
        f"- private runtime home: {home} (sessions, session_closeouts, heartbeat, tool/provider audit logs)",
        f"- live trace: {trace_dir}/trace_* (directory-based from {trace_tool} serve or .trace files; replay with `python {trace_tool} replay <path>`; list with `python {trace_tool} list`)",
        f"- backend monitor fallback: {home}/logs/monitor/backend_monitor-*.jsonl (when running mo.py directly)",
        f"- mo_trace.py: `python {trace_tool} serve <args>` (launches mo.py wrapped with auto-tracing; traces saved to {trace_dir}/)",
        f"- repo-local fallback: {repo_memory} (legacy/dev checkout memory when private home is unavailable)",
        "- if private paths are sandbox-blocked, state that explicitly and inspect repo-local memory plus source hooks instead of claiming trace coverage.",
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
