"""Shared runtime intent and tool-signal classifiers.

These helpers keep Gateway lifecycle decisions and Agent evidence checks aligned
without letting either layer mutate taskboard truth. They are pure classifiers;
Gateway still owns board creation and Agent still owns row advancement.
"""
from __future__ import annotations

import difflib
import re

from ..tooling.sandbox import (
    _mask_quoted_shell_text,
    _shell_has_ambiguous_controls,
    _split_shell_words,
    shell_command_is_inspection_only,
    shell_command_is_mutating,
)

_VERIFICATION_COMMAND_RE = re.compile(
    r"^("
    r"pytest|unittest|compileall|py_compile|mypy|pyright|"
    r"npm\s+test|npm\s+run\s+test|pnpm\s+test|pnpm\s+run\s+test|yarn\s+test|"
    r"vitest|jest|go\s+test|cargo\s+test|dotnet\s+test|git\s+diff\s+--check|"
    r"core\.diagnostics\.(?:test_suite|test_preflight)"
    r")(?=\s|$)"
)

_EXPLICIT_EXECUTION_REQUEST_RE = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+|"
    r"i\s+(?:want|need)\s+you\s+to\s+)?(?:run|execute)\b(?P<command>.+)$",
    re.I | re.S,
)

_EXPLICIT_WORK_START_RE = re.compile(
    r"^\s*(?:(?:(?:yes|ok(?:ay|at)?|sure|good)[,!.]?\s+)?"
    r"(?:(?:please\s+)?go\s+ahead(?:\s+and)?\s+)?(?:please\s+)?"
    r"(?:(?:let(?:['’])?s\s+)?(?:start|begin)(?:\s+(?:working|work|the\s+work)(?:\s+(?:on\s+)?\S.+?)?"
    r"|\s+on\s+(?:it|this|that|the\s+(?:task|tasks|work)))?"
    r"|let(?:['’])?s\s+work\s+on\s+\S.+?)"
    r"|(?:(?:yes|ok(?:ay|at)?|sure|good)[,!.]?\s+)(?:please\s+)?go"
    r"|(?:(?:yes|ok(?:ay|at)?|sure|good)[,!.]?\s+)?proceed(?:\s+please)?"
    r"\s+with\s+(?:all|the)\s+\S.+?)"
    r"(?:\s+(?:now|please))?\s*[.!?]*\s*$",
    re.I,
)
_REASSERTED_WORK_START_RE = re.compile(
    r"\b(?:(?:i|we)\s+(?:already\s+)?(?:asked|told)\s+you(?:\s+to)?|"
    r"you\s+(?:(?:were|are)\s+supposed\s+to|(?:need|have)\s+to|should))\b"
    r"[^.!?\n]{0,100}\b(?:start|begin)\s+(?:working|work|the\s+work|"
    r"on\s+(?:it|this|that|the\s+(?:task|tasks|work)))\b",
    re.I,
)
_SCOPED_WORK_CONTINUATION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:continue|contuine|contune)\s+(?:the\s+)?(?:"
    r"work(?:\s+on\s+\S.+?)?|working\s+on\s+\S.+?|"
    r"[^.!?\n]{1,120}\b(?:work|tasks?|changes?|implementation))"
    r"(?:\s+(?:now|please))?\s*[.!?]*\s*$",
    re.I,
)
_RESUME_ACK_RE = re.compile(r"^(?:yes|ok(?:ay|at)?|sure|good)\s*$", re.I)
_DIRECT_RESUME_CLAUSE_RE = re.compile(
    r"^(?:(?:yes|ok(?:ay|at)?|sure|good)[,\s]+)?"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:continue|contuine|resume|carry\s+on|proceed)(?:\s+please)?"
    r"(?:\s+with\s+(?:it|this|that|them|these|those|all))?$",
    re.I,
)
_CORRECTIVE_WORK_DISSATISFACTION_RE = re.compile(
    r"\b(?:i|we)\s+(?:do\s+not|don['’]?t|dont|did\s+not|didn['’]?t)\s+like\b|"
    r"\b(?:i|we)(?:'m|\s+am|\s+are)\s+(?:not\s+happy|unhappy)\s+with\b",
    re.I,
)
_CORRECTIVE_WORK_TARGET_RE = re.compile(
    r"\b(?:code|interface|ui|branding|theme|skin|renderer|activity|lane|layout|"
    r"style|behaviou?r|implementation|tests?|taskboard)\b",
    re.I,
)
_NEGATED_REASSERTED_WORK_START_RE = re.compile(
    r"\b(?:i|we)\s+(?:(?:never|did\s+not|didn['’]?t)\s+(?:ask(?:ed)?|tell|told)\s+you(?:\s+to)?|"
    r"(?:asked|told)\s+you(?:\s+to)?\s+not\s+(?:to\s+)?)"
    r"[^.!?\n]{0,40}\b(?:start|begin)\b",
    re.I,
)
_DECLINED_WORK_START_RE = re.compile(
    r"\b(?:(?:do\s+not|don['’]?t|dont)\s+(?:start|begin)|stop)\s+"
    r"(?:working|work|the\s+work|on\s+(?:it|this|that|the\s+(?:task|tasks|work)))\b",
    re.I,
)

