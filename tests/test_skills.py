"""Skills system: load packs, select the relevant one(s) per turn, skip greetings."""
from pathlib import Path

from core.skills import (
    load_skills,
    select_skills_context,
    should_include_skills,
    default_skill_roots,
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
