"""Tests for core/learning/learning_bundle.py — cross-instance learning portability."""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.learning.learning_bundle import export_learning_bundle, import_learning_bundle


def _profile(tmp_path):
    memory = tmp_path / "memory"
    (memory / "profile").mkdir(parents=True)
    return SimpleNamespace(_path=str(memory / "mo.db"))


def _seed_learning(tmp_path):
    memory = tmp_path / "memory"
    (memory / "profile" / "operator.md").write_text("# Operator Profile — Test\n- prefers brevity\n", encoding="utf-8")
    (memory / "learning_suggestions.jsonl").write_text(
        json.dumps({"id": "s1", "kind": "evidence_first", "recommendation": "Verify first.", "evidence": [], "status": "confirmed", "created_at": 1.0}) + "\n"
        + json.dumps({"id": "s2", "kind": "closeout:x", "recommendation": "Pending thing.", "evidence": [], "status": "suggested", "created_at": 2.0}) + "\n",
        encoding="utf-8",
    )
    (memory / "workflow_promoted.jsonl").write_text(
        json.dumps({"id": "w1", "trigger": "build turns", "behavior": "verify with tests", "status": "promoted"}) + "\n",
        encoding="utf-8",
    )


def test_export_includes_confirmed_only_and_counts(tmp_path):
    profile = _profile(tmp_path)
    _seed_learning(tmp_path)

    result = export_learning_bundle(profile)

    assert result["exported"] is True
    bundle = json.loads((tmp_path / "memory" / "exports").glob("*.json").__next__().read_text(encoding="utf-8"))
    assert [r["id"] for r in bundle["confirmed_suggestions"]] == ["s1"]  # pending s2 excluded
    assert [r["id"] for r in bundle["promoted_workflows"]] == ["w1"]
    assert "operator.md" in bundle["profile_files"]
    assert result["counts"]["confirmed_suggestions"] == 1


def test_export_refuses_secret_bearing_content(tmp_path):
    profile = _profile(tmp_path)
    (tmp_path / "memory" / "profile" / "operator.md").write_text('api_key = "sk-supersecret12345"\n', encoding="utf-8")

    result = export_learning_bundle(profile)

    assert result["exported"] is False
    assert "secret" in result["reason"]
    assert not (tmp_path / "memory" / "exports").exists()


def test_import_dry_run_then_confirm_appends_without_overwrite(tmp_path):
    source = _profile(tmp_path / "source")
    _seed_learning(tmp_path / "source")
    exported = export_learning_bundle(source)

    target_root = tmp_path / "target"
    target = _profile(target_root)
    (target_root / "memory" / "learning_suggestions.jsonl").write_text(
        json.dumps({"id": "local1", "kind": "scope_control", "recommendation": "Local rule.", "evidence": [], "status": "confirmed", "created_at": 3.0}) + "\n",
        encoding="utf-8",
    )

    dry = import_learning_bundle(target, exported["path"])
    assert dry["dry_run"] is True and dry["imported"] is False
    assert dry["new_confirmed_suggestions"] == 1
    # dry-run wrote nothing
    assert "s1" not in (target_root / "memory" / "learning_suggestions.jsonl").read_text(encoding="utf-8")

    applied = import_learning_bundle(target, exported["path"], confirm=True)
    assert applied["imported"] is True
    content = (target_root / "memory" / "learning_suggestions.jsonl").read_text(encoding="utf-8")
    assert "local1" in content and "s1" in content  # append-only, local survives
    assert "operator.md" in applied["profile_files_for_review"]
    assert applied["review_dir"]  # prose staged for manual review, never auto-merged

    # idempotent: re-import adds nothing new
    again = import_learning_bundle(target, exported["path"], confirm=True)
    assert again["new_confirmed_suggestions"] == 0


def test_import_rejects_missing_or_wrong_version(tmp_path):
    profile = _profile(tmp_path)
    assert import_learning_bundle(profile, tmp_path / "missing.json")["imported"] is False
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": "other-v9"}), encoding="utf-8")
    assert "unsupported bundle version" in import_learning_bundle(profile, bad)["reason"]
