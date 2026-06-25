"""Companion/Ghost UX regression tests.

Covers the companion UX regressions that must stay locked:
- render-layer redaction                 - mic released on auto-stop
- Guide/Do mode indicator                - auto-stop finishes the capture
- hot-mic button color                   - TTS failure surfaced
- no late GUI queueing post-stop         - mic-start failure reason
- status ellipsis                        - startup-toggle feedback
- scrollable replies                     - tray tooltip reflects mode
- blocked-route receipt styling          - history browse position preserved
The arrow-key fix lives in tests/test_keybindings.py (needs that harness).
"""
import sys
import threading
import types

import pytest

SECRET = "AKIAIOSFODNN7EXAMPLE"  # redact_sensitive_text -> [redacted-token]


def _drain(cs):
    cs._drain_gui_events({})


class _FakeLabel:
    def __init__(self):
        self.kwargs = {}

    def config(self, **kwargs):
        self.kwargs.update(kwargs)


class _FakeText:
    """Stands in for the scrollable response tk.Text widget."""
    def __init__(self):
        self.text = ""
        self.state = None
        self.seen = None

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def delete(self, *_a):
        self.text = ""

    def insert(self, _idx, s):
        self.text += s

    def see(self, idx):
        self.seen = idx


# ---------------------------------------------------- redaction / response box
def test_set_response_redacts_and_renders_to_text():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._response = _FakeText()
    cs._set_response(f"here is the key {SECRET} ok")
    _drain(cs)
    assert SECRET not in cs._response.text
    assert "[redacted-token]" in cs._response.text
    assert cs._response.state == "disabled"  # left read-only
    assert cs._response.seen == "1.0"         # finished answer scrolls to top


def test_on_assistant_text_redacts_and_follows_tail():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._response = _FakeText()
    cs._on_assistant_text(f"streaming {SECRET} reply")
    _drain(cs)
    assert SECRET not in cs._response.text
    assert cs._response.seen == "end"  # streaming follows the tail


# ----------------------------------------------------------- mode indicator
def test_mode_indicator_distinguishes_guide_and_do():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._mode = "guide"
    g_text, g_color = cs._mode_indicator()
    cs._mode = "do"
    d_text, d_color = cs._mode_indicator()
    assert "Guide" in g_text and "Do" in d_text
    assert g_color != d_color  # Do uses a distinct alert color (it can actuate)


def test_mode_indicator_refresh_posts_current_mode_to_open_window():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._mode = "do"
    cs._mode_label = _FakeLabel()

    cs._refresh_mode_indicator()
    _drain(cs)

    assert "Do" in cs._mode_label.kwargs["text"]


# ----------------------------------------------------------- voice button
def test_voice_button_color_helper_posts_config():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._voice_btn = _FakeLabel()
    cs._set_voice_btn_color("#ff4444")
    _drain(cs)
    assert cs._voice_btn.kwargs["fg"] == "#ff4444"


# ----------------------------------------------------------- GUI queueing
def test_post_gui_call_refused_after_stop():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    assert cs._post_gui_call(lambda: None) is True
    cs._stopped = True
    assert cs._post_gui_call(lambda: None) is False  # no misleading "queued" success


# ----------------------------------------------------------- status text
def test_set_status_ellipsizes_long_text():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._status_label = _FakeLabel()
    cs._set_status("x" * 300, "#fff")
    _drain(cs)
    text = cs._status_label.kwargs["text"]
    assert len(text) <= 120 and text.endswith("…")


