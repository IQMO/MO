
from core.initializer import initialize_mo, render_init_report
from core.profile import Profile


def test_initialize_mo_creates_private_home_without_project_writes(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Rules\n- Verify.\n", encoding="utf-8")

    report = initialize_mo(home=home, project_path=project)
    text = render_init_report(report)

    assert (home / "config.yaml").exists()
    assert f'home: "{str(home).replace(chr(92), "/")}"' in (home / "config.yaml").read_text(encoding="utf-8")
    assert (home / ".env").exists()
    assert (home / "bin" / "mo").exists()
    assert (home / "bin" / "mo.cmd").exists()
    assert (home / "memory" / "mo.db").exists()
    assert (home / "memory" / "profile" / "operator.md").exists()
    assert (home / "memory" / "sessions").is_dir()
    assert report.project_context_files == [project / "AGENTS.md"]
    assert "MO_PROJECT_CWD" in (home / "bin" / "mo").read_text(encoding="utf-8")
    assert "MO_PROJECT_CWD" in (home / "bin" / "mo.cmd").read_text(encoding="utf-8")
    assert "private home" in text
    assert "provider env:" in text
    assert not (project / "memory").exists()
    assert not (project / ".mo").exists()
    assert not (project / "logs").exists()


def test_initialize_mo_is_idempotent_and_preserves_private_profile(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    first = initialize_mo(home=home, project_path=project)
    profile = Profile.load(str(home / "memory" / "mo.db"))
    profile.user_name = "Private User"
    profile.save()
    config_text = (home / "config.yaml").read_text(encoding="utf-8") + "# custom\n"
    (home / "config.yaml").write_text(config_text, encoding="utf-8")

    second = initialize_mo(home=home, project_path=project)

    assert first.created
    assert "config.yaml" in second.existing
    assert "bin/mo" in second.existing
    assert "bin/mo.cmd" in second.existing
    assert (home / "config.yaml").read_text(encoding="utf-8") == config_text
    assert Profile.load(str(home / "memory" / "mo.db")).user_name == "Private User"


def test_render_init_report_never_prints_secret_values(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("OPENCODE_API_KEY", "secret-value-should-not-render")

    text = render_init_report(initialize_mo(home=home, project_path=project))

    assert "secret-value-should-not-render" not in text
    assert "OPENCODE_API_KEY" in text
