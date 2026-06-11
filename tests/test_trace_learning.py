from __future__ import annotations

import json
from pathlib import Path

from core.learning.proactive_learning import LearningSuggestion, SuggestionEvidence
from core.profile import Profile
from core.learning.trace_learning import analyze_runtime_closeout, analyze_trace_file, apply_trace_learning_suggestion


def _trace(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_analyze_trace_file_emits_inert_suggestions_for_failures(tmp_path):
    trace_path = _trace(
        tmp_path / "bad.trace",
        {
            "session_id": "trace-test",
            "mode": "run",
            "stdout": "agent output",
            "events": [
                {"type": "provider_request", "payload": {"provider": "mock"}},
                {"type": "provider_error", "payload": {"provider": "mock", "reason": "timeout"}},
                {"type": "tool_result", "payload": {"tool": "read_file", "error": True}},
                {"type": "sandbox_blocked", "payload": {"tool": "shell", "reason": "blocked"}},
            ],
            "validation": [
                {"name": "Memory indexed", "passed": False, "message": "No memory indexing events"},
                {"name": "Context activity", "passed": False, "message": "No context bridge events"},
            ],
        },
    )

    suggestions = analyze_trace_file(trace_path)
    kinds = {item.kind for item in suggestions}

    assert "trace:tool_errors" in kinds
    assert "trace:provider_errors" in kinds
    assert "trace:sandbox_blocks" in kinds
    assert "trace:no_memory_index" in kinds
    assert "trace:no_context_bridge" in kinds
    assert all(item.id.startswith("learning-suggestion:trace:") for item in suggestions)
    assert all("explicit" in item.promotion for item in suggestions)


def test_analyze_trace_file_is_bounded_and_malformed_safe(tmp_path):
    malformed = tmp_path / "bad.trace"
    malformed.write_text("not-json", encoding="utf-8")
    assert analyze_trace_file(malformed) == []

    oversized = tmp_path / "large.trace"
    oversized.write_text(json.dumps({"events": []}) + ("x" * 100), encoding="utf-8")
    assert analyze_trace_file(oversized, max_bytes=10) == []


def test_analyze_runtime_closeout_uses_closeout_meta_and_audit_deltas():
    suggestions = analyze_runtime_closeout(
        {
            "session_id": "closeout-test",
            "task_open": 1,
            "task_blocked": 1,
            "dirty_count": 2,
            "pressure": 0.9,
            "unresolved_preview": ["task active: Verify", "workspace has 2 uncommitted file(s)"],
        },
        audit_deltas={
            "tool": [{"tool": "shell", "blocked": True}],
            "provider": [{"event": "provider_error", "reason": "timeout"}],
        },
    )
    kinds = {item.kind for item in suggestions}

    assert "closeout:tool_blocks" in kinds
    assert "closeout:provider_errors" in kinds
    assert "closeout:blocked_tasks" in kinds
    assert "closeout:open_taskboard" in kinds
    assert "closeout:dirty_workspace" in kinds
    assert "closeout:context_pressure" in kinds
    assert all(item.id.startswith("learning-suggestion:closeout:") for item in suggestions)


def test_apply_trace_learning_suggestion_requires_profile_for_profile_write(tmp_path):
    suggestion = LearningSuggestion(
        id="learning-suggestion:trace:tool_errors:abc123",
        kind="trace:tool_errors",
        recommendation="verify after tool errors",
        evidence=(SuggestionEvidence("event-1", "tool error"),),
    )

    assert "profile learning unavailable" in apply_trace_learning_suggestion(None, suggestion)

    profile = Profile.load(str(tmp_path / "memory" / "mo.db"))
    result = apply_trace_learning_suggestion(profile, suggestion)

    assert result == "confirmed and added profile learning"
    learning = (tmp_path / "memory" / "profile" / "learning.md").read_text(encoding="utf-8")
    assert "tool errors" in learning.lower()


def test_apply_trace_learning_suggestion_keeps_diagnostics_inert():
    suggestion = LearningSuggestion(
        id="learning-suggestion:trace:no_memory_index:abc123",
        kind="trace:no_memory_index",
        recommendation="memory missing",
        evidence=(SuggestionEvidence("validation", "missing"),),
    )

    assert apply_trace_learning_suggestion(None, suggestion) == "confirmed diagnostic; no profile change"
