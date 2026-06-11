
import pytest

from core.agent.agent import Agent
from core.state_migration import (
    MigrationApprovalError,
    apply_state_migration,
    parse_migration_request,
    plan_state_migration,
    render_state_migration_report,
)


def test_state_migration_dry_run_reports_without_writing_or_secret_values(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    (source / "memory" / "unreachable").mkdir(parents=True)
    (source / "logs").mkdir(parents=True)
    (source / "memory" / "mo.db").write_text("profile-data", encoding="utf-8")
    (source / "memory" / "unreachable" / "secrets.env").write_text("TOKEN=super-secret-value", encoding="utf-8")
    (source / "logs" / "tool_audit.jsonl").write_text("{}\n", encoding="utf-8")

    plan = plan_state_migration(source_root=source, home=home)
    text = render_state_migration_report(plan)

    assert len(plan.planned_files) == 3
    assert "memory/mo.db" in text
    assert "memory/unreachable/secrets.env" in text
    assert "super-secret-value" not in text
    assert "No changes made" in text
    assert not home.exists()


def test_state_migration_apply_copies_missing_files_and_preserves_source(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "mo.db").write_text("legacy", encoding="utf-8")

    plan = plan_state_migration(source_root=source, home=home)
    with pytest.raises(MigrationApprovalError):
        apply_state_migration(plan)

    result = apply_state_migration(plan, confirm=True)

    assert result.copied == ["memory/mo.db"]
    assert (home / "memory" / "mo.db").read_text(encoding="utf-8") == "legacy"
    assert (source / "memory" / "mo.db").exists()


def test_state_migration_never_overwrites_existing_destination(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    (source / "memory").mkdir(parents=True)
    (home / "memory").mkdir(parents=True)
    (source / "memory" / "mo.db").write_text("legacy", encoding="utf-8")
    (home / "memory" / "mo.db").write_text("private", encoding="utf-8")

    plan = plan_state_migration(source_root=source, home=home)
    result = apply_state_migration(plan, confirm=True)

    assert [item.rel_path for item in plan.conflicts] == ["memory/mo.db"]
    assert result.copied == []
    assert (home / "memory" / "mo.db").read_text(encoding="utf-8") == "private"


def test_state_migration_refuses_nested_private_home(tmp_path):
    source = tmp_path / "checkout"
    home = source / "memory" / "pre_release_evidence" / "temp_home"
    (home / "memory").mkdir(parents=True)
    (home / "memory" / "mo.db").write_text("nested", encoding="utf-8")
    (source / "memory" / "legacy.txt").write_text("legacy", encoding="utf-8")

    plan = plan_state_migration(source_root=source, home=home)
    text = render_state_migration_report(plan)

    assert plan.planned_files == []
    assert "refusing to plan recursive state migration" in text


def test_state_migration_excludes_evidence_and_runtime_cache(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    (source / "memory" / "pre_release_evidence").mkdir(parents=True)
    (source / "memory" / "pre_release_evidence" / "artifact.txt").write_text("artifact", encoding="utf-8")
    (source / "memory" / "cache").mkdir(parents=True)
    (source / "memory" / "cache" / "cache.txt").write_text("cache", encoding="utf-8")
    (source / "memory" / "mo.db").write_text("legacy", encoding="utf-8")

    plan = plan_state_migration(source_root=source, home=home)

    assert [item.rel_path for item in plan.planned_files] == ["memory/mo.db"]


def test_state_migration_move_removes_only_copied_legacy_files(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "mo.db").write_text("legacy", encoding="utf-8")

    plan = plan_state_migration(source_root=source, home=home)
    result = apply_state_migration(plan, confirm=True, remove_source=True)

    assert result.copied == ["memory/mo.db"]
    assert result.removed_sources == ["memory/mo.db"]
    assert (home / "memory" / "mo.db").exists()
    assert not (source / "memory" / "mo.db").exists()
    assert not (source / "memory").exists()


def test_agent_migrate_command_dry_run_and_confirmed_apply(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "mo.db").write_text("legacy", encoding="utf-8")
    agent = Agent.__new__(Agent)
    agent.agent_root = str(source)
    agent.runtime_home = str(home)

    dry = agent._cmd_migrate("")
    no_confirm = agent._cmd_migrate("apply")
    applied = agent._cmd_migrate("apply --confirm")

    assert "MO state migration dry-run" in dry
    assert "No changes made" in dry
    assert "Apply not run" in no_confirm
    assert "MO state migration result" in applied
    assert (home / "memory" / "mo.db").read_text(encoding="utf-8") == "legacy"


def test_parse_migration_request():
    assert parse_migration_request("") == ("dry-run", False)
    assert parse_migration_request("apply --confirm") == ("apply", True)
    assert parse_migration_request(["move", "--confirm"]) == ("move", True)
