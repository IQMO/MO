from core.agent.agent_dna import (
    MO_AGENT_DNA,
    build_lean_build_context,
    build_dna_context,
    build_prd_context,
)


def test_agent_dna_default_is_concrete_not_public_skill_surface():
    context = build_dna_context(design=True)

    assert "MO Internal Build/Design DNA" in context
    assert "tight scoped high-quality" in context
    assert "align -> detect -> direct -> adapt -> compose -> quality gate" in context
    assert "R2 Tokens over raw" in context
    assert "R13 Anti-generic gate" in context
    assert "public command" in context
    assert "Taskboard truth" in context
    assert "is this approach still valid" in context
    assert "wait for direction" in context
    assert "Lean-build ladder" in context
    assert "already present in MO or the target codebase" in context
    assert "Python stdlib" in context
    assert "Ponytail" not in context
    assert "/skill" not in context
    assert "Reply " not in context


def test_agent_dna_source_contains_full_design_methodology():
    assert len(MO_AGENT_DNA.design_rules) == 14
    assert any(rule.code == "R12" and "Aesthetic" in rule.title for rule in MO_AGENT_DNA.design_rules)
    assert "colors" in MO_AGENT_DNA.dna_template
    assert "signature visual idea" in MO_AGENT_DNA.direction_template
    assert "MO Internal Build/Design DNA" in build_dna_context(design=True)


def test_lean_build_context_is_mo_native_and_safety_bounded():
    context = build_lean_build_context()

    assert "Lean-build ladder" in context
    assert "does this need to exist" in context
    assert "already present in MO or the target codebase" in context
    assert "already-installed dependency" in context
    assert "minimum complete code" in context
    assert "never remove required validation" in context
    assert "Ponytail" not in context
    assert ".agents" not in context


def test_prd_context_is_alignment_not_forced_build_gate_or_skill():
    context = build_prd_context()

    assert "MO Internal PRD Alignment" in context
    assert "optional planning/alignment artifact" in context
    assert "not a forced build gate" in context
    assert "JTBD" in context
    assert "acceptance criteria" in context
    assert "Taskboard truth" in context
    assert "/skill" in context
    assert "marketplace install" in context
    assert len(context) < 1800
