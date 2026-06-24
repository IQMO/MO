"""IAM05 live-measured audit ground truth.

The runtime hands the model measured numbers before it writes findings, so quantitative /
exhaustiveness / duplication claims start from disk instead of memory (the trust fix from
session 5361b54d). These tests pin: non-IAM05 turns get nothing; explicit targets are
measured exactly (real line count, ast function spans, symbol reference split); a bare
'Run IAM05' auto-scopes from the live tree; and everything degrades gracefully."""
import os

import pytest

from core.self_maintenance.iam05_ground_truth import (
    build_iam05_ground_truth,
    _named_paths,
    _named_symbols,
    _measure_file,
    _measure_symbol,
)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A tiny fake repo under the recognized source dirs."""
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "thing.py").write_text(
        "def run_turn():\n"
        + "\n".join(f"    x = {i}" for i in range(20))
        + "\n\ndef small():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "other.py").write_text(
        "def run_turn():\n    return 2\n\ndef helper():\n    return 3\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_thing.py").write_text(
        "from core.thing import run_turn\n\ndef test_run_turn():\n    run_turn()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_non_iam05_returns_empty(tree):
    assert build_iam05_ground_truth("fix the login bug", cwd=str(tree)) == ""
    assert build_iam05_ground_truth("start DEVMODE05", cwd=str(tree)) == ""


def test_named_path_is_measured(tree):
    paths = _named_paths("Run IAM05 on core/thing.py", tree)
    assert paths == ["core/thing.py"]
    # A path that does not exist on disk is dropped (not hallucinated into the report).
    assert _named_paths("Run IAM05 on core/ghost.py", tree) == []


def test_measure_file_reports_real_count_and_spans(tree):
    out = "\n".join(_measure_file(tree, "core/thing.py"))
    assert "core/thing.py: 24 lines (file)" in out
    # run_turn is the largest function and its span is measured via ast, not guessed.
    assert "run_turn() spans :1-:21 (21 lines)" in out


def test_named_symbol_resolves_only_real_defs(tree):
    # run_turn is defined; 'cluster' is an English word, not a symbol -> excluded.
    syms = _named_symbols("Run IAM05 auditing run_turn in the cluster", tree)
    assert "run_turn" in syms
    assert "cluster" not in syms


def test_measure_symbol_splits_test_vs_nontest(tree):
    out = "\n".join(_measure_symbol(tree, "run_turn"))
    # Defined in core/thing.py and core/other.py; referenced in both + the test file.
    assert "core/thing.py:1" in out and "core/other.py:1" in out
    assert "referenced in 3 files (1 test, 2 non-test)" in out


def test_explicit_target_block_shape(tree):
    block = build_iam05_ground_truth("Run IAM05 on core/thing.py and run_turn", cwd=str(tree))
    assert "Audit Ground Truth (live-measured" in block
    assert "Targets named in the request:" in block
    assert "core/thing.py" in block
    assert "symbol `run_turn`" in block
    assert "Gate 7" in block  # the refutation rule rides along


def test_bare_run_iam05_autoscopes_from_tree(tree):
    block = build_iam05_ground_truth("Run IAM05", cwd=str(tree))
    assert "AUDIT QUEUE, not a menu" in block          # work order, not a "pick one" prompt
    assert "Do NOT ask the operator which to pick" in block
    assert "Largest files" in block
    assert "Largest functions" in block
    assert "Churn hotspots" in block          # present even when git is absent
    assert "Duplication candidates" in block
    # run_turn is defined in two files -> a real duplication candidate surfaces.
    assert "run_turn" in block.split("Duplication candidates")[1]


def test_bare_mode_degrades_without_git(tree):
    # No .git in tmp_path: churn falls back to a clean 'unavailable' line, never raises.
    block = build_iam05_ground_truth("Run IAM05", cwd=str(tree))
    assert "git unavailable" in block or "none in last" in block


def test_unparseable_file_does_not_crash(tree):
    (tree / "core" / "broken.py").write_text("def (((:\n  not python", encoding="utf-8")
    block = build_iam05_ground_truth("Run IAM05", cwd=str(tree))
    assert block  # still produced; the broken file is skipped, not fatal
