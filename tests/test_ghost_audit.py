import json

from core.ghost import ghost_audit


def test_ghost_audit_redacts_and_writes_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "ghost_audit.jsonl"
    monkeypatch.setattr(ghost_audit, "LOG_PATH", path)
    monkeypatch.setenv("MO_GHOST_AUDIT_FORCE", "1")

    ghost_audit.append_ghost_audit(
        "reply",
        user_text="token=SECRET123",
        response_text="password=hunter2 handled",
        route="background",
    )

    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["event"] == "reply"
    assert event["route"] == "background"
    assert "SECRET123" not in event["user"]
    assert "hunter2" not in event["response"]
    assert "token=[redacted]" in event["user"]
    assert "password=[redacted]" in event["response"]
