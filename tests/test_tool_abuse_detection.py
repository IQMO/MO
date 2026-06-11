"""Tests for AgentTurn._detect_tool_abuse — redundant tool-call warnings."""

from core.agent.agent import Agent


def _agent():
    return object.__new__(Agent)


def test_first_and_second_read_are_silent():
    agent = _agent()
    assert agent._detect_tool_abuse("read_file", {"path": "core/a.py"}) == ""
    assert agent._detect_tool_abuse("read_file", {"path": "core/a.py"}) == ""


def test_third_consecutive_read_warns():
    agent = _agent()
    agent._detect_tool_abuse("read_file", {"path": "core/a.py"})
    agent._detect_tool_abuse("read_file", {"path": "core/a.py"})
    warning = agent._detect_tool_abuse("read_file", {"path": "core/a.py"})
    assert "[TOOL USE NOTICE]" in warning
    assert "core/a.py" in warning


def test_interleaved_reads_of_different_files_are_silent():
    agent = _agent()
    assert agent._detect_tool_abuse("read_file", {"path": "core/a.py"}) == ""
    assert agent._detect_tool_abuse("read_file", {"path": "core/b.py"}) == ""
    assert agent._detect_tool_abuse("read_file", {"path": "core/a.py"}) == ""
    assert agent._detect_tool_abuse("read_file", {"path": "core/b.py"}) == ""


def test_fourth_nonconsecutive_read_of_same_file_warns():
    agent = _agent()
    for _ in range(3):
        agent._detect_tool_abuse("read_file", {"path": "core/a.py"})
        agent._detect_tool_abuse("read_file", {"path": "core/b.py"})
    warning = agent._detect_tool_abuse("read_file", {"path": "core/a.py"})
    assert "[TOOL USE NOTICE]" in warning


def test_repeated_shell_command_warns_once():
    agent = _agent()
    assert agent._detect_tool_abuse("shell", {"command": "git status"}) == ""
    assert agent._detect_tool_abuse("shell", {"command": "git status"}) == ""
    warning = agent._detect_tool_abuse("shell", {"command": "git status"})
    assert "[TOOL USE NOTICE]" in warning
    # Deduped: same repeated command does not warn again
    assert agent._detect_tool_abuse("shell", {"command": "git status"}) == ""


def test_trivial_shell_python_print_warns():
    agent = _agent()
    warning = agent._detect_tool_abuse("shell", {"command": 'python -c "print(open(\'x\').read())"'})
    assert "[TOOL USE NOTICE]" in warning
    assert "read_file" in warning


def test_repeated_grep_pattern_warns_once():
    agent = _agent()
    assert agent._detect_tool_abuse("grep", {"pattern": "TODO"}) == ""
    assert agent._detect_tool_abuse("grep", {"pattern": "TODO"}) == ""
    warning = agent._detect_tool_abuse("grep", {"pattern": "TODO"})
    assert "[TOOL USE NOTICE]" in warning
    assert agent._detect_tool_abuse("grep", {"pattern": "TODO"}) == ""


def test_history_is_capped():
    agent = _agent()
    for i in range(100):
        agent._detect_tool_abuse("read_file", {"path": f"core/file_{i}.py"})
    assert len(agent._tool_history) <= 80
