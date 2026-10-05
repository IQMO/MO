"""MO Desktop's spoken conversation owner: fast replies, clause speech, delegation.

A voice utterance is answered here, not by a full MO agent turn. One request to
the voice provider streams the spoken reply, and each complete clause is spoken
as soon as it arrives while later clauses are still being written. Anything that
needs tools, files, apps, the screen, memory or the web is handed to MO through
the ``start_task`` tool, after a short spoken acknowledgement; MO's normal
Desktop turn then does the work and answers.

Speech boundary: only the model's own reply text reaches speech, one clause at a
time through the speech-safe projection. Tool-call arguments never do, and an
emotion tag only selects delivery and the cube's reaction.
"""
from __future__ import annotations

from collections import deque
import json
import re
import threading
import time
from typing import Any, Callable

# Emotion tags the voice model may put at the start of a clause, and the
# existing cube emote each one plays. Engines render the tags they support.
EMOTE_FOR_EMOTION = {
    "warm": "nod",
    "happy": "happy",
    "excited": "bounce",
    "calm": "",
    "serious": "",
    "sorry": "moody",
    "curious": "wiggle",
    "thinking": "thinking",
}

SYSTEM_PROMPT = "\n".join((
    (
        "You are MO, the user's desktop assistant, talking with them out loud in a live voice "
        'conversation.'
    ),
    (
        '- Speak naturally, like a capable person: usually one or two short sentences, always in the '
        'first person. No lists, Markdown, code, paths, URLs or emoji; anything visual appears in your '
        'written reply.'
    ),
    (
        '- Reply in the language the user just spoke (Arabic or English), matching their dialect when you'
        ' can.'
    ),
    (
        '- You may start a sentence with one emotion tag from [warm] [happy] [excited] [calm] [serious] '
        '[sorry] [curious] [thinking] when it fits; never invent other tags.'
    ),
    (
        '- Talking needs nothing else. Anything that needs the screen, apps, files, mail, the web, '
        "memory, the user's projects or any change on the computer is real work: say one short natural "
        'line about what you are about to do, then call start_task with a precise objective. Never '
        'describe this as handing off, never mention tools or another assistant, and never claim '
        'something was done, checked or found unless the conversation shows you reported it.'
    ),
    (
        '- A request to open, show, check, find or change something is a task even when it is short or '
        "names one of MO's own apps (Dashboard, Settings, Design, Files): start it instead of asking which "
        "one, and never say you don't know the user's apps. Ask one short question only when you cannot "
        'tell what to do at all.'
    ),
    '- Keep replies short so the user can interrupt; offer detail only when asked.',
))

DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "start_task",
        "description": (
            "Start doing the request with your full desktop, file, browser, mail, memory and project "
            "abilities. Call it after one short spoken acknowledgement."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "What MO should do, in one precise sentence with any detail the user gave.",
                },
            },
            "required": ["objective"],
        },
    },
}

_ARABIC = re.compile(r"[؀-ۿ]")
_TAG = re.compile(r"\[([a-z]+)\]\s*", re.I)
_SENTENCE_END = re.compile(r"[.!?؟…](?=\s)|\n")
_LONG_CLAUSE_BREAK = re.compile(r"[,،;؛:](?=\s)")
_LONG_CLAUSE_CHARS = 140
_FIRST_CLAUSE_CHARS = 48