# One conservative vocabulary owns inflection and light-typo recovery for
# engineering actions.  Routing consumers normalize through this helper before
# applying their own semantic rules; the vocabulary does not itself declare a
# turn to be work.
WORK_ACTION_BASES = (
    "address",
    "analyse",
    "analyze",
    "audit",
    "debug",
    "diagnose",
    "implement",
    "inspect",
    "investigate",
    "migrate",
    "refactor",
    "repair",
    "reproduce",
    "review",
    "simplify",
    "validate",
    "verify",
)
_WORK_ACTION_LOOKUP = frozenset(WORK_ACTION_BASES)
_WORK_ACTION_MAX_LENGTH = max(map(len, WORK_ACTION_BASES))
_WORK_ACTION_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]{4,}")
_OPERATOR_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]{5,}")
_MO_TRACE_REFERENCE_RE = re.compile(
    r"(?i)(?:\bmo\s+trace\b\W{0,24})?(?:\bsession\b\W{0,24})?"
    r"\btrace_\d{8}_\d{6}_\d{6}\b"
)

_DESCRIPTIVE_IMPLEMENTATION_QUESTION_RE = re.compile(
    r"(?is)(?:^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:what|which|why|how|explain|describe|tell\s+me\s+about|define)\b"
    r"[^.?!\n]{0,180}\bsimplif\w*\b|"
    r"\A(?=.*\b(?:what|which|where|why|how|explain|describe)\b)"
    r"(?=.*\b(?:implemented|built|added|existing|available|present|covered|supported)\b))"
)
_DIRECT_IMPLEMENTATION_ACTION_RE = re.compile(
    r"(?is)(?:^\s*|[.?!;]\s*|\b(?:and|then|also)\s+)"
    r"(?:(?:please|now)\s+|(?:can|could|would|will)\s+you\s+|"
    r"i\s+(?:want|need)\s+you\s+to\s+)*"
    r"(?:add|address|analyse|analyze|audit|build|create|debug|delete|diagnose|edit|fix|"
    r"implement|inspect|investigate|maintain|migrate|modify|refactor|remove|repair|"
    r"replace|reproduce|review|simplify|update|validate|verify|write)\b"
)

