from core.agent.agent import Agent
from core.goal import GoalRunner
from core.session.session import Session


class FakeProfile:
    _path = "memory/mo.db"
    user_name = "TestUser"


def test_goal_runner_uses_isolated_session_for_goal_turn():
    agent = object.__new__(Agent)
    agent._thread_state = __import__("threading").local()
    agent.system_message = "system"
    agent.session = Session("system")
    agent.session.add_user("main chat message")
    agent._goal_plan = None
    agent._goal_active = False
    agent._goal_runner = None
    agent.profile = FakeProfile()
    agent.sandbox_config = {"audit_log": None}

    seen_sessions = []

    def fake_run_turn(prompt):
        seen_sessions.append(agent.session)
        agent.session.add_user(prompt)
        return "read_file: evidence\nDone."

    agent.run_turn = fake_run_turn

    runner = GoalRunner(agent)
    runner.start("build test thing")

    assert seen_sessions
    assert seen_sessions[0] is not agent.session
    assert getattr(agent, "_goal_session") is seen_sessions[0]
    assert len(agent.session.messages) == 1
    assert agent.session.messages[0]["content"] == "main chat message"
    assert any("[GOAL iteration" in m.get("content", "") for m in agent._goal_session.messages)
