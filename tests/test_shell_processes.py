"""Tests for core/shell_processes.py — persistent shell process tracking."""
import subprocess
from unittest.mock import Mock, patch

import pytest

from core.shell_processes import (
    _register_shell_process,
    _unregister_shell_process,
    _kill_process_tree,
    active_shell_processes,
    cleanup_shell_processes,
    _SHELL_PROCESSES,
    _SHELL_PROCESS_LOCK,
)


@pytest.fixture(autouse=True)
def clean_shell_registry():
    """Clear shell process registry before and after each test."""
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES.clear()
    yield
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES.clear()


class TestRegisterShellProcess:
    """Tests for _register_shell_process function."""

    def test_register_process_stores_metadata(self):
        """Test that registering a process stores all required metadata."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        command = "echo hello world"
        cwd = "/test/path"
        timeout = 30

        _register_shell_process(proc, command, cwd, timeout)

        with _SHELL_PROCESS_LOCK:
            assert 12345 in _SHELL_PROCESSES
            info = _SHELL_PROCESSES[12345]
            assert info["pid"] == 12345
            assert info["command"] == "echo hello world"
            assert info["cwd"] == "/test/path"
            assert info["timeout"] == 30
            assert info["proc"] is proc
            assert "started" in info
            assert isinstance(info["started"], float)

    def test_register_process_truncates_long_command(self):
        """Test that commands longer than 160 chars are truncated."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12346
        long_command = "echo " + "x" * 200

        _register_shell_process(proc, long_command, "/test", 30)

        with _SHELL_PROCESS_LOCK:
            info = _SHELL_PROCESSES[12346]
            assert len(info["command"]) == 160

    def test_register_process_normalizes_whitespace(self):
        """Test that command whitespace is normalized."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12347
        command = "echo    hello     world"

        _register_shell_process(proc, command, "/test", 30)

        with _SHELL_PROCESS_LOCK:
            info = _SHELL_PROCESSES[12347]
            assert info["command"] == "echo hello world"

    def test_register_process_handles_none_command(self):
        """Test that None command is handled gracefully."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12348

        _register_shell_process(proc, None, "/test", 30)

        with _SHELL_PROCESS_LOCK:
            info = _SHELL_PROCESSES[12348]
            assert info["command"] == ""

    def test_register_multiple_processes(self):
        """Test registering multiple processes."""
        for i in range(5):
            proc = Mock(spec=subprocess.Popen)
            proc.pid = 10000 + i
            _register_shell_process(proc, f"cmd {i}", "/test", 30)

        with _SHELL_PROCESS_LOCK:
            assert len(_SHELL_PROCESSES) == 5


class TestUnregisterShellProcess:
    """Tests for _unregister_shell_process function."""

    def test_unregister_existing_process(self):
        """Test unregistering an existing process."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        _register_shell_process(proc, "test", "/test", 30)

        _unregister_shell_process(12345)

        with _SHELL_PROCESS_LOCK:
            assert 12345 not in _SHELL_PROCESSES

    def test_unregister_nonexistent_process(self):
        """Test unregistering a non-existent process doesn't raise."""
        _unregister_shell_process(99999)  # Should not raise

    def test_unregister_preserves_other_processes(self):
        """Test that unregistering one process preserves others."""
        for i in range(3):
            proc = Mock(spec=subprocess.Popen)
            proc.pid = 10000 + i
            _register_shell_process(proc, f"cmd {i}", "/test", 30)

        _unregister_shell_process(10001)

        with _SHELL_PROCESS_LOCK:
            assert len(_SHELL_PROCESSES) == 2
            assert 10000 in _SHELL_PROCESSES
            assert 10002 in _SHELL_PROCESSES


class TestKillProcessTree:
    """Tests for _kill_process_tree function."""

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    def test_kill_process_tree_windows(self, mock_run):
        """Test killing process tree on Windows."""
        mock_run.return_value = Mock(returncode=0)

        result = _kill_process_tree(12345)

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["taskkill", "/F", "/T", "/PID", "12345"]

    @patch("core.shell_processes.sys.platform", "linux")
    @patch("core.shell_processes.os.killpg", create=True)
    @patch("core.shell_processes.os.getpgid", create=True)
    def test_kill_process_tree_unix(self, mock_getpgid, mock_killpg):
        """Test killing process tree on Unix."""
        mock_getpgid.return_value = 12345
        mock_killpg.return_value = None

        result = _kill_process_tree(12345)

        assert result is True
        mock_getpgid.assert_called_once_with(12345)
        mock_killpg.assert_called_once_with(12345, 15)

    @patch("core.shell_processes.sys.platform", "linux")
    @patch("core.shell_processes.os.killpg", side_effect=Exception("killpg failed"), create=True)
    @patch("core.shell_processes.os.getpgid", create=True)
    @patch("core.shell_processes.os.kill")
    def test_kill_process_tree_unix_fallback(self, mock_kill, mock_getpgid, mock_killpg):
        """Test fallback to os.kill when killpg fails."""
        mock_getpgid.return_value = 12345
        mock_kill.return_value = None

        result = _kill_process_tree(12345)

        assert result is True
        mock_kill.assert_called_once_with(12345, 15)

    @patch("core.shell_processes.sys.platform", "linux")
    @patch("core.shell_processes.os.killpg", side_effect=Exception("killpg failed"), create=True)
    @patch("core.shell_processes.os.getpgid", create=True)
    @patch("core.shell_processes.os.kill", side_effect=Exception("kill failed"))
    def test_kill_process_tree_both_fail(self, mock_kill, mock_getpgid, mock_killpg):
        """Test when both kill methods fail."""
        mock_getpgid.return_value = 12345

        result = _kill_process_tree(12345)

        assert result is False