_SELF_DIRECTED_ACTION_EXPLANATION_RE = re.compile(
    r"(?is)^\s*(?:(?:no|ok(?:ay)?|well|so)[,!.]?\s+)*(?:"
    r"how\s+(?:do|can|could|should|would)\s+(?:i|we)\b|"
    r"what\s+(?:can|could|should|would)\s+(?:i|we)\s+"
    r"(?:add|build|change|create|edit|fix|implement|install|modify|refactor|remove|"
    r"repair|replace|set|update|use|write)\w*\b|"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:(?:show|tell)\s+(?:me|us)\s+how\s+to\b|"
    r"explain(?:\s+to\s+(?:me|us))?\s+how\s+to\b)|"
    r"(?:i|we)\s+(?:"
    r"(?:want|need|plan|intend)\s+to\b[^?!.]{0,200}\bhow\b\s*[?!.]*$|"
    r"meant\s+to\b|"
    r"(?:want|need|plan|intend)\s+to\b[^?!.]{0,160}"
    r"\b(?:manually|myself|ourselves)\b"
    r")"
    r")"
)
_AGENT_DIRECTED_ACTION_RE = re.compile(
    r"(?is)\b(?:can|could|would|will)\s+you\b"
    r"(?!\s+(?:please\s+)?(?:show|tell|explain)\b)[^.?!\n]{0,80}"
    r"\b(?:add|build|change|create|edit|fix|install|modify|remove|set|update|use)\w*\b|"
    r"\b(?:i|we)\s+(?:want|need)\s+you\s+to\b"
)

_EXPLICIT_INSTALL_REQUEST_RE = re.compile(
    r"(?is)^\s*(?:(?:yes|ok(?:ay)?|sure|please|now)[,!.]?\s+)*"
    r"(?:(?:can|could|would|will)\s+you\s+|"
    r"(?:i|we)\s+(?:want|need)\s+(?:you\s+)?to\s+)?"
    r"(?:(?:find|fetch|get|download|clone)\b[^.?!\n]{0,140}\band\s+)?"
    r"install\b[^\n]*$"
)


def _normalize_runtime_nouns(text: str) -> str:
    """Recover conservative typos in shared runtime nouns, not whole phrases."""
    def replace(match: "re.Match[str]") -> str:
        word = match.group(0)
        candidate = word.casefold()
        singular = candidate[:-1] if candidate.endswith("s") else candidate
        if singular == "taskboard":
            return "taskboards" if candidate.endswith("s") else "taskboard"
        if abs(len(singular) - len("taskboard")) > 1:
            return word
        close = difflib.get_close_matches(singular, ("taskboard",), n=1, cutoff=0.86)
        if not close:
            return word
        return "taskboards" if candidate.endswith("s") else "taskboard"

    return _OPERATOR_WORD_RE.sub(replace, str(text or ""))


def normalized_operator_text(text: str) -> str:
    """Normalize operator text for intent regexes without interpreting content."""
    value = " ".join(str(text or "").strip().lower().split())
    return _normalize_runtime_nouns(value)


def looks_like_mo_trace_reference(user_input: str) -> bool:
    """Return True when a turn names MO's own runtime trace diagnostic ID."""
    return bool(_MO_TRACE_REFERENCE_RE.search(str(user_input or "")))


def normalize_work_action_words(text: str) -> str:
    """Normalize inflected or lightly mistyped engineering action words.

    This is lexical normalization only.  It intentionally does not decide
    whether prose is an instruction, a question, or a negated action.
    """
    value = str(text or "")
    if not value:
        return value
    # A task heading supplies an action; mentioning its noun in ordinary prose
    # does not. Keep the raw operator message unchanged outside normalization.
    value = re.sub(r"(?i)^\s*(?:code[- ]+)?simplification\s*:", "simplify:", value)

    def replace(match: "re.Match[str]") -> str:
        word = match.group(0)
        lowered = word.casefold()
        candidate = lowered[3:] if lowered.startswith("re-") else lowered
        if candidate in _WORK_ACTION_LOOKUP:
            return candidate
        for suffix in ("ing", "ed", "es", "s"):
            if not candidate.endswith(suffix):
                continue
            stem = candidate[: -len(suffix)]
            stems = [stem]
            if len(stem) > 3 and len(stem) > 1 and stem[-1] == stem[-2]:
                stems.append(stem[:-1])
            for base in stems:
                if base in _WORK_ACTION_LOOKUP:
                    return base
                if base + "e" in _WORK_ACTION_LOOKUP:
                    return base + "e"
        close_candidates = [candidate]
        for suffix in ("ing", "ed", "es", "s"):
            if candidate.endswith(suffix):
                close_candidates.append(candidate[: -len(suffix)])
                break
        for typo_candidate in close_candidates:
            # One adjacent swap scores below the similarity cutoff for short
            # actions such as "review"; keep the same canonical vocabulary.
            if len(typo_candidate) <= _WORK_ACTION_MAX_LENGTH:
                for index in range(len(typo_candidate) - 1):
                    swapped = (
                        typo_candidate[:index] + typo_candidate[index + 1]
                        + typo_candidate[index] + typo_candidate[index + 2:]
                    )
                    if swapped in _WORK_ACTION_LOOKUP:
                        return swapped
            close = difflib.get_close_matches(typo_candidate, WORK_ACTION_BASES, n=1, cutoff=0.86)
            if close:
                return close[0]
        return word

    return _WORK_ACTION_WORD_RE.sub(replace, value)


