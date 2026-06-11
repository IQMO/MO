from __future__ import annotations

from core.agent.agent import Agent
from core.learning.proactive_learning import LearningSuggestion, SuggestionEvidence, read_learning_suggestions, write_learning_suggestions
from core.profile import Profile


class _LearningReport:
    def __init__(self):
        self.learning = {
            "profile_learning": {"entries": 8, "categories": {"work": 3, "personal": 5}},
            "behavior_rules": {"count": 2},
            "workflow": {"candidates": 1, "promoted": 0},
            "memory": {"turns": 4, "fts5": True, "miss_terms": 0},
        }
        self.graph = {"structural": {"nodes": 0, "edges": 0, "communities": 0}}


def test_learning_status_formats_profile_categories_without_raw_dict(monkeypatch):
    agent = Agent.__new__(Agent)
    agent.config = {}
    agent.runtime_home = "memory"

    monkeypatch.setattr("core.system_health.build_health_report", lambda _home: _LearningReport())
    monkeypatch.setattr("core.learning.proactive_learning.read_learning_suggestions", lambda **_kwargs: [])

    response = agent._cmd_learning("")

    assert "profile entries: 8 · categories: personal 5, work 3" in response
    assert "{'" not in response
    assert "'}" not in response


def test_learning_confirm_and_dismiss_review_suggestions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = Profile.load(str(tmp_path / "memory" / "mo.db"))
    agent = Agent.__new__(Agent)
    agent.profile = profile
    suggestion = LearningSuggestion(
        id="learning-suggestion:trace:tool_errors:cmdtest",
        kind="trace:tool_errors",
        recommendation="verify after tool errors",
        evidence=(SuggestionEvidence("event-1", "tool error"),),
    )
    write_learning_suggestions([suggestion])

    response = agent._cmd_learning("confirm learning-suggestion:trace:tool_errors:cmdtest")

    assert "Confirmed cluster: 1 suggestion(s)" in response
    assert read_learning_suggestions() == []
    assert read_learning_suggestions(include_inactive=True)[0].status == "confirmed"
    learning = (tmp_path / "memory" / "profile" / "learning.md").read_text(encoding="utf-8")
    assert "tool errors" in learning.lower()

    second = LearningSuggestion(
        id="learning-suggestion:trace:no_context_bridge:dismissme",
        kind="trace:no_context_bridge",
        recommendation="context missing",
        evidence=(SuggestionEvidence("validation", "missing"),),
    )
    write_learning_suggestions([second])

    dismissed = agent._cmd_learning("dismiss learning-suggestion:trace:no_context_bridge:dismissme")

    assert "Dismissed cluster: 1 suggestion(s)" in dismissed
    statuses = {item.id: item.status for item in read_learning_suggestions(include_inactive=True)}
    assert statuses[second.id] == "dismissed"


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
