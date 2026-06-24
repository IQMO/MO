"""Redaction must scrub real secrets without corrupting code reads.

Regression guard: a code read of `token: str` was being mangled to
`token: [redacted]` on the session/compaction path, which made MO chase a
phantom `[redacted]` finding during an OWNER_INTERFACE_AUDIT run. The `name = value`
redactor must distinguish secret literals (quoted / high-entropy) from code
(type annotations, function calls, attribute access).
"""
from core.sandbox import redact_sensitive_text as R


def test_code_annotations_and_calls_survive_redaction():
    keep = [
        "def wrap_long_token(token: str, width: int) -> list[str]:",
        "secret = compute_value()",
        "api_key = settings.get_key()",
        "password: Optional[str]",
        "self.token: str = token",
        "access_token: bool = False",
    ]
    for code in keep:
        assert "[redacted]" not in R(code), f"redactor corrupted code: {code!r} -> {R(code)!r}"


def test_real_secrets_are_still_redacted():
    secrets = [
        'password = "hunter2value"',
        'api_key: "ghp_abcDEF1234567"',
        "token=ghp_abcdefghij1234567",
        "api_key=supersecret",          # bare unquoted secret in web/config text
        "password=p4ssw0rdValue99",
    ]
    for s in secrets:
        assert "[redacted]" in R(s), f"redactor missed a secret: {s!r} -> {R(s)!r}"
