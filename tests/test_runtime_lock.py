"""Tests for core/runtime_lock.py — process-level singleton lock."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import pytest

from core.runtime_lock import (
    RuntimeLock,
    acquire_runtime_lock,
    release_runtime_lock,
    _live_owner,
    _pid_alive,
    _register_cleanup,
)


@pytest.fixture
def temp_lock_dir():
    """Create temporary directory for lock files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("tempfile.gettempdir", return_value=tmpdir):
            yield tmpdir


class TestRuntimeLock:
    """Tests for RuntimeLock dataclass."""

    def test_runtime_lock_creation(self):
        """Test RuntimeLock creation."""
        path = Path("/tmp/test.lock")
        lock = RuntimeLock(path=path, pid=12345)
        
        assert lock.path == path
        assert lock.pid == 12345

    def test_runtime_lock_frozen(self):
        """Test that RuntimeLock is immutable."""
        lock = RuntimeLock(path=Path("/tmp/test.lock"), pid=12345)
        
        with pytest.raises(AttributeError):
            lock.pid = 99999


class TestAcquireRuntimeLock:
    """Tests for acquire_runtime_lock function."""

    def test_acquire_lock_success(self, temp_lock_dir):
        """Test successful lock acquisition."""
        lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
        
        assert lock is not None
        assert isinstance(lock, RuntimeLock)
        assert lock.pid == os.getpid()
        assert lock.path.exists()
        assert lock.path.read_text() == str(os.getpid())

    def test_acquire_lock_with_skip_env(self, temp_lock_dir):
        """Test lock acquisition with skip environment variable."""
        with patch.dict(os.environ, {"MO_SKIP_LOCK": "1"}):
            lock = acquire_runtime_lock(lock_name="test.lock", skip_env="MO_SKIP_LOCK")
            
            assert lock is not None
            assert lock.pid == os.getpid()

    def test_acquire_lock_detects_live_owner(self, temp_lock_dir):
        """Test that live owner is detected and lock is not acquired."""
        lock_path = Path(temp_lock_dir) / "test.lock"
        other_pid = os.getpid() + 1000
        
        # Create lock file with other PID
        lock_path.write_text(str(other_pid))
        
        with patch("core.runtime_lock._pid_alive", return_value=True):
            lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
            
            assert lock is None

    def test_acquire_lock_ignores_stale_lock(self, temp_lock_dir):
        """Test that stale locks are ignored."""
        lock_path = Path(temp_lock_dir) / "test.lock"
        other_pid = os.getpid() + 1000
        
        # Create lock file with other PID
        lock_path.write_text(str(other_pid))
        
        with patch("core.runtime_lock._pid_alive", return_value=False):
            lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
            
            assert lock is not None
            assert lock.pid == os.getpid()

    def test_release_lock_removes_current_process_lock(self, temp_lock_dir):
        lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())

        release_runtime_lock(lock)

        assert not lock.path.exists()

    def test_acquire_lock_checks_legacy_locks(self, temp_lock_dir):
        """Test that legacy lock files are checked."""
        legacy_path = Path(temp_lock_dir) / "legacy.lock"
        other_pid = os.getpid() + 1000
        
        legacy_path.write_text(str(other_pid))
        
        with patch("core.runtime_lock._pid_alive", return_value=True):
            lock = acquire_runtime_lock(
                lock_name="test.lock",
                legacy_lock_names=("legacy.lock",)
            )
            
            assert lock is None

    def test_acquire_lock_own_pid_ignored(self, temp_lock_dir):
        """Test that own PID in lock file is ignored."""
        lock_path = Path(temp_lock_dir) / "test.lock"
        lock_path.write_text(str(os.getpid()))
        
        lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
        
        assert lock is not None
        assert lock.pid == os.getpid()

    def test_acquire_lock_handles_file_errors(self, temp_lock_dir):
        """Test that file errors are handled gracefully."""
        with patch("pathlib.Path.write_text", side_effect=PermissionError("Permission denied")):
            # Should fail open (return lock anyway)
            lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
            
            assert lock is not None

    def test_acquire_lock_invalid_pid_ignored(self, temp_lock_dir):
        """Test that invalid PID (0 or negative) is ignored."""
        lock_path = Path(temp_lock_dir) / "test.lock"
        lock_path.write_text("0")
        
        lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
        
        assert lock is not None

    def test_acquire_lock_registers_cleanup(self, temp_lock_dir):
        """Test that cleanup is registered on successful acquisition."""
        with patch("atexit.register") as mock_register:
            lock = acquire_runtime_lock(lock_name="test.lock", legacy_lock_names=())
            
            assert lock is not None
            mock_register.assert_called_once()


