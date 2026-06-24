from pathlib import Path

import pytest


def _require_private_docs() -> None:
    if not Path("docs/README.md").exists():
        pytest.skip("private docs tree is not present in this checkout")


def test_root_has_one_public_config_template():
    assert Path("config.example.yaml").is_file()
    assert not Path("config.mock.yaml").exists()
    assert not Path("config.yaml").exists()


def test_workspace_root_has_no_private_runtime_or_external_tooling_content():
    forbidden_dirs = (
        "memory",
        "logs",
        "." + "omx",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "operator",
        ".mo",
    )

    present = [name for name in forbidden_dirs if Path(name).exists()]

    assert present == []

    external_runner_dir = Path("." + "agents")
    if external_runner_dir.exists():
        assert external_runner_dir.is_dir()
        assert list(external_runner_dir.iterdir()) == []


def test_public_docs_do_not_reference_external_tooling_markers():
    public_docs = (
        "README.md",
        "MAP.md",
        "AGENTS.md",
        "CLAUDE.md",
        "config.example.yaml",
    )
    forbidden_markers = (
        "." + "agents",
        "." + "omx",
        "oh-my-" + "codex",
        "O" + "MX",
        "profile-" + "build",
    )

    for relative_path in public_docs:
        text = Path(relative_path).read_text(encoding="utf-8")
        for marker in forbidden_markers:
            assert marker not in text, f"{relative_path} contains {marker}"


def test_root_map_stays_compact_and_points_to_authoritative_surfaces():
    path = Path("MAP.md")
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]

    assert len(lines) <= 40
    assert "AGENTS.md" in text
    assert "core/prompts/system.md" in text
    # protocols are owner-only (untracked profile state); the map must say so
    # without advertising the pack's file layout
    assert "owner-only" in text
    assert "core/graph/structural_graph.py" in text
    assert "core/tasking/task_board.py" in text


def test_boundary_docs_do_not_advertise_old_nested_operator_layout():
    stale = "gitignored " + "`operator/`" + " + `docs/` + `~/.mo`"
    for path in (Path("MAP.md"), Path("CLAUDE.md")):
        text = path.read_text(encoding="utf-8")
        assert stale not in text
        assert "~/.mo/operator" in text


def test_companion_voice_docs_separate_capture_from_transcription():
    config = Path("config.example.yaml").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "faster-whisper + sounddevice" not in config
    assert "mic capture uses sounddevice" in config
    assert "transcription requires faster-whisper" in config
    assert "microphone capture uses" in readme
    assert "transcription requires `faster-whisper`" in readme


def test_public_docs_describe_multi_instance_model():
    readme = Path("README.md").read_text(encoding="utf-8")
    map_text = Path("MAP.md").read_text(encoding="utf-8")
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    config = Path("config.example.yaml").read_text(encoding="utf-8")

    for text in (readme, map_text, agents):
        assert "MO_INSTANCE_ID" in text
        assert "main-<instance>" in text
        assert "resource-lock" in text or "resource lock" in text
    assert "shared_session: false" in config
    assert "legacy shared `main` session" in config


def test_current_docs_do_not_import_retired_root_trace_tools():
    _require_private_docs()
    current_paths = [
        Path("MAP.md"),
        Path("docs/README.md"),
        Path("docs/TRACKING.md"),
        Path("docs/status/DOCS-CURRENT-STATUS-AUDIT.md"),
        Path("docs/status/BACKEND-DIAGNOSTICS.md"),
        Path("docs/deployment/DOCKER-READINESS.md"),
        Path("docs/taskboard/TASKBOARD-CURRENT-IMPLEMENTATION-REPORT.md"),
        Path("docs/product/MO.md"),
        Path("docs/product/MO-PRD.md"),
        Path("docs/product/MO-HYGIENE-TASK-EVIDENCE-PLAN.md"),
        Path("docs/interface/INTERFACE-PRODUCTION-READINESS-PLAN.md"),
        Path("docs/status/TELEGRAM-HEARTBEAT-COMPLETE.md"),
    ]

    for path in current_paths:
        text = path.read_text(encoding="utf-8")
        assert "status/TRACE-VALIDATION-COMPLETE" not in text
        assert "../mo_trace.py" not in text
        assert "core/mo_trace.py" not in text
        assert "compileall -q core interface tools mo.py mo_monitor.py" not in text
        if "mo_monitor.py" in text:
            assert "private operator `mo_monitor.py`" in text


def test_private_docs_index_declares_internal_untracked_boundary():
    _require_private_docs()

    docs_readme = Path("docs/README.md").read_text(encoding="utf-8")
    tracking = Path("docs/TRACKING.md").read_text(encoding="utf-8")

    assert "ignored by git" in docs_readme
    assert "Public tracked documentation lives at the repository root" in docs_readme
    assert "ignored by git" in tracking


def test_private_docs_read_order_does_not_duplicate_backend_diagnostics():
    _require_private_docs()

    text = Path("docs/README.md").read_text(encoding="utf-8")

    assert text.count("status/BACKEND-DIAGNOSTICS.md") == 1


def test_e2e_mission_records_are_audit_not_current_status():
    _require_private_docs()
    mission_names = [
        "MISSION_E2E_BEHAVIORAL_COVERAGE.md",
        "MISSION1_LIVE_CHECKLIST.md",
        "MISSION_PREMORTEM_FAULT_TOLERANCE.md",
        "DUMPZONE-MOMENTUM-COMPLETE.md",
    ]

    for name in mission_names:
        assert not (Path("docs/status") / name).exists()
        assert (Path("docs/audit") / name).exists()