def looks_like_descriptive_implementation_question(text: str) -> bool:
    """Return whether implementation words describe current state, not authority.

    Questions about existing features may mention earlier implementation across
    sentence boundaries. Preserve that distinction before lexical normalization
    turns ``implemented`` into ``implement``. Explicit current work still wins.
    """
    value = str(text or "").strip()
    return bool(
        value
        and _DESCRIPTIVE_IMPLEMENTATION_QUESTION_RE.search(value)
        and not _DIRECT_IMPLEMENTATION_ACTION_RE.search(normalize_work_action_words(value))
    )


def looks_like_self_directed_action_explanation(text: str) -> bool:
    """Return True when the operator asks how they can act or clarifies their own action.

    Mutation vocabulary alone does not authorize MO to treat a Blender edit,
    spreadsheet change, or other user-performed application action as repository
    maintenance. Explicit requests for MO to perform the action remain work.
    """
    value = str(text or "").strip()
    return bool(
        value
        and _SELF_DIRECTED_ACTION_EXPLANATION_RE.search(value)
        and not _AGENT_DIRECTED_ACTION_RE.search(value)
        and not _DIRECT_IMPLEMENTATION_ACTION_RE.search(
            normalize_work_action_words(value)
        )
    )


def looks_like_explicit_install_request(text: str) -> bool:
    """Return True for a direct request to install a named or contextual target."""
    value = str(text or "").strip()
    return bool(
        value
        and _EXPLICIT_INSTALL_REQUEST_RE.fullmatch(value)
        and not looks_like_self_directed_action_explanation(value)
    )


def looks_like_declined_work_start_request(user_input: str) -> bool:
    """Return True when the operator explicitly rejects starting MO's work."""
    text = str(user_input or "")
    return bool(
        _NEGATED_REASSERTED_WORK_START_RE.search(text)
        or _DECLINED_WORK_START_RE.search(text)
    )


def looks_like_explicit_work_start_request(user_input: str) -> bool:
    """Return True for a standalone instruction to begin the offered work.

    Keep this narrower than application/process ``start`` commands. Those have
    their own work shapes; this signal owns standalone conversational approval
    to begin already-offered work.
    """
    text = str(user_input or "")
    if looks_like_declined_work_start_request(text):
        return False
    return bool(
        _EXPLICIT_WORK_START_RE.fullmatch(text)
        or _REASSERTED_WORK_START_RE.search(text)
        or _SCOPED_WORK_CONTINUATION_RE.fullmatch(text)
    )


def looks_like_corrective_work_feedback(user_input: str) -> bool:
    """Return True for dissatisfaction aimed at implementation or interface work.

    Corrective feedback may omit an imperative (for example, ``I don't like
    this UI change``) while still asking MO to repair current work. Requiring
    both dissatisfaction and a concrete product target keeps ordinary dislikes
    and casual conversation boardless.
    """
    text = str(user_input or "")
    return bool(
        _CORRECTIVE_WORK_DISSATISFACTION_RE.search(text)
        and _CORRECTIVE_WORK_TARGET_RE.search(text)
    )


