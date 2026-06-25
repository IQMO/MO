import json
import sqlite3

from core.system_health import (
    SystemHealth,
    build_health_report,
    check_config_coverage,
    check_file_health,
)


def test_build_health_report_imports_and_returns_sections(tmp_path):
    report = build_health_report(str(tmp_path))

    assert isinstance(report, SystemHealth)
    assert set(report.files)
    assert "structural" in report.graph
    assert "profile_learning" in report.learning
    assert "MO_GHOST_AUDIT_MAX_BYTES" in report.config


def test_health_feedback_bridge_reflects_recorded_feedback(tmp_path):
    """The review->patterns bridge is live truth, not a hardcoded False."""
    assert build_health_report(str(tmp_path)).learning["bridges"]["feedback_to_finding_patterns"] is False

    pdir = tmp_path / "memory" / "review_history"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "patterns.json").write_text(
        json.dumps({"operator_preferences": {"bug_risk": {"fixed": 2}}}), encoding="utf-8"
    )
    assert build_health_report(str(tmp_path)).learning["bridges"]["feedback_to_finding_patterns"] is True


def test_file_health_reports_sizes_lines_and_missing_files(tmp_path):
    audit = tmp_path / "logs" / "ghost_audit.jsonl"
    audit.parent.mkdir(parents=True)
    audit.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")

    files = check_file_health(str(tmp_path))

    assert files["logs/ghost_audit.jsonl"]["exists"] is True
    assert files["logs/ghost_audit.jsonl"]["lines"] == 2
    assert files["logs/ghost_audit.jsonl"]["entries"] == 2
    assert files["memory/workflow_promoted.jsonl"]["status"] == "missing"


def test_file_health_uses_entry_counts_for_profile_markdown(tmp_path):
    profile_dir = tmp_path / "memory" / "profile"
    profile_dir.mkdir(parents=True)
    body = "# Operator Learning\n" + "\n".join(
        f"## 2026-06-01T00:00:{index:02d}Z — profile learning\n- core_traits: x\n- current_focus: y"
        for index in range(3)
    )
    (profile_dir / "learning.md").write_text(body, encoding="utf-8")

    files = check_file_health(str(tmp_path))

    assert files["memory/profile/learning.md"]["entries"] == 3
    assert files["memory/profile/learning.md"]["status"] == "ok"


def test_health_report_reads_graph_learning_and_sqlite(tmp_path):
    graph_dir = tmp_path / "memory" / "structural_graph"
    graph_dir.mkdir(parents=True)
    (graph_dir / "graph.json").write_text(
        json.dumps(
            {
                "version": "test",
                "nodes": [{"id": "file:a.py", "label": "a.py", "community": 1}],
                "links": [{"source": "file:a.py", "target": "file:b.py"}],
            }
        ),
        encoding="utf-8",
    )
    profile_dir = tmp_path / "memory" / "profile"
    profile_dir.mkdir(parents=True)
    (profile_dir / "learning.md").write_text(
        "## 2026-06-01T00:00:00Z — profile learning\n- core_traits: verify\n",
        encoding="utf-8",
    )
    skill = tmp_path / "skills" / "verify-first" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: \"Verify first\"\ndescription: \"generated\"\ntriggers:\n  - \"verify\"\ncandidate_id: \"learning-suggestion:x\"\n---\nbody\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "memory" / "learning.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO messages DEFAULT VALUES")

    report = build_health_report(str(tmp_path))

    assert report.graph["structural"]["nodes"] == 1
    assert report.learning["profile_learning"]["entries"] == 1
    assert report.learning["skills"]["packs"] == 1
    assert report.learning["skills"]["generated"] == 1
    assert report.learning["memory"]["turns"] == 1


def test_config_coverage_marks_set_values(monkeypatch):
    monkeypatch.setenv("MO_GHOST_AUDIT_MAX_BYTES", "123")

    config = check_config_coverage()

    assert config["MO_GHOST_AUDIT_MAX_BYTES"]["set"] is True
    assert config["MO_GHOST_AUDIT_MAX_BYTES"]["value"] == "123"