def test_run_turn_error_status_redacts_exception_text(capsys):
    from contextlib import nullcontext
    from interface.ghost_desktop.companion import CompanionSurface

    class FakeAgent:
        system_message = "base"
        _session = None

        def lane_scope(self, _lane):
            return nullcontext()

        def isolated_session(self, _session):
            return nullcontext()

    class FakeGateway:
        def run_turn(self, *_args, **_kwargs):
            raise RuntimeError(f"provider leaked {SECRET}")

    cs = CompanionSurface(agent=FakeAgent(), gateway=FakeGateway())
    cs._root = object()
    cs._status_label = _FakeLabel()

    cs._run_turn("hello")
    _drain(cs)
    stderr = capsys.readouterr().err

    assert SECRET not in cs._status_label.kwargs["text"]
    assert "[redacted-token]" in cs._status_label.kwargs["text"]
    assert SECRET not in stderr


# ----------------------------------------------------------- mic release
def _fake_sounddevice():
    mod = types.ModuleType("sounddevice")

    class CallbackStop(Exception):
        pass

    mod.CallbackStop = CallbackStop
    return mod


def test_recorder_releases_stream_on_autostop(monkeypatch):
    """Hitting the max-seconds cap must release the mic device (CallbackStop),
    not merely flip the recording flag — otherwise the InputStream stays hot."""
    from interface.ghost_desktop.voice import PushToTalkRecorder
    sd = _fake_sounddevice()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)

    closed = {"stop": 0, "close": 0}

    class FakeStream:
        def stop(self):
            closed["stop"] += 1

        def close(self):
            closed["close"] += 1

    rec = PushToTalkRecorder(sample_rate=100, max_seconds=1.0)  # cap = 100 samples
    rec._recording = True
    rec._stream = FakeStream()

    with pytest.raises(sd.CallbackStop):
        for _ in range(5):
            rec._audio_callback([0.0] * 50, 50, None, None)
    assert rec._recording is False

    rec.stop()  # must tear down even though _recording is already False
    assert closed["stop"] == 1 and closed["close"] == 1
    assert rec._stream is None


def test_recorder_callback_without_stream_stays_test_safe():
    from interface.ghost_desktop.voice import PushToTalkRecorder
    rec = PushToTalkRecorder(sample_rate=100, max_seconds=1.0)
    rec._recording = True
    for _ in range(5):
        rec._audio_callback([0.0] * 50, 50, None, None)  # _stream is None -> no raise
    assert sum(len(c) for c in rec._buffer) <= 100
    assert rec._recording is False


# ----------------------------------------------------------- voice autostop
def test_poll_voice_autostop_finishes_capture_when_recorder_self_stopped():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._recording_voice = True

    class FakeRec:
        _recording = False  # recorder hit the cap on its own thread

    class FakeVoice:
        recorder = FakeRec()

    cs._voice = FakeVoice()
    finished = []
    cs._finish_voice_capture = lambda v: finished.append(v)
    cs._poll_voice_autostop()
    assert cs._recording_voice is False
    assert finished == [cs._voice]


def test_poll_voice_autostop_noop_while_still_recording():
    from interface.ghost_desktop.companion import CompanionSurface
    cs = CompanionSurface(agent=None, gateway=None)
    cs._root = object()
    cs._recording_voice = True

    class FakeRec:
        _recording = True

    class FakeVoice:
        recorder = FakeRec()

    cs._voice = FakeVoice()
    finished = []
    cs._finish_voice_capture = lambda v: finished.append(v)
    cs._poll_voice_autostop()
    assert cs._recording_voice is True and finished == []


# ----------------------------------------------------------- TTS errors
def test_speak_async_invokes_on_error_when_speak_fails():
    from interface.ghost_desktop.voice import VoiceSpeaker
    spk = VoiceSpeaker(voice_model_path="")  # no model -> speak() returns False
    errors = []
    done = threading.Event()

    def on_err(reason):
        errors.append(reason)
        done.set()

    spk.speak_async("hello", on_error=on_err)
    assert done.wait(3.0)
    assert errors and "model" in errors[0].lower()


# ----------------------------------------------------------- mic errors
def test_recorder_start_records_last_error_when_backend_missing(monkeypatch):
    from interface.ghost_desktop.voice import PushToTalkRecorder
    rec = PushToTalkRecorder()
    monkeypatch.setattr(type(rec), "available", property(lambda self: False))
    assert rec.start() is False
    assert rec.last_error  # non-empty, human-readable reason