_COMMANDS = (
    ("stop", re.compile(
        r"^(?:(?:ok(?:ay)?|mo)[, ]+)?(?:stop(?: it| talking)?|cancel(?: that)?|never ?mind|be quiet|quiet|"
        r"shut up|enough|that'?s enough|hush)[.! ]*$"
        r"|^(?:قف|توقف|وقف|اسكت|اسكتي|خلاص|بس|كفى|كفاية|ألغ|الغ|إلغاء|الغاء)[.!؟ ]*$",
        re.I,
    )),
    ("repeat", re.compile(
        r"^(?:(?:sorry|pardon)[, ]*)?(?:repeat(?: that| it)?|say (?:that|it) again|come again|"
        r"what did you (?:just )?say)[?.! ]*$"
        r"|^(?:عيد|عيدها|أعد|اعد|أعيد|كرر|كررها|شو قلت|ماذا قلت|ايش قلت)[?.!؟ ]*$",
        re.I,
    )),
    ("status", re.compile(
        r"^(?:what are you (?:doing|working on)|what'?s (?:the )?(?:status|progress)|are you (?:still )?working)"
        r"[?.! ]*$"
        r"|^(?:شو عم تعمل|ماذا تفعل|شو تسوي|ايش تسوي|وين وصلت|شو صار)[?.!؟ ]*$",
        re.I,
    )),
)


def match_command(text: str) -> str:
    """Return the deterministic command an utterance is, or ``""``."""
    clean = " ".join(str(text or "").split())
    for name, pattern in _COMMANDS:
        if pattern.match(clean):
            return name
    return ""


def is_arabic(text: str) -> bool:
    return bool(_ARABIC.search(str(text or "")))


def split_clauses(buffer: str, *, first: bool = False) -> tuple[list[str], str]:
    """Cut complete clauses off a streaming buffer; return them and the remainder.

    A clause ends at sentence punctuation followed by whitespace, or a newline.
    A long run without one breaks at its last comma; before anything has been
    spoken (``first``) that happens much earlier, so the first audio is early.
    """
    clauses: list[str] = []
    rest = buffer
    while True:
        match = _SENTENCE_END.search(rest)
        if match is None:
            break
        clause, rest = rest[:match.end()].strip(), rest[match.end():]
        if clause:
            clauses.append(clause)
    if not clauses and len(rest) >= (_FIRST_CLAUSE_CHARS if first else _LONG_CLAUSE_CHARS):
        breaks = list(_LONG_CLAUSE_BREAK.finditer(rest))
        if breaks:
            cut = breaks[-1].end()
            clauses.append(rest[:cut].strip())
            rest = rest[cut:]
    return clauses, rest


# While a spoken task runs, MO says what it is doing instead of going silent.
PROGRESS_GAP_SECONDS = 4.0
_PROGRESS_LINES = (
    (("computer_act desktop:launch",), "Opening it now."),
    (("computer_act",), "Doing that now."),
    (("computer_observe", "computer_targets"), "Checking the screen."),
    (("mail",), "Looking at your mail."),
    (("web_search", "web_fetch", "browser"), "Looking that up."),
    (("read_file", "search_files", "code_search", "list_dir", "grep", "glob"), "Going through it."),
)
_STILL_LINES = ("Still on it.", "Almost there.", "One moment.")


def progress_line(activity: str, *, spoken: tuple[str, ...] = ()) -> str:
    """A short spoken line for a running task's latest activity; never the last one again."""
    low = " ".join(str(activity or "").lower().split())
    if low.startswith("tooling (tool_search"):
        return ""  # choosing tools is not something the user needs to hear
    if low.startswith("tooling ("):
        name = low[len("tooling ("):]
        for prefixes, line in _PROGRESS_LINES:
            if name.startswith(prefixes) and line not in spoken:
                return line
    elif not low.startswith(("waiting on model", "model request")):
        return ""
    last = spoken[-1] if spoken else ""
    return next((line for line in _STILL_LINES if line != last and line not in spoken[-2:]), _STILL_LINES[0])


def take_emotion(clause: str) -> tuple[str, str]:
    """Return (emotion, text) with every bracket tag removed from the text."""
    emotion = ""
    match = _TAG.match(clause.strip())
    if match and match.group(1).lower() in EMOTE_FOR_EMOTION:
        emotion = match.group(1).lower()
    return emotion, " ".join(_TAG.sub(" ", clause).split())


