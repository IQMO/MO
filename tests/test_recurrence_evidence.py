"""Factual recurrence evidence.

Pure facts from git history: which files/areas were patched repeatedly. The parsers are
tested directly on synthetic git output (deterministic, no temp repo); the public build
is tested for shape + graceful degradation."""
from core.recurrence_evidence import (
    build_recurrence_evidence,
    render_recurrence_evidence,
    _commit_file_blocks,
    _repeat_patched_files,
    _recurring_scopes,
)


def test_commit_file_blocks_splits_on_unit_separator():
    # `git log --name-only --pretty=format:%x1f` => \x1f header then files per commit.
    raw = "\x1f\ncore/a.py\ncore/b.py\n\x1f\ncore/a.py\n"
    blocks = _commit_file_blocks(raw)
    assert blocks == [["core/a.py", "core/b.py"], ["core/a.py"]]


def test_repeat_patched_counts_distinct_commits():
    blocks = [
        ["core/final_gates.py", "core/x.py"],
        ["core/final_gates.py"],
        ["core/final_gates.py", "core/y.py"],
        ["core/once.py"],
    ]
    out = _repeat_patched_files(blocks, min_commits=2)
    assert out[0] == {"path": "core/final_gates.py", "commits": 3}  # in 3 commits
    paths = {r["path"] for r in out}
    assert "core/once.py" not in paths  # only 1 commit -> below threshold


def test_repeat_patched_same_file_twice_in_one_commit_counts_once():
    # Distinct-commit semantics: a file listed twice in the SAME commit is one hit.
    blocks = [["core/a.py", "core/a.py"]]
    assert _repeat_patched_files(blocks, min_commits=1) == [{"path": "core/a.py", "commits": 1}]


def test_recurring_scopes_extracts_conventional_scope():
    subjects = [
        "fix(devmode): one",
        "fix(devmode): two",
        "feat(devmode): three",
        "fix(sandbox): a",
        "refactor: no scope here",
    ]
    out = _recurring_scopes(subjects, min_count=2)
    scopes = {r["scope"]: r["count"] for r in out}
    assert scopes.get("devmode") == 3      # 3 commits across fix+feat, same area
    assert "sandbox" not in scopes         # only 1 -> below threshold
    assert "refactor" not in scopes        # bare type with no recurrence


def test_recurring_scopes_bang_and_bare_type():
    # 'fix!: x' (breaking, no scope) falls back to the type 'fix'.
    subjects = ["fix!: a", "fix: b"]
    out = _recurring_scopes(subjects, min_count=2)
    assert {"scope": "fix", "count": 2} in out


def test_build_returns_shape_on_real_repo():
    ev = build_recurrence_evidence(".")
    assert set(ev) == {"window", "available", "repeat_patched", "recurring_scopes"}
    assert ev["available"] is True
    assert isinstance(ev["repeat_patched"], list)
    assert isinstance(ev["recurring_scopes"], list)


def test_build_degrades_when_not_a_repo(tmp_path):
    ev = build_recurrence_evidence(str(tmp_path))
    assert ev["available"] is False
    assert ev["repeat_patched"] == [] and ev["recurring_scopes"] == []


def test_render_unavailable_history_is_not_clean():
    out = render_recurrence_evidence({"window": 20, "available": False, "repeat_patched": [], "recurring_scopes": []})
    assert "unavailable" in out
    assert "Clean history" not in out


def test_render_clean_history():
    out = render_recurrence_evidence({"window": 20, "repeat_patched": [], "recurring_scopes": []})
    assert "No recurrence detected" in out


def test_render_lists_signals():
    ev = {
        "window": 20,
        "repeat_patched": [{"path": "core/final_gates.py", "commits": 4}],
        "recurring_scopes": [{"scope": "iam05", "count": 3}],
    }
    out = render_recurrence_evidence(ev)
    assert "core/final_gates.py: 4 commits" in out
    assert "iam05: 3 commits" in out
    assert "not WHY" in out  # the facts-not-diagnosis guardrail rides along
