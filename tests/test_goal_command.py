from core.agent.agent import Agent


class FakeRunner:
    def __init__(self, agent):
        self.agent = agent

    def stop(self):
        return "stopped"

    def status(self):
        return "status"


def test_goal_rejects_generic_objective(monkeypatch):
    agent = Agent.__new__(Agent)
    agent._goal_runner = None
    agent._goal_active = False
    agent._goal_plan = None
    monkeypatch.setattr("core.goal.GoalRunner", FakeRunner)

    result = agent._cmd_goal("give yourself a goal")

    assert "specific objective" in result
    assert not hasattr(agent, "_goal_pending_objective")


def test_goal_accepts_specific_objective(monkeypatch):
    agent = Agent.__new__(Agent)
    agent._goal_runner = None
    agent._goal_active = False
    agent._goal_plan = None
    monkeypatch.setattr("core.goal.GoalRunner", FakeRunner)

    result = agent._cmd_goal("review interface visuals and report issues")

    assert result == "[GOAL_START]"
    assert agent._goal_pending_objective == "review interface visuals and report issues"
