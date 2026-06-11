"""Tests for core/runtime_work_signals.py — shared runtime intent classifiers."""
from __future__ import annotations


from core.runtime_work_signals import (
    looks_like_interrupted_resume_request,
    normalized_operator_text,
    shell_is_verification_command,
    tool_is_runtime_work_signal,
    tool_is_verification_signal,
)


class TestNormalizedOperatorText:
    def test_lowercase_and_single_spaces(self):
        assert normalized_operator_text("  Hello   World  ") == "hello world"

    def test_empty_string(self):
        assert normalized_operator_text("") == ""

    def test_none_input(self):
        assert normalized_operator_text(None) == ""


class TestLooksLikeInterruptedResumeRequest:
    def test_continue_exact(self):
        assert looks_like_interrupted_resume_request("continue")

    def test_resume_exact(self):
        assert looks_like_interrupted_resume_request("resume")

    def test_carry_on_exact(self):
        assert looks_like_interrupted_resume_request("carry on")

    def test_proceed(self):
        assert looks_like_interrupted_resume_request("proceed")
        assert looks_like_interrupted_resume_request("yes proceed")
        assert looks_like_interrupted_resume_request("proceed with it")

    def test_continue_unfinished_work(self):
        assert looks_like_interrupted_resume_request("continue unfinished work")
        assert looks_like_interrupted_resume_request("resume previous work")
        assert looks_like_interrupted_resume_request("continue working on the unfinished")

    def test_focus_back(self):
        assert looks_like_interrupted_resume_request("focus again on work")
        assert looks_like_interrupted_resume_request("refocus on what was left")

    def test_finish_this(self):
        assert looks_like_interrupted_resume_request("finish this")
        assert looks_like_interrupted_resume_request("complete this work")
        assert looks_like_interrupted_resume_request("jump back to it")

    def test_keep_working(self):
        assert looks_like_interrupted_resume_request("keep working on this")
        assert looks_like_interrupted_resume_request("pick it back up")

    def test_declined_requests(self):
        assert not looks_like_interrupted_resume_request("don't continue")
        assert not looks_like_interrupted_resume_request("do not resume")
        assert not looks_like_interrupted_resume_request("stop working")
        assert not looks_like_interrupted_resume_request("leave it")
        assert not looks_like_interrupted_resume_request("cancel this")

    def test_plain_requests_not_resume(self):
        assert not looks_like_interrupted_resume_request("hello")
        assert not looks_like_interrupted_resume_request("fix the bug")
        assert not looks_like_interrupted_resume_request("")

    def test_contuine_typo(self):
        """Real-world typo from operator."""
        assert looks_like_interrupted_resume_request("contuine")


class TestShellIsVerificationCommand:
    def test_pytest_verification(self):
        assert shell_is_verification_command("python -m pytest -q")
        assert shell_is_verification_command("pytest tests/")

    def test_lint_verification(self):
        assert shell_is_verification_command("ruff check .")
        assert shell_is_verification_command("mypy core/")

    def test_go_test_verification(self):
        assert shell_is_verification_command("go test ./...")
        assert shell_is_verification_command("cargo test")

    def test_non_verification_commands(self):
        assert not shell_is_verification_command("echo hello")
        assert not shell_is_verification_command("git status")
        assert not shell_is_verification_command("rm -rf /")
        assert not shell_is_verification_command("")

    def test_case_insensitive(self):
        assert shell_is_verification_command("PYTEST -q")
        assert shell_is_verification_command("Pytest")


class TestToolIsVerificationSignal:
    def test_test_runner_is_verification(self):
        assert tool_is_verification_signal("test_runner")

    def test_shell_with_pytest_is_verification(self):
        assert tool_is_verification_signal("shell", {"command": "pytest -q"})

    def test_shell_with_non_test_is_not_verification(self):
        assert not tool_is_verification_signal("shell", {"command": "echo hello"})

    def test_read_file_is_not_verification(self):
        assert not tool_is_verification_signal("read_file")

    def test_write_file_is_not_verification(self):
        assert not tool_is_verification_signal("write_file")


class TestToolIsRuntimeWorkSignal:
    def test_write_file_is_work(self):
        assert tool_is_runtime_work_signal("write_file")

    def test_edit_file_is_work(self):
        assert tool_is_runtime_work_signal("edit_file")

    def test_test_runner_is_work(self):
        assert tool_is_runtime_work_signal("test_runner")

    def test_verification_shell_is_work(self):
        assert tool_is_runtime_work_signal("shell", {"command": "pytest -q"})

    def test_mutating_shell_is_work(self):
        """Mutating shell commands (write/create) are work signals."""
        # A shell command that creates/modifies files
        assert tool_is_runtime_work_signal("shell", {"command": "git commit -m x"})

    def test_read_only_tools_not_work(self):
        assert not tool_is_runtime_work_signal("read_file")
        assert not tool_is_runtime_work_signal("grep")
        assert not tool_is_runtime_work_signal("git_status")

    def test_non_mutating_shell_not_work(self):
        """Read-only shell commands are not work signals."""
        assert not tool_is_runtime_work_signal("shell", {"command": "git log"})
        assert not tool_is_runtime_work_signal("shell", {"command": "ls"})
