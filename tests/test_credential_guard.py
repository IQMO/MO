"""Credential-read guard: drift must not read an unrelated project's credentials,
even in full-access mode (cross-project drift incident). Operator may still direct a path."""
import os
import tempfile

from core.sandbox import guard_tool_call

OUTSIDE = os.path.join(tempfile.gettempdir(), "some-other-project")


def _read(path, *, override=False, roots=None):
    return guard_tool_call("read_file", {"path": path}, lane=None,
                           allowed_roots=roots, sandbox_config={"enabled": True},
                           operator_override=override)


def test_blocks_outside_env_in_full_mode():
    # full mode = empty allowed_roots; guard must still fire
    reason = _read(os.path.join(OUTSIDE, ".env"))
    assert reason and "CREDENTIAL BLOCKED" in reason


def test_blocks_outside_credentials_txt():
    reason = _read(os.path.join(OUTSIDE, "PROJECT_CREDENTIALS.txt"))
    assert reason and "CREDENTIAL BLOCKED" in reason


def test_blocks_outside_key_and_pem():
    assert _read(os.path.join(OUTSIDE, "id_rsa.pem"))
    assert _read(os.path.join(OUTSIDE, "server.key"))


def test_allows_workspace_env():
    # the project's own .env (under cwd) stays readable
    reason = _read(os.path.join(os.getcwd(), ".env"))
    assert reason is None


def test_allows_profile_home_env():
    reason = _read(os.path.join(os.path.expanduser("~"), ".mo", ".env"))
    assert reason is None


def test_operator_override_allows_explicit_path():
    # operator explicitly directed this exact path -> allowed
    reason = _read(os.path.join(OUTSIDE, ".env"), override=True)
    assert reason is None


def test_does_not_block_normal_outside_file():
    # non-credential reads in full mode are unchanged (no over-blocking)
    reason = _read(os.path.join(OUTSIDE, "README.md"))
    assert reason is None
