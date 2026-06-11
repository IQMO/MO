from types import SimpleNamespace

from core.agent.agent import Agent
from core.ghost.ghost_routing import enhance_route_objective
from core.prompt_enhancer import clean_prompt_text, enhance_prompt, profile_prompt_guidance
from core.provider.provider import SimpleResponse
from core.session.session import Session


def test_prompt_enhancer_corrects_typos_without_sending_work():
    assert clean_prompt_text("mo investigate for me in the codebse") == "investigate in the codebase"

    enhanced = enhance_prompt("/ignored".replace("/ignored", "mo investigate for me in the codebse"))

    assert enhanced.startswith("Investigate in the codebase")
    assert "verify findings" in enhanced


def test_prompt_enhancer_corrects_observed_operator_typos():
    text = "verfiy the delpoy fixses before i confrim if adrseed turly"

    assert clean_prompt_text(text) == "verify the deploy fixes before i confirm if addressed truly"


def test_prompt_enhancer_strips_i_want_to_prefix():
    enhanced = enhance_prompt("I want to new game funny and has visuals")

    assert enhanced.startswith("New game funny and has visuals")
    assert "preserve the requested outcome" in enhanced
    assert not enhanced.startswith("I want")


def test_prompt_enhancer_has_specific_logs_metrics_performance_shape():
    enhanced = enhance_prompt("him lets dig into lgos and meterics and performance but be comphernsive")

    assert enhanced.startswith("Audit logs, metrics, and performance comprehensively")
    assert "empty-response/stuck cases" in enhanced
    assert "comphernsive" not in enhanced


def test_prompt_enhancer_uses_explicit_operator_profile(tmp_path):
    pdir = tmp_path / "profile"
    pdir.mkdir()
    (pdir / "operator.md").write_text(
        "# Operator Profile\n- Use direct concise answers\n- Evidence-first\n- Hates excessive clarifying questions\n",
        encoding="utf-8",
    )
    (pdir / "thinking_model.md").write_text(
        "# Thinking\n- Preserve the operator goal frame\n- Anti-over-engineering: simplest working solution\n",
        encoding="utf-8",
    )
    profile = SimpleNamespace(_path=str(tmp_path / "mo.db"))

    guidance = profile_prompt_guidance(profile)
    enhanced = enhance_prompt("fix the ghost prompt route", profile)

    assert guidance.direct
    assert guidance.concise
    assert guidance.evidence_first
    assert "keep the answer direct and concise" in enhanced
    assert "do not broaden scope" in enhanced
    assert "prefer the smallest maintainable change" in enhanced
    assert "ask only if blocked or risk changes" in enhanced


def test_agent_prompt_enhance_uses_no_tools_provider_and_returns_replacement_text():
    agent = object.__new__(Agent)
    agent.profile = None
    agent.session = Session("system")
    agent.max_tokens = 1000
    calls = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        return SimpleResponse(content="Review the taskboard UI, verify evidence, and report only blockers.") , SimpleNamespace(name="flash")

    agent.complete_ghost_no_tools = fake_complete

    enhanced = agent.enhance_prompt_for_input("check ui")

    assert enhanced == "Review the taskboard UI, verify evidence, and report only blockers."
    assert calls[0]["surface"] == "ghost_prompt_enhance"
    assert calls[0]["messages"][0]["role"] == "system"


def test_agent_prompt_enhance_can_include_compact_marker():
    agent = object.__new__(Agent)
    agent.profile = None
    agent.session = Session("system")
    agent.max_tokens = 1000

    def fake_complete(**_kwargs):
        return SimpleResponse(content="Review the taskboard UI and verify blockers."), SimpleNamespace(name="flash")

    agent.complete_ghost_no_tools = fake_complete

    enhanced = agent.enhance_prompt_for_input("check ui", include_marker=True)

    assert enhanced.endswith("_[prompt enhanced]_")



def test_ghost_route_objective_prefers_explicit_suggested_ask():
    response = "Looks good.\nSuggested ask: Audit the provider logs with latency buckets.\nReply yes."

    assert enhance_route_objective("rough typo text", response) == "Audit the provider logs with latency buckets."


def test_ghost_route_objective_passes_through_original():
    """After simplification: enhance_route_objective passes through original text."""
    enhanced = enhance_route_objective("him lets dig into lgos and meterics and performance")

    assert "him lets dig into lgos" in enhanced
    assert "meterics" in enhanced
