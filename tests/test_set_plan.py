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


def test_set_plan_no_usable_tasks_is_noop_even_when_on():
    b = _ghost_board()
    changed = _Agent(model_owned=True)._advance_task_board_after_tool(b, "set_plan", {"tasks": ["", "   "]})
    assert changed is False
    assert [t.title for t in b.tasks] == ["ghost row"]
