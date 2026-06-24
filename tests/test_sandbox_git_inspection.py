"""Sandbox: read-only git inspection of tracked SOURCE pathspecs is allowed even when the
filename contains a credential keyword (e.g. core/secrets.py — the live 2026-06-24T0404
false block), while inspection of actual secret-bearing paths (.env/.pem/.key/.ssh/
credentials) stays blocked. Direct guard_tool_call regressions."""
import os

from core.sandbox import guard_tool_call

ROOTS = [os.getcwd()]


def _shell(cmd):
    return guard_tool_call("shell", {"command": cmd}, lane=None, allowed_roots=ROOTS)


# ── allowed: read-only git inspection of source files (even credential-keyword names) ──
def test_git_diff_stat_of_secrets_module_is_allowed():
    # The exact live false-positive: a read-only --stat diff naming a source module
    # whose filename contains "secrets".
    assert _shell("git diff 2c65f36e..HEAD -- core/sandbox.py core/secrets.py --stat") is None


def test_git_show_and_log_of_secret_named_source_allowed():
    assert _shell("git show HEAD:core/secrets.py") is None
    assert _shell("git log -p -- core/secrets.py") is None
    # a source helper whose name embeds "credentials" is still source, not a secret file
    assert _shell("git blame core/credentials_helper.py") is None


def test_plain_git_inspection_unaffected():
    assert _shell("git diff --stat") is None
    assert _shell("git log --oneline -5") is None
    assert _shell("git show HEAD:core/final_gates.py") is None


# ── still blocked: secret-bearing pathspecs ──
def test_git_show_of_env_file_blocked():
    r = _shell("git show HEAD:.env")
    assert r and "secret-bearing" in r


def test_git_inspection_of_key_pem_blocked():
    assert _shell("git diff -- config/server.key")
    assert _shell("git show HEAD:certs/tls.pem")


def test_git_inspection_of_ssh_and_credentials_blocked():
    assert _shell("git log -p -- .ssh/id_rsa")
    assert _shell("git diff -- config/credentials.json")


# ── unchanged: masking is scoped to read-only git; other guards keep firing ──
def test_non_git_hard_boundary_still_blocks():
    assert _shell("deploy to production now")  # boundary still active for non-git


def test_git_push_still_blocked():
    # `push` is not a read-only subcommand → not masked → "push to origin" boundary holds.
    assert _shell("git push to origin main")


def test_compound_command_other_segment_still_guarded():
    # masking only neutralizes the git source pathspec; a deploy phrase in another
    # segment is still caught.
    assert _shell("git diff core/secrets.py && deploy to production")
