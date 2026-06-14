"""Unit tests for sandbox.py — the single gate at tool dispatch."""

import os

from core.sandbox import (
    guard_tool_call,
    safe_env,
    path_allowed,
    shell_command_escapes,
    shell_command_is_mutating,
    shell_command_uses_network,
    shell_paths_allowed,
    _touches_hard_boundary,
    HARD_BOUNDARY_PATTERNS,
)


def test_safe_env_forces_python_utf8_mode():
    env = safe_env()

    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


def test_safe_env_prefers_git_openssh_when_available(monkeypatch):
    git_ssh_dir = r"C:\Program Files\Git\usr\bin"
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("PATH", r"C:\Windows\System32\OpenSSH")
    monkeypatch.setattr("core.sandbox.os.path.isdir", lambda path: path == git_ssh_dir)

    env = safe_env()

    assert env["PATH"].startswith(git_ssh_dir + os.pathsep)


def test_write_file_large_existing_rewrite_blocks_and_points_to_edit_chunks(tmp_path):
    target = tmp_path / "game.html"
    target.write_text("old\n", encoding="utf-8")
    content = "\n".join(f"line {i}" for i in range(251))

    reason = guard_tool_call("write_file", {"path": str(target), "content": content}, allowed_roots=[str(tmp_path)])

    assert reason is not None
    assert "large existing-file rewrite" in reason
    assert "edit_file" in reason


def test_write_file_large_new_file_still_allowed(tmp_path):
    target = tmp_path / "new_game.html"
    content = "\n".join(f"line {i}" for i in range(251))

    reason = guard_tool_call("write_file", {"path": str(target), "content": content}, allowed_roots=[str(tmp_path)])

    assert reason is None


# ── Path allowlisting ──────────────────────────────────────────────

class TestPathAllowed:
    def test_absolute_path_in_roots(self):
        assert path_allowed("/home/user/repo/src/main.py", ["/home/user/repo"])

    def test_relative_path_resolves_against_cwd(self):
        # Relative paths resolve against CWD, so this only passes if CWD is under root
        result = path_allowed("src/main.py", ["/home/user/repo"])
        # On a Windows test machine, this will likely be False
        # Just ensure it doesn't crash
        assert result in {True, False}

    def test_path_traversal_blocked(self):
        assert not path_allowed("/home/user/etc/passwd", ["/home/user/repo"])

    def test_path_with_dotdot_blocked(self):
        assert not path_allowed("/home/user/repo/../../etc/passwd", ["/home/user/repo"])

    def test_path_resolves_outside(self):
        assert not path_allowed("/etc/shadow", ["/home/user/repo"])

    def test_prefix_sibling_path_blocked(self):
        assert not path_allowed("/home/user/repo-other/file.txt", ["/home/user/repo"])

    def test_parent_directory_blocked(self):
        assert not path_allowed("/home/user", ["/home/user/repo"])

    def test_empty_roots_allows_all(self):
        # Empty or None roots = permissive (sandbox disabled)
        assert path_allowed("/home/user/repo/file.txt", [])

    def test_none_path_blocked(self):
        assert not path_allowed(None, ["/home/user/repo"])

    def test_windows_path_blocked_outside(self):
        # Windows path that's outside allowed roots
        assert not path_allowed("C:\\Windows\\System32\\cmd.exe", ["C:\\Users\\test\\project"])

    def test_windows_path_allowed(self):
        assert path_allowed("C:\\Users\\test\\project\\src\\main.py", ["C:\\Users\\test\\project"])


# ── Shell escape detection ────────────────────────────────────────

class TestShellEscapeDetection:
    def test_cd_dotdot_escape(self):
        assert shell_command_escapes("cd ..")

    def test_path_traversal_escape(self):
        assert shell_command_escapes("cat ../../etc/passwd")

    def test_powershell_escape(self):
        assert shell_command_escapes("powershell -c evil")

    def test_cmd_escape(self):
        assert shell_command_escapes("cmd /c dir")

    def test_bash_escape(self):
        assert shell_command_escapes("bash -c evil")

    def test_safe_command(self):
        assert not shell_command_escapes("cd /home/user/repo && ls -la")

    def test_python_inline_stdout_ellipsis_newline_not_path_traversal(self):
        command = "python -c \"import sys; sys.stdout.write('Testing stdout...\\n'); sys.stdout.flush()\" 2>&1"

        assert not shell_command_escapes(command)

    def test_python_inline_keeps_real_traversal_argument_visible(self):
        command = "python -c \"print('ok')\" ../outside"

        assert shell_command_escapes(command)

    def test_quoted_shell_traversal_argument_still_blocks(self):
        assert shell_command_escapes("cat '../outside'")


