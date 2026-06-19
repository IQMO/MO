"""Write-time secret guard: block hardcoded secret literals in file writes
without firing on legitimate code (env refs, placeholders, expression RHS).

Closes the gap where turn-end security_check only *reported* secrets in written
files; this stops the write at dispatch time via guard_tool_call.
"""
from core.text_safety import (
    contains_hardcoded_secret_literal,
    is_placeholder_secret_value,
)
from core.sandbox import guard_tool_call


# ── detector: precision is the point ────────────────────────────────

def test_detects_unambiguous_secret_literals():
    assert contains_hardcoded_secret_literal("token = 'sk-0123456789abcdef0123'")
    assert contains_hardcoded_secret_literal("ghp_0123456789abcdefABCD")           # GitHub
    assert contains_hardcoded_secret_literal("aws = AKIA0123456789ABCD")            # AWS key id
    assert contains_hardcoded_secret_literal("-----BEGIN RSA PRIVATE KEY-----\nMII...")
    # high-entropy quoted value assigned to a secret-named key (non-provider shape)
    assert contains_hardcoded_secret_literal('API_KEY = "a1b2c3d4e5f6g7h8i9j0k1"')
    assert contains_hardcoded_secret_literal('{"client_secret": "x9y8z7w6v5u4t3s2r1q0p9"}')


def test_does_not_block_legitimate_code():
    # env / config / expression references — the most common false-positive source
    assert not contains_hardcoded_secret_literal('api_key = os.environ["MY_KEY"]')
    assert not contains_hardcoded_secret_literal("password = input('pw: ')")
    assert not contains_hardcoded_secret_literal('token = resp.json()["token"]')
    assert not contains_hardcoded_secret_literal("secret = config.get('secret')")
    # placeholders
    assert not contains_hardcoded_secret_literal('API_KEY = "your-key-here"')
    assert not contains_hardcoded_secret_literal('password = "changeme"')
    assert not contains_hardcoded_secret_literal('API_KEY = "your_example_key_1234567"')  # high-entropy but placeholder words
    assert not contains_hardcoded_secret_literal('token = "<your-token>"')
    # short / non-secret values
    assert not contains_hardcoded_secret_literal('password = "test"')
    assert not contains_hardcoded_secret_literal("x = 1\ny = compute()\nreturn x + y")
    assert not contains_hardcoded_secret_literal("")


def test_placeholder_helper():
    assert is_placeholder_secret_value("your-api-key-here")
    assert is_placeholder_secret_value("os.environ['X']")
    assert is_placeholder_secret_value("<token>")
    assert is_placeholder_secret_value("changeme")
    assert is_placeholder_secret_value("")
    assert not is_placeholder_secret_value("a1b2c3d4e5f6g7h8i9j0")


# ── the gate ────────────────────────────────────────────────────────

_CFG = {"enabled": True, "block_write_secrets": True}
_ROOTS = ["."]


def _guard(name, arguments, **kw):
    return guard_tool_call(name, arguments, allowed_roots=_ROOTS, sandbox_config=_CFG, **kw)


def test_guard_blocks_write_file_with_secret():
    reason = _guard("write_file", {"path": "cfg.py", "content": 'API_KEY = "a1b2c3d4e5f6g7h8i9j0k1"'})
    assert reason and "hardcoded secret" in reason.lower()


def test_guard_blocks_edit_file_with_secret():
    reason = _guard("edit_file", {"path": "cfg.py", "old_text": "X", "new_text": "ghp_0123456789abcdefABCD"})
    assert reason and "hardcoded secret" in reason.lower()


def test_guard_allows_legit_write():
    assert _guard("write_file", {"path": "cfg.py", "content": 'api_key = os.environ["MY_KEY"]'}) is None


def test_guard_operator_override_bypasses():
    reason = _guard(
        "write_file", {"path": "cfg.py", "content": 'API_KEY = "a1b2c3d4e5f6g7h8i9j0k1"'},
        operator_override=True,
    )
    assert reason is None


def test_guard_disabled_by_config():
    cfg = {"enabled": True, "block_write_secrets": False}
    reason = guard_tool_call(
        "write_file", {"path": "cfg.py", "content": 'API_KEY = "a1b2c3d4e5f6g7h8i9j0k1"'},
        allowed_roots=_ROOTS, sandbox_config=cfg,
    )
    assert reason is None
