"""Skills system: load packs, select the relevant one(s) per turn, skip greetings."""

from core.skills import (
    load_confirmed_suggestion_skills,
    load_skills,
    record_skill_outcome,
    retire_stale_generated_skills,
    select_skills_context,
    should_include_skills,
    validate_skill_pack,
    default_skill_roots,
    skills_root,
    write_skill_pack,
)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


SKILL_A = """# Python testing
description: how to run pytest
triggers: test, pytest, coverage
---
Run python -m pytest -q; scope to affected tests first.
"""

SKILL_B = """# Safe refactor
description: refactor without changing behavior
triggers: refactor, rename, restructure
---
Use edit_file, not full rewrites; map callers first.
"""

SKILL_NO_TRIGGER = """# Orphan
description: never selectable
---
body
"""


def test_load_skills_parses_packs(tmp_path):
    _write(tmp_path, "a.md", SKILL_A)
    _write(tmp_path, "b.md", SKILL_B)
    _write(tmp_path, "README.md", "# ignored\ntriggers: x\n---\nno")
    skills = load_skills([tmp_path])
    names = {s.name for s in skills}
    assert "Python testing" in names and "Safe refactor" in names
    assert "ignored" not in {n.lower() for n in names}  # README excluded


def test_triggerless_pack_is_not_loaded(tmp_path):
    _write(tmp_path, "orphan.md", SKILL_NO_TRIGGER)
    assert load_skills([tmp_path]) == []


def test_select_matches_relevant_skill(tmp_path):
    _write(tmp_path, "a.md", SKILL_A)
    _write(tmp_path, "b.md", SKILL_B)
    ctx = select_skills_context("help me write a pytest for the parser", [tmp_path])
    assert "Python testing" in ctx
    assert "Safe refactor" not in ctx  # not triggered


def test_select_returns_empty_when_no_match(tmp_path):
    _write(tmp_path, "a.md", SKILL_A)
    assert select_skills_context("what's the weather", [tmp_path]) == ""


def test_greeting_skips_skills(tmp_path):
    _write(tmp_path, "a.md", SKILL_A)
    assert not should_include_skills("hi")
    assert select_skills_context("hi", [tmp_path]) == ""


def test_cap_respected(tmp_path):
    _write(tmp_path, "a.md", "# Big\ndescription: d\ntriggers: refactor\n---\n" + ("x " * 5000))
    ctx = select_skills_context("refactor this", [tmp_path], max_chars=500)
    assert len(ctx) <= 520


def test_shipped_skills_present_and_loadable():
    roots = default_skill_roots()
    skills = load_skills(roots)
    names = {s.name.lower() for s in skills}
    # The repo ships at least the python-testing and safe-refactor packs.
    assert any("test" in n for n in names)
    assert any("refactor" in n for n in names)


def test_skill_md_frontmatter_pack_loads_and_records_mastery(tmp_path):
    profile = type("P", (), {"_path": str(tmp_path / "memory" / "mo.db")})()
    root = skills_root(profile)
    path = write_skill_pack(
        root=root,
        name="SQLite persistence",
        description="Use SQLite safely",
        triggers=("sqlite", "database"),
        body="Open read-only when inspecting.",
    )

    skills = load_skills([root])
    assert skills[0].name == "SQLite persistence"

    ctx = select_skills_context("inspect the sqlite database", [root])
    assert "SQLite persistence" in ctx
    assert "mastery_uses: 0" in path.read_text(encoding="utf-8")

    ctx = select_skills_context("inspect the sqlite database", [root], profile=profile)
    assert "SQLite persistence" in ctx
    text = path.read_text(encoding="utf-8")
    assert "mastery_uses: 1" in text

    assert record_skill_outcome(path, "correction") is True
    assert "mastery_corrections: 1" in path.read_text(encoding="utf-8")


def test_exact_activation_contract_rejects_extra_triggers(tmp_path):
    root = tmp_path / "skills" / "exact-mode-alpha"
    root.mkdir(parents=True)
    path = root / "SKILL.md"
    path.write_text(
        """---
name: "Exact Mode Alpha"
description: "Exact trigger example"
triggers:
  - "exact-mode-alpha"
  - "general mode"
  - "exact alpha"
---
# Exact Mode Alpha

- Activate only when the user explicitly writes `exact-mode-alpha`. Do not activate for `exact`, `alpha`, `exact alpha`, or general requests.
""",
        encoding="utf-8",
    )

    issues = validate_skill_pack(path)

    assert any("extra triggers" in issue for issue in issues)
    assert load_skills([tmp_path / "skills"]) == []


