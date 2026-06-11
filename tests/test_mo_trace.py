from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import mo_trace
from core.learning.proactive_learning import read_learning_suggestions


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_read_jsonl_tail_reads_appended_entries(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"a": 1}) + "\n", encoding="utf-8")
    offset = path.stat().st_size
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not-json\n")
        fh.write(json.dumps({"b": 2}) + "\n")

    assert mo_trace._read_jsonl_tail(path, offset) == [{"b": 2}]


def test_read_jsonl_tail_handles_rotation(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")
    old_size = path.stat().st_size + 100
    path.write_text(json.dumps({"new": True}) + "\n", encoding="utf-8")

    assert mo_trace._read_jsonl_tail(path, old_size) == [{"new": True}]


def test_collect_jsonl_delta_uses_supplied_config_paths(tmp_path, monkeypatch):
    configured = tmp_path / "state" / "logs" / "provider_audit.jsonl"
    legacy = tmp_path / "logs" / "provider_audit.jsonl"
    _write_jsonl(configured, [{"id": "configured-old"}])
    _write_jsonl(legacy, [{"id": "legacy-old"}])
    sizes_before = {"provider": {str(configured): configured.stat().st_size}}
    with configured.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "configured-new"}) + "\n")
    with legacy.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "legacy-new"}) + "\n")

    def fake_source_paths(config=None):
        if config and config.get("state") == "configured":
            return {"provider": [configured]}
        return {"provider": [legacy]}

    monkeypatch.setattr(mo_trace, "_source_paths", fake_source_paths)

    delta = mo_trace._collect_jsonl_delta(sizes_before, {"state": "configured"})

    assert delta["provider"] == [{"id": "configured-new"}]


def test_jsonl_entries_normalize_to_validator_events():
    backend = mo_trace._events_from_jsonl_entry("backend", {"ts": 1, "type": "turn_context", "payload": {"extra_context_chars": 7}})
    tool = mo_trace._events_from_jsonl_entry("tool", {"ts": 2, "tool": "read_file", "arguments": {"path": "x.py"}, "blocked": False, "result_chars": 3})
    provider = mo_trace._events_from_jsonl_entry("provider", {"ts": 3, "event": "provider_error", "provider": "p"})

    assert backend[0]["type"] == "turn_context"
    assert [event["type"] for event in tool] == ["tool_call", "tool_result"]
    assert provider[0]["type"] == "provider_error"

    events = backend + tool + provider
    assert mo_trace._v_context(events)[0] is True
    assert mo_trace._v_tool_usage(events)[0] is True
    assert mo_trace._v_provider_errors(events)[0] is False


def test_tool_error_validator_warns_for_recovered_clean_completion():
    trace = {
        "events": [
            {"type": "turn_context", "payload": {}},
            {"type": "tool_result", "payload": {"tool": "shell", "error": True, "blocked": False}},
            {"type": "board_advance", "payload": {"completed": "1", "total": "1"}},
            {"type": "turn_end", "payload": {"status": "ok"}},
        ]
    }

    passed, message, status = mo_trace._v_tool_errors_trace(trace)

    assert passed is True
    assert status == "warn"
    assert "recovered after clean completion" in message


def test_tool_error_validator_fails_without_clean_completion():
    trace = {
        "events": [
            {"type": "turn_context", "payload": {}},
            {"type": "tool_result", "payload": {"tool": "shell", "error": True, "blocked": False}},
        ]
    }

    passed, message, status = mo_trace._v_tool_errors_trace(trace)

    assert passed is False
    assert status == "fail"
    assert "no clean completion evidence" in message


def test_tool_error_validator_fails_when_hard_runtime_boundary_remains():
    trace = {
        "events": [
            {"type": "tool_result", "payload": {"tool": "shell", "error": True, "blocked": True}},
            {"type": "sandbox_blocked", "payload": {"tool": "shell", "reason": "blocked"}},
            {"type": "turn_end", "payload": {"status": "ok"}},
        ]
    }

    passed, message, status = mo_trace._v_tool_errors_trace(trace)

    assert passed is False
    assert status == "fail"
    assert "sandbox block" in message


def test_timing_order_ignores_ghost_provider_before_main_turn_context():
    trace = {
        "events": [
            {"ts": 1.0, "type": "provider_request", "payload": {"surface": "ghost_panel"}},
            {"ts": 2.0, "type": "turn_context", "payload": {}},
            {"ts": 3.0, "type": "provider_request", "payload": {"surface": "main"}},
        ]
    }

    passed, message, status = mo_trace._v_timing_order_trace(trace)

    assert passed is True
    assert status == "pass"
    assert "provider_request before turn_context" not in message


