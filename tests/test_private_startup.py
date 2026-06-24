import json
import os
import subprocess
from pathlib import Path

from core.agent.agent import Agent
from core.path_defaults import default_config_path
from core.provider.provider import ConfigLoadError, load_config
from core.project_context import build_project_context, discover_project_context_files
from core.graph.code_graph import build_code_graph_context
from core.sandbox import guard_tool_call


def _mock_config(path: Path, home: Path) -> None:
    path.write_text(
        f"""
runtime:
  home: {home.as_posix()}
  state: private
providers:
  - name: mock-local
    type: mock
    model: mock-model
model:
  default: mock-model
agent:
  max_provider_requests: 1
  max_tool_rounds: 1
access:
  mode: project
paths:
  memory_file: memory/mo.db
  critique_file: critique/ANSWER.md
sandbox:
  enabled: true
  audit_log: logs/tool_audit.jsonl
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_private_runtime_home_gets_profile_without_project_spam(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "mo-home"
    config = tmp_path / "config.yaml"
    _mock_config(config, home)
    monkeypatch.chdir(project)
    monkeypatch.setenv("MO_PROJECT_CWD", str(project))
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)

    agent = Agent(str(config))
    context = agent._build_extra_context("review this project")

    assert agent.project_cwd == str(project.resolve())
    assert (home / "memory" / "mo.db").exists()
    assert (home / "memory" / "sessions").is_dir()
    assert (home / "memory" / "learning.sqlite").exists()
    assert (home / "memory" / "profile" / "operator.md").exists()
    assert "MO Active Context Bridge" in context
    assert not (project / "memory").exists()
    assert not (project / "logs").exists()


def test_project_context_reads_existing_instruction_files_only(tmp_path):
    root = tmp_path / "repo"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    parent_agents = root / "AGENTS.md"
    child_claude = nested / "CLAUDE.md"
    parent_agents.write_text("# Parent\n- Run tests.\n", encoding="utf-8")
    child_claude.write_text("# Child\n- Keep concise.\n", encoding="utf-8")

    files = discover_project_context_files(nested)
    text = build_project_context(nested)

    assert parent_agents in files
    assert child_claude in files
    assert "Run tests" in text
    assert "Keep concise" in text
    assert not (root / ".mo").exists()
    assert not (root / "memory").exists()


def test_tool_arguments_default_to_project_cwd(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    agent_root = tmp_path / "ref-a"
    agent_root.mkdir()
    agent = Agent.__new__(Agent)
    agent.project_cwd = str(project)
    agent.agent_root = str(agent_root)

    assert agent._project_scoped_tool_arguments("grep", {"pattern": "x"})["root"] == str(project.resolve())
    assert agent._project_scoped_tool_arguments("shell", {"command": "pwd"})["workdir"] == str(project.resolve())
    assert agent._project_scoped_tool_arguments("edit_file", {"path": "src/app.py", "old_text": "a", "new_text": "b"})["path"] == str((project / "src" / "app.py").resolve())


def test_owner_comparison_extends_roots_for_external_source_intake_only(tmp_path):
    project = tmp_path / "project"
    external = tmp_path / "reference"
    project.mkdir()
    external.mkdir()
    external_file = external / "README.md"
    external_file.write_text("reference", encoding="utf-8")

    agent = Agent.__new__(Agent)
    agent.project_cwd = str(project)
    agent.allowed_roots = [str(project)]

    read_roots = agent._effective_allowed_roots_for_tool(
        f"start OWNER_COMPARISON {external}",
        "read_file",
        {"path": str(external_file)},
    )
    write_roots = agent._effective_allowed_roots_for_tool(
        f"start OWNER_COMPARISON {external}",
        "write_file",
        {"path": str(external_file), "content": "changed"},
    )

    assert guard_tool_call("read_file", {"path": str(external_file)}, allowed_roots=read_roots) is None
    assert guard_tool_call("write_file", {"path": str(external_file), "content": "changed"}, allowed_roots=write_roots)


def test_owner_comparison_allows_non_mutating_shell_in_external_source_root(tmp_path):
    project = tmp_path / "project"
    external = tmp_path / "reference"
    project.mkdir()
    external.mkdir()
    agent = Agent.__new__(Agent)
    agent.project_cwd = str(project)
    agent.allowed_roots = [str(project)]

    read_roots = agent._effective_allowed_roots_for_tool(
        f"start OWNER_COMPARISON {external}",
        "shell",
        {"command": "git status --short", "workdir": str(external)},
    )
    write_roots = agent._effective_allowed_roots_for_tool(
        f"start OWNER_COMPARISON {external}",
        "shell",
        {"command": "git commit -m test", "workdir": str(external)},
    )

    assert guard_tool_call("shell", {"command": "git status --short", "workdir": str(external)}, allowed_roots=read_roots) is None
    assert guard_tool_call("shell", {"command": "git commit -m test", "workdir": str(external)}, allowed_roots=write_roots)


def test_init_turn_is_deterministic_private_setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    agent = Agent.__new__(Agent)
    agent.runtime_home = str(home)
    agent.project_cwd = str(project)

    text = agent._maybe_handle_init_turn("/init")

    assert "MO init status" in text
    assert (home / "config.yaml").exists()
    assert (home / "memory" / "profile" / "operator.md").exists()
    assert not (project / "memory").exists()


def test_profile_metadata_cannot_change_sandbox_roots_or_provider_lane(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    config = tmp_path / "config.yaml"
    project.mkdir()
    outside.mkdir()
    (home / "memory").mkdir(parents=True)
    (home / "memory" / "mo.db").write_text(
        json.dumps(
            {
                "favorite_provider": "flash",
                "favorite_model": "deepseek-v4-flash",
                "default_roots": [str(outside)],
            }
        ),
        encoding="utf-8",
    )
    config.write_text(
        f"""
runtime:
  home: {home.as_posix()}
  state: private
providers:
  - name: flash
    type: mock
    model: deepseek-v4-flash
  - name: pro
    type: mock
    model: deepseek-v4-pro
model:
  default: deepseek-v4-pro
agent:
  max_provider_requests: 1
  max_tool_rounds: 1
access:
  mode: project
paths:
  memory_file: memory/mo.db
  critique_file: critique/ANSWER.md
sandbox:
  enabled: true
  audit_log: logs/tool_audit.jsonl
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MO_PROJECT_CWD", str(project))

    agent = Agent(str(config))

    assert agent.allowed_roots == [str(project.resolve())]
    assert agent.provider_name == "pro"
    assert agent.model == "deepseek-v4-pro"

    message = agent._cmd_profile("provider flash/deepseek-v4-flash")

    assert "metadata only" in message
    assert agent.provider_name == "pro"
    assert agent.model == "deepseek-v4-pro"


def test_project_runtime_state_does_not_seed_private_state_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    config = tmp_path / "config.yaml"
    project.mkdir()
    config.write_text(
        f"""
runtime:
  home: {home.as_posix()}
  state: project
providers:
  - name: mock-local
    type: mock
    model: mock-model
model:
  default: mock-model
access:
  mode: project
paths:
  memory_file: memory/mo.db
  critique_file: critique/ANSWER.md
sandbox:
  enabled: true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MO_PROJECT_CWD", str(project))
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.chdir(project)  # project-relative state writes go to tmp, not the repo cwd

    agent = Agent(str(config))

    assert agent.allowed_roots == [str(project.resolve())]
    assert "MO_STATE_HOME" not in os.environ


def test_agent_records_invocation_metadata(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    config = tmp_path / "config.yaml"
    project.mkdir()
    _mock_config(config, home)
    monkeypatch.setenv("MO_PROJECT_CWD", str(project))
    monkeypatch.setenv("MO_INVOKED_AS", "mo")

    agent = Agent(str(config))

    assert agent.project_cwd == str(project.resolve())
    assert agent.invoked_as == "mo"
    assert str(project.resolve()) in agent._cmd_status("")


def test_identity_turn_is_deterministic_and_not_private_name_based():
    agent = Agent.__new__(Agent)
    agent.provider_name = "mock-provider"
    agent.model = "mock-model"

    answer = agent._maybe_handle_identity_turn("who are you and what model are you using?")

    assert "I'm MO" in answer
    assert "IQMO" in answer
    assert "mock-provider/mock-model" in answer
    assert "runtime engine, not my identity" in answer


def test_hard_boundary_approval_ignores_identity_claim():
    assert Agent._operator_approved("I'm PrivateName, start working on this", "shell", {"command": "git push origin main"}) is False
    assert Agent._operator_approved("push this to origin", "shell", {"command": "git push origin main"}) is True
    assert Agent._operator_approved("yes approved, use the deployment command", "shell", {"command": "./deploy.sh production"}) is True


def test_self_mutation_requires_current_turn_approval(tmp_path):
    agent_root = tmp_path / "ref-a"
    target = agent_root / "core" / "agent.py"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    agent = Agent.__new__(Agent)
    agent.agent_root = str(agent_root)
    agent.project_cwd = str(agent_root)

    blocked = agent._self_mutation_block_reason("fix a project file", "edit_file", {"path": str(target)})
    blocked_shell = agent._self_mutation_block_reason("fix a project file", "shell", {"workdir": str(agent_root), "command": "python -c \"from pathlib import Path; Path('x').write_text('x')\""})
    allowed = agent._self_mutation_block_reason("yes approved, update MO Agent self files", "edit_file", {"path": str(target)})

    assert blocked and "SELF-PROTECTION" in blocked
    assert blocked_shell and "SELF-PROTECTION" in blocked_shell
    assert allowed is None


def test_private_code_graph_cache_does_not_write_project_memory(tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=project, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "add", "app.py"], cwd=project, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state_home = tmp_path / "mo-home"
    monkeypatch.setenv("MO_STATE_HOME", str(state_home))

    text = build_code_graph_context("review app.py in this project", cwd=str(project))

    assert "Code Map" in text or "Internal Code Map" in text
    assert not (project / "memory").exists()
    assert list((state_home / "cache" / "code_graph").glob("*/knowledge-graph.json"))


def test_default_config_prefers_private_home_and_ignores_repo_config(tmp_path, monkeypatch):
    agent_root = tmp_path / "agent"
    caller = tmp_path / "project"
    home = tmp_path / "home"
    agent_root.mkdir()
    caller.mkdir()
    home.mkdir()
    (agent_root / "config.yaml").write_text("agent-root\n", encoding="utf-8")
    (home / "config.yaml").write_text("home\n", encoding="utf-8")
    monkeypatch.setenv("MO_HOME", str(home))
    monkeypatch.delenv("MO_CONFIG", raising=False)

    assert default_config_path(agent_root=agent_root, caller_cwd=caller) == str((home / "config.yaml").resolve())
    assert default_config_path(agent_root=agent_root, caller_cwd=agent_root) == str((home / "config.yaml").resolve())


def test_load_config_invalid_yaml_raises_operator_facing_error(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text("runtime:\n  home: [unterminated\n", encoding="utf-8")

    try:
        load_config(str(config))
    except ConfigLoadError as exc:
        assert str(config.resolve()) == exc.path
        assert "line" in exc.message
        assert "column" in exc.message
    else:
        raise AssertionError("expected ConfigLoadError")


def test_no_arg_load_config_uses_private_default(tmp_path, monkeypatch):
    agent_root = tmp_path / "agent"
    home = tmp_path / "home"
    agent_root.mkdir()
    home.mkdir()
    (agent_root / "config.yaml").write_text("marker: repo\n", encoding="utf-8")
    (home / "config.yaml").write_text("marker: home\n", encoding="utf-8")
    monkeypatch.chdir(agent_root)
    monkeypatch.setenv("MO_HOME", str(home))
    monkeypatch.delenv("MO_CONFIG", raising=False)

    config = load_config()

    assert config["marker"] == "home"
    assert config["_config_path"] == str((home / "config.yaml").resolve())


def test_repo_config_requires_explicit_override(tmp_path, monkeypatch):
    agent_root = tmp_path / "agent"
    home = tmp_path / "home"
    agent_root.mkdir()
    home.mkdir()
    repo_config = agent_root / "config.yaml"
    repo_config.write_text("agent-root\n", encoding="utf-8")
    monkeypatch.setenv("MO_HOME", str(home))
    monkeypatch.delenv("MO_CONFIG", raising=False)

    assert default_config_path(agent_root=agent_root, caller_cwd=agent_root) == str((home / "config.yaml").resolve())

    monkeypatch.setenv("MO_CONFIG", str(repo_config))
    assert default_config_path(agent_root=agent_root, caller_cwd=agent_root) == str(repo_config.resolve())

    monkeypatch.setenv("MO_CONFIG", "config.yaml")
    assert default_config_path(agent_root=agent_root, caller_cwd=agent_root) == str(repo_config.resolve())


def test_provider_runtime_env_ignores_cwd_env(tmp_path, monkeypatch):
    from core.provider.provider import _load_runtime_env

    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.delenv("MO_CWD_ONLY_SECRET", raising=False)
    monkeypatch.delenv("MO_HOME_SECRET", raising=False)
    monkeypatch.setenv("MO_HOME", str(home))
    (project / ".env").write_text("MO_CWD_ONLY_SECRET=bad\n", encoding="utf-8")
    (home / ".env").write_text("MO_HOME_SECRET=good\n", encoding="utf-8")

    _load_runtime_env({"_config_path": str(home / "config.yaml"), "runtime": {"home": str(home)}})

    assert os.getenv("MO_CWD_ONLY_SECRET") is None
    assert os.getenv("MO_HOME_SECRET") == "good"