def test_exact_activation_contract_allows_only_exact_trigger(tmp_path):
    root = tmp_path / "skills" / "exact-mode-alpha"
    path = write_skill_pack(
        root=tmp_path / "skills",
        name="Exact Mode Alpha",
        description="Exact trigger example",
        triggers=("exact-mode-alpha",),
        body="- Activate only when the user explicitly writes `exact-mode-alpha`. Do not activate for `exact`, `alpha`, `exact alpha`, or general requests.",
    )

    assert path.parent == root
    assert validate_skill_pack(path) == []
    assert load_skills([tmp_path / "skills"])[0].triggers == ("exact-mode-alpha",)


def test_write_skill_pack_rejects_exact_contract_mismatch(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="extra triggers"):
        write_skill_pack(
            root=tmp_path / "skills",
            name="Exact Mode Alpha",
            description="Exact trigger example",
            triggers=("exact-mode-alpha", "general mode"),
            body="- Activate only when the user explicitly writes `exact-mode-alpha`. Do not activate for `exact`, `alpha`, `exact alpha`, or general requests.",
        )

    assert not (tmp_path / "skills" / "exact-mode-alpha" / "SKILL.md").exists()


def test_project_local_skill_selection_does_not_mutate_pack(tmp_path):
    profile = type("P", (), {"_path": str(tmp_path / "memory" / "mo.db")})()
    project_root = tmp_path / "project" / "skills"
    path = write_skill_pack(
        root=project_root,
        name="Project lint",
        description="Project-local lint guidance",
        triggers=("lint",),
        body="Run the project lint command.",
    )

    ctx = select_skills_context("lint this project", [project_root], profile=profile)

    assert "Project lint" in ctx
    assert "mastery_uses: 0" in path.read_text(encoding="utf-8")


def test_default_roots_are_profile_first_and_project_local_opt_in(tmp_path):
    profile = type("P", (), {"_path": str(tmp_path / "memory" / "mo.db")})()
    roots = default_skill_roots(str(tmp_path / "project"), profile=profile, config={})

    assert roots == [str(tmp_path / "skills")]
    assert skills_root(profile) == tmp_path / "skills"

    roots_with_project = default_skill_roots(
        str(tmp_path / "project"),
        profile=profile,
        config={"skills": {"project_local": True}},
    )
    assert str(tmp_path / "project" / "skills") in roots_with_project


def test_confirmed_learning_suggestions_are_generated_skills(tmp_path):
    import json

    profile = type("P", (), {"_path": str(tmp_path / "mo.db")})()
    (tmp_path / "learning_suggestions.jsonl").write_text(
        json.dumps({
            "id": "learning-suggestion:evidence:x1",
            "kind": "evidence_first",
            "recommendation": "Verify files before claiming done.",
            "evidence": [],
            "status": "confirmed",
            "created_at": 1.0,
        }) + "\n",
        encoding="utf-8",
    )

    skills = load_confirmed_suggestion_skills(profile)
    assert skills and skills[0].provenance == "confirmed-learning"
    ctx = select_skills_context("fix this and verify it", [], profile=profile)
    assert "Verify files before claiming done" in ctx


def test_semantic_fallback_uses_existing_embeddings_backend(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    write_skill_pack(
        root=root,
        name="Database migration",
        description="Safe schema changes",
        triggers=("schema-change-only",),
        body="Plan rollback before changing persistence.",
    )

    def fake_build_embedder(_config):
        return lambda text: [1.0, 0.0] if "database" in text.lower() or "sqlite" in text.lower() else [0.0, 1.0]

    monkeypatch.setattr("core.learning.embeddings.build_embedder", fake_build_embedder)

    ctx = select_skills_context("sqlite persistence update", [root], config={"embeddings": {"enabled": True}})

    assert "Database migration" in ctx


def test_retire_stale_generated_skills(tmp_path):
    root = tmp_path / "skills"
    path = write_skill_pack(
        root=root,
        name="Weak generated skill",
        description="unused",
        triggers=("weak",),
        body="body",
        candidate_id="learning-suggestion:x",
    )
    for _ in range(3):
        record_skill_outcome(path, "opportunity", now=1_000_000.0)
    retired = retire_stale_generated_skills(root, decay_days=1, now=2_000_000.0)

    assert retired
    assert retired[0].name.endswith(".retired")