class TestActiveShellProcesses:
    """Tests for active_shell_processes function."""

    def test_active_processes_returns_active_only(self):
        """Test that only active processes are returned."""
        # Register 3 processes
        for i in range(3):
            proc = Mock(spec=subprocess.Popen)
            proc.pid = 10000 + i
            proc.poll = Mock(return_value=None if i < 2 else 0)  # First 2 active, last finished
            _register_shell_process(proc, f"cmd {i}", "/test", 30)

        active = active_shell_processes()

        assert len(active) == 2
        pids = [p["pid"] for p in active]
        assert 10000 in pids
        assert 10001 in pids
        assert 10002 not in pids

    def test_active_processes_removes_stale(self):
        """Test that stale processes are removed from registry."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll = Mock(return_value=0)  # Process finished
        _register_shell_process(proc, "test", "/test", 30)

        active_shell_processes()

        with _SHELL_PROCESS_LOCK:
            assert 12345 not in _SHELL_PROCESSES

    def test_active_processes_excludes_proc_object(self):
        """Test that returned info doesn't include proc object."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll = Mock(return_value=None)
        _register_shell_process(proc, "test", "/test", 30)

        active = active_shell_processes()

        assert len(active) == 1
        assert "proc" not in active[0]
        assert "pid" in active[0]
        assert "command" in active[0]

    def test_active_processes_empty_registry(self):
        """Test with empty registry."""
        active = active_shell_processes()
        assert active == []


class TestCleanupShellProcesses:
    """Tests for cleanup_shell_processes function."""

    @patch("sys.platform", "win32")
    @patch("subprocess.run")
    def test_cleanup_windows(self, mock_run):
        """Test cleanup on Windows."""
        mock_run.return_value = Mock(returncode=0)
        
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll = Mock(return_value=None)
        _register_shell_process(proc, "test", "/test", 30)

        result = cleanup_shell_processes()

        assert result["killed"] == 1
        assert result["active_after"] == 0
        mock_run.assert_called_once()

    @patch("sys.platform", "linux")
    @patch("os.kill")
    def test_cleanup_unix(self, mock_kill):
        """Test cleanup on Unix."""
        mock_kill.return_value = None
        
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll = Mock(return_value=None)
        _register_shell_process(proc, "test", "/test", 30)

        result = cleanup_shell_processes()

        assert result["killed"] == 1
        assert result["active_after"] == 0
        mock_kill.assert_called_once_with(12345, 15)

    def test_cleanup_removes_from_registry(self):
        """Test that cleanup removes processes from registry."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll = Mock(return_value=None)
        _register_shell_process(proc, "test", "/test", 30)

        with patch("sys.platform", "linux"), patch("os.kill"):
            cleanup_shell_processes()

        with _SHELL_PROCESS_LOCK:
            assert 12345 not in _SHELL_PROCESSES

    def test_cleanup_handles_kill_failure(self):
        """Test that cleanup handles kill failures gracefully."""
        proc = Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.poll = Mock(return_value=None)
        _register_shell_process(proc, "test", "/test", 30)

        with patch("core.shell_processes.sys.platform", "linux"), patch("core.shell_processes.os.kill", side_effect=Exception("kill failed")):
            result = cleanup_shell_processes()

        # When kill fails, process is NOT added to killed list
        assert result["killed"] == 0
        # Process should still be removed from registry (happens before kill attempt)
        with _SHELL_PROCESS_LOCK:
            assert 12345 not in _SHELL_PROCESSES

    def test_cleanup_empty_registry(self):
        """Test cleanup with empty registry."""
        result = cleanup_shell_processes()
        assert result["killed"] == 0
        assert result["active_after"] == 0

    def test_cleanup_multiple_processes(self):
        """Test cleanup with multiple processes."""
        for i in range(3):
            proc = Mock(spec=subprocess.Popen)
            proc.pid = 10000 + i
            proc.poll = Mock(return_value=None)
            _register_shell_process(proc, f"cmd {i}", "/test", 30)

        with patch("sys.platform", "linux"), patch("os.kill"):
            result = cleanup_shell_processes()

        assert result["killed"] == 3
        assert result["active_after"] == 0
