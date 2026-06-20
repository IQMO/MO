"""A2 — per-turn changed-file verify-and-self-heal gate.

Tests the decision logic of `_affected_test_failure_instruction`:
- doc-only turns never run tests
- failing affected tests -> self-heal instruction
- passing / no affected tests / empty diff -> None
- fail-open: any error -> None (never blocks a turn)

The real affected-test runner (PRT) and git are stubbed so no pytest spawns.
"""
import subprocess
from types import SimpleNamespace

import core.graph.code_graph as code_graph
import core.path_defaults as path_defaults
import core.review.diff_review as diff_review
from core.agent.agent import Agent


def _agent():
    return object.__new__(Agent)


def _stub_common(monkeypatch, *, diff="diff --git a/core/x.py b/core/x.py\n+code"):
    monkeypatch.setattr(path_defaults, "repo_root", lambda *a, **k: ".")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=diff, stderr="", returncode=0))


def test_doc_only_turn_never_runs_tests(monkeypatch):
    ran = []
    monkeypatch.setattr(diff_review, "_run_affected_tests", lambda *a, **k: ran.append(1) or ([], {}))
    a = _agent()
    assert a._affected_test_failure_instruction([("README.md", "x"), ("docs/n.md", "y")]) is None
    assert ran == []


def test_failing_affected_tests_return_self_heal_instruction(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(code_graph, "affected_tests", lambda diff, root: ["tests/test_x.py"])
    finding = SimpleNamespace(explanation="pytest exited non-zero\nFAILED tests/test_x.py::test_a", message="Affected tests failed")
    monkeypatch.setattr(diff_review, "_run_affected_tests", lambda agent, tests, root: ([finding], {"failed": True}))
    a = _agent()
    instr = a._affected_test_failure_instruction([("core/x.py", "code")])
    assert instr is not None
    assert "[VERIFY]" in instr and "FAILING" in instr
    assert "FAILED tests/test_x.py::test_a" in instr


def test_passing_affected_tests_return_none(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(code_graph, "affected_tests", lambda diff, root: ["tests/test_x.py"])
    monkeypatch.setattr(diff_review, "_run_affected_tests", lambda agent, tests, root: ([], {"returncode": 0}))
    a = _agent()
    assert a._affected_test_failure_instruction([("core/x.py", "code")]) is None


def test_no_affected_tests_return_none(monkeypatch):
    _stub_common(monkeypatch)
    monkeypatch.setattr(code_graph, "affected_tests", lambda diff, root: [])
    a = _agent()
    assert a._affected_test_failure_instruction([("core/x.py", "code")]) is None


def test_empty_diff_return_none(monkeypatch):
    _stub_common(monkeypatch, diff="   ")
    a = _agent()
    assert a._affected_test_failure_instruction([("core/x.py", "code")]) is None


def test_fail_open_on_exception(monkeypatch):
    monkeypatch.setattr(path_defaults, "repo_root", lambda *a, **k: ".")

    def boom(*a, **k):
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(subprocess, "run", boom)
    a = _agent()
    # Must swallow and return None — never block a turn on the verifier's own error.
    assert a._affected_test_failure_instruction([("core/x.py", "code")]) is None
