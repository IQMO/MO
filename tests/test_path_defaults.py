"""Tests for core/path_defaults.py — centralized local path defaults."""
from __future__ import annotations

from pathlib import Path


from core.path_defaults import (
    ENV_CODEX_AUTH_PATH,
    ENV_CODEX_AUTH_PATH_COMPAT,
    ENV_DEFAULT_ROOTS,
    ENV_HEARTBEAT_LEDGER_DISABLE,
    ENV_HEARTBEAT_LEDGER_PATH,
    ENV_MO_CONFIG,
    ENV_MO_HOME,
    ENV_MO_PROJECT_CWD,
    ENV_MO_STATE_HOME,
    ENV_TASKBOARD_LEDGER_DISABLE,
    ENV_TASKBOARD_LEDGER_PATH,
    HEARTBEAT_LEDGER_DIR,
    HEARTBEAT_LEDGER_PATH,
    TASKBOARD_LEDGER_DIR,
    TASKBOARD_LEDGER_PATH,
    default_config_path,
    default_project_roots,
    mo_home,
    private_state_enabled,
    project_cache_dir,
    project_cwd,
    repo_root,
    resolve_state_path,
)


class TestEnvConstants:
    def test_all_env_constants_are_strings(self):
        for name, value in [
            ("ENV_DEFAULT_ROOTS", ENV_DEFAULT_ROOTS),
            ("ENV_CODEX_AUTH_PATH", ENV_CODEX_AUTH_PATH),
            ("ENV_CODEX_AUTH_PATH_COMPAT", ENV_CODEX_AUTH_PATH_COMPAT),
            ("ENV_MO_CONFIG", ENV_MO_CONFIG),
            ("ENV_MO_HOME", ENV_MO_HOME),
            ("ENV_MO_PROJECT_CWD", ENV_MO_PROJECT_CWD),
            ("ENV_MO_STATE_HOME", ENV_MO_STATE_HOME),
            ("ENV_TASKBOARD_LEDGER_PATH", ENV_TASKBOARD_LEDGER_PATH),
            ("ENV_TASKBOARD_LEDGER_DISABLE", ENV_TASKBOARD_LEDGER_DISABLE),
            ("ENV_HEARTBEAT_LEDGER_PATH", ENV_HEARTBEAT_LEDGER_PATH),
            ("ENV_HEARTBEAT_LEDGER_DISABLE", ENV_HEARTBEAT_LEDGER_DISABLE),
        ]:
            assert isinstance(value, str), f"{name} should be a string"
            if name != "ENV_CODEX_AUTH_PATH_COMPAT":
                assert "MO_" in value, f"{name} should contain MO_ prefix"

    def test_env_constants_have_mo_prefix(self):
        for value in [
            ENV_DEFAULT_ROOTS,
            ENV_CODEX_AUTH_PATH,
            ENV_MO_CONFIG,
            ENV_MO_HOME,
            ENV_MO_PROJECT_CWD,
            ENV_MO_STATE_HOME,
            ENV_TASKBOARD_LEDGER_PATH,
            ENV_TASKBOARD_LEDGER_DISABLE,
            ENV_HEARTBEAT_LEDGER_PATH,
            ENV_HEARTBEAT_LEDGER_DISABLE,
        ]:
            assert "MO_" in value
        # CODEX_AUTH_PATH_COMPAT is a legacy compat name without MO_ prefix
        assert isinstance(ENV_CODEX_AUTH_PATH_COMPAT, str)


class TestRepoRoot:
    def test_returns_string(self):
        assert isinstance(repo_root(), str)

    def test_is_absolute(self):
        assert Path(repo_root()).is_absolute()

    def test_contains_core_directory(self):
        assert (Path(repo_root()) / "core").is_dir()


class TestMoHome:
    def test_default_is_dot_mo(self):
        result = mo_home()
        assert result.name == ".mo"

    def test_config_home_override(self):
        result = mo_home({"runtime": {"home": "~/.custom_mo"}})
        assert result.name == ".custom_mo"

    def test_env_home_override(self, monkeypatch):
        monkeypatch.setenv("MO_HOME", "~/.env_mo")
        result = mo_home()
        assert result.name == ".env_mo"

    def test_env_state_home_override(self, monkeypatch):
        monkeypatch.setenv("MO_STATE_HOME", "~/.state_mo")
        monkeypatch.delenv("MO_HOME", raising=False)
        result = mo_home()
        assert result.name == ".state_mo"

    def test_none_config(self):
        result = mo_home(None)
        assert result.name == ".mo"


