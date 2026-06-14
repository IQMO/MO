import json

from core.learning.memory import EpisodicMemory
from core.learning.proactive_learning import (
    mine_learning_suggestions,
    read_learning_suggestions,
    render_learning_suggestions,
    update_learning_suggestion_status,
    write_learning_suggestions,
)


def test_mine_learning_suggestions_requires_repeated_pattern(tmp_path):
    memory = EpisodicMemory(tmp_path / "learning.sqlite")
    memory.index_turn("t1", "feedback: verify evidence before claiming done", "understood and implemented")
    memory.index_turn("t2", "hello", "hello there")

    suggestions = mine_learning_suggestions(memory.path, min_occurrences=2)

    assert suggestions == []


def test_mine_learning_suggestions_ignores_assistant_self_echo(tmp_path):
    # Regression: MO's own assistant prose ("I verified the tests") must not
    # complete an operator-feedback pattern started by the user. With the trigger
    # words only in the user turn and the evidence words only in the assistant
    # turn, no suggestion should be mined.
    memory = EpisodicMemory(tmp_path / "learning.sqlite")
    memory.index_turn("t1", "you didn't do it", "I verified the tests and checked the logs and files")
    memory.index_turn("t2", "you didn't finish", "I verified the runtime evidence and tests again")

    suggestions = mine_learning_suggestions(memory.path, min_occurrences=2)

    assert suggestions == []


def test_mine_learning_suggestions_returns_reviewable_evidence(tmp_path):
    memory = EpisodicMemory(tmp_path / "learning.sqlite")
    memory.index_turn("t1", "feedback: verify evidence before claiming done", "understood and implemented")
    memory.index_turn("t2", "next time use test evidence before ready claims", "noted and fixed")

    suggestions = mine_learning_suggestions(memory.path, min_occurrences=2)

    assert len(suggestions) == 1
    assert suggestions[0].kind == "evidence_first"
    assert suggestions[0].status == "suggested"
    assert "requires explicit operator approval" in suggestions[0].promotion
    assert {item.turn_id for item in suggestions[0].evidence} == {"t1", "t2"}


def test_write_learning_suggestions_appends_unique_records(tmp_path):
    memory = EpisodicMemory(tmp_path / "learning.sqlite")
    memory.index_turn("t1", "feedback: verify evidence before claiming done", "understood and implemented")
    memory.index_turn("t2", "next time use test evidence before ready claims", "noted and fixed")
    suggestions = mine_learning_suggestions(memory.path, min_occurrences=2)
    out = tmp_path / "learning_suggestions.jsonl"

    write_learning_suggestions(suggestions, path=out)
    write_learning_suggestions(suggestions, path=out)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "suggested"
    assert rows[0]["evidence"][0]["turn_id"] in {"t1", "t2"}


def test_read_and_update_learning_suggestion_status(tmp_path):
    memory = EpisodicMemory(tmp_path / "learning.sqlite")
    memory.index_turn("t1", "feedback: verify evidence before claiming done", "understood and implemented")
    memory.index_turn("t2", "next time use test evidence before ready claims", "noted and fixed")
    suggestions = mine_learning_suggestions(memory.path, min_occurrences=2)
    out = tmp_path / "learning_suggestions.jsonl"
    write_learning_suggestions(suggestions, path=out)

    active = read_learning_suggestions(path=out)
    assert len(active) == 1
    assert update_learning_suggestion_status(active[0].id, "dismissed", path=out) is True

    assert read_learning_suggestions(path=out) == []
    inactive = read_learning_suggestions(path=out, include_inactive=True)
    assert len(inactive) == 1
    assert inactive[0].status == "dismissed"


def test_render_learning_suggestions_states_inert_boundary(tmp_path):
    memory = EpisodicMemory(tmp_path / "learning.sqlite")
    memory.index_turn("t1", "feedback: verify evidence before claiming done", "understood and implemented")
    memory.index_turn("t2", "next time use test evidence before ready claims", "noted and fixed")
    suggestions = mine_learning_suggestions(memory.path, min_occurrences=2)

    rendered = render_learning_suggestions(suggestions, path="memory/learning_suggestions.jsonl")

    assert "inert suggestions" in rendered
    assert "approve explicitly" in rendered
    assert "Evidence:" in rendered


# ── clustering / confidence / expiry (review-loop closure) ───────────────────

def _mk_suggestion(sid, kind, rec, created_at, status="suggested"):
    from core.learning.proactive_learning import LearningSuggestion
    return LearningSuggestion(id=sid, kind=kind, recommendation=rec, evidence=(), status=status, created_at=created_at)


