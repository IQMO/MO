"""CompanionSurface's voice lane: STT capture, Voice Chat, speech output
lifecycle, and the speech event loop."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from mo_desktop.desktop_log import (
    log_event,
)

_VOICE_STATUS_REQUEST_RE = re.compile(
    r"\b(?:double[\s-]*alt|voice\s+chat|voice|microphone|mic|audio)\b"
    r"[^\n]{0,140}\b(?:status|state|enabled|activated|working|listen(?:ing)?|"
    r"hear(?:ing)?|talk(?:ing)?|speak(?:ing)?|silent|slow|why)\b|"
    r"\b(?:status|state|enabled|activated|working|listen(?:ing)?|hear(?:ing)?|"
    r"talk(?:ing)?|speak(?:ing)?|silent|slow|why)\b[^\n]{0,140}"
    r"\b(?:double[\s-]*alt|voice\s+chat|voice|microphone|mic|audio)\b|"
    r"\b(?:are\s+you\s+listening|can\s+you\s+hear\s+me|why\s+(?:are(?:n['’]t)?|"
    r"won['’]t|don['’]t)\s+you\s+(?:talk|speak))\b",
    re.I,
)


class CompanionVoiceMixin:
    """Verbatim extraction from companion.py; state and composition stay
    with the host class."""

    def _init_voice(self) -> None:
        """Initialize configured voice input without disturbing speech output."""
        if not self._voice_cfg:
            self._voice = None
            return
        stt_enabled = self._voice_cfg.get("stt_enabled", False) or self._voice_chat_enabled()
        if not stt_enabled:
            self._voice = None
            return
        from mo_desktop.voice.input import (
            CompanionVoice,
            make_voice_recognizer,
            make_voice_recorder,
        )

        recognizer = make_voice_recognizer(self._voice_cfg)
        self._voice = CompanionVoice(
            recognizer=recognizer,
            recorder=make_voice_recorder(self._voice_cfg),
        )

    def _init_speech_output(self, *, force: bool = False) -> None:
        self._close_speech()
        if not force and not (bool(self._voice_cfg.get("tts_enabled", False)) or self._voice_chat_enabled()):
            return
        try:
            from mo_desktop.voice.output import SpeechOutput

            self._speech = SpeechOutput(self._voice_cfg, on_event=self._on_speech_event)
            self._speech.start()
        except Exception:
            self._speech = None
            self._speech_state = "unavailable"

    def set_speech_enabled(self, on: bool) -> bool:
        self._voice_cfg["tts_enabled"] = bool(on)
        if on:
            if self._speech is None:
                self._init_speech_output()
            return bool(self._speech is not None and self._speech.installed)
        if not self._voice_chat_enabled():
            self._close_speech()
        return True

    def set_voice_speech_rate(self, value: Any) -> None:
        """Apply the next-reply Piper rate without restarting the speech worker."""
        from mo_desktop.voice.output import normalize_speech_rate

        self._voice_cfg["speech_rate"] = normalize_speech_rate(value)

    def set_voice_output_device(self, device: str) -> None:
        self._voice_cfg["output_device"] = str(device or "default")
        speech = getattr(self, "_speech", None)
        if speech is None:
            return
        setter = getattr(speech, "set_device", None)
        if callable(setter):
            # Only the audio stream moves. Reloading the speech model to change
            # speakers would be seconds of dead air for a device swap.
            setter(self._voice_cfg["output_device"])
            return
        self._init_speech_output()

    def preview_voice(self) -> bool:
        if self._speech is None:
            self._init_speech_output(force=True)
        speech = self._speech
        return bool(speech is not None and speech.speak(
            "Hello. MO voice is ready.", speed=self._voice_cfg.get("speech_rate", 1.0),
        ))

    def _voice_chat_enabled(self) -> bool:
        return bool(getattr(self, "_voice_cfg", {}).get("chat_enabled", False))

    def _voice_status_banner(self, user_input: str) -> str:
        """Supply current local voice state only when the operator asks about it."""
        if not _VOICE_STATUS_REQUEST_RE.search(str(user_input or "")):
            return ""
        config = getattr(self, "_voice_cfg", {}) or {}
        voice_turn = bool(float(getattr(self, "_voice_turn_started_at", 0.0) or 0.0))
        voice = getattr(self, "_voice", None)
        speech = getattr(self, "_speech", None)
        state = {
            "request_origin": "voice" if voice_turn else "text",
            "double_alt_input": bool(config.get("stt_enabled", False)),
            "voice_input_ready": bool(
                voice is not None and getattr(voice, "recording_configured", False)
            ),
            "continuous_voice_chat": self._voice_chat_enabled(),
            "continuous_voice_chat_paused": bool(
                getattr(self, "_voice_chat_paused", False)
            ),
            "speak_typed_replies": bool(config.get("tts_enabled", False)),
            "gesture_hook_registered": getattr(self, "_tap_hook", None) is not None,
            "recording": bool(getattr(self, "_recording_voice", False)),
            "transcribing": bool(getattr(self, "_voice_transcribing", False)),
            "speech_worker_present": speech is not None,
            "speech_worker_running": bool(
                speech is not None and getattr(speech, "running", False)
            ),
            "speech_state": str(getattr(self, "_speech_state", "idle") or "idle"),
        }
        evidence = json.dumps(state, ensure_ascii=True, separators=(",", ":"))
        return (
            "[MO Desktop native voice status (current local runtime evidence): "
            f"{evidence}. An accepted voice-origin request is routed to spoken output even "
            "when speak_typed_replies is false; continuous_voice_chat alone controls automatic "
            "microphone reopening. Answer from this evidence without screen inspection, "
            "tool discovery, or generic app-focus/hotkey guesses.]\n\n"
        )

    def _voice_turn_response_policy(self) -> str:
        """Ask for a natural spoken opening only on an accepted voice turn."""
        if not float(getattr(self, "_voice_turn_started_at", 0.0) or 0.0):
            return ""
        return (
            "[Live voice turn: Answer directly in one or two short, natural sentences. "
            "Keep the response concise unless the operator asks for detail. Put any "
            "necessary commands, paths, code, lists, or other screen-only detail after "
            "that opening; do not narrate formatting or tool syntax.]\n\n"
        )

    def set_voice_chat_enabled(self, on: bool) -> bool:
        self._voice_cfg["chat_enabled"] = bool(on)
        self._voice_chat_paused = not bool(on)
        if not on:
            if self._recording_voice:
                self._on_stop_click()
            if not bool(self._voice_cfg.get("stt_enabled", False)):
                self.set_voice_enabled(False)
            if not bool(self._voice_cfg.get("tts_enabled", False)):
                self._close_speech()
            return True
        if self._voice is None:
            self._init_voice()
        if self._speech is None:
            self._init_speech_output()
        ready = bool(self._voice is not None and self._speech is not None and self._speech.installed)
        if not ready:
            self._voice_cfg["chat_enabled"] = False
            return False
        self._post_gui_call(self._start_voice_chat_listening)
        return True

    def _close_speech(self) -> None:
        speech, self._speech = getattr(self, "_speech", None), None
        if speech is not None:
            try:
                speech.close()
            except Exception:
                pass
        self._speech_state = "idle"

    def _on_speech_event(self, event: str) -> None:
        previous_state = getattr(self, "_speech_state", "idle")
        self._speech_state = str(event or "idle")
        cube = getattr(self, "_cube", None)
        setter = getattr(cube, "set_speaking", None)
        if callable(setter):
            speaking = self._speech_state == "speaking"
            self._post_gui_call(lambda active=speaking: setter(active))
        if self._speech_state == "speaking":
            self._speech_was_speaking = True
            queued_at = float(getattr(self, "_voice_speech_queued_at", 0.0) or 0.0)
            turn_started = float(getattr(self, "_voice_turn_started_at", 0.0) or 0.0)
            now = time.monotonic()
            if queued_at:
                detail = f"voice audio started; tts_ms={round((now - queued_at) * 1000)}"
                if turn_started:
                    detail += f"; total_ms={round((now - turn_started) * 1000)}"
                log_event(detail, config=getattr(self._agent, "config", None))
        elif self._speech_state == "ready" and self._voice_chat_enabled() and not self._voice_chat_paused:
            self._post_gui_call(self._start_voice_chat_listening)
        elif self._speech_state == "idle":
            follow_up = self._speech_was_speaking
            self._speech_was_speaking = False
            if follow_up and self._voice_chat_enabled() and not self._voice_chat_paused:
                self._post_gui_call(self._start_voice_chat_listening)
            turn = getattr(self, "_turn_thread", None)
            if turn is None or not turn.is_alive():
                # A voice task's acknowledgement or progress line ends while the
                # task still runs; its result keeps the voice origin and is spoken.
                self._reset_voice_timing()
        if self._speech_state == "device_fallback":
            self._set_status("Voice output device unavailable; using system default.", self._visual_palette.warn)
        elif self._speech_state == "restarting":
            self._set_status("Voice worker restarting once…", self._visual_palette.warn)
        elif self._speech_state == "unavailable":
            self._set_status("Voice output unavailable; the text reply is preserved.", self._visual_palette.warn)
        elif self._speech_state == "error" and previous_state != "error":
            self._set_status("Voice playback failed; the text reply is preserved.", self._visual_palette.warn)
        if self._speech_state in {"unavailable", "error"}:
            # No `idle` will arrive for this reply, so the Voice Chat loop has to
            # be re-armed here or it stops listening without saying so.
            self._speech_was_speaking = False
            self._reset_voice_timing()
            self._resume_voice_chat_after_turn()

    def _cancel_speech(self) -> None:
        self._speech_was_speaking = False
        self._reset_voice_timing()
        conversation = getattr(self, "_voice_conversation_owner", None)
        if conversation is not None:
            # New speech or a stop also ends the fast reply still being written.
            conversation.cancel()
        speech = getattr(self, "_speech", None)
        if speech is not None:
            try:
                speech.cancel()
            except Exception:
                pass

    def _speak_reply(self, text: str) -> bool:
        """Speak the reply. Returns False when nothing was handed to the worker,
        which is the caller's signal that no speech event will follow."""
        voice_cfg = getattr(self, "_voice_cfg", {})
        voice_turn = bool(float(getattr(self, "_voice_turn_started_at", 0.0) or 0.0))
        if not (
            bool(voice_cfg.get("tts_enabled", False))
            or self._voice_chat_enabled()
            or voice_turn
        ):
            self._reset_voice_timing()
            return False
        speech = getattr(self, "_speech", None)
        if speech is None and voice_turn:
            # A manual double-Alt turn is a voice conversation even when the
            # separate setting for speaking typed replies is off. Normally this
            # worker began warming with capture; retry here if it was lost.
            self._init_speech_output(force=True)
            speech = getattr(self, "_speech", None)
        if speech is None:
            self._reset_voice_timing()
            return False
        from mo_desktop.voice.output import prepare_spoken_text, spoken_max_chars

        spoken = prepare_spoken_text(text, max_chars=spoken_max_chars(voice_cfg))
        if not self._voice_can_speak(spoken):
            self._reset_voice_timing()
            return False
        self._voice_speech_queued_at = time.monotonic()
        submitted = bool(speech.speak(spoken, speed=voice_cfg.get("speech_rate", 1.0)))
        if not submitted:
            self._reset_voice_timing()
        return submitted

    # ------------------------------------------------------------------
    # Voice conversation: fast spoken replies; MO turns only for real work
    # ------------------------------------------------------------------

    def _voice_conversation(self) -> Any:
        conversation = getattr(self, "_voice_conversation_owner", None)
        if conversation is not None:
            return conversation
        from mo_desktop.voice.conversation import VoiceConversation
        from mo_desktop.voice.output import speech_safe_text

        config = getattr(self._agent, "config", None)
        conversation = VoiceConversation(
            provider=self._voice_conversation_provider,
            speech=self._voice_conversation_speech,
            can_speak=self._voice_can_speak,
            speech_safe=speech_safe_text,
            show=lambda text, final: self._render_reply_dialog(text, follow_tail=not final, controls=final),
            record=self._record_voice_exchange,
            delegate=self._delegate_voice_request,
            emote=self._voice_conversation_emote,
            task_state=self._voice_task_state,
            log=lambda detail: log_event(detail, config=config),
            operator_name=str(getattr(getattr(self._agent, "profile", None), "user_name", "") or ""),
        )
        self._voice_conversation_owner = conversation
        return conversation

    def _voice_conversation_provider(self) -> Any:
        """The provider named by ``voice.conversation_provider``, else MO's active one."""
        from core.state.configuration_defaults import DEFAULT_PREFERENCES

        cfg = getattr(self, "_voice_cfg", {}) or {}
        name = str(cfg.get("conversation_provider",
                           DEFAULT_PREFERENCES["mo_desktop.voice.conversation_provider"]) or "").strip()
        providers = list(getattr(self._agent, "providers", []) or [])
        for provider in providers:
            if name and getattr(provider, "name", "") == name:
                return provider
        return getattr(self._agent, "active_provider", None) or (providers[0] if providers else None)

    def voice_conversation_provider_names(self) -> tuple[str, ...]:
        """Configured provider names Settings may offer for spoken replies."""
        names = (str(getattr(provider, "name", "") or "").strip()
                 for provider in getattr(self._agent, "providers", []) or [])
        return tuple(dict.fromkeys(name for name in names if name))

    def set_voice_conversation_provider(self, name: str) -> bool:
        """Choose the spoken-reply provider live; an empty name follows MO's active provider."""
        value = str(name or "").strip()
        if value and value not in self.voice_conversation_provider_names():
            return False
        self._voice_cfg["conversation_provider"] = value
        return True

    def _voice_conversation_speech(self) -> Any:
        if getattr(self, "_speech", None) is None:
            self._init_speech_output(force=True)
        return getattr(self, "_speech", None)

    def _voice_can_speak(self, text: str) -> bool:
        # The installed voice is English. Arabic is spoken only through the
        # operator's own configured Arabic voice; otherwise it stays visible
        # instead of being misread.
        from mo_desktop.voice.conversation import is_arabic
        from mo_desktop.voice.output import arabic_voice_model

        return not is_arabic(text) or arabic_voice_model(getattr(self, "_voice_cfg", {})) is not None

    def _voice_conversation_emote(self, name: str) -> None:
        cube = getattr(self, "_cube", None)
        if cube is not None and name:
            self._post_gui_call(lambda: cube.play_emote(name))

    def _voice_task_state(self) -> str:
        turn = getattr(self, "_turn_thread", None)
        if turn is None or not turn.is_alive():
            return ""
        objective = str(getattr(self, "_voice_delegated_objective", "") or "").strip()
        state = f"MO is working on: {objective}" if objective else "MO is working on a request."
        step = str(getattr(self, "_last_activity_text", "") or "").strip()
        return f"{state} (now: {step})" if step else state

    def _record_voice_exchange(self, user_text: str, reply: str) -> None:
        turn = getattr(self, "_turn_thread", None)
        if turn is not None and turn.is_alive():
            return  # the running turn owns the session; the voice layer keeps its own history
        session = getattr(self, "_desktop_session", None)
        if session is not None and self._record_direct_desktop_exchange(session, user_text, reply):
            persist = getattr(self, "_persist_desktop_session", None)
            if callable(persist):
                try:
                    persist()
                except Exception:
                    pass

    def _delegate_voice_request(self, user_text: str, objective: str) -> bool:
        """Hand the operator's own words to a normal MO voice turn."""
        from mo_desktop.voice.conversation import is_arabic

        turn = getattr(self, "_turn_thread", None)
        busy = turn is not None and turn.is_alive()
        objective_text = str(objective or user_text)[:200]
        # Progress lines are English, so they are spoken only in an English exchange.
        speaks_language = not is_arabic(user_text)
        self._voice_task_admission = (str(user_text), objective_text, speaks_language)
        if not busy:
            self._voice_delegated_objective = objective_text
            self._last_activity_text = ""  # a new task has no step yet
        submitted = self._submit_text_request(
            user_text,
            source="voice",
            _voice_started_at=float(getattr(self, "_voice_conversation_accepted_at", 0.0) or time.monotonic()),
            _request_panic_generation=getattr(self, "_voice_conversation_panic_generation", None),
            _keep_speech=True,
        )
        if not submitted:
            self._reset_voice_timing()
        if not busy:
            self._arm_voice_progress(getattr(self, "_turn_thread", None) if submitted and speaks_language else None)
        return submitted

    def _arm_voice_progress(self, thread: Any) -> None:
        self._voice_progress_thread = thread
        self._voice_progress_at = time.monotonic()
        self._voice_progress_spoken = ()

    def _take_voice_task_text(self, user_input: str) -> str:
        """The voice layer's objective for the spoken task now starting, once.

        The turn still answers the operator's own words; only its admission
        reads the clean objective, so loose wording never hides the computer
        tools from a task the voice layer already judged to be real work.
        """
        task = getattr(self, "_voice_task_admission", None)
        if isinstance(task, tuple) and len(task) == 3 and task[0] == user_input:
            self._voice_task_admission = None
            self._voice_delegated_objective = str(task[1] or "")
            self._last_activity_text = ""
            if getattr(self, "_voice_progress_thread", None) is not threading.current_thread():
                # A task queued while another ran starts now, on this turn thread.
                self._arm_voice_progress(threading.current_thread() if task[2] else None)
            return str(task[1] or "")
        return ""

    def _voice_progress(self, activity: str) -> None:
        """Fill a voice task's silence with one short line about what MO is doing."""
        thread = getattr(self, "_voice_progress_thread", None)
        if thread is None or thread is not getattr(self, "_turn_thread", None) or not thread.is_alive():
            return
        if getattr(self, "_speech_state", "idle") in {"loading", "synthesizing", "speaking"}:
            return
        from mo_desktop.voice.conversation import PROGRESS_GAP_SECONDS, progress_line

        now = time.monotonic()
        if now - float(getattr(self, "_voice_progress_at", 0.0) or 0.0) < PROGRESS_GAP_SECONDS:
            return
        spoken = tuple(getattr(self, "_voice_progress_spoken", ()) or ())
        line = progress_line(activity, spoken=spoken)
        speech = getattr(self, "_speech", None)
        if not line or speech is None:
            return
        if speech.speak(line, speed=self._voice_cfg.get("speech_rate", 1.0)):
            self._voice_progress_at = now
            self._voice_progress_spoken = (*spoken, line)[-3:]

    def _converse(self, text: str, *, accepted_at: float, panic_generation: int) -> None:
        """Answer one utterance in the conversation layer (runs off the GUI thread)."""
        self._voice_conversing = True
        self._voice_conversation_accepted_at = accepted_at
        self._voice_conversation_panic_generation = panic_generation
        self._set_status("Answering…", self._visual_palette.accent)
        try:
            outcome = self._voice_conversation().handle(text)
        except Exception as exc:
            outcome = {"kind": "error", "error": type(exc).__name__, "spoken": False}
        finally:
            self._voice_conversing = False
        log_event(
            f"voice conversation {outcome.get('kind')}; spoken={bool(outcome.get('spoken'))}",
            config=getattr(self._agent, "config", None),
        )
        if outcome.get("kind") == "delegated" and outcome.get("submitted"):
            return  # the MO turn speaks its result and re-arms Voice Chat itself
        if outcome.get("kind") == "error":
            self._set_status("Voice reply failed — the request is shown instead.", self._visual_palette.warn)
            self._render_reply_dialog(f"I heard: “{text}”, but the voice reply failed.", controls=True)
        else:
            self._set_status("Done", self._visual_palette.ok)
        if not outcome.get("spoken"):
            # No speech event will follow, so nothing else re-opens the mic.
            self._reset_voice_timing()
            self._resume_voice_chat_after_turn()

    def _warm_voice_conversation(self) -> None:
        """Open the provider connection while the operator is still speaking."""
        now = time.monotonic()
        if now - float(getattr(self, "_voice_conversation_warmed_at", 0.0) or 0.0) < 120.0:
            return
        self._voice_conversation_warmed_at = now
        try:
            threading.Thread(
                target=self._voice_conversation().warm, name="mo-voice-warm", daemon=True,
            ).start()
        except Exception:
            pass

    def _resume_voice_chat_after_turn(self) -> None:
        """Re-open the Voice Chat mic when no spoken reply will do it for us.

        The listen loop is otherwise re-armed only by the speech worker's `idle`
        event. A reply that is not spoken — worker gone, speech disabled, a
        rejected transcript — produces no event, and without this the toggle
        stays on while the mic never reopens."""
        if not self._voice_chat_enabled() or self._voice_chat_paused:
            return
        self._post_gui_call(self._start_voice_chat_listening)

    def _pause_voice_chat(self) -> None:
        """Pause automatic listening for this run and release an open Voice Chat mic."""
        self._voice_chat_paused = True
        self._discard_voice_capture()

    def _discard_voice_capture(self) -> bool:
        """Stop and discard one active capture, clearing all capture state."""
        was_recording = bool(getattr(self, "_recording_voice", False))
        self._recording_voice = False
        self._voice_capture_auto = False
        if not was_recording:
            return False
        try:
            self._cube_set_listening(False)
        except Exception:
            pass
        recorder = getattr(getattr(self, "_voice", None), "recorder", None)
        if recorder is not None:
            try:
                recorder.stop()
            except Exception:
                pass
        return True

    def set_voice_enabled(self, on: bool) -> bool:
        """Turn voice STT on/off live — the settings panel flips
        mo_desktop.voice.stt_enabled without a restart. Returns True if voice is ready."""
        if not isinstance(self._voice_cfg, dict):
            self._voice_cfg = {}
        self._voice_cfg["stt_enabled"] = bool(on)
        if on:
            if self._voice is None:
                try:
                    self._init_voice()
                except Exception:
                    self._voice = None
            return self._voice is not None
        if self._voice_chat_enabled():
            return True
        self._discard_voice_capture()
        self._close_voice_input()
        if not self._voice_chat_enabled() and not bool(self._voice_cfg.get("tts_enabled", False)):
            self._close_speech()
        return True

    def _close_voice_input(self) -> None:
        voice, self._voice = getattr(self, "_voice", None), None
        if voice is not None:
            try:
                voice.close()
            except Exception:
                pass

    def _cancel_voice_transcription(self) -> None:
        recognizer = getattr(getattr(self, "_voice", None), "recognizer", None)
        cancel = getattr(recognizer, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def _on_voice_input(self, chat_auto: bool = False) -> None:
        """Start or finish the one owned voice capture.

        Manual capture starts on the held second Alt and finishes when that Alt is
        released. Voice Chat uses the same owner with automatic endpointing. No blocking
        input is used, and every stop path closes the microphone."""
        voice = self._voice
        if not self._voice_input_configured():
            self._set_status("Voice is off — turn it on in Settings", self._visual_palette.warn)
            cube = getattr(self, "_cube", None)
            if cube is not None:
                self._post_gui_call(lambda: cube.show_bubble("voice off — Settings"))
            else:
                self._show_reply_dialog("Voice input is off in mo_desktop.voice.stt_enabled")
            return
        if voice is None or not voice.recording_configured:
            self._set_status(self._voice_input_unavailable_message(), self._visual_palette.warn)
            self._show_reply_dialog(self._voice_input_unavailable_message())
            return
        if self._recording_voice:
            # second voice gesture → stop, transcribe, submit
            self._recording_voice = False
            self._cube_set_listening(False)
            self._finish_voice_capture(voice, chat_auto=self._voice_capture_auto)
            return
        if bool(getattr(self, "_voice_transcribing", False)):
            self._set_status("Still transcribing…", self._visual_palette.warn)
            return
        # First manual voice gesture starts even while a turn is running. The
        # accepted transcript joins the same bounded FIFO as typed follow-ups;
        # automatic Voice Chat listening remains between turns below.
        # Visible immediately (mic startup can take a moment) but NOT moved: Alt,Alt is a reply
        # gesture, and the cursor is usually resting on the panel being replied to.
        self._cube_wake(at_pointer=False)
        self._cube_set_listening(True)
        recorder = getattr(voice, "recorder", None)
        if recorder is not None:
            recorder.adaptive_endpointing = bool(chat_auto) or bool(
                getattr(recorder, "configured_adaptive_endpointing", False)
            )
            recorder.initial_silence_timeout = 5.0 if chat_auto else None
        # Load the heavy model only after actual voice demand. The worker loads
        # while the microphone captures and is awaited only after release.
        prepare = getattr(voice, "prepare_recognizer", None)
        if callable(prepare):
            prepare()
        if voice.start_recording():
            self._warm_voice_conversation()
            if self._voice_chat_enabled() and not chat_auto:
                self._voice_chat_paused = False
            self._cancel_speech()
            self._recording_voice = True
            self._voice_capture_auto = bool(chat_auto)
            self._set_status(
                "Listening…" if chat_auto else "Listening… release Alt to send",
                self._visual_token("_LISTEN"),
            )
            if not chat_auto and getattr(self, "_speech", None) is None:
                # Make listening visible before starting optional output work,
                # then warm Piper while the operator is still speaking instead
                # of putting its cold start after transcription and the answer.
                self._init_speech_output(force=True)
        else:
            self._cube_set_listening(False)
            rec = getattr(voice, "recorder", None)
            reason = (getattr(rec, "last_error", "") or "").strip() if rec else ""
            msg = f"Could not start the microphone — {reason}" if reason \
                else "Could not start the microphone — check OS mic permissions."
            self._set_status(msg, self._visual_palette.error)
            self._show_reply_dialog(msg)

    def _start_voice_chat_listening(self) -> None:
        """Open the mic only between turns and spoken replies while Voice Chat is active."""
        if (
            not self._voice_chat_enabled()
            or self._voice_chat_paused
            or self._recording_voice
            or bool(getattr(self, "_voice_transcribing", False))
            or bool(getattr(self, "_voice_conversing", False))
        ):
            return
        if self._turn_thread is not None and self._turn_thread.is_alive():
            return
        if getattr(self, "_speech_state", "idle") in {"loading", "synthesizing", "speaking"}:
            return
        self._on_voice_input(chat_auto=True)

    def _finish_voice_capture(self, voice: Any, *, chat_auto: bool | None = None) -> None:
        """Stop capture, transcribe, and submit from Alt release or the recording cap."""
        if chat_auto is None:
            chat_auto = bool(getattr(self, "_voice_capture_auto", False))
        self._voice_capture_auto = False
        transcription_started_at = time.monotonic()
        self._set_status("Transcribing…", self._visual_palette.accent)
        log_event("voice capture finishing; transcribing", config=getattr(self._agent, "config", None))
        # Claim the transcription owner before the worker starts. A fast second
        # gesture can otherwise enter through the GUI queue before the thread has
        # set this flag and run a second Whisper job beside the first one.
        self._voice_transcribing = True
        panic_generation = int(getattr(self, "_panic_generation", 0))
        transcription_prompt = self._voice_transcription_prompt()

        def _finish() -> None:
            try:
                text = voice.stop_and_transcribe(initial_prompt=transcription_prompt)
            finally:
                self._voice_transcribing = False
            if panic_generation != int(getattr(self, "_panic_generation", 0)):
                self._reset_voice_timing()
                log_event(
                    "voice transcription discarded after Panic Stop",
                    config=getattr(self._agent, "config", None),
                )
                return
            evidence = getattr(voice, "last_transcription_evidence", None)
            if text and bool(getattr(evidence, "uncertain", False)):
                reason = re.sub(
                    r"[^a-z0-9_-]+",
                    "-",
                    str(getattr(evidence, "reason", "") or "low-confidence").lower(),
                ).strip("-") or "low-confidence"
                # Never act on dubious speech — but show what was heard instead
                # of discarding it, and keep the conversation alive: one marginal
                # transcript is not a reason to stop listening.
                heard = " ".join(text.split())[:160]
                self._set_status("I didn't catch that clearly — please repeat.", self._visual_palette.warn)
                self._show_reply_dialog(
                    f'I didn\'t catch that clearly — I heard: "{heard}". Say it again, or type it.'
                )
                log_event(
                    f"voice transcription rejected; reason={reason[:48]}; chars={len(text)}",
                    config=getattr(self._agent, "config", None),
                )
                if chat_auto:
                    self._resume_voice_chat_after_turn()
                return
            if text and not text.startswith("[STT") and not text.startswith("[Voice"):
                accepted_at = time.monotonic()
                if getattr(self, "_cube", None) is not None:  # a quick "heard you" cube flourish
                    self._post_gui_call(lambda: self._cube.play_emote("happy"))
                turn = getattr(self, "_turn_thread", None)
                busy = turn is not None and turn.is_alive()
                log_event(
                    "voice transcribed; "
                    f"chars={len(text)}; transcription_ms="
                    f"{round((accepted_at - transcription_started_at) * 1000)}; "
                    + ("answering in voice conversation while MO works" if busy else "answering in voice conversation"),
                    config=getattr(self._agent, "config", None),
                )
                # The voice layer answers even while MO works; it writes no session
                # then, and work it starts joins the follow-up FIFO behind the turn.
                self._converse(text, accepted_at=accepted_at, panic_generation=panic_generation)
            else:
                self._reset_voice_timing()
                message = text or "No speech detected."
                if chat_auto and self._voice_chat_enabled():
                    self._voice_chat_paused = True
                    self._set_status(
                        "Voice Chat paused — double Alt to speak",
                        self._visual_palette.muted,
                    )
                    log_event("voice chat follow-up window ended without speech",
                              config=getattr(self._agent, "config", None))
                    return
                self._set_status(message, self._visual_palette.error)
                self._show_reply_dialog(message)
                log_event(f"voice transcription did not produce a request; chars={len(message)}",
                          config=getattr(self._agent, "config", None))

        try:
            threading.Thread(target=_finish, name="mo-desktop-voice", daemon=True).start()
        except Exception:
            self._voice_transcribing = False
            raise

    def _voice_transcription_prompt(self) -> str:
        """Return bounded recent operator wording for local Whisper continuity."""
        session = getattr(self, "_desktop_session", None)
        messages = list(getattr(session, "messages", []) or []) if session is not None else []
        recent: list[str] = []
        plain = getattr(self, "_plain_desktop_message_content", None)
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            if message.get("_mo_internal_continuation"):
                continue
            content = message.get("content")
            text = plain(content) if callable(plain) else str(content or "")
            text = " ".join(str(text or "").split())
            if not text:
                continue
            recent.append(text[-120:])
            if len(recent) == 3:
                break
        # The assistant's own name leads, so Whisper hears "MO" rather than a
        # look-alike word; recent wording follows for continuity.
        return ("MO. " + " ".join(reversed(recent))[-316:]).strip()

    def _poll_voice_autostop(self) -> None:
        """The recorder can self-stop at the max-seconds cap from its audio thread.
        Reflect that on the GUI instead of leaving a stale 'Recording…' hint with the
        mic already released — finish the buffered capture through the same owner."""
        if not self._recording_voice or self._voice is None:
            return
        rec = getattr(self._voice, "recorder", None)
        if rec is None or getattr(rec, "_recording", False):
            return
        self._recording_voice = False
        self._cube_set_listening(False)
        self._finish_voice_capture(self._voice, chat_auto=self._voice_capture_auto)

    def _voice_input_configured(self) -> bool:
        return bool(self._voice_cfg.get("stt_enabled", False) or self._voice_chat_enabled())

    def _voice_input_unavailable_message(self) -> str:
        voice = self._voice
        if voice is None:
            return "Voice input unavailable: STT is enabled but voice did not initialize"

        missing: list[str] = []
        recorder = getattr(voice, "recorder", None)
        if recorder is None:
            missing.append("microphone capture")
        if missing:
            return "Voice input unavailable: " + "; ".join(missing)
        return "Voice input unavailable: microphone capture is not ready"

    def _reset_voice_timing(self) -> None:
        self._voice_turn_started_at = 0.0
        self._voice_speech_queued_at = 0.0
