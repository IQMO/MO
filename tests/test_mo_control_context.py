
import core.mo_control_context as mo_control_context
from core.mo_control_context import build_mo_control_context, resolve_mo_control_workspace, should_include_mo_control_context


def test_mo_control_context_triggers_on_cross_repo_operations():
    assert should_include_mo_control_context("commit and deploy the service") is True
    assert should_include_mo_control_context("hi") is False


def test_mo_control_context_triggers_on_casual_deploy_phrasing():
    # Regression: casual deploy/release phrasing must pull the operator-authority
    # block (no-clobber, no-secrets, reviewed-paths-only), not just the formal word
    # "deploy"/"production". And ordinary turns must NOT over-fire (cost).
    for fires in ("ship it to prod", "roll this out to the box", "release the changes",
                  "restart the service", "rsync to the host"):
        assert should_include_mo_control_context(fires) is True, fires
    for quiet in ("hi", "what does this function do", "figure out the bug", "write a haiku"):
        assert should_include_mo_control_context(quiet) is False, quiet


def test_operator_trigger_terms_come_from_config_not_code():
    """Operator project codenames are private config data, never hardcoded."""
    codename_only = "check acmeproj status"
    assert should_include_mo_control_context(codename_only) is False
    cfg = {"mo_control": {"trigger_terms": ["acmeproj"]}}
    assert should_include_mo_control_context(codename_only, cfg) is True


def test_mo_control_context_uses_configured_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SECURITY.md").write_text("Never print secrets. Runtime owner approves deploys.\n", encoding="utf-8")
    (workspace / "SYNC.md").write_text("AcmeRepo deploy uses reviewed changed paths only.\nLiveTrader is live-money.\n", encoding="utf-8")

    cfg = {"mo_control": {"workspace_path": str(workspace), "trigger_terms": ["acmerepo", "livetrader"]}}
    text = build_mo_control_context(user_input="deploy AcmeRepo and LiveTrader", config=cfg)

    assert resolve_mo_control_workspace(cfg) == workspace
    assert "the configured operator is the owner/operator" in text
    assert "Never print secrets" in text
    assert "AcmeRepo" in text
    assert "LiveTrader" in text


def test_mo_control_context_fails_safe_without_workspace(monkeypatch):
    monkeypatch.setattr(mo_control_context, "resolve_mo_control_workspace", lambda _config=None: None)
    text = build_mo_control_context(user_input="who owns deploy", config={})

    # Bridge retired: no "control workspace" framing is emitted, but the active
    # safety rules must still be present so MO fails safe without a workspace.
    assert "workspace" not in text.lower()
    assert "MO Agent is the delegated operator" in text
    assert "Never print secrets" in text


def test_mo_control_context_can_use_private_owner_label(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = {"mo_control": {"workspace_path": str(workspace), "owner_label": "OwnerName"}}

    text = build_mo_control_context(user_input="deploy production", config=cfg)

    assert "OwnerName is the owner/operator" in text


def test_full_access_mode_stays_unrestricted_despite_control_workspace(tmp_path, monkeypatch):
    """REGRESSION (live 2026-06-10 03:19): with access.mode full, allowed_roots
    is empty = unrestricted. Appending the control workspace inverted that into
    'only the workspace', blocking MO from its own project. Empty roots must
    pass through untouched."""
    from core.agent.agent import Agent
    from core.sandbox import guard_tool_call

    monkeypatch.delenv("MO_CONTROL_WORKSPACE", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    agent = object.__new__(Agent)
    agent.allowed_roots = []  # access.mode: full
    agent.config = {"mo_control": {"workspace_path": str(workspace)}}

    roots = agent._effective_allowed_roots_for_tool("start DEVMODE05", "read_file", {"path": r"E:\anything\file.md"})
    assert roots == []
    assert guard_tool_call("read_file", {"path": str(tmp_path / "any.md")}, allowed_roots=roots) is None
    shell_roots = agent._effective_allowed_roots_for_tool("start DEVMODE05", "shell", {"command": "git status"})
    assert shell_roots == []


def test_control_workspace_is_readable_but_not_writable_by_tools(tmp_path, monkeypatch):
    """The context block advertises the workspace, so read tools must reach it
    while write tools stay blocked (the 2026-06-10 live-session conflict)."""
    from core.agent.agent import Agent
    from core.sandbox import guard_tool_call

    monkeypatch.delenv("MO_CONTROL_WORKSPACE", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    truth = workspace / "SOURCE_OF_TRUTH.md"
    truth.write_text("owner rules\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    agent = object.__new__(Agent)
    agent.allowed_roots = [str(project)]
    agent.config = {"mo_control": {"workspace_path": str(workspace)}}

    read_roots = agent._effective_allowed_roots_for_tool("check the source of truth", "read_file", {"path": str(truth)})
    assert str(workspace) in read_roots
    assert guard_tool_call("read_file", {"path": str(truth)}, allowed_roots=read_roots) is None

    write_roots = agent._effective_allowed_roots_for_tool("check the source of truth", "write_file", {"path": str(truth)})
    assert str(workspace) not in write_roots
    assert guard_tool_call("write_file", {"path": str(truth), "content": "x"}, allowed_roots=write_roots)

    mutating_shell_roots = agent._effective_allowed_roots_for_tool("check", "shell", {"command": f"del {truth}"})
    assert str(workspace) not in mutating_shell_roots


def test_devmode_effective_roots_include_operator_pack_and_records(tmp_path, monkeypatch):
    """DEVMODE05 tools must reach the migrated private pack and session records."""
    from core.agent.agent import Agent
    import core.agent.agent_turn_dispatch as dispatch
    from core.sandbox import guard_tool_call

    project = tmp_path / "product"
    project.mkdir()
    pack = tmp_path / "operator"
    pack.mkdir()
    (pack / "mo_trace.py").write_text("print('trace')\n", encoding="utf-8")
    home = tmp_path / "home"
    records = home / "memory" / "devmode"
    records.mkdir(parents=True)

    monkeypatch.setattr(dispatch, "is_devmode05_activation", lambda _text: True)
    monkeypatch.setattr(dispatch, "is_ifdev05_activation", lambda _text: False)
    monkeypatch.setattr(dispatch, "is_vs05_activation", lambda _text: False)
    monkeypatch.setattr(dispatch, "operator_pack_root", lambda: pack)
    monkeypatch.setattr(dispatch, "mo_home", lambda: home)

    agent = object.__new__(Agent)
    agent.allowed_roots = [str(project)]
    agent.config = {}

    command = f"python {pack / 'mo_trace.py'} list"
    roots = agent._effective_allowed_roots_for_tool("start DEVMODE05", "shell", {"command": command})

    assert str(pack.resolve()) in roots
    assert str(records.resolve()) in roots
    assert guard_tool_call("shell", {"command": command}, allowed_roots=roots) is None
