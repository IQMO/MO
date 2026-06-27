"""Regression tests for the 2026-06-18 full-project audit fixes.

Each test pins a concrete bug that was reproduced during the audit so it cannot
silently return. Grouped by subsystem; see commit message for the finding ids.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.critic import AnswerCritic
from core.review.diff_review import _extract_json_root
from core.sandbox import guard_tool_call, _guard_mcp_tool, _MCP_MUTATING_NAME_PATTERN
from core.session.session import Session
from core.tasking.task_board import TaskBoard


# ── Sandbox: shell variable/tilde path-scope (H3) ──────────────────────
class TestShellVarPathScope:
    ROOTS = ["/home/user/repo"]

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
    def test_born_completed_evidence_gated_row_coerced(self):
        # H2: an evidence-gated row that arrives "completed" with no evidence is
        # coerced to pending (model prose can't pre-close real work).
        b = TaskBoard()
        b.set_rows("w", [{"title": "edit", "kind": "edit", "status": "completed"}])
        assert b.tasks[0].status != "completed"

    def test_born_completed_evidence_gated_with_evidence_preserved(self):
        b = TaskBoard()
        b.set_rows("w", [{"title": "edit", "kind": "edit", "status": "completed", "evidence": ["edit_file:x.py"]}])
        assert b.tasks[0].status == "completed"

    def test_born_completed_non_evidence_row_preserved(self):
        # A row that requires no evidence (no kind/gate) keeps its normalized status.
        b = TaskBoard()
        b.set_rows("w", [{"id": "1", "title": "note", "status": "done"}])
        assert b.tasks[0].status == "completed"

    def test_closing_gate_audits_completions_across_rounds(self):
        # H1: direct completion now rejects evidence-free rows at the mutation
        # boundary, while the closing gate still catches corrupted/legacy state.
        from core.tasking.contract import enforce_contract_gate
        b = TaskBoard()
        b.set_rows("w", [
            {"id": "1", "title": "edit", "kind": "edit", "status": "active"},
            {"id": "2", "title": "report", "kind": "report", "status": "pending"},
        ])
        turn_initial = {t.id for t in b.tasks if t.status == "completed"}  # empty at turn start
        result = b.complete("1")
        assert result.ok is False
        assert result.reason == "missing_required_evidence"
        b.task("1").status = "completed"  # simulate persisted/corrupted old state
        b.complete("2", evidence="final: summary")  # later round closes the board
        just = {t.id for t in b.tasks if t.status == "completed"} - turn_initial
        ok, reasons, _ = enforce_contract_gate(b, persisted_tasks=None, board_closing=True, task_ids=just)
        assert not ok and any("missing_evidence:1" in r for r in reasons)


# ── Graph search: product code outranks test files (LOW) ──────────────
def test_search_deprioritizes_test_files(tmp_path):
    (tmp_path / "widget.py").write_text("def build_widget():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_widget.py").write_text(
        "def test_build_widget():\n    assert True\n", encoding="utf-8"
    )
    from core.graph.search import search
    res = search("build widget", cwd=tmp_path)
    assert res, "no search results"
    top_src = str(res[0].get("source_file") or res[0].get("id") or "")
    assert "test" not in top_src.rsplit("/", 1)[-1], f"test file ranked first: {top_src}"


# ── Session: created_at survives a /session switch (LOW) ──────────────
def test_session_created_at_roundtrips_through_switch(tmp_path):
    from core.session.session import Session
    from core.session.sessions import SessionManager

    mgr = SessionManager(str(tmp_path))  # current slot is "main"
    s = Session("SYS")
    s.created_at = 12345.0
    mgr.save_snapshot("slot_a", s)  # write slot_a without making it current

    other = Session("SYS")
    other.created_at = 99999.0
    mgr.switch("slot_a", other)  # from "main" → "slot_a"
    assert other.created_at == 12345.0


# ── Tasking: current.json written atomically, no stray tmp (LOW) ──────
def test_task_manager_save_is_atomic_and_roundtrips(tmp_path):
    from core.tasking.task_manager import TaskManager

    mgr = TaskManager(str(tmp_path))
    mgr.save({"board_id": "b1", "tasks": [{"id": "1", "title": "t", "status": "active"}], "state": "active"})
    reloaded = TaskManager(str(tmp_path))
    assert reloaded._data.get("board_id") == "b1"
    assert not list(tmp_path.glob("**/*.tmp")), "atomic write left a stray .tmp file"


# ── MCP: oversized-frame guard exists and is bounded (LOW) ────────────
def test_mcp_max_line_cap_present():
    from core.mcp.client import _MCP_MAX_LINE_BYTES
    assert 1_000_000 <= _MCP_MAX_LINE_BYTES <= 64 * 1024 * 1024


# ── Secret coverage parity across all 3 surfaces (SEC-1) ──────────────
class TestSecretCoverageParity:
    """The turn-end security check (text_safety) and tool/web/audit redactor
    (sandbox) must catch the same high-confidence secrets the answer-critic does,
    so a secret can't slip a weaker guard."""

    GAPS = [
        '{"api_key": "randomSecret123456"}',
        "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIxyz1234",
        "xoxb-fake-not-a-real-slack-token-000",  # matches our regex, not GitHub's validator
        "client_secret: s3cr3tvalue9876",
    ]

    def test_text_safety_detects(self):
        from core.text_safety import contains_secret_value
        for t in self.GAPS:
            assert contains_secret_value(t), t

    def test_sandbox_redacts(self):
        from core.sandbox import redact_sensitive_text
        for t in self.GAPS:
            assert redact_sensitive_text(t) != t, t

    def test_no_false_positives(self):
        from core.text_safety import contains_secret_value
        from core.sandbox import redact_sensitive_text
        for ok in ("the password field is required", "tokens and secrets in prose", "your_api_key_here"):
            assert not contains_secret_value(ok), ok
            assert redact_sensitive_text(ok) == ok, ok


# ── Session: reasoning_content not re-sent (M7) ───────────────────────
def test_reasoning_content_stripped_from_payload_but_stored():
    s = Session("SYS")
    s.add_user("hi")
    s.add_assistant("answer", reasoning_content="chain of thought " * 20)
    s.add_user("again")
    payload = s.get_messages(extra_context="DYN")
    assert not any("reasoning_content" in m for m in payload)
    assert any("reasoning_content" in m for m in s.messages)


def test_reasoning_content_can_be_preserved_for_deepseek_reasoning_mode():
    s = Session("SYS")
    s.add_user("hi")
    s.add_assistant("answer", reasoning_content="reasoning required by provider")

    payload = s.get_messages(include_reasoning_content=True)

    assert any(m.get("reasoning_content") == "reasoning required by provider" for m in payload if isinstance(m, dict))


def test_deepseek_agent_call_preserves_reasoning_content():
    from core.agent.agent import Agent

    seen: dict[str, object] = {}

    class _Provider:
        name = "opencode-pro"
        model = "deepseek-v4-pro"

        def complete(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(content="ok", tool_calls=[], usage=None, finish_reason="stop")

    agent = object.__new__(Agent)
    agent.session = Session("SYS")
    agent.session.add_user("hi")
    agent.session.add_assistant("answer", reasoning_content="reasoning required by provider")
    agent.providers = [_Provider()]
    agent.provider_index = 0
    agent.provider_name = "opencode-pro"
    agent.model = "deepseek-v4-pro"
    agent.tool_definitions = []
    agent.temperature = 0
    agent.max_tokens = 10

    agent._call_provider()

    messages = seen["messages"]
    assert any(m.get("reasoning_content") == "reasoning required by provider" for m in messages if isinstance(m, dict))