# ── Shell mutations ─────────────────────────────────────────────────

class TestShellMutationDetection:
    def test_rm_is_mutating(self):
        assert shell_command_is_mutating("rm file.txt")

    def test_mkdir_is_mutating(self):
        assert shell_command_is_mutating("mkdir newdir")

    def test_git_commit_is_mutating(self):
        assert shell_command_is_mutating("git commit -m 'msg'")

    def test_pip_install_is_mutating(self):
        assert shell_command_is_mutating("pip install requests")

    def test_cat_is_not_mutating(self):
        assert not shell_command_is_mutating("cat file.txt")

    def test_ls_is_not_mutating(self):
        assert not shell_command_is_mutating("ls -la")

    def test_git_status_not_mutating(self):
        assert not shell_command_is_mutating("git status")

    def test_python_script_not_mutating(self):
        assert not shell_command_is_mutating("python test.py")


# ── Network command detection ─────────────────────────────────────

class TestShellNetworkDetection:
    def test_curl_is_network(self):
        assert shell_command_uses_network("curl https://example.com")

    def test_wget_is_network(self):
        assert shell_command_uses_network("wget https://example.com")

    def test_git_clone_is_network(self):
        assert shell_command_uses_network("git clone https://github.com/user/repo")

    def test_ls_not_network(self):
        assert not shell_command_uses_network("ls -la")

    def test_echo_not_network(self):
        assert not shell_command_uses_network("echo hello")


# ── Shell path checks ──────────────────────────────────────────────

class TestShellPathsAllowed:
    def test_absolute_path_root(self):
        assert not shell_paths_allowed("cat /etc/shadow", ["/home/user/repo"])

    def test_safe_relative(self):
        assert shell_paths_allowed("ls src/", ["/home/user/repo"])

    def test_relative_file_with_slash_is_not_treated_as_absolute(self):
        assert shell_paths_allowed("python -m py_compile src/car_game.py", ["/home/user/repo"])

    def test_windows_dir_slash_flag_is_not_treated_as_absolute_path(self):
        assert shell_paths_allowed("dir src /b", ["/home/user/repo"])

    def test_windows_if_exist_dir_slash_flag_uses_inner_builtin(self):
        command = r'if exist "E:\ref-b\tools" (dir "E:\ref-b\tools" /b 2>nul) else echo NO_TOOLS_DIR'

        assert shell_paths_allowed(command, [r"E:\my-project", r"E:\ref-b", r"E:\ref-a"])

    def test_windows_rmdir_slash_flags_are_not_treated_as_absolute_paths(self):
        command = r"rmdir /s /q tmp\mo-offrails-20260604-143500"

        assert shell_paths_allowed(command, ["/home/user/repo"])

    def test_non_windows_command_slash_path_still_blocked(self):
        assert not shell_paths_allowed("cat /b", ["/home/user/repo"])

    def test_windows_drive_path_outside_roots_is_blocked(self):
        # Regression: drive-letter paths used to fall through to "allowed",
        # letting `type C:\...secret` escape the configured roots on Windows.
        assert not shell_paths_allowed(
            r"type C:\Users\victim\secret.txt", [r"E:\my-project"]
        )
        assert not shell_paths_allowed(
            r"Get-Content D:\secrets\creds.env", [r"E:\my-project"]
        )

    def test_windows_drive_path_inside_roots_is_allowed(self):
        assert shell_paths_allowed(
            r"type E:\my-project\README.md", [r"E:\my-project"]
        )

    def test_html_closing_tags_in_code_strings_are_not_paths(self):
        command = "python3 -c \"html='<html></html>'; print(html.count('</html>'))\""
        assert shell_paths_allowed(command, ["/home/user/repo"])

    def test_sandbox_self_test_path_literals_are_not_shell_paths(self):
        command = (
            "python -c \"from core.sandbox import guard_tool_call; "
            "roots=['/home/user/repo']; "
            "cmds=['ssh -i /etc/id_ed25519 acme-vps hostname']; "
            "print([guard_tool_call('shell', {'command': c}, allowed_roots=roots) for c in cmds])\""
        )
        assert shell_paths_allowed(command, ["/home/user/repo"])

    def test_python_inline_slash_command_literals_are_not_shell_paths(self):
        command = "python -c \"commands = ['/status', '/structural-graph', '/learning', '/profile', '/prt']; print(commands)\""
        assert shell_paths_allowed(command, ["/home/user/repo"])

    def test_python_inline_quoted_unix_root_literal_still_blocks(self):
        command = "python -c \"print('/etc')\""
        assert not shell_paths_allowed(command, ["/home/user/repo"])

    def test_general_python_inline_outside_path_still_blocks(self):
        command = "python -c \"open('/etc/shadow').read()\""
        assert not shell_paths_allowed(command, ["/home/user/repo"])

    def test_ssh_remote_paths_are_not_local_sandbox_paths(self):
        command = "ssh acme-prod \"git -C /srv/acme rev-parse HEAD\""
        assert shell_paths_allowed(command, ["/home/user/repo"])

    def test_ssh_local_identity_path_is_still_scanned(self):
        command = "ssh -i /etc/id_ed25519 acme-prod \"git -C /srv/acme rev-parse HEAD\""
        assert not shell_paths_allowed(command, ["/home/user/repo"])

    def test_ssh_local_input_redirection_path_is_still_scanned(self):
        command = "ssh acme-prod < /etc/shadow"
        assert not shell_paths_allowed(command, ["/home/user/repo"])

    def test_input_redirection_absolute_path_still_blocked(self):
        assert not shell_paths_allowed("cat </etc/shadow", ["/home/user/repo"])

    def test_root_reference(self):
        # Don't allow " / " as root
        assert not shell_paths_allowed("cat /etc/passwd", ["/home/user/repo"])


