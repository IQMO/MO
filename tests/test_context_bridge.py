from core.context_bridge import ContextSource, build_active_context_bridge


def test_context_bridge_prioritizes_contract_before_sources():
    bridge = build_active_context_bridge(
        "review current files",
        [
            ContextSource("memory", "Recalled past interactions", "### Memory\nold claim", 5, "orientation only"),
            ContextSource("profile", "Current operator profile", "Keep routine replies concise.", 2, "profile guidance"),
        ],
    ).text

    assert bridge.index("Priority 1 — Non-negotiable contract") < bridge.index("Priority 2 — Current operator profile")
    assert bridge.index("Priority 2 — Current operator profile") < bridge.index("Priority 5 — Recalled past interactions")
    assert "current user request win" in bridge
    assert "Evidence rule" in bridge


def test_context_bridge_labels_memory_and_graph_as_orientation_only():
    bridge = build_active_context_bridge(
        "inspect agent runtime",
        [
            ContextSource("memory", "Recalled past interactions", "### Recalled Past Interactions - orientation only\n- old", 5, "orientation only; not proof"),
            ContextSource("code_graph", "Code map", "### MO Internal Code Map - orientation only\n- core/agent.py", 5, "orientation only; verify with tools"),
        ],
    ).text

    assert "Priority 5 — Recalled past interactions" in bridge
    assert "Priority 5 — Code map" in bridge
    assert "orientation only" in bridge
    assert "re-read files and run relevant checks" in bridge


def test_context_bridge_resolves_concise_profile_vs_review_depth():
    bridge = build_active_context_bridge(
        "review this diff and report evidence",
        [
            ContextSource("profile", "Current operator profile", "Keep routine replies concise and direct.", 2, "profile guidance"),
            ContextSource("work_pattern", "Active work pattern", "### MO Internal Work Pattern — review/evidence\nInspect scoped target before reporting findings.", 3, "process guidance"),
        ],
    ).text

    assert "Concise profile vs review/audit depth" in bridge
    assert "include enough evidence refs" in bridge


def test_context_bridge_deduplicates_repeated_source_lines():
    bridge = build_active_context_bridge(
        "fix bug",
        [
            ContextSource("work_pattern", "Active work pattern", "repeat this exact guidance\nrepeat this exact guidance\nunique guidance", 3, "process guidance"),
        ],
    ).text

    assert bridge.count("repeat this exact guidance") == 1
    assert "unique guidance" in bridge


def test_context_bridge_token_aware_truncation_is_gated(monkeypatch):
    content = "alpha " * 100
    monkeypatch.setenv("MO_TOKEN_AWARE_TRUNCATION", "1")

    bridge = build_active_context_bridge(
        "fix bug",
        [ContextSource("memory", "Memory", content, 5, "orientation", max_chars=12)],
    ).text

    assert "[memory context truncated]" in bridge
    assert "alpha alpha" in bridge
