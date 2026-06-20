"""A1 — independent read-family tool calls execute concurrently before the
serial dispatch loop, while gating/ordering stay the loop's authority.

Verifies: real concurrency (barrier), index→result mapping, <2-read serial
fallback, non-read exclusion, and that gate-blocked reads never execute.
"""
import threading

import pytest

from core.agent.agent import Agent


def _agent(dispatch, *, block=lambda ui, name, args: None):
    a = object.__new__(Agent)
    a._active_lane = None
    a.sandbox_config = {}
    a._parsed_tool_arguments = lambda tc: dict(tc["function"].get("args") or {})
    a._project_scoped_tool_arguments = lambda name, args: args
    a._operator_approved = lambda ui, name, args: False
    a._effective_allowed_roots_for_tool = lambda ui, name, args: None
    a._self_mutation_block_reason = block
    a._dispatch_tool = dispatch
    return a


def _tc(i, name, **args):
    return {"id": f"c{i}", "function": {"name": name, "args": args}}


@pytest.fixture(autouse=True)
def _permissive_sandbox(monkeypatch):
    # Sandbox gate has its own tests; here we exercise the prefetch orchestration.
    monkeypatch.setattr("core.agent.agent_turn_dispatch.guard_tool_call", lambda *a, **k: "")


def test_prefetch_runs_reads_concurrently():
    # Barrier(3) only releases if all three dispatches are in flight at once.
    # Serial execution would block the first wait() until timeout -> failure.
    barrier = threading.Barrier(3, timeout=5)

    def dispatch(name, args):
        barrier.wait()
        return f"R:{args['p']}"

    a = _agent(dispatch)
    tcs = [_tc(0, "read_file", p="a"), _tc(1, "grep", p="b"), _tc(2, "find_files", p="c")]
    out = a._prefetch_read_family_results(tcs, "read several files")
    assert out == {0: "R:a", 1: "R:b", 2: "R:c"}


def test_prefetch_preserves_index_mapping_with_interleaved_non_reads():
    def dispatch(name, args):
        return f"R:{args['p']}"

    a = _agent(dispatch)
    tcs = [
        _tc(0, "read_file", p="a"),
        _tc(1, "shell", command="ls"),   # not read-family
        _tc(2, "grep", p="c"),
        _tc(3, "edit_file", path="x"),   # not read-family
    ]
    out = a._prefetch_read_family_results(tcs, "mixed")
    # Only the read indices are prefetched, mapped to their original positions.
    assert out == {0: "R:a", 2: "R:c"}


def test_single_read_falls_back_to_serial():
    calls = []
    a = _agent(lambda name, args: calls.append(args) or "x")
    out = a._prefetch_read_family_results([_tc(0, "read_file", p="a")], "one read")
    assert out == {}
    assert calls == []  # nothing pre-executed; serial loop will handle it


def test_one_read_plus_one_nonread_falls_back():
    a = _agent(lambda name, args: "x")
    tcs = [_tc(0, "read_file", p="a"), _tc(1, "shell", command="ls")]
    assert a._prefetch_read_family_results(tcs, "one read one shell") == {}


def test_gate_blocked_read_never_executes():
    executed = []

    def dispatch(name, args):
        executed.append(args["p"])
        return f"R:{args['p']}"

    # Block any read whose path is "secret".
    def block(ui, name, args):
        return "blocked" if args.get("p") == "secret" else None

    a = _agent(dispatch, block=block)
    tcs = [_tc(0, "read_file", p="ok1"), _tc(1, "read_file", p="secret"), _tc(2, "read_file", p="ok2")]
    out = a._prefetch_read_family_results(tcs, "reads")
    assert set(out) == {0, 2}                # blocked index absent
    assert "secret" not in executed          # blocked read was never run
    assert sorted(executed) == ["ok1", "ok2"]
