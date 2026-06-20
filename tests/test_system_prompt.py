from core.system_prompt import internal_system_prompt_path, load_system_prompt


def test_internal_system_prompt_is_packaged_under_core():
    text, source = load_system_prompt("")

    assert source == "internal"
    assert internal_system_prompt_path().as_posix().endswith("core/prompts/system.md")
    assert "You are MO" in text
    assert "Provider-first" in text


def test_internal_prompt_carries_honesty_directive():
    # VS05 2026-06-20 (Fable 5): MO must default to honest pushback over flattery
    # and own mistakes without groveling.
    text, _source = load_system_prompt("")

    assert "Be honest over agreeable" in text
    assert "no groveling" in text


def test_internal_prompt_carries_anti_drift_contract():
    # Drift fix (2026-06-21): MO must stay on the current request, treat the
    # profile as background, re-anchor (or ask) after an interruption, and never
    # take an unasked sensitive/outward action.
    text, _source = load_system_prompt("")

    assert "Stay on the operator's CURRENT request" in text
    assert "do NOT guess what you were doing" in text
    assert "Never take a sensitive or outward action unasked" in text


def test_internal_prompt_carries_batched_reads_directive():
    # A1 (2026-06-20): the prompt must tell the model to batch independent reads
    # so the runtime's concurrent read dispatch actually gets exercised.
    text, _source = load_system_prompt("")

    assert "Batch independent reads" in text


def test_legacy_root_system_md_config_resolves_to_internal_prompt(tmp_path, monkeypatch):
    root_prompt = tmp_path / "system.md"
    root_prompt.write_text("ROOT PROMPT MUST NOT BE DEFAULT", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    text, source = load_system_prompt("system.md")

    assert source == "internal"
    assert "ROOT PROMPT MUST NOT BE DEFAULT" not in text
    assert "You are MO" in text


def test_custom_system_prompt_path_is_disabled_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MO_ALLOW_SYSTEM_PROMPT_OVERRIDE", raising=False)
    custom = tmp_path / "custom-system.md"
    custom.write_text("Custom operator prompt", encoding="utf-8")

    text, source = load_system_prompt(str(custom))

    assert source == f"internal (override disabled: {custom})"
    assert text != "Custom operator prompt"
    assert "You are MO" in text


def test_explicit_custom_system_prompt_path_requires_env_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_ALLOW_SYSTEM_PROMPT_OVERRIDE", "1")
    custom = tmp_path / "custom-system.md"
    custom.write_text("Custom operator prompt", encoding="utf-8")

    text, source = load_system_prompt(str(custom))

    assert text == "Custom operator prompt"
    assert source == str(custom)


def test_missing_custom_system_prompt_fails_closed_to_internal_when_env_enabled(monkeypatch):
    monkeypatch.setenv("MO_ALLOW_SYSTEM_PROMPT_OVERRIDE", "1")

    text, source = load_system_prompt("missing/custom-system.md")

    assert source == "internal (missing override: missing/custom-system.md)"
    assert "You are MO" in text