def test_timing_order_ignores_prior_audit_events_before_turn_start():
    trace = {
        "events": [
            {"ts": 1.0, "type": "provider_request", "payload": {"surface": "main"}},
            {"ts": 10.0, "type": "turn_start", "payload": {}},
            {"ts": 11.0, "type": "turn_context", "payload": {}},
            {"ts": 12.0, "type": "provider_request", "payload": {"surface": "main"}},
        ]
    }

    passed, message, status = mo_trace._v_timing_order_trace(trace)

    assert passed is True
    assert status == "pass"
    assert "provider_request before turn_context" not in message


def test_provider_error_validator_ignores_prior_audit_events_before_turn_start():
    trace = {
        "events": [
            {"ts": 1.0, "type": "provider_error", "payload": {"surface": "main", "reason": "old"}},
            {"ts": 10.0, "type": "turn_start", "payload": {}},
            {"ts": 11.0, "type": "turn_context", "payload": {}},
            {"ts": 12.0, "type": "provider_request", "payload": {"surface": "main"}},
            {"ts": 13.0, "type": "provider_response", "payload": {"surface": "main"}},
        ]
    }

    passed, message, status = mo_trace._v_provider_errors_trace(trace)

    assert passed is True
    assert status == "pass"
    assert message == "No provider errors"


def test_monitor_trace_output_without_stdout_is_info():
    passed, message, status = mo_trace._v_output("", trace_mode="monitor")

    assert passed is True
    assert status == "info"
    assert "monitor trace" in message


def test_monitor_trace_runtime_artifacts_are_optional():
    passed, message, status = mo_trace._v_runtime_artifacts_trace({"mode": "monitor"})

    assert passed is True
    assert status == "info"
    assert "Monitor trace" in message


def test_monitor_trace_closeout_artifacts_are_optional_until_session_end():
    passed, message, status = mo_trace._v_closeout_artifacts_trace({"mode": "monitor"})

    assert passed is True
    assert status == "info"
    assert "closeout ledger" in message


def test_cmd_serve_collects_backend_and_audit_deltas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mo_trace, "TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(mo_trace, "_snapshot_learning", lambda *a, **k: {"status": "ok", "total_turns": 2, "indexed": 2})

    def fake_run(args, timeout, env):
        monitor_dir = Path(env["MO_BACKEND_MONITOR_DIR"])
        _write_jsonl(
            monitor_dir / "backend_monitor-20260601-000000-deadbeef.jsonl",
            [
                {"ts": 10.0, "type": "turn_context", "payload": {"extra_context_chars": 42}},
                {"ts": 11.0, "type": "memory_index", "payload": {"turn_id": "t1"}},
            ],
        )
        _write_jsonl(tmp_path / "logs" / "tool_audit.jsonl", [{"ts": 12.0, "tool": "read_file", "arguments": {"path": "README.md"}, "blocked": False, "result_chars": 10}])
        _write_jsonl(tmp_path / "logs" / "provider_audit.jsonl", [{"ts": 13.0, "event": "provider_response", "provider": "mock", "ok": True}])
        _write_jsonl(tmp_path / "memory" / "file_operations.jsonl", [{"closed_at": 14.0, "files_read": ["README.md"], "files_modified": []}])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mo_trace.subprocess, "run", fake_run)

    trace = mo_trace.cmd_serve(["--trace", "--init"])

    assert trace["mode"] == "serve"
    assert trace["return_code"] == 0
    assert len(trace["jsonl_delta"]["backend"]) == 2
    assert any(event["type"] == "turn_context" for event in trace["events"])
    assert any(event["type"] == "tool_call" for event in trace["events"])
    validation = {row["name"]: row for row in trace["validation"]}
    assert validation["Context activity"]["passed"] is True
    assert validation["Memory indexed"]["passed"] is True
    assert validation["Tool usage"]["passed"] is True
    assert validation["Tool audit"]["passed"] is True
    assert validation["File operations"]["passed"] is True
    assert validation["Output produced"]["passed"] is True


def test_anti_hallucination_contract_handles_non_dict_handoff_payload():
    trace = {"events": [{"type": "context_handoff", "payload": ["orientation only, not proof"]}]}

    assert mo_trace._v_anti_hallucination_contract(trace) == (
        True,
        "1 handoff event(s) — all labeled orientation",
        "pass",
    )


def test_tool_compression_validator_reports_saved_context():
    events = [
        {
            "type": "tool_compress",
            "payload": {
                "format": "grep",
                "before_chars": 1200,
                "after_chars": 400,
                "saved_chars": 800,
                "saved_pct": 66.7,
            },
        },
        {
            "type": "tool_compress",
            "payload": {
                "format": "truncate",
                "before_chars": 9000,
                "after_chars": 6016,
                "saved_chars": 2984,
                "saved_pct": 33.2,
            },
        },
    ]

    passed, message, status = mo_trace._v_tool_compression(events)

    assert passed is True
    assert status == "pass"
    assert "2 context-saving event(s)" in message
    assert "3,784 chars" in message
    assert "~946 tokens" in message
    assert "structural=1" in message
    assert "truncate=1" in message


