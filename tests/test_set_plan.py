"""Phase 1: MO-owned taskboard via set_plan (flag-gated, default off).

set_plan lets MO replace the board with its own plan and advance rows with
complete_task. Inert unless taskboard.model_owned is enabled, so the current
Ghost/procedure-seeded flow is unchanged by default."""
from core.tasking.task_board import TaskBoard
from core.tasking.agent_taskboard import AgentTaskBoard


class _Agent(AgentTaskBoard):
    def __init__(self, model_owned: bool):
        self.config = {"taskboard": {"model_owned": model_owned}}


def _ghost_board() -> TaskBoard:
    b = TaskBoard(turn_id="t", session_id="s", source="gateway")
    b.set_rows("ghost", [{"id": "1", "text": "ghost row", "status": "active"}])
    return b


def test_set_plan_tool_registered():
    from tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
    assert "set_plan" in TOOL_EXECUTORS
    assert any(d["function"]["name"] == "set_plan" for d in TOOL_DEFINITIONS)


def test_set_plan_is_inert_when_flag_off():
    b = _ghost_board()
    changed = _Agent(model_owned=False)._advance_task_board_after_tool(b, "set_plan", {"tasks": ["mine 1", "mine 2"]})
    assert changed is False
    assert [t.title for t in b.tasks] == ["ghost row"]  # board unchanged


def test_set_plan_owns_board_when_flag_on():
    b = _ghost_board()
    changed = _Agent(model_owned=True)._advance_task_board_after_tool(
        b, "set_plan", {"tasks": ["inspect", {"text": "fix it", "kind": "edit"}, "verify"]}
    )
    assert changed is True
    assert [t.title for t in b.tasks] == ["inspect", "fix it", "verify"]
    assert b.tasks[0].status == "active"
    assert all(t.status == "pending" for t in b.tasks[1:])


def test_set_plan_refuses_to_overwrite_owner_protocol_board():
    # Regression for live mo-1782436480: set_plan clobbered a DEVMODE05 board.
    # A protocol board carries a 'final' closeout gate that drives the protocol's
    # completion contract; set_plan must NOT replace it even with model_owned on —
    # MO advances those rows with complete_task instead.
    b = TaskBoard(turn_id="t", session_id="s", source="gateway")
    b.set_rows("start DEVMODE05", [
        {"id": "1", "text": "Boot protocol", "status": "active", "completion_gate": "tool"},
        {"id": "2", "text": "Final OWNER_MAINTENANCE report", "status": "pending", "completion_gate": "final"},
    ])
    changed = _Agent(model_owned=True)._advance_task_board_after_tool(
        b, "set_plan", {"tasks": ["my", "own", "plan"]}
    )
    assert changed is False
    assert [t.title for t in b.tasks] == ["Boot protocol", "Final OWNER_MAINTENANCE report"]  # untouched


def test_set_plan_no_usable_tasks_is_noop_even_when_on():
    b = _ghost_board()
    changed = _Agent(model_owned=True)._advance_task_board_after_tool(b, "set_plan", {"tasks": ["", "   "]})
    assert changed is False
    assert [t.title for t in b.tasks] == ["ghost row"]


# ── Phase 2: gateway stops Ghost/procedure seeding when MO owns the board ──

def test_new_gateway_board_model_owned_starts_empty_not_ghost_rows():
    from core.gateway import _new_gateway_board
    b = _new_gateway_board(
        "t", "s", "commit and push everything",
        rows=[{"id": "1", "text": "ghost a", "status": "active"}, {"id": "2", "text": "ghost b"}],
        model_owned=True,
    )
    # Empty board: ghost/procedure rows dropped; MO populates it via set_plan. An
    # empty board can't false-trip the done-claim/contract gates if MO doesn't plan.
    assert b.tasks == []


def test_new_gateway_board_default_keeps_ghost_rows():
    from core.gateway import _new_gateway_board
    b = _new_gateway_board(
        "t", "s", "commit and push", rows=[{"id": "1", "text": "ghost a", "status": "active"}], model_owned=False,
    )
    assert [t.title for t in b.tasks] == ["ghost a"]
