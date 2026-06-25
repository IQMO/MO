"""Location-aware skill selection — conventions surfaced by WHERE MO is working.

The skills system matches by user-input text (task triggers). This adds a parallel path:
a skill whose `scope` carries file-globs surfaces when the graph-selected node file-paths
fall under that scope — so MO sees its conventions for the code in scope, not every rule.
Additive to skills.py; does not change the existing text-match selection."""
from core.skills import (
    Skill,
    _scope_path_globs,
    skill_matches_location,
    select_skills_by_location,
)


def _skill(name, scope, body="do the thing"):
    return Skill(name=name, description="", triggers=(), body=body, source="t", scope=scope)


def test_scope_globs_extracts_paths_not_descriptive_words():
    assert _scope_path_globs("core/tasking/*, core/agent/agent_taskboard.py") == [
        "core/tasking/*", "core/agent/agent_taskboard.py"
    ]
    # descriptive scopes carry no location
    assert _scope_path_globs("universal") == []
    assert _scope_path_globs("matching turns") == []
    assert _scope_path_globs("") == []


def test_location_match_directory_glob():
    s = _skill("taskboard-truth", "core/tasking/*")
    assert skill_matches_location(s, ["core/tasking/agent_taskboard.py"]) is True
    assert skill_matches_location(s, ["core/agent/agent.py"]) is False


def test_location_match_exact_file():
    s = _skill("gate-rule", "core/final_gates.py")
    assert skill_matches_location(s, ["core/final_gates.py"]) is True
    assert skill_matches_location(s, ["core/gateway.py"]) is False


def test_location_match_handles_backslashes_and_dotslash():
    s = _skill("ui-rule", "interface/*")
    assert skill_matches_location(s, ["interface\\ghost.py"]) is True
    assert skill_matches_location(s, ["./interface/hints.py"]) is True


def test_descriptive_scope_never_location_matches():
    # A universal/behavioral skill is NOT a location skill — it must not surface by path.
    s = _skill("evidence-first", "universal")
    assert skill_matches_location(s, ["core/anything.py"]) is False


def test_select_renders_only_matching_conventions():
    skills = [
        _skill("taskboard-truth", "core/tasking/*", body="task truth only via complete_task"),
        _skill("ui-states", "interface/*", body="cover loading/empty/error states"),
        _skill("behavioral", "universal", body="give a verdict and stop"),
    ]
    out = select_skills_by_location(skills, ["core/tasking/agent_taskboard.py"])
    assert "conventions for the code in scope" in out
    assert "taskboard-truth" in out and "complete_task" in out
    assert "ui-states" not in out          # different location
    assert "behavioral" not in out         # universal, not location-scoped


def test_select_empty_when_no_location_or_no_match():
    skills = [_skill("taskboard-truth", "core/tasking/*")]
    assert select_skills_by_location(skills, []) == ""              # no location context
    assert select_skills_by_location(skills, ["core/gateway.py"]) == ""  # no match
    assert select_skills_by_location([], ["core/tasking/x.py"]) == ""    # no skills


# --- Slice 4: autonomous evidence-gated convention write ----------------------

def test_write_convention_rejects_noise():
    import pytest
    from core.skills import write_convention
    with pytest.raises(ValueError):  # 'universal' has no file-glob -> not a convention
        write_convention(name="x", rule="always verify", scope="universal")
    with pytest.raises(ValueError):  # empty rule
        write_convention(name="x", rule="", scope="core/tasking/*")


def test_write_convention_writes_scoped_pack_that_location_matches(tmp_path):
    from core.skills import write_convention, load_skills, skill_matches_location

    class FakeProfile:
        _path = str(tmp_path / "memory" / "mo.db")

    (tmp_path / "memory").mkdir(parents=True)
    path = write_convention(
        name="taskboard truth",
        rule="task rows advance only via complete_task evidence",
        scope="core/tasking/*",
        evidence="core/tasking/agent_taskboard.py",
        profile=FakeProfile(),
    )
    assert path.exists()
    skills = load_skills([str(tmp_path / "skills")])
    assert len(skills) == 1
    assert skills[0].scope == "core/tasking/*"          # scope persisted to frontmatter
    assert "complete_task" in skills[0].body
    assert "Evidence:" in skills[0].body
    assert skill_matches_location(skills[0], ["core/tasking/x.py"]) is True


def test_record_convention_tool_is_registered():
    # Their test suite asserts every tool name maps def<->executor; keep that invariant.
    from tools import TOOL_DEFINITIONS, TOOL_EXECUTORS
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
    assert "record_convention" in names
    assert "record_convention" in TOOL_EXECUTORS


def test_record_profile_fact_tool_registered_and_persists(tmp_path, monkeypatch):
    from tools import TOOL_DEFINITIONS, TOOL_EXECUTORS, execute_record_profile_fact
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
    assert "record_profile_fact" in names and "record_profile_fact" in TOOL_EXECUTORS

    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    # Writes a durable fact, dedups, and surfaces in facts.md.
    out = execute_record_profile_fact({"category": "server", "fact": "prod runs the api service on the deploy host", "evidence": "user said"})
    assert "Recorded operator fact" in out
    assert "already recorded" in execute_record_profile_fact({"category": "server", "fact": "prod runs the api service on the deploy host"})
    # Refuses secret values and empty input.
    assert "NOT recorded" in execute_record_profile_fact({"category": "credential", "fact": "key is sk-live-abcdef1234567890ABCDEF"})
    assert "NOT recorded" in execute_record_profile_fact({"category": "", "fact": ""})

    from pathlib import Path
    from core.path_defaults import resolve_state_path
    facts = Path(resolve_state_path("memory/profile/facts.md")).read_text(encoding="utf-8")
    assert "api service on the deploy host" in facts and "sk-live" not in facts
