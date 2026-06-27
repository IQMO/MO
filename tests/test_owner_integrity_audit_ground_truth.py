"""OWNER_INTEGRITY_AUDIT live-measured audit ground truth.

The runtime hands the model measured numbers before it writes findings, so quantitative /
exhaustiveness / duplication claims start from disk instead of memory (the trust fix from
session 5361b54d). These tests pin: non-OWNER_INTEGRITY_AUDIT turns get nothing; explicit targets are
measured exactly (real line count, ast function spans, symbol reference split); a bare
'Run OWNER_INTEGRITY_AUDIT' auto-scopes from the live tree; and everything degrades gracefully."""

import pytest

from core.self_maintenance.owner_integrity_audit_ground_truth import (
    build_owner_integrity_audit_ground_truth,
    normalize_owner_integrity_audit_report_text,
    owner_integrity_audit_function_span_index,
    reconcile_latest_owner_integrity_audit_report,
    owner_integrity_audit_source_corpus_count,
    _named_paths,
    _named_symbols,
    _measure_file,
    _measure_symbol,
)


@pytest.fixture
def tree(tmp_path):
    """A tiny fake repo under the recognized source dirs."""
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


def test_non_owner_integrity_audit_returns_empty(tree):
    assert build_owner_integrity_audit_ground_truth("fix the login bug", cwd=str(tree)) == ""
    assert build_owner_integrity_audit_ground_truth("start OWNER_MAINTENANCE", cwd=str(tree)) == ""


def test_named_path_is_measured(tree):
    paths = _named_paths("Run OWNER_INTEGRITY_AUDIT on core/thing.py", tree)
    assert paths == ["core/thing.py"]
    # A path that does not exist on disk is dropped (not hallucinated into the report).
    assert _named_paths("Run OWNER_INTEGRITY_AUDIT on core/ghost.py", tree) == []


def test_measure_file_reports_real_count_and_spans(tree):
    out = "\n".join(_measure_file(tree, "core/thing.py"))
    assert "core/thing.py: 24 lines (file)" in out
    # run_turn is the largest function and its span is measured via ast, not guessed.
    assert "run_turn() spans :1-:21 (21 lines)" in out


def test_named_symbol_resolves_only_real_defs(tree):
    # run_turn is defined; 'cluster' is an English word, not a symbol -> excluded.
    syms = _named_symbols("Run OWNER_INTEGRITY_AUDIT auditing run_turn in the cluster", tree)
    assert "run_turn" in syms
    assert "cluster" not in syms


def test_measure_symbol_splits_test_vs_nontest(tree):
    out = "\n".join(_measure_symbol(tree, "run_turn"))
    # Defined in core/thing.py and core/other.py; referenced in both + the test file.
    assert "core/thing.py:1" in out and "core/other.py:1" in out
    assert "referenced in 3 files (1 test, 2 non-test)" in out


def test_explicit_target_block_shape(tree):
    block = build_owner_integrity_audit_ground_truth("Run OWNER_INTEGRITY_AUDIT on core/thing.py and run_turn", cwd=str(tree))
    assert "Audit Ground Truth (live-measured" in block
    assert "Targets named in the request:" in block
    assert "core/thing.py" in block
    assert "symbol `run_turn`" in block
    assert "Gate 7" in block  # the refutation rule rides along


def test_bare_run_owner_integrity_audit_autoscopes_from_tree(tree):
    block = build_owner_integrity_audit_ground_truth("Run OWNER_INTEGRITY_AUDIT", cwd=str(tree))
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
    block = build_owner_integrity_audit_ground_truth("Run OWNER_INTEGRITY_AUDIT", cwd=str(tree))
    assert "git unavailable" in block or "none in last" in block


def test_unparseable_file_does_not_crash(tree):
    (tree / "core" / "broken.py").write_text("def (((:\n  not python", encoding="utf-8")
    block = build_owner_integrity_audit_ground_truth("Run OWNER_INTEGRITY_AUDIT", cwd=str(tree))
    assert block  # still produced; the broken file is skipped, not fatal