class TestPrivateStateEnabled:
    def test_default_false(self):
        assert not private_state_enabled()

    def test_env_mo_state_home_enables(self, monkeypatch):
        monkeypatch.setenv("MO_STATE_HOME", "~/.mo")
        assert private_state_enabled()

    def test_env_mo_home_enables(self, monkeypatch):
        monkeypatch.setenv("MO_HOME", "~/.mo")
        assert private_state_enabled()

    def test_config_home_enables(self):
        assert private_state_enabled({"runtime": {"home": "~/.mo"}})

    def test_config_state_private(self):
        assert private_state_enabled({"runtime": {"state": "private"}})
        assert private_state_enabled({"runtime": {"state": "home"}})
        assert private_state_enabled({"runtime": {"state": "mo_home"}})

    def test_config_state_non_private(self):
        assert not private_state_enabled({"runtime": {"home": "~/.mo", "state": "project"}})
        assert not private_state_enabled({"runtime": {"state": "project"}})
        assert not private_state_enabled({"runtime": {"state": ""}})


class TestResolveStatePath:
    def test_absolute_path_preserved(self):
        result = resolve_state_path("/absolute/path")
        assert result == str(Path("/absolute/path"))

    def test_empty_string_returns_empty(self):
        assert resolve_state_path("") == ""
        assert resolve_state_path(None) == ""

    def test_default_fallback(self):
        result = resolve_state_path(None, default="/fallback")
        assert result
        assert "fallback" in result

    def test_expands_user(self):
        result = resolve_state_path("~/test")
        assert result.startswith(str(Path.home()))


class TestProjectCwd:
    def test_default_is_cwd(self):
        result = project_cwd()
        assert result.is_absolute()

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MO_PROJECT_CWD", str(tmp_path))
        result = project_cwd()
        assert result == tmp_path.resolve()

    def test_default_parameter(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MO_PROJECT_CWD", raising=False)
        result = project_cwd(default=str(tmp_path))
        assert result == tmp_path.resolve()


class TestDefaultConfigPath:
    def test_returns_mo_home_config_by_default(self):
        result = default_config_path()
        assert ".mo" in result
        assert result.endswith("config.yaml")

    def test_env_mo_config_absolute(self, monkeypatch, tmp_path):
        config_file = tmp_path / "my_config.yaml"
        config_file.write_text("key: value")
        monkeypatch.setenv("MO_CONFIG", str(config_file))
        result = default_config_path()
        assert result == str(config_file.resolve())

    def test_env_mo_config_relative(self, monkeypatch, tmp_path):
        config_file = tmp_path / "my_config.yaml"
        config_file.write_text("key: value")
        monkeypatch.setenv("MO_CONFIG", "my_config.yaml")
        monkeypatch.setenv("MO_PROJECT_CWD", str(tmp_path))
        result = default_config_path()
        assert Path(result).name == "my_config.yaml"


class TestProjectCacheDir:
    def test_returns_path(self):
        result = project_cache_dir("test", "/some/root")
        assert isinstance(result, Path)

    def test_sanitizes_kind(self):
        result = project_cache_dir("test!@#kind", "/root")
        assert "testkind" in str(result) or "test-kind" in str(result)

    def test_same_root_same_cache(self):
        a = project_cache_dir("graph", "/root")
        b = project_cache_dir("graph", "/root")
        assert a == b

    def test_different_roots_different_cache(self):
        a = project_cache_dir("graph", "/root1")
        b = project_cache_dir("graph", "/root2")
        assert a != b


class TestLedgerPaths:
    def test_taskboard_ledger_dir(self):
        assert TASKBOARD_LEDGER_DIR == "memory/taskboards"

    def test_taskboard_ledger_path(self):
        assert TASKBOARD_LEDGER_PATH == "memory/taskboards/taskboards.jsonl"

    def test_heartbeat_ledger_dir(self):
        assert HEARTBEAT_LEDGER_DIR == "memory/heartbeat"

    def test_heartbeat_ledger_path(self):
        assert HEARTBEAT_LEDGER_PATH == "memory/heartbeat/heartbeats.jsonl"


class TestDefaultProjectRoots:
    def test_returns_list(self):
        result = default_project_roots()
        assert isinstance(result, list)

    def test_full_access_mode_returns_empty(self):
        result = default_project_roots({"access": {"mode": "full"}})
        assert result == []

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MO_DEFAULT_ROOTS", str(tmp_path))
        result = default_project_roots()
        assert [str(Path(r).resolve()) for r in result] == [str(tmp_path.resolve())]

    def test_project_mode_excludes_private_runtime_home(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setenv("MO_PROJECT_CWD", str(project))
        result = default_project_roots({"runtime": {"home": str(home)}, "access": {"mode": "project"}})
        resolved = [str(Path(r).resolve()) for r in result]
        assert resolved == [str(project.resolve())]
        assert str(home.resolve()) not in resolved

    def test_none_config(self):
        result = default_project_roots(None)
        assert isinstance(result, list)


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
