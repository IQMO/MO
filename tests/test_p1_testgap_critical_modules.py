"""P1-TESTGAP: Dedicated tests for previously-untested critical modules.

Covers:
- agent_turn.py static methods (the most testable surface)
- Smoke imports for agent_slash, agent_prt, goal_auditor, service
"""
import pytest


# ── agent_turn static methods ──────────────────────────────────────────────

class TestAgentTurnRawToolPayload:
    """Tests for _looks_like_raw_tool_payload."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_empty_string(self):
        assert self.cls._looks_like_raw_tool_payload("") is False

    def test_none_input(self):
        assert self.cls._looks_like_raw_tool_payload(None) is False

    def test_normal_text(self):
        assert self.cls._looks_like_raw_tool_payload("Hello, here is the answer.") is False

    def test_tool_calls_requested_marker(self):
        assert self.cls._looks_like_raw_tool_payload("Some text [tool calls requested] more") is True

    def test_edit_file_call(self):
        assert self.cls._looks_like_raw_tool_payload('I will edit_file("path", ...) now') is True

    def test_write_file_call(self):
        assert self.cls._looks_like_raw_tool_payload("Let me write_file(...) this") is True

    def test_read_file_call(self):
        assert self.cls._looks_like_raw_tool_payload("read_file('x') shows...") is True

    def test_test_runner_call(self):
        assert self.cls._looks_like_raw_tool_payload("test_runner('cmd') output") is True

    def test_project_bridge_call(self):
        assert self.cls._looks_like_raw_tool_payload("project_bridge() result") is True

    def test_tool_json_pattern_path(self):
        assert self.cls._looks_like_raw_tool_payload('{"path": "/tmp/x"}') is True

    def test_tool_json_pattern_old_new_text(self):
        assert self.cls._looks_like_raw_tool_payload('"old_text": "a", "new_text": "b"') is True

    def test_case_insensitive(self):
        assert self.cls._looks_like_raw_tool_payload("EDIT_FILE(x)") is True


class TestAgentTurnRetryMessage:
    def test_returns_string(self):
        from core.agent.agent_turn import AgentTurn
        msg = AgentTurn._raw_tool_payload_retry_message()
        assert isinstance(msg, str)
        assert len(msg) > 20
        assert "TOOL PAYLOAD RETRY" in msg


class TestAgentTurnSafeToolSummary:
    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_read_file_path(self):
        result = self.cls._safe_tool_summary("read_file", {"path": "/some/long/path/here.txt"})
        assert "/some/long/path/here.txt" in result

    def test_write_file_path(self):
        result = self.cls._safe_tool_summary("write_file", {"path": "/tmp/out.txt"})
        assert "/tmp/out.txt" in result

    def test_edit_file_path(self):
        result = self.cls._safe_tool_summary("edit_file", {"path": "core/x.py"})
        assert "core/x.py" in result

    def test_grep_root(self):
        result = self.cls._safe_tool_summary("grep", {"root": "/project/src"})
        assert "/project/src" in result

    def test_shell_command(self):
        result = self.cls._safe_tool_summary("shell", {"command": "echo hello"})
        assert "echo hello" in result

    def test_unknown_tool(self):
        result = self.cls._safe_tool_summary("unknown_tool", {"x": "y"})
        assert result == ""

    def test_truncates_to_240_chars(self):
        path = "x" * 300
        result = self.cls._safe_tool_summary("read_file", {"path": path})
        assert len(result) == 240

    def test_find_files_falls_back_to_pattern(self):
        result = self.cls._safe_tool_summary("find_files", {"root": "/a", "path": "/b", "pattern": "*.py"})
        assert "/a" in result


class TestAgentTurnToolResultIsError:
    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_starts_with_error(self):
        assert self.cls._tool_result_is_error("Error: something failed") is True

    def test_path_blocked(self):
        assert self.cls._tool_result_is_error("[path blocked] cannot access") is True

    def test_shell_blocked(self):
        assert self.cls._tool_result_is_error("[shell blocked] denied") is True

    def test_nonzero_exit_code(self):
        assert self.cls._tool_result_is_error("output\n[exit code 1]") is True

    def test_zero_exit_code(self):
        assert self.cls._tool_result_is_error("output\n[exit code 0]") is False

    def test_no_markers(self):
        assert self.cls._tool_result_is_error("normal output here") is False

    def test_negative_exit_code(self):
        """Negative exit codes are non-zero, thus errors."""
        assert self.cls._tool_result_is_error("[exit code -1]") is True

    def test_case_insensitive_error(self):
        assert self.cls._tool_result_is_error("ERROR: failed") is True


class TestAgentTurnSafeInt:
    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_positive_int(self):
        assert self.cls._safe_int(42) == 42

    def test_zero(self):
        assert self.cls._safe_int(0) == 0

    def test_negative_clamped(self):
        assert self.cls._safe_int(-5) == 0

    def test_none(self):
        assert self.cls._safe_int(None) == 0

    def test_string_number(self):
        assert self.cls._safe_int("123") == 123

    def test_invalid_string(self):
        assert self.cls._safe_int("abc") == 0

    def test_float_truncated(self):
        assert self.cls._safe_int(3.7) == 3


class TestAgentTurnToolCallArgumentBlockReason:
    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_length_finish_reason(self):
        result = self.cls._tool_call_argument_block_reason([], "length")
        assert "TRUNCATED" in result

    def test_empty_no_reason(self):
        result = self.cls._tool_call_argument_block_reason([], "stop")
        assert result == ""

    def test_invalid_json_arguments(self):
        tc = [{"function": {"name": "edit_file", "arguments": "not json"}}]
        result = self.cls._tool_call_argument_block_reason(tc, "stop")
        assert "edit_file" in result
        assert "INVALID" in result

    def test_valid_json_dict_returns_empty(self):
        tc = [{"function": {"name": "read_file", "arguments": '{"path":"x"}'}}]
        result = self.cls._tool_call_argument_block_reason(tc, "stop")
        assert result == ""

    def test_parsed_non_dict_arguments(self):
        tc = [{"function": {"name": "shell", "arguments": "[1,2,3]"}}]
        result = self.cls._tool_call_argument_block_reason(tc, "stop")
        assert "shell" in result
        assert "INVALID" in result


class TestAgentTurnParsedToolArguments:
    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_valid_dict(self):
        result = self.cls._parsed_tool_arguments(
            {"function": {"arguments": '{"path": "/tmp/test.txt"}'}}
        )
        assert result == {"path": "/tmp/test.txt"}

    def test_invalid_json_raises(self):
        import json as _json
        with pytest.raises(_json.JSONDecodeError):
            self.cls._parsed_tool_arguments(
                {"function": {"arguments": "not json{"}}
            )

    def test_parsed_non_dict_returns_empty(self):
        result = self.cls._parsed_tool_arguments(
            {"function": {"arguments": "[1, 2, 3]"}}
        )
        assert result == {}

    def test_missing_function_key(self):
        result = self.cls._parsed_tool_arguments({})
        assert result == {}

    def test_missing_arguments_key(self):
        result = self.cls._parsed_tool_arguments({"function": {}})
        assert result == {}


class TestAgentTurnExtractWorkflow:
    @pytest.fixture(autouse=True)
    def _import(self):
        from core.agent.agent_turn import AgentTurn
        self.cls = AgentTurn

    def test_extract_inline_with_colon(self):
        result = self.cls._extract_inline_workflow_source("key: some workflow text here enough long")
        assert "some workflow text" in result

    def test_extract_inline_no_colon(self):
        result = self.cls._extract_inline_workflow_source("no colon here")
        assert result == ""

    def test_extract_inline_too_short(self):
        result = self.cls._extract_inline_workflow_source("x: short")
        assert result == ""


# ── Smoke imports for other gap modules ─────────────────────────────────────

class TestAgentSlashSmoke:
    def test_import(self):
        import core.agent.agent_slash
        assert hasattr(core.agent.agent_slash, 'AgentSlashCommands')

    def test_class_exists(self):
        from core.agent.agent_slash import AgentSlashCommands
        assert AgentSlashCommands is not None


class TestAgentPrtSmoke:
    def test_import(self):
        import core.agent.agent_prt
        assert core.agent.agent_prt is not None

    def test_class_exists(self):
        from core.agent.agent_prt import AgentPRT
        assert AgentPRT is not None


class TestGoalAuditorSmoke:
    def test_import(self):
        import core.goal.goal_auditor
        assert core.goal.goal_auditor is not None


class TestServiceSmoke:
    def test_import(self):
        import core.service
        assert core.service is not None