def test_reporting_contract_present_in_both_modes(tree):
    # The honesty contract rides on every OWNER_INTEGRITY_AUDIT turn — bare and targeted.
    for prompt in ("start OWNER_INTEGRITY_AUDIT", "Run OWNER_INTEGRITY_AUDIT on core/thing.py"):
        block = build_owner_integrity_audit_ground_truth(prompt, cwd=str(tree))
        assert "OWNER_INTEGRITY_AUDIT Reporting Contract" in block
        assert "Scope honesty" in block and "sampled N of" in block
        assert "Self-report truth" in block and "tool-error count" in block
        assert "Ledger location" in block


def test_contract_scope_denominator_is_real_corpus_count(tree):
    # 'sampled N of <count>' must use the actual file count, not a guess.
    block = build_owner_integrity_audit_ground_truth("start OWNER_INTEGRITY_AUDIT", cwd=str(tree))
    # tree has core/thing.py, core/other.py, tests/test_thing.py = 3 source files
    assert "sampled N of 3" in block
    assert owner_integrity_audit_source_corpus_count(cwd=str(tree)) == 3


def test_contract_ledger_path_is_private_home_not_repo(tree):
    block = build_owner_integrity_audit_ground_truth("start OWNER_INTEGRITY_AUDIT", cwd=str(tree))
    assert "memory/owner_integrity_audit" in block               # canonical ~/.mo private location
    assert "NEVER repo-local `memory/`" in block  # the exact violation this run made
    assert "session-unique filename" in block
    assert "never a date-only `evidence_ledger_YYYYMMDD.md`" in block


def test_runtime_truth_normalizes_model_authored_counts():
    text = (
        "# Report\n"
        "- **Tool calls:** 77 (0 errors)\n"
        "_Tool calls: 77. Tool errors: 0. Sampled 38 of 370._\n"
    )

    out = normalize_owner_integrity_audit_report_text(text, tool_calls=79, tool_errors=1, corpus=370)

    assert "**Tool calls:** 79" in out
    assert "(1 error)" in out
    assert "_Tool calls: 79. Tool errors: 1." in out
    assert "### Runtime Truth (authoritative)" in out
    assert "- Tool calls: 79" in out
    assert "- Tool errors: 1" in out


def test_runtime_truth_reconciles_latest_owner_integrity_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    report_dir = tmp_path / "memory" / "owner_integrity_audit"
    report_dir.mkdir(parents=True)
    old = report_dir / "old.md"
    latest = report_dir / "latest.md"
    old.write_text("Tool calls: 1\n", encoding="utf-8")
    latest.write_text("Tool calls: 77\nTool errors: 0\n", encoding="utf-8")

    reconciled = reconcile_latest_owner_integrity_audit_report(tool_calls=79, tool_errors=0, corpus=370)

    assert reconciled == latest
    assert "Tool calls: 79" in latest.read_text(encoding="utf-8")
    assert "Source corpus: 370" in latest.read_text(encoding="utf-8")


def test_runtime_truth_reconciles_cited_artifact_not_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    report_dir = tmp_path / "memory" / "owner_integrity_audit"
    report_dir.mkdir(parents=True)
    cited = report_dir / "evidence_ledger_20260624T213000.md"
    newest = report_dir / "newer.md"
    cited.write_text("Tool calls: 77\nTool errors: 0\n", encoding="utf-8")
    newest.write_text("Tool calls: 1\n", encoding="utf-8")
    cited_time = 100
    newest_time = 200
    import os
    os.utime(cited, (cited_time, cited_time))
    os.utime(newest, (newest_time, newest_time))

    reconciled = reconcile_latest_owner_integrity_audit_report(
        tool_calls=79,
        tool_errors=0,
        corpus=370,
        report_text="Ledger: ~/.mo/memory/owner_integrity_audit/evidence_ledger_20260624T213000.md",
    )

    assert reconciled == cited
    assert "Tool calls: 79" in cited.read_text(encoding="utf-8")
    assert newest.read_text(encoding="utf-8") == "Tool calls: 1\n"


def test_function_span_index_includes_qualified_methods(tree):
    (tree / "core" / "agent.py").write_text(
        "class Agent:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "        self.y = 2\n",
        encoding="utf-8",
    )
    (tree / "tests" / "test_agent.py").write_text(
        "class Agent:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "\ndef run_turn():\n"
        "    return None\n",
        encoding="utf-8",
    )
    spans = owner_integrity_audit_function_span_index(cwd=str(tree))
    assert spans["Agent.__init__"] == {3}
    assert spans["__init__"] == {3}
    assert spans["run_turn"] == set()  # ambiguous bare name: core/thing.py + core/other.py
