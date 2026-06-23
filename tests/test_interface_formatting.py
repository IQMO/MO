from types import SimpleNamespace

from interface.formatting import (
    MOON_PHASES,
    activity_label,
    format_k,
    idle_status_text,
    moon_phase_frame,
    token_status_from_agent,
)


def _stub_agent(*, compression_saved=500, compaction_chars=0):
    return SimpleNamespace(
        session=SimpleNamespace(token_log=[{"input_tokens": 1000, "output_tokens": 200}]),
        _compression_saved_tokens_estimate=lambda: compression_saved,
        _tool_context_saved_chars=lambda: compression_saved * 4,
        _tool_context_saving_ops=lambda: 3,
        session_compaction_total_saved=compaction_chars,
    )


def test_footer_saved_folds_in_session_compaction():
    # /usage shows tool-compression and session-compaction on separate lines; the
    # single footer "saved" number must aggregate BOTH (chars are disjoint -> add).
    base = token_status_from_agent(_stub_agent(compression_saved=500, compaction_chars=0))
    assert base.saved_tokens_est == 500

    folded = token_status_from_agent(_stub_agent(compression_saved=500, compaction_chars=4000))
    assert folded.saved_tokens_est == 500 + 1000  # 4000 compaction chars / 4
    assert folded.saved_chars == base.saved_chars + 4000


def test_footer_saved_tolerates_missing_compaction_counter():
    agent = _stub_agent(compression_saved=300)
    del agent.session_compaction_total_saved  # older/stub agents lack the counter
    assert token_status_from_agent(agent).saved_tokens_est == 300


def test_activity_label_normalizes_live_lane_text():
    assert activity_label("preparing proposal") == "Preparing…"
    assert activity_label("thinking (request #2)") == "Thinking…"
    assert activity_label("critiquing final response") == "Finalizing…"
    assert activity_label("receiving answer") == "Answering…"
    assert activity_label("running tool grep") == "Working…"
    assert activity_label("goal loop") == "Goal Working…"


def test_idle_status_text_is_deterministic_when_time_is_passed():
    assert MOON_PHASES == ("○", "◔", "◑", "◕", "●", "◕", "◑", "◔")
    assert moon_phase_frame(0.0) == "○"
    assert idle_status_text(0.0) == "○ idle"
    assert idle_status_text(0.9).startswith("◑ idle")


def test_existing_format_helpers_stay_stable():
    assert format_k(999) == "999"
    assert format_k(1500) == "1.5k"
    # raw live-lane text feeds activity_label directly (activity_display removed)
    assert activity_label("thinking (request #3)...") == "Thinking…"