class VoiceConversation:
    """Answer each utterance fast, speak it clause by clause, delegate work to MO."""

    def __init__(
        self,
        *,
        provider: Callable[[], Any],
        speech: Callable[[], Any],
        can_speak: Callable[[str], bool],
        speech_safe: Callable[[str], str],
        show: Callable[[str, bool], None],
        record: Callable[[str, str], None],
        delegate: Callable[[str, str], bool],
        emote: Callable[[str], None] = lambda _name: None,
        task_state: Callable[[], str] = lambda: "",
        log: Callable[[str], None] = lambda _text: None,
        operator_name: str = "",
        history_limit: int = 8,
        history_chars: int = 4000,
        max_tokens: int = 320,
    ) -> None:
        self._provider = provider
        self._speech = speech
        self._can_speak = can_speak
        self._speech_safe = speech_safe
        self._show = show
        self._record = record
        self._delegate = delegate
        self._emote = emote
        self._task_state = task_state
        self._log = log
        name = " ".join(str(operator_name or "").split())[:80]
        # Stable for the conversation, so it stays inside the cached prompt prefix.
        self._system_prompt = (
            f"{SYSTEM_PROMPT}\n- You are talking with {name}; use the name naturally, not in every reply."
            if name else SYSTEM_PROMPT
        )
        self._history: deque[dict[str, str]] = deque(maxlen=max(2, int(history_limit)) * 2)
        self._history_chars = max(500, int(history_chars))
        self._max_tokens = max(64, int(max_tokens))
        self._lock = threading.Lock()
        self._generation = 0
        self._last_reply = ""

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Stop the reply in flight: no more clauses are spoken or shown."""
        with self._lock:
            self._generation += 1
        speech = self._speech()
        if speech is not None:
            try:
                speech.cancel()
            except Exception:
                pass

    def warm(self) -> None:
        """Open the provider connection before the first utterance (cold requests are slow)."""
        provider = self._provider()
        if provider is None:
            return
        try:
            from core.provider.provider import provider_request_overrides

            with provider_request_overrides({"thinking_disabled": True}):
                stream = provider.stream(
                    messages=[{"role": "system", "content": self._system_prompt}, {"role": "user", "content": "hi"}],
                    tools=[], temperature=0.0, max_tokens=1,
                )
                for _chunk in stream:
                    pass
        except Exception as exc:
            self._log(f"voice conversation warm-up failed; {type(exc).__name__}")

    def handle(self, utterance: str) -> dict[str, Any]:
        """Answer one accepted utterance; returns what happened (for callers and tests)."""
        text = " ".join(str(utterance or "").split())
        if not text:
            return {"kind": "empty", "spoken": False}
        command = match_command(text)
        if command == "stop":
            self.cancel()
            return {"kind": "command", "command": "stop", "spoken": False}
        if command == "repeat":
            reply = self._last_reply or ("ما قلت شيء بعد." if is_arabic(text) else "I haven't said anything yet.")
            return {"kind": "command", "command": "repeat", "reply": reply, "spoken": self._speak_whole(reply)}
        if command == "status":
            state = self._task_state()
            reply = state or ("ما في شغل شغّال هلأ." if is_arabic(text) else "Nothing is running right now.")
            return {"kind": "command", "command": "status", "reply": reply, "spoken": self._speak_whole(reply)}
        return self._reply(text)

    # ------------------------------------------------------------------
    # Fast reply
    # ------------------------------------------------------------------

    def _messages(self, text: str) -> list[dict[str, str]]:
        """Static prompt first, bounded history next, live state and the utterance last."""
        history: list[dict[str, str]] = []
        used = 0
        for message in reversed(self._history):
            used += len(message["content"])
            if used > self._history_chars:
                break
            history.append(message)
        history.reverse()
        state = self._task_state()
        current = f"[MO status: {state}]\n{text}" if state else text
        return [{"role": "system", "content": self._system_prompt}, *history, {"role": "user", "content": current}]

    def _reply(self, text: str) -> dict[str, Any]:
        with self._lock:
            self._generation += 1
            generation = self._generation
        provider = self._provider()
        if provider is None:
            return {"kind": "error", "error": "no voice provider", "spoken": False}
        started = time.monotonic()
        visible: list[str] = []
        buffer = ""
        tool_name = ""
        tool_args = ""
        speech_open = False
        spoken = False
        first_token_at = 0.0

        def speak_clause(clause: str) -> None:
            nonlocal speech_open, spoken
            emotion, words = take_emotion(clause)
            if not words:
                return
            visible.append(words)
            if emotion and EMOTE_FOR_EMOTION.get(emotion):
                self._emote(EMOTE_FOR_EMOTION[emotion])
            self._show(" ".join(visible), False)
            safe = self._speech_safe(words)
            if not safe or not self._can_speak(safe):
                return
            speech = self._speech()
            if speech is None:
                return
            if not speech_open:
                speech_open = bool(speech.begin())
                if speech_open:
                    self._log(f"voice conversation first clause queued; ms={round((time.monotonic() - started) * 1000)}")
            if speech_open and speech.say(safe):
                spoken = True

        try:
            from core.provider.provider import provider_request_overrides

            with provider_request_overrides({"thinking_disabled": True}):
                stream = provider.stream(
                    messages=self._messages(text), tools=[DELEGATE_TOOL],
                    temperature=0.6, max_tokens=self._max_tokens,
                )
                for chunk in stream:
                    if generation != self._generation:
                        break
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    for call in getattr(delta, "tool_calls", None) or []:
                        function = getattr(call, "function", None)
                        tool_name = tool_name or str(getattr(function, "name", "") or "")
                        tool_args += str(getattr(function, "arguments", "") or "")
                    content = str(getattr(delta, "content", "") or "")
                    if not content:
                        continue
                    if not first_token_at:
                        first_token_at = time.monotonic()
                        self._log(f"voice conversation first token; ms={round((first_token_at - started) * 1000)}")
                    buffer += content
                    clauses, buffer = split_clauses(buffer, first=not visible)
                    for clause in clauses:
                        speak_clause(clause)
            if generation == self._generation and buffer.strip():
                speak_clause(buffer)
        except Exception as exc:
            self._log(f"voice conversation reply failed; {type(exc).__name__}")
            self._close_speech(speech_open)
            return {"kind": "error", "error": type(exc).__name__, "spoken": spoken}
        if generation != self._generation:
            return {"kind": "cancelled", "spoken": spoken}

        reply = " ".join(visible).strip()
        objective = ""
        if tool_name == DELEGATE_TOOL["function"]["name"]:
            try:
                objective = str((json.loads(tool_args or "{}") or {}).get("objective") or "").strip()
            except ValueError:
                objective = ""
            if not reply:
                speak_clause("لحظة، أنا على الموضوع." if is_arabic(text) else "One moment, I'm on it.")
                reply = " ".join(visible).strip()
        self._close_speech(speech_open)
        self._remember(text, reply)
        if tool_name == DELEGATE_TOOL["function"]["name"]:
            self._log(f"voice conversation delegated to MO; chars={len(objective)}")
            submitted = bool(self._delegate(text, objective or text))
            return {"kind": "delegated", "reply": reply, "objective": objective, "submitted": submitted, "spoken": spoken}
        self._record(text, reply)
        self._show(reply, True)
        return {"kind": "reply", "reply": reply, "spoken": spoken}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _remember(self, text: str, reply: str) -> None:
        self._history.append({"role": "user", "content": text})
        if reply:
            self._history.append({"role": "assistant", "content": reply})
            self._last_reply = reply

    def _close_speech(self, opened: bool) -> None:
        if not opened:
            return
        speech = self._speech()
        if speech is not None:
            try:
                speech.finish()
            except Exception:
                pass

    def _speak_whole(self, reply: str) -> bool:
        self._show(reply, True)
        safe = self._speech_safe(reply)
        speech = self._speech()
        if not safe or speech is None or not self._can_speak(safe):
            return False
        try:
            return bool(speech.speak(safe))
        except Exception:
            return False
