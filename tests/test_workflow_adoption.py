import json
from types import SimpleNamespace

from core.agent.agent import Agent
from core.gateway_helpers import select_template
from interface.command_registry import COMMANDS


class SessionStub:
    def __init__(self):
        self.messages = []
        self.turn_count = 0

    def add_user(self, text):
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text, **_kwargs):
        self.messages.append({"role": "assistant", "content": text})


class ProfileStub(SimpleNamespace):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.learned = []

    def append_profile_learning(self, source, insights):
        self.learned.append((source, insights))


def _agent(tmp_path):
    agent = Agent.__new__(Agent)
    profile = ProfileStub(_path=str(tmp_path / "mo.db"))
    agent.profile = profile
    agent.allowed_roots = [str(tmp_path)]
    agent.config = {}
    agent.sandbox_config = {
        "enabled": True,
        "audit_log": str(tmp_path / "tool_audit.jsonl"),
        "web_fetch_enabled": False,
        "web_fetch_allowed_hosts": [],
        "block_shell_escape": True,
        "shell_network_enabled": False,
        "max_output_chars": 50000,
        "clean_env": True,
    }
    agent._active_lane = None
    
    agent._thread_state = None
    agent.session = SessionStub()
    agent._session = agent.session
    agent.memory = None
    agent.context_handoff_enabled = False
    return agent


def test_workflow_adoption_request_stays_simple_chat_even_for_review_words():
    assert select_template("adopt this code review workflow from docs/review.md") == "simple_chat"
    assert select_template("learn this testing skill: always run pytest") == "simple_chat"


def test_inline_workflow_source_rejects_assistant_narration():
    # RC-C regression: MO's own multi-step narration (or carried-over session text)
    # must never be mined as an "adopted workflow".
    narration = (
        "Let me check the config dir for the keys; Let me check the main project and "
        "find the actual configuration; Now let me actually test the keys against the API"
    )
    legit = (
        "Inspect relevant files before findings; separate verified and inferred "
        "claims; report blockers and next move"
    )
    assert Agent._extract_inline_workflow_source(f"adopt workflow: {narration}") == ""
    assert Agent._extract_inline_workflow_source(f"adopt workflow: {legit}") != ""
    assert Agent._extract_inline_workflow_source("adopt workflow: do x") == ""  # too short


def test_bare_use_method_does_not_hijack_ordinary_turn(tmp_path):
    # Regression: WORKFLOW_ADOPTION_RE matches everyday phrasing like
    # "use the same method to fix …" / "use this skill to refactor …". With no
    # concrete workflow source and no literal "workflow" word, the handler must fall
    # through (return None) so the provider handles the turn — NOT hijack it with the
    # "give me a workflow source" prompt.
    agent = _agent(tmp_path)
    for text in (
        "use the same method to fix the other files",
        "use this skill to refactor the parser",
        "adopt the existing style for the new module",
    ):
        assert agent._maybe_handle_workflow_control_turn(text) is None, text


def test_no_public_skill_command_registered():
    names = {spec.name for spec in COMMANDS} | {alias for spec in COMMANDS for alias in spec.aliases}
    assert "/skill" not in names
    assert "/skills" not in names
    assert "/discover" not in names


def test_agent_stages_workflow_from_local_file_without_provider(tmp_path):
    source = tmp_path / "review-workflow.md"
    source.write_text(
        "# Review workflow\n"
        "- Inspect relevant files before findings.\n"
        "- Separate verified and inferred claims.\n"
        "- Report blockers and next move.\n",
        encoding="utf-8",
    )
    agent = _agent(tmp_path)

    response = agent.run_turn(f"adopt this review workflow from {source}")

    assert "Skill candidate staged" in response
    assert "Approve with: approve skill candidate workflow-candidate:" in response
    assert "Inspect relevant files" in response
    stored = (tmp_path / "workflow_candidates.jsonl").read_text(encoding="utf-8")
    assert "review-workflow.md" in stored
    audit = [json.loads(line) for line in (tmp_path / "tool_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[0]["tool"] == "read_file"
    assert audit[0]["blocked"] is False


def test_agent_blocks_workflow_url_when_sandbox_disallows_fetch(tmp_path):
    agent = _agent(tmp_path)

    response = agent.run_turn("adopt this workflow from https://example.com/workflow.md")

    assert response.startswith("Skill source blocked:")
    audit = [json.loads(line) for line in (tmp_path / "tool_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[0]["tool"] == "web_fetch"
    assert audit[0]["blocked"] is True


def test_agent_promotes_staged_workflow_after_explicit_approval(tmp_path):
    source = tmp_path / "test-workflow.md"
    source.write_text("# Testing workflow\n- Run tests before claiming fixed.\n", encoding="utf-8")
    agent = _agent(tmp_path)
    staged = agent.run_turn(f"adopt this testing workflow from {source}")
    candidate_id = staged.split("approve skill candidate ", 1)[1].strip()

    promoted = agent.run_turn(f"approve skill candidate {candidate_id}")

    assert promoted.startswith("Skill promoted:")
    assert "Skill pack:" in promoted
    assert (tmp_path / "skills").exists()
    skill_main = next((tmp_path / "skills").glob("*/SKILL.md"))
    assert "Use this skill" in skill_main.read_text(encoding="utf-8")
    assert "Testing workflow" in (skill_main.parent / "references" / "source.md").read_text(encoding="utf-8")
    promoted_store = (tmp_path / "workflow_promoted.jsonl").read_text(encoding="utf-8")
    assert candidate_id in promoted_store
    assert agent.profile.learned


def test_agent_does_not_promote_from_bare_candidate_id(tmp_path):
    source = tmp_path / "test-workflow.md"
    source.write_text("# Testing workflow\n- Run tests before claiming fixed.\n", encoding="utf-8")
    agent = _agent(tmp_path)
    staged = agent.run_turn(f"adopt this testing workflow from {source}")
    candidate_id = staged.split("approve skill candidate ", 1)[1].strip()

    response = agent._maybe_handle_workflow_control_turn(candidate_id)

    assert response is None
    assert not (tmp_path / "workflow_promoted.jsonl").exists()
