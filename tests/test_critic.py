from core.critic import AnswerCritic


def test_critic_allows_secret_placeholders_in_run_instructions():
    critic = AnswerCritic(path="does-not-exist.md")

    answer = """Run it like this:

```env
OPENCODE_API_KEY=your_key_here
AGENTROUTER_API_KEY=your_key_here
```
"""

    result = critic.review(answer)

    assert result.ok
    assert "answer held by critique" not in result.text
    assert "OPENCODE_API_KEY=your_key_here" in result.text


def test_critic_redacts_real_assignment_without_holding_whole_answer():
    critic = AnswerCritic(path="does-not-exist.md")

    result = critic.review("Use api_key=abc123456789 then run python mo.py")

    assert result.ok
    assert "answer held by critique" not in result.text
    assert "abc123456789" not in result.text
    assert "api_key=[redacted]" in result.text
    assert "run python mo.py" in result.text

    quoted = critic.review('Use api_key="abc123456789" then run python mo.py')
    assert 'api_key="[redacted]"' in quoted.text


def test_critic_redacts_bearer_token_without_holding_whole_answer():
    critic = AnswerCritic(path="does-not-exist.md")

    result = critic.review("Header: Authorization: Bearer abcdefghijklmnop")

    assert result.ok
    assert "abcdefghijklmnop" not in result.text
    assert "Bearer [redacted]" in result.text


def test_critic_redacts_private_key_block_without_holding_whole_answer():
    critic = AnswerCritic(path="does-not-exist.md")
    answer = "before\n-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----\nafter"

    result = critic.review(answer)

    assert result.ok
    assert "BEGIN PRIVATE KEY" not in result.text
    assert "[redacted private key]" in result.text
    assert result.text.startswith("before")
    assert result.text.endswith("after")


def test_critic_redacts_ssh_user_at_host_ip():
    critic = AnswerCritic(path="does-not-exist.md")
    result = critic.review("ssh ubuntu@203.0.113.77 to connect")
    assert result.ok
    assert "203.0.113.77" not in result.text
    assert "ubuntu@[redacted-host]" in result.text
    assert "ssh" in result.text
    assert "to connect" in result.text


def test_critic_redacts_ssh_user_at_hostname():
    critic = AnswerCritic(path="does-not-exist.md")
    result = critic.review("connect via admin@db.internal.example.com")
    assert result.ok
    assert "db.internal.example.com" not in result.text
    assert "admin@[redacted-host]" in result.text


def test_critic_redacts_auth_header():
    critic = AnswerCritic(path="does-not-exist.md")
    result = critic.review('Header: Authorization: Bearer abc123xyz456token')
    assert result.ok
    assert "abc123xyz456token" not in result.text
    assert "Bearer [redacted]" in result.text
    assert "Authorization" in result.text


def test_critic_redacts_bare_ipv4():
    critic = AnswerCritic(path="does-not-exist.md")
    result = critic.review("The server IP is 203.0.113.77 and port 22")
    assert result.ok
    assert "203.0.113.77" not in result.text
    assert "[redacted-ip]" in result.text
    assert "port 22" in result.text


def test_critic_warns_when_secret_redacted():
    critic = AnswerCritic(path="does-not-exist.md")
    result = critic.review("ssh ubuntu@10.0.0.1")
    assert result.ok
    assert "secret material redacted" in result.warnings


def test_critic_combined_redaction():
    """SSH + IP + auth all in one answer should all be redacted."""
    critic = AnswerCritic(path="does-not-exist.md")
    result = critic.review(
        "Connect via ubuntu@203.0.113.77 or use IP 203.0.113.77 directly. "
        "Auth: Authorization: Bearer secret-token-abc123."
    )
    assert result.ok
    assert "203.0.113.77" not in result.text
    assert "secret-token-abc123" not in result.text
    assert "[redacted-host]" in result.text
    assert "[redacted-ip]" in result.text
    assert "secret material redacted" in result.warnings