def test_cluster_collapses_same_kind_and_recommendation():
    from core.learning.proactive_learning import cluster_suggestions
    now = 1_800_000_000.0
    suggestions = [
        _mk_suggestion("a1", "closeout:provider_errors", "Review provider errors.", now - 86400 * 3),
        _mk_suggestion("a2", "closeout:provider_errors", "Review provider errors.", now - 86400 * 2),
        _mk_suggestion("a3", "closeout:provider_errors", "review  provider errors", now - 86400),
        _mk_suggestion("b1", "evidence_first", "Verify before claiming.", now - 86400),
    ]

    clusters = cluster_suggestions(suggestions, now=now)

    assert len(clusters) == 2
    big = next(c for c in clusters if c.kind == "closeout:provider_errors")
    assert big.count == 3
    assert set(big.ids) == {"a1", "a2", "a3"}
    assert big.representative.id == "a3"  # latest member represents the cluster


def test_cluster_confidence_orders_operator_feedback_above_trace_noise():
    from core.learning.proactive_learning import cluster_suggestions
    now = 1_800_000_000.0
    suggestions = [
        _mk_suggestion("t1", "trace:tool_errors", "Investigate tool errors.", now - 86400),
        _mk_suggestion("f1", "evidence_first", "Verify before claiming.", now - 86400),
    ]

    clusters = cluster_suggestions(suggestions, now=now)

    assert clusters[0].kind == "evidence_first"  # operator feedback outranks trace-derived
    assert clusters[0].confidence > clusters[1].confidence
    assert all(0.0 < c.confidence <= 1.0 for c in clusters)


def test_recurrence_raises_confidence():
    from core.learning.proactive_learning import cluster_suggestions
    now = 1_800_000_000.0
    once = cluster_suggestions([_mk_suggestion("x1", "scope_control", "Stay on scope.", now)], now=now)[0]
    thrice = cluster_suggestions([
        _mk_suggestion("y1", "scope_control", "Stay on scope.", now - 200),
        _mk_suggestion("y2", "scope_control", "Stay on scope.", now - 100),
        _mk_suggestion("y3", "scope_control", "Stay on scope.", now),
    ], now=now)[0]

    assert thrice.confidence > once.confidence


def test_expire_stale_suggestions_marks_old_unreviewed(tmp_path):
    import json, time
    from core.learning.proactive_learning import expire_stale_suggestions, read_learning_suggestions
    path = tmp_path / "suggestions.jsonl"
    now = time.time()
    rows = [
        {"id": "old1", "kind": "closeout:x", "recommendation": "Old thing.", "evidence": [], "status": "suggested", "created_at": now - 86400 * 40},
        {"id": "new1", "kind": "closeout:x", "recommendation": "New thing.", "evidence": [], "status": "suggested", "created_at": now - 86400 * 2},
        {"id": "conf1", "kind": "closeout:x", "recommendation": "Kept.", "evidence": [], "status": "confirmed", "created_at": now - 86400 * 90},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    expired = expire_stale_suggestions(path=path, ttl_days=30, now=now)

    assert expired == 1
    active = {s.id for s in read_learning_suggestions(path=path)}
    assert active == {"new1"}  # old1 expired; conf1 untouched (already reviewed)
    everything = {s.id: s.status for s in read_learning_suggestions(path=path, include_inactive=True)}
    assert everything["old1"] == "expired"
    assert everything["conf1"] == "confirmed"


def test_resolve_cluster_ids_returns_all_members(tmp_path):
    import json, time
    from core.learning.proactive_learning import resolve_cluster_ids
    path = tmp_path / "suggestions.jsonl"
    now = time.time()
    rows = [
        {"id": "m1", "kind": "closeout:y", "recommendation": "Same insight.", "evidence": [], "status": "suggested", "created_at": now - 100},
        {"id": "m2", "kind": "closeout:y", "recommendation": "Same insight.", "evidence": [], "status": "suggested", "created_at": now},
        {"id": "z1", "kind": "evidence_first", "recommendation": "Different.", "evidence": [], "status": "suggested", "created_at": now},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    assert set(resolve_cluster_ids("m1", path=path)) == {"m1", "m2"}
    assert resolve_cluster_ids("missing", path=path) == []


def test_render_learning_clusters_shows_top_five_and_totals():
    from core.learning.proactive_learning import cluster_suggestions, render_learning_clusters
    now = 1_800_000_000.0
    suggestions = [
        _mk_suggestion(f"s{i}", f"closeout:kind{i}", f"Recommendation number {i}.", now - i)
        for i in range(8)
    ]
    clusters = cluster_suggestions(suggestions, now=now)

    text = render_learning_clusters(clusters, raw_count=8, expired_count=2)

    assert "8 cluster(s) from 8 raw suggestion(s)" in text
    assert "2 stale suggestion(s) auto-expired" in text
    assert "+3 lower-confidence cluster(s)" in text
    assert "MO" in text and "skills" in text
