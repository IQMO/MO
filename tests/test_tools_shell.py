from tools import _shell_command, _test_runner_timeout, _tool_timeout, execute_shell, execute_test_runner


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


def test_test_runner_timeout_default_fits_full_pytest():
    assert _test_runner_timeout("python -m pytest -q") == 420


def test_test_runner_timeout_floors_short_pytest_values():
    assert _test_runner_timeout("python -m pytest tests -q", 120) == 420
    assert _test_runner_timeout("pytest tests -q", 300) == 420


def test_test_runner_timeout_preserves_higher_pytest_value():
    assert _test_runner_timeout("python -m pytest -q", 900) == 900


def test_shell_timeout_floors_raw_pytest_but_keeps_non_pytest_default():
    assert _tool_timeout("python -m pytest tests -q", 120, 60) == 420
    assert _tool_timeout("python -c \"print('ok')\"", None, 60) == 60


def test_execute_test_runner_applies_pytest_timeout_floor(monkeypatch):
    captured = {}

    def fake_execute_shell(arguments):
        captured.update(arguments)
        return "ok"

    monkeypatch.setattr("tools.execute_shell", fake_execute_shell)

    assert execute_test_runner({"command": "python -m pytest tests -q", "timeout": 120}) == "ok"
    assert captured["timeout"] == 420
