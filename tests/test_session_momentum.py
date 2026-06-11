from __future__ import annotations

import json
from types import SimpleNamespace

from core.backend_monitor import BackendMonitor
from core.session.session import Session
from core.session.session_momentum import compact_completed_tool_chains, maybe_compact_session


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_compact_completed_tool_chains_replaces_old_chain_with_orientation_summary():
    session = Session("system", max_history=100)
    session.add_user("inspect the diff and report")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "git diff"})]})
    session.add_tool_result("c1", "diff --git a/a.py b/a.py\n" + "+line\n" * 200)
    session.add_assistant("Diff inspected; one file changed.")
    for idx in range(20):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")

    result = compact_completed_tool_chains(session, keep_recent=18, max_chains=2)

    assert result["changed"] is True
    assert result["saved_chars"] > 0
    assert not any(msg.get("role") == "tool" for msg in session.messages[:-18])
    compacted = [msg for msg in session.messages if "SESSION MOMENTUM COMPACTED" in str(msg.get("content", ""))]
    assert len(compacted) == 1
    assert "shell(command=git diff)" in compacted[0]["content"]
    assert "orientation only, not proof" in compacted[0]["content"]


def test_compact_completed_tool_chains_skips_unfinished_tail():
    session = Session("system", max_history=100)
    session.add_user("start work")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "read_file", {"path": "core/agent.py"})]})
    session.add_tool_result("c1", "content" * 100)

    result = compact_completed_tool_chains(session, keep_recent=0, max_chains=2)

    assert result["changed"] is False
    assert any(msg.get("role") == "tool" for msg in session.messages)


def test_maybe_compact_session_force_compacts_for_overflow_recovery():
    session = Session("system", max_history=100)
    session.add_user("inspect the diff and report")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "git diff"})]})
    session.add_tool_result("c1", "diff --git a/a.py b/a.py\n" + "+line\n" * 200)
    session.add_assistant("Diff inspected; one file changed.")
    for idx in range(20):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")
    agent = SimpleNamespace(
        session=session,
        config={"agent": {"context_momentum_compact_threshold": 0.80, "context_momentum_keep_recent": 18}},
        _provider_context_max_chars=lambda: 1_000_000,
        _is_foreground_session=lambda: True,
        session_compaction_total_ops=0,
        session_compaction_total_saved=0,
    )

    result = maybe_compact_session(agent, stage="overflow_recovery", force=True)

    assert result["changed"] is True
    assert result["force"] is True
    assert result["stage"] == "overflow_recovery"
    assert agent.session_compaction_total_ops == 1


def test_compact_archives_full_chain_before_replacing(tmp_path):
    """Tool-result aging: the exact outputs survive on disk and the summary
    references the archive path so MO can read them back."""
    session = Session("system", max_history=100)
    session.add_user("inspect the diff and report")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "git diff"})]})
    big_output = "diff --git a/a.py b/a.py\n" + "+line\n" * 200
    session.add_tool_result("c1", big_output)
    session.add_assistant("Diff inspected; one file changed.")
    for idx in range(20):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")

    result = compact_completed_tool_chains(session, keep_recent=18, max_chains=2, archive_dir=tmp_path / "chains")

    assert result["changed"] is True
    assert len(result["archived_paths"]) == 1
    archived = json.loads((tmp_path / "chains").glob("*.json").__next__().read_text(encoding="utf-8"))
    assert any(big_output in str(m.get("content") or "") for m in archived)
    compacted = [msg for msg in session.messages if "SESSION MOMENTUM COMPACTED" in str(msg.get("content", ""))]
    assert "Full tool results archived:" in compacted[0]["content"]
    assert result["archived_paths"][0] in compacted[0]["content"]


def test_compact_without_archive_dir_keeps_legacy_behavior():
    session = Session("system", max_history=100)
    session.add_user("inspect")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "ls"})]})
    session.add_tool_result("c1", "out\n" * 300)
    session.add_assistant("done.")
    for idx in range(20):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")

    result = compact_completed_tool_chains(session, keep_recent=18, max_chains=2)

    assert result["changed"] is True
    assert result["archived_paths"] == []
    compacted = [msg for msg in session.messages if "SESSION MOMENTUM COMPACTED" in str(msg.get("content", ""))]
    assert "Full tool results archived:" not in compacted[0]["content"]


def test_maybe_compact_session_triggers_on_oversized_old_tool_results():
    """Aging trigger: huge old tool results justify compaction even when
    context pressure is far below the percentage threshold."""
    session = Session("system", max_history=200)
    session.add_user("run the full suite")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "pytest -q"})]})
    session.add_tool_result("c1", "test output line\n" * 4000)  # ~68K chars
    session.add_assistant("Suite ran.")
    for idx in range(20):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")
    agent = SimpleNamespace(
        session=session,
        config={"agent": {"context_momentum_compact_threshold": 0.80, "context_momentum_keep_recent": 18}},
        _provider_context_max_chars=lambda: 10_000_000,  # pressure ≈ 0
        _is_foreground_session=lambda: True,
        session_compaction_total_ops=0,
        session_compaction_total_saved=0,
    )

    result = maybe_compact_session(agent, stage="pre_turn")

    assert result["changed"] is True
    assert result["tool_chars_trigger"] is True
    assert result["old_tool_chars"] > 48_000


def test_maybe_compact_session_stays_idle_below_tool_chars_threshold():
    session = Session("system", max_history=200)
    session.add_user("small check")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "git status"})]})
    session.add_tool_result("c1", "clean tree\n")
    session.add_assistant("Tree clean.")
    for idx in range(20):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")
    agent = SimpleNamespace(
        session=session,
        config={"agent": {"context_momentum_compact_threshold": 0.80, "context_momentum_keep_recent": 18}},
        _provider_context_max_chars=lambda: 10_000_000,
        _is_foreground_session=lambda: True,
        session_compaction_total_ops=0,
        session_compaction_total_saved=0,
    )

    result = maybe_compact_session(agent, stage="pre_turn")

    assert result["changed"] is False
    assert result["reason"] == "below_threshold"


def test_maybe_compact_session_emits_monitor_event_under_pressure(tmp_path):
    session = Session("system", max_history=100)
    session.add_user("inspect the diff and report")
    session.add_message({"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", "shell", {"command": "git diff"})]})
    session.add_tool_result("c1", "diff --git a/a.py b/a.py\n" + "+line\n" * 200)
    session.add_assistant("Diff inspected; one file changed.")
    for idx in range(30):
        session.add_user(f"recent {idx}")
        session.add_assistant(f"answer {idx}")
    agent = SimpleNamespace(
        session=session,
        config={"agent": {"context_momentum_compact_threshold": 0.25, "context_momentum_keep_recent": 18}},
        _provider_context_max_chars=lambda: 10_000,
        _is_foreground_session=lambda: True,
        session_compaction_total_ops=0,
        session_compaction_total_saved=0,
    )
    monitor_path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(monitor_path)

    result = maybe_compact_session(agent, stage="test", monitor=monitor)

    assert result["changed"] is True
    assert agent.session_compaction_total_ops == 1
    text = monitor_path.read_text(encoding="utf-8")
    assert '"type": "session_compact"' in text
    assert '"kind": "session_compact"' in text