def looks_like_explicit_execution_request(user_input: str) -> bool:
    """Keep action tools available for execution without assuming a shell target.

    The provider selects the actual tool: ``run the window`` and ``run a script``
    both request execution. Recognized shell inspections retain the read lane.
    Neither an unknown target nor the execution verb establishes project work.
    """
    match = _EXPLICIT_EXECUTION_REQUEST_RE.match(str(user_input or ""))
    if not match:
        return False
    command = str(match.group("command") or "").strip().rstrip(".?!")
    return bool(command and not shell_command_is_inspection_only(command))


def looks_like_interrupted_resume_request(user_input: str) -> bool:
    """Recognize one direct resume clause independently of later constraints."""
    clauses = [
        normalized_operator_text(value).strip(" ,")
        for value in re.split(r"(?:[.!?;\n]+|…+)", str(user_input or ""))
        if normalized_operator_text(value).strip(" ,")
    ]
    while clauses and _RESUME_ACK_RE.fullmatch(clauses[0]):
        clauses.pop(0)
    text = clauses[0] if clauses else ""
    if not text or re.search(r"\b(?:don'?t|do\s+not|dont|stop|leave|cancel)\b", text):
        return False
    return bool(
        _DIRECT_RESUME_CLAUSE_RE.fullmatch(text)
        or (
            re.search(r"\b(?:continue|contuine|resume|proceed|finish|complete|carry\s+on|keep\s+working)\b", text)
            and re.search(
                r"\b(?:interrupted|unfinished|paused|parked)\b|\bprevious\s+work\b|"
                r"\btask\s*board\b|\b(?:current|existing|open|previous)\s+board\b|"
                r"\bfrom\s+(?:task|row)\s*#?\s*\d+\b", text,
            )
        )
        or (
            re.search(r"\b(?:finish|complete|jump\s+back|go\s+back|work\s+on|keep\s+working|pick\s+(?:it|that|this|them)?\s*(?:back\s+)?up)\b", text)
            and re.search(r"\b(?:this|that|it|unfinished|parked|previous|back|left)\b", text)
        )
        or re.search(r"\b(?:re)?focus\b.{0,40}?\b(?:again|back|left|unfinished|parked|previous|work|what\s+was\s+left)\b", text)
    )


def looks_like_contextual_followup(user_input: str) -> bool:
    """Whether a bounded reply needs its preceding task for relevance only.

    This grants no execution/resume authority. Named new targets must keep
    their own query, even when an earlier task still has an open board.
    """
    text = normalized_operator_text(user_input).strip(" .!?,")
    if _DIRECT_RESUME_CLAUSE_RE.fullmatch(text):
        return True
    if re.fullmatch(
        r"(?:have|did)\s+you\s+(?:actually\s+)?"
        r"(?:check|checked|review|reviewed|verify|verified|test|tested)\s+"
        r"(?:it|this|that|them|these|those|everything|all|the\s+current\s+implementation)"
        r"(?:\s+(?:entirely|fully|completely|all))?",
        text,
    ):
        return True
    return bool(re.fullmatch(
        r"(?:(?:yes|ok(?:ay)?|good|sure|please|now|and|then)[,\s]+)*"
        r"(?:let(?:['’])?s\s+)?(?:fix|repair|review|check|verify|test|finish|start|begin|approved?)"
        r"(?:\s+(?:it|this|that|them|these|those|all|everything|working|work|"
        r"your|the|findings|changes|report|please|now))*",
        text,
    ))


def looks_like_taskboard_repair_request(user_input: str) -> bool:
    """Return True when the operator explicitly asks about an open/stuck board."""
    text = normalized_operator_text(user_input)
    if "taskboard" not in text and "task board" not in text:
        return False
    return bool(re.search(
        r"\b(?:open|left|stuck|hanging|dangling|blocked|error|close|closed|clean|clear|why)\b",
        text,
    ))