def test_tool_compression_validator_fails_invalid_savings():
    events = [
        {
            "type": "tool_compress",
            "payload": {
                "format": "grep",
                "before_chars": 400,
                "after_chars": 500,
                "saved_chars": 0,
            },
        }
    ]

    passed, message, status = mo_trace._v_tool_compression(events)

    assert passed is False
    assert status == "fail"
    assert "non-positive savings" in message
    assert "after>=before" in message


def test_anti_hallucination_validates_handoff_even_with_compaction():
    trace = {
        "events": [
            {
                "type": "session_compact",
                "payload": {
                    "label": "orientation only",
                    "saved_chars": 1000,
                    "before_messages": 10,
                    "after_messages": 4,
                    "truth_boundary": {
                        "labeled": True,
                        "evidence_preserved": ["tool:read_file", "file:core/agent.py"],
                    },
                },
            },
            {
                "type": "context_handoff",
                "payload": {"text": "summary without the required label"},
            },
        ],
    }

    passed, message, status = mo_trace._v_anti_hallucination_contract(trace)

    assert passed is False
    assert status == "fail"
    assert "handoff missing orientation label" in message


def test_anti_hallucination_requires_compaction_evidence_anchors():
    trace = {
        "events": [
            {
                "type": "session_compact",
                "payload": {
                    "label": "orientation only",
                    "saved_chars": 1000,
                    "before_messages": 10,
                    "after_messages": 4,
                    "truth_boundary": {"labeled": True, "evidence_preserved": []},
                },
            }
        ],
    }

    passed, message, status = mo_trace._v_anti_hallucination_contract(trace)

    assert passed is False
    assert status == "fail"
    assert "no preserved evidence anchors" in message


def test_session_momentum_validator_reports_quality_and_handoff_overlap():
    trace = {
        "events": [
            {
                "type": "session_compact",
                "payload": {
                    "stage": "pre_turn",
                    "saved_chars": 2400,
                    "before_messages": 40,
                    "after_messages": 28,
                    "before_chars": 9000,
                    "after_chars": 6600,
                    "pressure": 0.52,
                    "message_ratio": 0.30,
                    "force": False,
                    "truth_boundary": {
                        "evidence_preserved": ["shell(command=git diff)"],
                    },
                },
            },
            {
                "type": "session_event",
                "payload": {
                    "kind": "session_compact",
                    "stage": "pre_turn",
                    "saved_chars": 2400,
                },
            },
            {
                "type": "context_handoff",
                "payload": {"text": "orientation only, not proof"},
            },
            {
                "type": "context_handoff",
                "source": "provider",
                "payload": {"event": "context_handoff", "reason": "audit mirror without provider-facing content"},
            },
        ],
    }

    passed, message, status = mo_trace._v_session_momentum_trace(trace)

    assert passed is True
    assert status == "pass"
    assert "1 session momentum compaction event" in message
    assert "2,400 chars saved" in message
    assert "handoff also observed=1" in message


def test_anti_hallucination_ignores_provider_audit_handoff_mirrors():
    trace = {
        "events": [
            {
                "type": "context_handoff",
                "source": "provider",
                "payload": {"event": "context_handoff", "reason": "legacy audit mirror"},
            },
            {
                "type": "context_handoff",
                "payload": {"text": "orientation only, not proof"},
            },
        ],
    }

    assert mo_trace._v_anti_hallucination_contract(trace) == (
        True,
        "1 handoff event(s) — all labeled orientation",
        "pass",
    )


def test_session_momentum_validator_fails_premature_or_empty_compaction():
    trace = {
        "events": [
            {
                "type": "session_compact",
                "payload": {
                    "stage": "post_context",
                    "saved_chars": 0,
                    "before_messages": 20,
                    "after_messages": 20,
                    "before_chars": 5000,
                    "after_chars": 5000,
                    "pressure": 0.10,
                    "message_ratio": 0.10,
                    "force": False,
                    "truth_boundary": {"evidence_preserved": []},
                },
            }
        ],
    }

    passed, message, status = mo_trace._v_session_momentum_trace(trace)

    assert passed is False
    assert status == "fail"
    assert "non-positive savings" in message
    assert "below minimum pressure" in message
    assert "no preserved evidence anchors" in message


def test_write_trace_learning_suggestions_is_best_effort(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    trace_path = tmp_path / "trace.trace"
    trace_path.write_text(
        json.dumps({
            "session_id": "trace-learning-test",
            "mode": "run",
            "stdout": "agent output",
            "events": [
                {"type": "turn_context", "payload": {"extra_context_chars": 5}},
                {"type": "tool_result", "payload": {"tool": "read_file", "error": True}},
            ],
            "validation": [],
        }),
        encoding="utf-8",
    )

    assert mo_trace._write_trace_learning_suggestions(trace_path) == 2
    suggestions = read_learning_suggestions(path=tmp_path / "memory" / "learning_suggestions.jsonl")
    assert {item.kind for item in suggestions} == {"trace:tool_errors", "trace:no_memory_index"}
