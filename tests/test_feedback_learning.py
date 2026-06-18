import json
import os
from pathlib import Path

from core.learning.feedback_learning import extract_feedback_learning, record_feedback_learning
from core.agent.agent import Agent


class FakeProfile:
    def __init__(self, path=None):
        self.calls = []
        if path is not None:
            self._path = path

    def append_profile_learning(self, source, insights):
        self.calls.append((source, insights))


class FakeMemory:
    def __init__(self):
        self.turns = []

    def index_turn(self, **kwargs):
        self.turns.append(kwargs)


def test_extract_feedback_learning_is_quiet_for_normal_chat():
    assert extract_feedback_learning("hello, how are you?") == {}


def test_no_false_positive_learning_from_normal_technical_chat():
    # Bare common words ("feedback"/"stop"/"did not"/"didn't") must NOT auto-bake
    # learning from ordinary technical conversation — the accuracy fix.
    assert extract_feedback_learning("the test didn't verify the legacy audit, stop") == {}
    assert extract_feedback_learning("add a feedback form and stop the server") == {}
    assert extract_feedback_learning("the build did not compile and the dirty cache stayed") == {}


def test_mo_directed_correction_still_learns():
    # Clear MO-directed corrections still produce learning.
    assert extract_feedback_learning("you didn't verify the tests before claiming done") != {}
    assert extract_feedback_learning("what did you learn? stop leaving dirty legacy behind") != {}


def test_extract_feedback_learning_captures_self_improvement_feedback():
    insights = extract_feedback_learning(
        "what did you learn from this feedback? self improvement means audit exact, tested, no dirty legacy left behind, and don't make my terms sound crazy"
    )

    joined = "\n".join("; ".join(v) for v in insights.values())
    assert "operational self-improvement" in joined
    assert "Preserve the operator's wording" in joined
    assert "verified reality" in joined or "Evidence-first" in joined
    assert "no abandoned legacy paths" in joined


def test_record_feedback_learning_uses_stable_feedback_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = FakeProfile()
    text = "feedback: when corrected update learning with evidence and fix the method"

    assert record_feedback_learning(profile, text, "done") is True
    assert record_feedback_learning(profile, text, "done again") is True

    assert len(profile.calls) == 2
    assert profile.calls[0][0] == profile.calls[1][0]
    assert profile.calls[0][0].startswith("feedback:")
    # FindingPatterns writes under the runtime state home (set by conftest),
    # which is where production reviews + system_health also read it.
    state_home = Path(os.environ["MO_STATE_HOME"])
    data = json.loads((state_home / "memory" / "review_history" / "patterns.json").read_text(encoding="utf-8"))
    assert "evidence" in data["operator_preferences"]


def test_agent_records_memory_and_feedback_learning(tmp_path):
    agent = object.__new__(Agent)
    agent.memory = FakeMemory()
    agent.profile = FakeProfile(tmp_path / "profile.md")

    notes = agent._record_turn_memory_and_learning(
        "what did you learn? this is self-improvement feedback, tested no dirty work left behind",
        "implemented",
    )

    assert any(note.startswith("Noted:") for note in notes)
    assert all(len(note) < 50 for note in notes)
    assert len(agent.memory.turns) == 1
    assert agent.memory.turns[0]["assistant"] == "implemented"
    assert len(agent.profile.calls) == 1
    assert "feedback:" in agent.profile.calls[0][0]


def test_agent_reports_staged_workflow_candidate_notice(tmp_path):
    agent = object.__new__(Agent)
    agent.memory = FakeMemory()
    agent.profile = FakeProfile(tmp_path / "profile.md")

    notes = agent._record_turn_memory_and_learning(
        "from now on when I ask to audit, always report verified evidence first",
        "understood",
    )

    assert "Workflow staged: approve latest" in notes
    assert all(len(note) < 50 for note in notes)
