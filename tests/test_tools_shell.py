from tools import _shell_command, execute_shell


def test_execute_shell_clean_env_drops_secret_env(monkeypatch):
    monkeypatch.setenv("MO_TEST_SECRET_TOKEN", "visible-secret")

    output = execute_shell({"command": "python -c \"import os; print(os.getenv('MO_TEST_SECRET_TOKEN', 'missing'))\"", "timeout": 20})

    assert "visible-secret" not in output
    assert "missing" in output


def test_execute_shell_unclean_env_keeps_env_when_explicit(monkeypatch):
    monkeypatch.setenv("MO_TEST_SECRET_TOKEN", "visible-secret")

    output = execute_shell({"command": "python -c \"import os; print(os.getenv('MO_TEST_SECRET_TOKEN', 'missing'))\"", "timeout": 20, "_clean_env": False})

    assert "visible-secret" in output


def test_shell_command_uses_posix_shell_from_environment(monkeypatch):
    monkeypatch.setattr("tools.sys.platform", "linux")
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    monkeypatch.delenv("MO_TOOL_SHELL", raising=False)
    monkeypatch.delenv("MO_SHELL", raising=False)

    shell_cmd, use_shell, registered = _shell_command("printf ok")

    assert shell_cmd == ["/usr/bin/zsh", "-c", "printf ok"]
    assert use_shell is False
    assert registered == "printf ok"


def test_shell_command_uses_windows_comspec_without_forcing_powershell(monkeypatch):
    monkeypatch.setattr("tools.sys.platform", "win32")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.delenv("MO_TOOL_SHELL", raising=False)
    monkeypatch.delenv("MO_SHELL", raising=False)

    shell_cmd, use_shell, registered = _shell_command("echo ok")

    assert shell_cmd == "echo ok"
    assert use_shell is True
    assert registered == "echo ok"


def test_shell_command_supports_explicit_powershell(monkeypatch):
    monkeypatch.setattr("tools.sys.platform", "win32")
    monkeypatch.setenv("MO_TOOL_SHELL", "pwsh")

    shell_cmd, use_shell, registered = _shell_command("Write-Output ok")

    assert shell_cmd[:4] == ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert shell_cmd[4] == "-Command"
    assert "Write-Output ok" in shell_cmd[5]
    assert "LASTEXITCODE" in shell_cmd[5]
    assert use_shell is False
    assert registered == shell_cmd[5]
