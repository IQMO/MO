"""Tests for core/hooks.py — operator lifecycle hooks on monitor events."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from core.hooks import dispatch_hooks, load_hooks, matching_hooks


def _write_hooks(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_load_hooks_parses_enabled_entries(tmp_path):
    path = _write_hooks(tmp_path / "hooks.yaml", (
        "enabled: true\n"
        "hooks:\n"
        "  - event: turn_end\n"
        "    run: echo done\n"
        "  - event: 'provider_*'\n"
        "    match: overflow\n"
        "    run: echo provider\n"
    ))

    hooks = load_hooks(path)

    assert len(hooks) == 2
    assert hooks[0] == {"event": "turn_end", "match": "", "run": "echo done"}
    assert hooks[1]["event"] == "provider_*"
    assert hooks[1]["match"] == "overflow"


def test_load_hooks_disabled_or_missing_returns_empty(tmp_path):
    disabled = _write_hooks(tmp_path / "off.yaml", "enabled: false\nhooks:\n  - event: x\n    run: echo x\n")
    assert load_hooks(disabled) == []
    assert load_hooks(tmp_path / "missing.yaml") == []


def test_load_hooks_skips_malformed_entries(tmp_path):
    path = _write_hooks(tmp_path / "hooks.yaml", (
        "enabled: true\n"
        "hooks:\n"
        "  - event: ''\n"
        "    run: echo no-event\n"
        "  - event: ok\n"
        "    run: ''\n"
        "  - event: good\n"
        "    run: echo good\n"
    ))

    hooks = load_hooks(path)

    assert len(hooks) == 1
    assert hooks[0]["event"] == "good"


def test_matching_hooks_event_pattern_and_payload_substring():
    hooks = [
        {"event": "turn_end", "match": "", "run": "a"},
        {"event": "provider_*", "match": "", "run": "b"},
        {"event": "*", "match": "critical", "run": "c"},
    ]

    assert [h["run"] for h in matching_hooks("turn_end", {}, hooks)] == ["a"]
    assert [h["run"] for h in matching_hooks("provider_error", {}, hooks)] == ["b"]
    assert [h["run"] for h in matching_hooks("security_check", {"severity": "critical"}, hooks)] == ["c"]
    assert matching_hooks("memory_recall", {"x": 1}, hooks) == []


def _hook_script(tmp_path: Path, marker: Path, env_keys: str = "MO_HOOK_EVENT|MO_HOOK_PAYLOAD") -> Path:
    script = tmp_path / "hook_script.py"
    keys = env_keys.split("|")
    script.write_text(
        "import os, pathlib\n"
        f"value = '|'.join(os.environ.get(k, '') for k in {keys!r})\n"
        f"pathlib.Path({str(marker)!r}).write_text(value, encoding='utf-8')\n",
        encoding="utf-8",
    )
    return script


def test_dispatch_runs_command_with_event_env(tmp_path):
    marker = tmp_path / "marker.txt"
    script = _hook_script(tmp_path, marker)
    path = _write_hooks(tmp_path / "hooks.yaml", (
        "enabled: true\n"
        "hooks:\n"
        "  - event: turn_end\n"
        f"    run: '\"{sys.executable}\" \"{script}\"'\n"
    ))

    launched = dispatch_hooks("turn_end", {"status": "ok"}, path=path)

    assert launched == 1
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    content = marker.read_text(encoding="utf-8")
    assert content.startswith("turn_end|")
    assert "ok" in content


def test_dispatch_no_match_launches_nothing(tmp_path):
    path = _write_hooks(tmp_path / "hooks.yaml", (
        "enabled: true\nhooks:\n  - event: turn_end\n    run: echo hi\n"
    ))

    assert dispatch_hooks("provider_error", {}, path=path) == 0


def test_dispatch_suppressed_under_pytest_without_explicit_path():
    # No path argument -> production default path; pytest guard must refuse.
    assert dispatch_hooks("turn_end", {"status": "ok"}) == 0


def test_dispatch_payload_is_redacted(tmp_path):
    marker = tmp_path / "marker.txt"
    script = _hook_script(tmp_path, marker, env_keys="MO_HOOK_PAYLOAD")
    path = _write_hooks(tmp_path / "hooks.yaml", (
        "enabled: true\n"
        "hooks:\n"
        "  - event: '*'\n"
        f"    run: '\"{sys.executable}\" \"{script}\"'\n"
    ))

    launched = dispatch_hooks("tool_result", {"output": "api_key = sk-supersecret12345"}, path=path)

    assert launched == 1
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    content = marker.read_text(encoding="utf-8")
    assert "sk-supersecret12345" not in content


def test_monitor_emit_dispatches_hooks(tmp_path, monkeypatch):
    from core import backend_monitor as bm

    calls = []
    monkeypatch.setattr("core.hooks.dispatch_hooks", lambda event_type, payload, **kw: calls.append((event_type, payload)) or 0)
    monitor = bm.BackendMonitor(tmp_path / "monitor.jsonl")

    monitor.emit("backend_status", {"message": "hello"})

    assert calls and calls[0][0] == "backend_status"