class TestLiveOwner:
    """Tests for _live_owner function."""

    def test_live_owner_no_file(self, temp_lock_dir):
        """Test with non-existent lock file."""
        path = Path(temp_lock_dir) / "nonexistent.lock"
        
        result = _live_owner(path)
        
        assert result is None

    def test_live_owner_with_live_pid(self, temp_lock_dir):
        """Test with live PID in lock file."""
        path = Path(temp_lock_dir) / "test.lock"
        other_pid = os.getpid() + 1000
        path.write_text(str(other_pid))
        
        with patch("core.runtime_lock._pid_alive", return_value=True):
            result = _live_owner(path)
            
            assert result == other_pid

    def test_live_owner_with_dead_pid(self, temp_lock_dir):
        """Test with dead PID in lock file."""
        path = Path(temp_lock_dir) / "test.lock"
        other_pid = os.getpid() + 1000
        path.write_text(str(other_pid))
        
        with patch("core.runtime_lock._pid_alive", return_value=False):
            result = _live_owner(path)
            
            assert result is None

    def test_live_owner_with_own_pid(self, temp_lock_dir):
        """Test with own PID in lock file."""
        path = Path(temp_lock_dir) / "test.lock"
        path.write_text(str(os.getpid()))
        
        result = _live_owner(path)
        
        assert result is None

    def test_live_owner_with_invalid_pid(self, temp_lock_dir):
        """Test with invalid PID in lock file."""
        path = Path(temp_lock_dir) / "test.lock"
        path.write_text("not_a_number")
        
        result = _live_owner(path)
        
        assert result is None

    def test_live_owner_with_negative_pid(self, temp_lock_dir):
        """Test with negative PID in lock file."""
        path = Path(temp_lock_dir) / "test.lock"
        path.write_text("-123")
        
        result = _live_owner(path)
        
        assert result is None

    def test_live_owner_with_whitespace(self, temp_lock_dir):
        """Test with whitespace around PID."""
        path = Path(temp_lock_dir) / "test.lock"
        other_pid = os.getpid() + 1000
        path.write_text(f"  {other_pid}  \n")
        
        with patch("core.runtime_lock._pid_alive", return_value=True):
            result = _live_owner(path)
            
            assert result == other_pid


class TestPidAlive:
    """Tests for _pid_alive function."""

    @patch("sys.platform", "win32")
    def test_pid_alive_windows_alive(self):
        """Test PID alive check on Windows when process is alive."""
        mock_kernel32 = Mock()
        mock_handle = Mock()
        mock_kernel32.OpenProcess.return_value = mock_handle
        
        with patch("ctypes.windll.kernel32", mock_kernel32):
            result = _pid_alive(12345)
            
            assert result is True
            mock_kernel32.OpenProcess.assert_called_once_with(0x1000, False, 12345)
            mock_kernel32.CloseHandle.assert_called_once_with(mock_handle)

    @patch("sys.platform", "win32")
    def test_pid_alive_windows_dead(self):
        """Test PID alive check on Windows when process is dead."""
        mock_kernel32 = Mock()
        mock_kernel32.OpenProcess.return_value = None
        
        with patch("ctypes.windll.kernel32", mock_kernel32):
            result = _pid_alive(12345)
            
            assert result is False

    @patch("sys.platform", "linux")
    @patch("os.kill")
    def test_pid_alive_unix_alive(self, mock_kill):
        """Test PID alive check on Unix when process is alive."""
        mock_kill.return_value = None
        
        result = _pid_alive(12345)
        
        assert result is True
        mock_kill.assert_called_once_with(12345, 0)

    @patch("sys.platform", "linux")
    @patch("os.kill", side_effect=OSError("No such process"))
    def test_pid_alive_unix_dead(self, mock_kill):
        """Test PID alive check on Unix when process is dead."""
        result = _pid_alive(12345)
        
        assert result is False

    @patch("sys.platform", "linux")
    @patch("os.kill", side_effect=PermissionError("Permission denied"))
    def test_pid_alive_unix_permission_error(self, mock_kill):
        """Test PID alive check with permission error (process exists but no permission)."""
        result = _pid_alive(12345)
        
        assert result is False


class TestRegisterCleanup:
    """Tests for _register_cleanup function."""

    def test_register_cleanup_registers_function(self):
        """Test that cleanup function is registered with atexit."""
        path = Path("/tmp/test.lock")
        
        with patch("atexit.register") as mock_register:
            _register_cleanup(path)
            
            mock_register.assert_called_once()
            # The registered function should be a closure
            registered_func = mock_register.call_args[0][0]
            assert callable(registered_func)

    def test_cleanup_removes_lock_file(self, temp_lock_dir):
        """Test that cleanup removes lock file if PID matches."""
        path = Path(temp_lock_dir) / "test.lock"
        path.write_text(str(os.getpid()))
        
        with patch("atexit.register") as mock_register:
            _register_cleanup(path)
            
            # Get the registered cleanup function
            cleanup_func = mock_register.call_args[0][0]
            
            # Call the cleanup function
            cleanup_func()
            
            # Lock file should be removed
            assert not path.exists()

    def test_cleanup_preserves_other_pid_lock(self, temp_lock_dir):
        """Test that cleanup doesn't remove lock file if PID doesn't match."""
        path = Path(temp_lock_dir) / "test.lock"
        other_pid = os.getpid() + 1000
        path.write_text(str(other_pid))
        
        with patch("atexit.register") as mock_register:
            _register_cleanup(path)
            
            cleanup_func = mock_register.call_args[0][0]
            cleanup_func()
            
            # Lock file should still exist
            assert path.exists()
            assert path.read_text() == str(other_pid)

    def test_cleanup_handles_file_errors(self, temp_lock_dir):
        """Test that cleanup handles file errors gracefully."""
        path = Path(temp_lock_dir) / "test.lock"
        path.write_text(str(os.getpid()))
        
        with patch("atexit.register") as mock_register:
            _register_cleanup(path)
            
            cleanup_func = mock_register.call_args[0][0]
            
            # Make file unreadable
            with patch("pathlib.Path.read_text", side_effect=PermissionError("Permission denied")):
                # Should not raise
                cleanup_func()