def shell_is_verification_command(command: str) -> bool:
    """Recognize a direct verifier invocation, never words in command data."""
    value = str(command or "").strip()
    if (
        not value or _shell_has_ambiguous_controls(value)
        or re.search(r"`|\$\(", value)
        or re.search(r"[;&|<>\r\n]", _mask_quoted_shell_text(value))
    ):
        return False
    parts = _split_shell_words(value)
    tokens = [part.strip("\"'") for part in parts]
    if not tokens:
        return False
    if tokens[0].lower() in {"mo", "mo.exe"}:
        # The native quality checker is a verifier regardless of whether it
        # arrived through shell or test_runner. Admit only its literal CLI form.
        return bool(
            len(tokens) == 4
            and parts[0].lower() in {"mo", "mo.exe"}
            and parts[1:3] == ["--explainer", "check"]
            and tokens[3] and not tokens[3].startswith("-")
            and re.fullmatch(r'"[^"\r\n]+"|\'[^\'\r\n]+\'|[^\s\'"~]+', parts[3])
            and not re.search(r"[$`%!^~*?{}\[\];&|<>\r\n]", value)
        )
    executable = re.split(r"[\\/]", tokens[0])[-1].lower().removesuffix(".exe")
    tokens[0] = executable
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?|py", executable):
        idx = 1
        while idx < len(tokens) and (
            tokens[idx] in {"-u", "-B", "-E", "-I", "-s", "-S", "-O", "-OO"}
            or executable == "py" and re.fullmatch(r"-\d+(?:\.\d+)?", tokens[idx])
        ):
            idx += 1
        if tokens[idx:idx + 1] != ["-m"]:
            return False
        tokens = tokens[idx + 1:]
    # Linter configuration/options can write files. Preserve proof only for
    # explicit no-write modes followed by path operands; unknown options rerun.
    if tokens and tokens[0] == "ruff":
        return tokens[:3] in (
            ["ruff", "check", "--no-fix"],
            ["ruff", "format", "--check"],
            ["ruff", "format", "--diff"],
        ) and all(not token.startswith("-") for token in tokens[3:])
    if tokens and tokens[0] == "eslint":
        return tokens[:2] == ["eslint", "--fix-dry-run"] and all(
            not token.startswith("-") for token in tokens[2:]
        )
    if tokens and tokens[0] == "git" and not shell_command_is_inspection_only(value):
        return False
    return bool(_VERIFICATION_COMMAND_RE.match(" ".join(tokens).lower()))


def tool_is_verification_signal(tool_name: str, arguments: dict | None = None) -> bool:
    """Return True when a tool call represents verification/test evidence."""
    name = str(tool_name or "").strip()
    if name == "test_runner":
        return True
    if name in {
        "computer_observe", "phone_context", "phone_files",
        "phone_storage_report", "phone_file_read",
        "phone_capabilities", "phone_system_status", "phone_cache_report", "phone_packages",
    }:
        return True
    if name != "shell":
        return False
    command = str((arguments or {}).get("command") or "")
    return shell_is_verification_command(command)


def tool_is_runtime_work_signal(tool_name: str, arguments: dict | None = None) -> bool:
    """Return True for tool calls that imply doing work, not read-only orientation."""
    name = str(tool_name or "").strip()
    args = arguments or {}
    if name in {"write_file", "edit_file", "test_runner", "map_project"}:
        return True
    if name in {
        "computer_act", "point_on_screen", "phone_click", "phone_set_text",
        "phone_scroll", "phone_key", "phone_file_delete", "phone_cache_trim",
        "phone_package_action", "phone_shell",
    }:
        return True
    if name == "shell":
        command = str(args.get("command") or "")
        if shell_is_verification_command(command):
            return True
        return shell_command_is_mutating(command)
    return False
