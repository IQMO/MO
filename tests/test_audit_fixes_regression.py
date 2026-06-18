"""Regression tests for the 2026-06-18 full-project audit fixes.

Each test pins a concrete bug that was reproduced during the audit so it cannot
silently return. Grouped by subsystem; see commit message for the finding ids.
"""
from __future__ import annotations

from core.critic import AnswerCritic
from core.review.diff_review import _extract_json_root
from core.sandbox import guard_tool_call, _guard_mcp_tool, _MCP_MUTATING_NAME_PATTERN
from core.session.session import Session
from core.tasking.task_board import TaskBoard


# ── Sandbox: shell variable/tilde path-scope (H3) ──────────────────────
class TestShellVarPathScope:
    ROOTS = ["E:\\MO-clean"]

    def _blocked(self, cmd: str) -> bool:
        return bool(guard_tool_call("shell", {"command": cmd}, lane=None, allowed_roots=self.ROOTS))

    def test_env_var_and_tilde_paths_blocked(self):
        assert self._blocked(r"type %USERPROFILE%\notes.txt")
        assert self._blocked("cat $HOME/Documents/x")
        assert self._blocked("type ~/secret")
        assert self._blocked(r"cat $env:APPDATA\config.json")

    def test_plain_commands_and_cwd_vars_allowed(self):
        assert not self._blocked("python -m pytest -q")
        assert not self._blocked("git status")
        assert not self._blocked("echo $PWD/local")  # cwd var stays in-root


# ── Sandbox: MCP arg scoping + mutating names (H4, M1) ─────────────────
class TestMcpGuards:
    ROOTS = ["/home/user/repo"]

    def test_plural_nested_alternate_path_args_blocked(self):
        for args in (
            {"path": "/etc/passwd"},
            {"paths": ["/etc/passwd"]},
            {"destination": "/etc/cron.d/x"},
            {"source": "/etc/shadow"},
            {"options": {"path": "/etc/passwd"}},
        ):
            assert _guard_mcp_tool("mcp__fs__readFile", args, None, self.ROOTS), args

    def test_in_root_path_allowed(self):
        assert _guard_mcp_tool("mcp__fs__readFile", {"path": "/home/user/repo/a.txt"}, None, self.ROOTS) is None

    def test_camelcase_mutating_names_flagged(self):
        for name in ("mcp__fs__write_file", "mcp__fs__writeFile", "mcp__fs__deleteFile", "mcp__fs__dropTable"):
            assert _MCP_MUTATING_NAME_PATTERN.search(name), name

    def test_read_names_not_flagged(self):
        for name in ("mcp__fs__readFile", "mcp__fs__list_dir", "mcp__fs__getStatus"):
            assert not _MCP_MUTATING_NAME_PATTERN.search(name), name


# ── Critic: secret coverage (M2) ──────────────────────────────────────
class TestCriticCoverage:
    def setup_method(self):
        self.c = AnswerCritic()

    def test_quoted_json_keys_redacted(self):
        for t in ('{"api_key": "randomSecret123456"}', '{"client_secret": "s3cr3tValueXYZ123"}'):
            _, changed = self.c._redact_secret_material(t)
            assert changed, t

    def test_standalone_provider_tokens_redacted(self):
        for t in ("token ghp_abcdefgh1234567890ABCDijkl", "AKIAIOSFODNN7EXAMPLE12"):
            _, changed = self.c._redact_secret_material(t)
            assert changed, t

    def test_no_false_positive_on_placeholders(self):
        for t in ("your_api_key_here", "just a sentence with the word token"):
            _, changed = self.c._redact_secret_material(t)
            assert not changed, t


# ── PRT: robust JSON extraction (H5) ──────────────────────────────────
class TestDiffReviewJson:
    def test_object_form_parses(self):
        root = _extract_json_root('{"findings": [{"message": "x"}], "positives": ["good"]}')
        assert isinstance(root, dict)
        assert len(root["findings"]) == 1 and root["positives"] == ["good"]

    def test_array_and_fenced_forms_parse(self):
        assert isinstance(_extract_json_root('[{"message": "y"}]'), list)
        assert isinstance(_extract_json_root('```json\n{"findings": []}\n```'), dict)

    def test_garbage_returns_none(self):
        assert _extract_json_root("no json here") is None


# ── Taskboard: evidence gate (H1, H2) ─────────────────────────────────
class TestTaskboardEvidence:
    def test_born_completed_without_evidence_not_completed(self):
        b = TaskBoard()
        b.set_rows("w", [{"title": "edit", "kind": "edit", "status": "completed"}])
        assert b.tasks[0].status != "completed"

    def test_born_completed_with_evidence_preserved(self):
        b = TaskBoard()
        b.set_rows("w", [{"title": "edit", "kind": "edit", "status": "completed", "evidence": ["edit_file:x.py"]}])
        assert b.tasks[0].status == "completed"

    def test_complete_evidence_gated_row_without_evidence_blocks(self):
        b = TaskBoard()
        b.set_rows("w", [{"title": "edit", "kind": "edit", "status": "active"}])
        b.complete(b.tasks[0].id)
        assert b.tasks[0].status == "blocked"

    def test_complete_with_evidence_completes(self):
        b = TaskBoard()
        b.set_rows("w", [{"title": "edit", "kind": "edit", "status": "active"}])
        b.complete(b.tasks[0].id, evidence="edit_file:x.py")
        assert b.tasks[0].status == "completed"

    def test_report_row_completes_without_evidence(self):
        b = TaskBoard()
        b.set_rows("w", [{"title": "final", "kind": "report", "status": "active"}])
        b.complete(b.tasks[0].id)
        assert b.tasks[0].status == "completed"


# ── Session: reasoning_content not re-sent (M7) ───────────────────────
def test_reasoning_content_stripped_from_payload_but_stored():
    s = Session("SYS")
    s.add_user("hi")
    s.add_assistant("answer", reasoning_content="chain of thought " * 20)
    s.add_user("again")
    payload = s.get_messages(extra_context="DYN")
    assert not any("reasoning_content" in m for m in payload)
    assert any("reasoning_content" in m for m in s.messages)