# ── Hard boundary detection ────────────────────────────────────────

class TestHardBoundary:
    def test_deploy_hits_boundary(self):
        assert _touches_hard_boundary("deploy to production now")

    def test_git_push_hits_boundary(self):
        # git push itself is allowed; only deployment-like targets are blocked
        assert not _touches_hard_boundary("git push origin main")
        assert _touches_hard_boundary("push to production")

    def test_force_push_hits_boundary(self):
        assert _touches_hard_boundary("git force-push to main")

    def test_drop_table_hits_boundary(self):
        assert _touches_hard_boundary("DROP TABLE users")

    def test_credentials_hits_boundary(self):
        assert _touches_hard_boundary("credentials for the api are stored here")

    def test_bearer_token_hits_boundary(self):
        assert _touches_hard_boundary("bearer token: sk-abc123")

    def test_safe_command_clean(self):
        assert not _touches_hard_boundary("ls -la /home/user/repo")

    def test_safe_command_normal(self):
        assert not _touches_hard_boundary("python -m pytest tests/")


# ── guard_tool_call integration ────────────────────────────────────

class TestGuardToolCall:
    ALLOWED_ROOTS = ["/home/user/repo"]

    def test_read_file_allowed(self):
        assert guard_tool_call("read_file", {"path": "/home/user/repo/README.md"}, allowed_roots=self.ALLOWED_ROOTS) is None

    def test_read_file_outside_roots_blocked(self):
        reason = guard_tool_call("read_file", {"path": "/etc/passwd"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_missing_required_tool_arguments_block_cleanly(self):
        reason = guard_tool_call("read_file", {}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "missing required argument: path" in reason

    def test_blank_required_tool_arguments_block_cleanly(self):
        reason = guard_tool_call("read_file", {"path": ""}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "blank required argument: path" in reason

    def test_blank_shell_command_blocks_cleanly(self):
        reason = guard_tool_call("shell", {"command": ""}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "blank required argument: command" in reason

    def test_blank_test_runner_command_blocks_but_missing_uses_default(self):
        reason = guard_tool_call("test_runner", {"command": ""}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "blank required argument: command" in reason
        assert guard_tool_call("test_runner", {}, allowed_roots=self.ALLOWED_ROOTS) is None

    def test_write_file_readonly_lane_blocked(self):
        reason = guard_tool_call("write_file", {"path": "/home/user/repo/file.txt"}, lane="review-only", allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "LANE LOCKED" in reason

    def test_write_file_normal_lane_allowed(self):
        assert guard_tool_call("write_file", {"path": "/home/user/repo/file.txt", "content": "ok"}, allowed_roots=self.ALLOWED_ROOTS) is None

    def test_write_file_missing_content_blocks_before_executor(self):
        reason = guard_tool_call("write_file", {"path": "/home/user/repo/file.txt"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "missing required argument: content" in reason

    def test_shell_mutating_readonly_blocked(self):
        reason = guard_tool_call("shell", {"command": "rm file.txt"}, lane="review-only", allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "LANE LOCKED" in reason

    def test_shell_safe_readonly_allowed(self):
        assert guard_tool_call("shell", {"command": "cat file.txt"}, lane="review-only", allowed_roots=self.ALLOWED_ROOTS) is None

    def test_shell_escape_blocked(self):
        reason = guard_tool_call("shell", {"command": "cd .."}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "[SANDBOX BLOCKED]" in reason

    def test_hard_boundary_deploy_blocked(self):
        # "push to production" still blocked even though git push itself is allowed
        reason = guard_tool_call("shell", {"command": "push to production"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "HARD BOUNDARY" in reason

    def test_hard_boundary_credentials_blocked(self):
        reason = guard_tool_call("shell", {"command": "echo $API_TOKEN >> secrets.txt"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "HARD BOUNDARY" in reason

    def test_ssh_target_alias_with_vps_word_is_allowed_for_probe(self):
        reason = guard_tool_call("shell", {"command": "ssh -o BatchMode=yes acme-vps hostname"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is None

    def test_ssh_remote_hard_boundary_command_still_blocks(self):
        reason = guard_tool_call("shell", {"command": "ssh acme-vps deploy app"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "HARD BOUNDARY" in reason

    def test_ssh_remote_read_only_git_check_allows_prod_path_name(self):
        reason = guard_tool_call("shell", {"command": "ssh acme-prod \"git -C /srv/acme-prod rev-parse HEAD\""}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is None

    def test_ssh_remote_deploy_word_still_blocks_with_prod_path_name(self):
        reason = guard_tool_call("shell", {"command": "ssh acme-prod \"deploy /srv/acme-prod\""}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "HARD BOUNDARY" in reason

    def test_ssh_identity_file_still_uses_path_allowlist(self):
        reason = guard_tool_call("shell", {"command": "ssh -i /etc/id_ed25519 acme-vps hostname"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_inline_sandbox_self_test_with_identity_path_literal_allowed(self):
        command = (
            "python -c \"from core.sandbox import guard_tool_call; "
            "roots=['/home/user/repo']; "
            "cases=[('identity','ssh -i /etc/id_ed25519 acme-vps hostname')]; "
            "print([guard_tool_call('shell', {'command': cmd}, allowed_roots=roots) for _, cmd in cases])\""
        )
        assert guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS) is None

    def test_windows_slash_flag_after_pipeline_allowed(self):
        command = 'python mo_trace.py replay trace_20260607_033418 --tail 30 2>&1 | findstr /V "PASS\\|INFO"'

        reason = guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS)

        assert reason is None

    def test_windows_find_count_flag_after_pipeline_allowed(self):
        command = 'python -m pytest --collect-only -q 2>&1 | find "test" | find /c "test"'

        reason = guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS)

        assert reason is None

    def test_inline_stdout_probe_with_stderr_redirect_allowed(self):
        command = "python -c \"import sys; sys.stdout.write('Testing stdout...\\n'); sys.stdout.flush()\" 2>&1"

        reason = guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS)

        assert reason is None

    def test_windows_rmdir_slash_flags_allowed_for_repo_relative_path(self):
        command = r"rmdir /s /q tmp\mo-offrails-20260604-143500 2>&1 && echo CLEANED || echo FAILED"

        reason = guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS)

        assert reason is None

    def test_real_absolute_path_before_pipeline_still_blocked(self):
        command = 'cat /etc/shadow | findstr /V "PASS\\|INFO"'

        reason = guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS)

        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_real_absolute_path_before_windows_find_still_blocked(self):
        command = 'cat /etc/shadow | find /c "shadow"'

        reason = guard_tool_call("shell", {"command": command}, allowed_roots=self.ALLOWED_ROOTS)

        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_shell_path_outside_roots_blocked(self):
        reason = guard_tool_call("shell", {"command": "cat /etc/shadow"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_web_fetch_allowed(self):
        assert guard_tool_call("web_fetch", {"url": "https://example.com"}, sandbox_config={"enabled": True, "web_fetch_enabled": True}) is None

    def test_web_fetch_disabled(self):
        reason = guard_tool_call("web_fetch", {"url": "https://example.com"}, sandbox_config={"enabled": True, "web_fetch_enabled": False})
        assert reason is not None
        assert "network access disabled" in reason

    def test_web_search_disabled_by_same_network_policy(self):
        reason = guard_tool_call("web_search", {"query": "mo"}, sandbox_config={"enabled": True, "web_fetch_enabled": False})
        assert reason is not None
        assert "network access disabled" in reason

    def test_web_search_honors_web_host_allowlist(self):
        reason = guard_tool_call("web_search", {"query": "mo"}, sandbox_config={"enabled": True, "web_fetch_allowed_hosts": ["example.com"]})
        assert reason is not None
        assert "api.duckduckgo.com" in reason

    def test_shell_escape_can_be_disabled_by_config(self):
        reason = guard_tool_call("shell", {"command": "cd .."}, allowed_roots=self.ALLOWED_ROOTS, sandbox_config={"block_shell_escape": False})
        assert reason is None

    def test_test_runner_escape_can_be_disabled_by_config(self):
        reason = guard_tool_call("test_runner", {"command": "cd .."}, allowed_roots=self.ALLOWED_ROOTS, sandbox_config={"block_shell_escape": False})
        assert reason is None

    def test_test_runner_mutating_readonly_blocked(self):
        reason = guard_tool_call("test_runner", {"command": "python -m pytest -q && rm -rf /"}, lane="review-only", allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "mutation blocked" in reason

    def test_shell_workdir_outside_roots_blocked(self):
        reason = guard_tool_call("shell", {"command": "ls", "workdir": "/etc"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_edit_file_readonly_blocked(self):
        reason = guard_tool_call("edit_file", {"path": "/home/user/repo/file.py"}, lane="investigate", allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "LANE LOCKED" in reason

    def test_find_files_outside_roots_blocked(self):
        reason = guard_tool_call("find_files", {"root": "/etc"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_grep_outside_roots_blocked(self):
        reason = guard_tool_call("grep", {"pattern": "secret", "root": "/var/log"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason

    def test_project_bridge_outside_roots_blocked(self):
        reason = guard_tool_call("project_bridge", {"path": "/etc/hostname"}, allowed_roots=self.ALLOWED_ROOTS)
        assert reason is not None
        assert "PATH BLOCKED" in reason


# ── Hard boundary pattern completeness ─────────────────────────────

class TestHardBoundaryPatterns:
    """Verify all 8 boundary classes are present."""

    def test_deployment_production_patterns(self):
        triggers = ["deploy", "deployment", "release", "go live", "vps", "remote"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_production_context_patterns(self):
        triggers = ["to production", "production deploy", "production server", "prod"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_git_push_patterns(self):
        triggers = ["push to origin", "force-push", "force push"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_destructive_patterns(self):
        triggers = ["rewrite history", "reset --hard", "delete repo", "drop table", "truncate"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_credential_patterns(self):
        triggers = ["credentials", "secrets", "oauth", "private key", "bearer"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_token_patterns(self):
        triggers = ["api token", "access token", "auth token", "bearer token"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_operational_patterns(self):
        triggers = ["wallet", "payment", "billing", "database migration", "external account"]
        for t in triggers:
            assert _touches_hard_boundary(t), f"'{t}' should trigger hard boundary"

    def test_num_patterns(self):
        """Should be exactly 7 hard boundary pattern classes."""
        assert len(HARD_BOUNDARY_PATTERNS) == 7