# ----------------------------------------------------------- startup toggle
def test_toggle_startup_notifies_on_failure(monkeypatch):
    from interface.ghost_desktop.tray import CompanionTray
    t = CompanionTray(None)
    notes = []
    t._notify = lambda m: notes.append(m)
    monkeypatch.setattr(CompanionTray, "_set_startup", staticmethod(lambda enable: False))
    monkeypatch.setattr(CompanionTray, "_startup_enabled", staticmethod(lambda: False))
    t._on_toggle_startup(None, None)
    assert notes and "pywin32" in notes[0]


def test_toggle_startup_silent_on_success(monkeypatch):
    from interface.ghost_desktop.tray import CompanionTray
    t = CompanionTray(None)
    notes = []
    t._notify = lambda m: notes.append(m)
    monkeypatch.setattr(CompanionTray, "_set_startup", staticmethod(lambda enable: True))
    monkeypatch.setattr(CompanionTray, "_startup_enabled", staticmethod(lambda: False))
    t._on_toggle_startup(None, None)
    assert notes == []


# ----------------------------------------------------------- tray mode title
def test_tray_update_title_reflects_mode():
    from interface.ghost_desktop.tray import CompanionTray
    t = CompanionTray(None)

    class FakeIcon:
        title = "x"

    t._tray = FakeIcon()
    t._mode = "do"
    t._update_title()
    assert "Do" in t._tray.title


def test_tray_mode_change_refreshes_companion_badge():
    from interface.ghost_desktop.tray import CompanionTray

    calls = []

    class FakeCompanion:
        def _refresh_mode_indicator(self):
            calls.append("refresh")

    t = CompanionTray(FakeCompanion())
    t._on_mode_do(None, None)

    assert t.mode == "do"
    assert calls == ["refresh"]


# ----------------------------------------------------------- route receipts
def test_route_line_style_flags_unavailable_receipt_blocked():
    from interface.ghost_panel import route_line_style
    assert route_line_style("class:ghost-response", "MO queue unavailable · queued") \
        == "class:ghost-route-blocked"
    assert route_line_style("class:ghost-response", "Worker unavailable") \
        == "class:ghost-route-blocked"
    # ordinary prose that merely mentions the word must NOT be restyled
    assert route_line_style("class:ghost-response", "The service was unavailable yesterday") \
        == "class:ghost-response"


# ----------------------------------------------------------- Ghost history
def _bg_harness(monkeypatch):
    monkeypatch.setattr("interface.ghost_history.append_ghost_audit", lambda *a, **k: None)
    from interface.ghost_controller import GhostControllerMixin
    from interface.ghost_history import GhostHistoryMixin

    class H(GhostControllerMixin, GhostHistoryMixin):
        def __init__(self):
            self._ghost_history = []
            self._ghost_history_index = None
            self._ghost_panel_lines = []
            self._ghost_panel_open = True
            self._app = None

    h = H()
    for i in range(4):
        h._record_ghost_history("reply", f"q{i}", f"a{i}")
    return h


def test_background_notification_preserves_browse_position(monkeypatch):
    h = _bg_harness(monkeypatch)
    h._ghost_history_index = 1  # user paged back to an earlier entry
    h._ghost_panel_lines = [("class:ghost-user", "q1")]
    h._record_background_notification("worker done")
    assert h._ghost_history_index == 1                       # not yanked to latest
    assert h._ghost_panel_lines == [("class:ghost-user", "q1")]  # panel not overwritten


def test_background_notification_updates_panel_when_not_browsing(monkeypatch):
    h = _bg_harness(monkeypatch)
    h._ghost_history_index = 3  # at the latest entry -> not browsing
    h._record_background_notification("worker done")
    assert h._ghost_panel_lines == [("class:ghost-hint", "worker done")]
