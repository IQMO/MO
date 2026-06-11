from types import SimpleNamespace

from core.agent.agent import Agent


class FakeMemory:
    def __init__(self):
        self.indexed = []

    def index_turn(self, **kwargs):
        self.indexed.append(kwargs)


def test_goal_surface_does_not_index_private_iterations_into_foreground_memory():
    agent = object.__new__(Agent)
    agent._thread_state = None
    agent._session = SimpleNamespace()
    agent.session = SimpleNamespace()  # isolated goal session, not foreground
    agent.memory = FakeMemory()
    agent.profile = None

    with agent.provider_scope("goal", worker_id="w-goal"):
        agent._record_turn_memory_and_learning("[GOAL iteration 1]", "loop text")

    assert agent.memory.indexed == []


def test_main_foreground_turn_indexes_memory():
    agent = object.__new__(Agent)
    agent._thread_state = None
    session = SimpleNamespace()
    agent._session = session
    agent.session = session
    agent.memory = FakeMemory()
    agent.profile = None

    agent._record_turn_memory_and_learning("hi", "hello there")

    assert len(agent.memory.indexed) == 1
    assert agent.memory.indexed[0]["user"] == "hi"
