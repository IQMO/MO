import json

from core.agent.agent import Agent


def test_tool_audit_redacts_sensitive_arguments_and_does_not_store_file_content(tmp_path):
    agent = object.__new__(Agent)
    audit_path = tmp_path / "tool_audit.jsonl"
    agent.sandbox_config = {"audit_log": str(audit_path)}

    secret_content = "token=sk-SHOULD-NOT-BE-HERE"
    with Agent.provider_scope(agent, "goal", worker_id="w-test"):
        Agent._write_tool_audit(
            agent,
            "write_file",
            {"path": "secret.txt", "content": secret_content},
            "Wrote 1 bytes",
            None,
        )
    Agent._write_tool_audit(
        agent,
        "web_fetch",
        {"url": "https://example.com", "headers": '{"Authorization":"Bearer abc123SECRET"}'},
        "[HTTP 200]",
        None,
    )

    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["surface"] == "goal"
    assert rows[0]["worker_id"] == "w-test"
    assert rows[0]["arguments"] == {"path": "secret.txt", "content_chars": len(secret_content)}
    assert "[redacted]" in rows[1]["arguments"]["headers"]
    raw = audit_path.read_text(encoding="utf-8")
    assert "SHOULD-NOT-BE-HERE" not in raw
    assert "abc123SECRET" not in raw
