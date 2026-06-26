"""Desktop Ghost additions: moon-orb pointer routing + cross-restart session continuity."""
from __future__ import annotations


# ── Orb pointer registry + point_on_screen routing ────────────────────────────

def test_desktop_pointer_registry_dispatch_and_clear():
    from core.ghost import desktop_pointer as dp
    calls = []
    dp.set_desktop_pointer(lambda x, y, label, secs: calls.append((x, y, label, secs)) or True)
    try:
        assert dp.point_with_desktop_orb(10, 20, "btn", 3.0) is True
        assert calls == [(10, 20, "btn", 3.0)]
    finally:
        dp.set_desktop_pointer(None)
    assert dp.point_with_desktop_orb(1, 2) is False  # cleared → nobody handles it


def test_point_on_screen_prefers_orb_over_subprocess(monkeypatch):
    from core.ghost import desktop_pointer as dp
    from tools import desktop
    seen = {}
    dp.set_desktop_pointer(lambda x, y, label, secs: seen.update(x=x, y=y, label=label) or True)

    def _boom(*_a, **_k):
        raise AssertionError("overlay subprocess must not run when the orb handles the point")

    monkeypatch.setattr(desktop.subprocess, "Popen", _boom)
    try:
        out = desktop.execute_point_on_screen({"x": 5, "y": 6, "label": "here"})
    finally:
        dp.set_desktop_pointer(None)
    assert "Pointing at (5,6)" in out
    assert seen == {"x": 5, "y": 6, "label": "here"}


def test_point_on_screen_falls_back_to_subprocess_without_orb(monkeypatch):
    from core.ghost import desktop_pointer as dp
    from tools import desktop
    dp.set_desktop_pointer(None)
    spawned = {}

    class _FakePopen:
        def __init__(self, args, **_k):
            spawned["args"] = args

    monkeypatch.setattr(desktop.subprocess, "Popen", _FakePopen)
    out = desktop.execute_point_on_screen({"x": 7, "y": 8})
    assert "Pointing at (7,8)" in out
    assert "interface.screen_overlay" in " ".join(spawned["args"])


def test_orb_easing_clamped_and_monotonic():
    from interface.ghost_desktop.orb import _ease_out_cubic
    assert _ease_out_cubic(0.0) == 0.0
    assert _ease_out_cubic(1.0) == 1.0
    assert _ease_out_cubic(-1.0) == 0.0 and _ease_out_cubic(2.0) == 1.0  # clamped
    vals = [_ease_out_cubic(i / 10.0) for i in range(11)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_orb_color_helpers_yield_valid_hex():
    import re
    from interface.ghost_desktop.orb import _lerp_hex, _glow_shade, _MOON_RGB, _DARK_RGB
    hexre = re.compile(r"^#[0-9a-f]{6}$")
    assert _lerp_hex(_MOON_RGB, _DARK_RGB, 0.0) == "#00cccc"
    assert hexre.match(_lerp_hex(_MOON_RGB, _DARK_RGB, 0.5))
    assert _lerp_hex(_MOON_RGB, _DARK_RGB, 1.0) == "#0b1418"
    assert _lerp_hex(_MOON_RGB, _DARK_RGB, 5.0) == "#0b1418"  # clamped
    assert all(hexre.match(_glow_shade(i)) for i in range(4))


# ── Session continuity across restarts (isolated 'ghost-desktop' slot) ─────────

class _FakeSessions:
    """Minimal SessionManager double storing snapshots in a dict."""

    def __init__(self):
        self.store: dict = {}

    def load(self, name):
        return self.store.get(name)

    def save_snapshot(self, name, session):
        self.store[name] = {
            "session_id": session.session_id,
            "turn_count": session.turn_count,
            "messages": list(session.messages),
            "total_tokens": session.total_tokens,
            "output_tokens": session.output_tokens,
            "token_log": list(session.token_log),
        }


class _FakeAgent:
    system_message = "base system prompt"

    def __init__(self, sessions):
        self._sessions = sessions


def test_ghost_desktop_session_persists_to_own_slot_and_reloads():
    from interface.ghost_desktop.companion import (
        CompanionSurface, GHOST_DESKTOP_SESSION_SLOT,
    )

    sessions = _FakeSessions()
    first = CompanionSurface(agent=_FakeAgent(sessions), gateway=None)
    s1 = first._ensure_ghost_session()
    s1.add_user("open notepad and type hello")
    s1.turn_count = 1
    first._persist_ghost_session()

    # Saved to the desktop's OWN slot, never 'main'.
    assert GHOST_DESKTOP_SESSION_SLOT in sessions.store
    assert "main" not in sessions.store

    # A fresh process (new surface, same store) regains continuity.
    second = CompanionSurface(agent=_FakeAgent(sessions), gateway=None)
    s2 = second._ensure_ghost_session()
    assert any("open notepad" in str(m.get("content", "")) for m in s2.messages)
    assert s2.turn_count == 1


def test_recorder_auto_stops_after_trailing_silence():
    import numpy as np
    from interface.ghost_desktop.voice import PushToTalkRecorder
    r = PushToTalkRecorder(sample_rate=16000, silence_threshold=0.01, silence_hangover=0.5)
    n = 1600  # 0.1s chunks at 16kHz
    loud = np.full((n, 1), 0.1, dtype="float32")
    quiet = np.zeros((n, 1), dtype="float32")

    # Pre-speech silence must NOT arm the auto-stop.
    assert r._is_trailing_silence(quiet) is False
    assert r._speech_started is False

    # First speech heard → armed, but not a stop yet.
    assert r._is_trailing_silence(loud) is False
    assert r._speech_started is True

    # Trailing quiet accumulates; fires only once the hangover (0.5s) is reached.
    fired = [r._is_trailing_silence(quiet) for _ in range(5)]
    assert fired[:4] == [False, False, False, False]
    assert fired[4] is True


def test_ghost_desktop_session_persist_is_noop_without_manager():
    from interface.ghost_desktop.companion import CompanionSurface

    class _NoSessions:
        system_message = "base"
        _sessions = None

    surface = CompanionSurface(agent=_NoSessions(), gateway=None)
    surface._ensure_ghost_session()
    surface._persist_ghost_session()  # must not raise
