from interface.formatting import MOON_PHASES, activity_label, format_k, idle_status_text, moon_phase_frame


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
