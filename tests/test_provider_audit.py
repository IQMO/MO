import json

from core.provider import provider_audit


def test_provider_audit_redacts_and_writes_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "provider_audit.jsonl"
    monkeypatch.setattr(provider_audit, "LOG_PATH", path)
    monkeypatch.setenv("MO_PROVIDER_AUDIT_FORCE", "1")

    provider_audit.append_provider_audit(
        "provider_fallback",
        surface="goal",
        provider="openai-codex",
        model="gpt-5.5",
        request=2,
        session_id="mo-test",
        worker_id="w-1",
        reason="token sk-SECRET-123 failed",
        from_provider="opencode",
        from_model="deepseek-v4-pro",
        to_provider="openai-codex",
        to_model="gpt-5.5",
        input_tokens=10,
        output_tokens=3,
        total_tokens=13,
        ok=True,
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["event"] == "provider_fallback"
    assert row["surface"] == "goal"
    assert row["from_provider"] == "opencode"
    assert row["to_model"] == "gpt-5.5"
    assert row["total_tokens"] == 13
    assert "SECRET" not in row["reason"]


def test_provider_audit_prunes_large_log_to_recent_safe_lines(tmp_path, monkeypatch):
    path = tmp_path / "provider_audit.jsonl"
    monkeypatch.setattr(provider_audit, "LOG_PATH", path)
    monkeypatch.setenv("MO_PROVIDER_AUDIT_FORCE", "1")
    monkeypatch.setenv("MO_PROVIDER_AUDIT_MAX_BYTES", "420")
    monkeypatch.setenv("MO_PROVIDER_AUDIT_KEEP_LINES", "3")
    path.write_text(
        "\n".join(json.dumps({"event": f"old-{index}", "padding": "x" * 120}) for index in range(8)) + "\n",
        encoding="utf-8",
    )

    provider_audit.append_provider_audit("provider_response", provider="opencode", model="deepseek-v4-pro", ok=True)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) <= 3
    assert rows[-1]["event"] == "provider_response"
    assert "old-0" not in {row.get("event") for row in rows}
    assert path.stat().st_size <= 420


def test_context_handoff_provider_audit_is_labeled_orientation(tmp_path, monkeypatch):
    path = tmp_path / "provider_audit.jsonl"
    monkeypatch.setattr(provider_audit, "LOG_PATH", path)
    monkeypatch.setenv("MO_PROVIDER_AUDIT_FORCE", "1")

    provider_audit.append_provider_audit("context_handoff", provider="opencode", ok=True)

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["event"] == "context_handoff"
    assert row["text"] == "Context handoff audit record is orientation only, not proof."
