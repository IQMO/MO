"""Shared turn-intent routing for runtime truth, context, and boards.

This module is intentionally deterministic and side-effect free. The provider
still owns reasoning and wording; this only decides which runtime facts and
taskboard scaffold are safe to put in front of it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import re

from .work_signals import (
    looks_like_corrective_work_feedback,
    looks_like_descriptive_implementation_question,
    looks_like_explicit_execution_request,
    looks_like_explicit_install_request,
    looks_like_explicit_work_start_request,
    looks_like_interrupted_resume_request,
    looks_like_mo_trace_reference,
    looks_like_taskboard_repair_request,
    normalize_work_action_words,
    normalized_operator_text,
)

KIND_CHAT = "chat"
KIND_LOOKUP = "lookup"
KIND_STATUS = "status"
KIND_RESUME = "resume"
KIND_TASKBOARD_REPAIR = "taskboard_repair"
KIND_WORK = "work"

CONTEXT_CHAT = "chat"
CONTEXT_GREETING = "greeting"
CONTEXT_LOOKUP = "lookup"
CONTEXT_MEMORY = "memory"
CONTEXT_PROFILE = "profile"
CONTEXT_RUNTIME_STATUS = "runtime_status"
CONTEXT_WORK = "work"

BOARD_NONE = "none"
BOARD_RESUME = "resume"
BOARD_PROCEDURE = "procedure"
BOARD_MODEL_PLAN = "model_plan"

# A follow-up challenge to prior claims ("are you sure? verify your findings",
# "double-check that", "prove it"). Narrow on purpose: these phrases demand
# re-verification with evidence, so the turn needs the bounded read catalog —
# never the toolless chat lane. Full work requests that merely contain
# "double-check" are classified by the work branches before this fires.
_VERIFICATION_CHALLENGE_RE = re.compile(
    r"(?i)\b(?:"
    r"are\s+you\s+sure"
    r"|(?:re-?)?verif(?:y|iy)\s+(?:your|the|these|those|all)\s+(?:findings?|claims?|answers?|reports?|results?|conclusions?)"
    r"|double-?\s?check\s+(?:that|this|it|your|the|everything)"
    r"|prove\s+(?:it|that|this)"
    r"|did\s+you\s+(?:actually\s+)?(?:verify|check|test|confirm)"
    r"|(?:is|was)\s+(?:this|that)\s+(?:actually\s+)?(?:true|correct|right|accurate)(?=\s*(?:[?!.]|$))"
    r"|(?:is|was)\s+it\s+(?:actually\s+)?(?:true|correct|accurate)(?=\s*(?:[?!.]|$))"
    # A flat contradiction of MO's own last answer is a strong demand for
    # evidence. Bare "no" is excluded — it is ordinary conversation, not a
    # verification challenge.
    r"|(?:that(?:'|’)?s|that\s+is|this\s+is|you(?:'|’)?re|you\s+are)\s+(?:not\s+right|wrong|incorrect|mistaken)"
    r"|\bnot\s+(?:true|correct|right)\b"
    r"|\bwrong\b[^.?!\n]{0,40}\b(?:we|i|there)\b"
    r")\b"
)


_SURFACE_LINEAGE_PATTERN = (
    r"what\s+(?:(?:exactly|specifically|actually|precisely)\s+)?(?:were|are)\s+we\s+(?:busy\s+with|working\s+on|doing)"
    r"|where\s+(?:(?:exactly|specifically|precisely)\s+)?(?:were|are)\s+we"
    r"|what\s+(?:(?:exactly|specifically|actually|precisely)\s+)?(?:was|is)\s+(?:the\s+)?last\s+(?:thing|task|work)"
    r"|(?:what\s+(?:(?:exactly|specifically|actually|precisely)\s+)?(?:was|is)|(?:tell|remind)\s+me(?:\s+about)?)\s+(?:(?:our|the|my|your)\s+)?(?:last|previous|recent)\s+(?:chat|conversation|session)"
    r"|what\s+happened\s+(?:in|during)\s+(?:(?:our|the|my|your)\s+)?(?:last|previous|recent)\s+(?:chat|conversation|session)"
    r"|what\s+(?:(?:exactly|specifically|actually|precisely)\s+)?did\s+we\s+(?:do|finish|work\s+on)"
    r"|where\s+(?:(?:exactly|specifically|precisely)\s+)?did\s+we\s+(?:leave|left)\s+off"
    r"|continue\s+(?:where\s+we\s+left|from\s+last)"
    r"|what\s+(?:was|were)\s+(?:my|our|the)\s+(?:request|message|question|ask)\b"
    r"[^.?!\n]{0,80}\b(?:last|previous|prev|prior|recent)\s+(?:chat|conversation|session)"
    r"|(?:(?:our|the|my|your)\s+)?(?:last|previous|prior|recent)\s+(?:work|tasks?|changes?)"
)

_SURFACE_LINEAGE_RE = re.compile(r"(?i)\b(" + _SURFACE_LINEAGE_PATTERN + r")\b")

# One spelling-tolerant atom owns recall routing. Keep observed variants here
# instead of letting continuity and episodic memory drift into separate lists.
_REMEMBER_WORD_PATTERN = r"reme(?:m(?:ber|ebr|eber)|ber)"

_CONTINUITY_RE = re.compile(
    r"(?i)\b("
    + _SURFACE_LINEAGE_PATTERN
    # Past-work asks use a generous semantic net, not enumerated phrasings:
    # three natural variants leaked through narrower patterns in one day
    # ("our last tasks", "what did we do", "what was our work"), and a miss
    # lands in the bare chat lane where the model has fabricated a work
    # history. A false positive only costs a status answer with the live
    # snapshot and recall — always a safe way to answer these.
    + r"|what\s+(?:was|were|is|are|have)\b[^.?!\n]{0,30}\b(?:we|our|us|i|my|you)\b[^.?!\n]{0,30}\b(?:work(?:ing)?|tasks?|things?|doing|busy|focus(?:ed)?|done)"
    r"|what\s+(?:did|do|have)\s+we\b[^.?!\n]{0,20}\b(?:do(?:ing)?|done|work(?:ed|ing)?\s*(?:on)?|busy)"
    r"|where\s+(?:did\s+we|were\s+we|we\s+were)\b[^.?!\n]{0,20}\b(?:leave\s+off|stop|busy|at|working)"
    r"|what\s+have\s+we\s+been\s+(?:doing|working\s+on|busy\s+with)"
    r"|what\s+(?:(?:is|are)\s+)?still\s+open"
    r"|what\s+(?:(?:do|did)\s+(?:you|we)\s+)?(?:still\s+have|have\s+still)\s+open"
    r"|what\s+(?:still\s+)?remains\s+open"
    r"|what(?:'s|\s+is)\s+(?:left|remaining|open)"
    r"|what\s+(?:(?:is|are)\s+)?(?:still\s+)?(?:left|remaining|open)"
    r"|(?:anything|something|tasks?|rows?|work)\s+still\s+open"
    r"|what\s+(?:is|are)\s+(?:the\s+)?(?:\d+\s+)?(?:task|tasks|row|rows)\s+(?:still\s+)?open"
    r"|what\s+(?:is|are)\s+(?:the\s+)?(?:\d+\s+)?(?:open|unfinished|resumable)\s+(?:task|tasks|row|rows|work)"
    # A direct recent-session recall is continuity, not generic autobiographical
    # memory. Without this routing, the request can re-read guessed project files
    # instead of using the canonical saved-session record.
    rf"|do\s+you\s+(?:{_REMEMBER_WORD_PATTERN})\b[^.?!\n]{{0,40}}\b(?:last|previous|recent)\s+(?:chat|conversation|session)"
    r")\b"
)

# These short fragments are useful direct status commands, but matching them
# inside a longer product/design sentence misroutes ordinary work into a
# continuity lookup. Keep them anchored so only the whole request is status.
_BARE_CONTINUITY_RE = re.compile(
    r"(?i)^\s*(?:"
    r"(?:show|tell)\s+me\s+(?:the\s+)?current\s+(?:work|task|state)|"
    r"current\s+(?:work|task|state)|"
    r"work\s+in\s+flight|"
    r"active\s+(?:work|task|taskboard)|"
    r"(?:open|unfinished|resumable)\s+(?:task|tasks|row|rows|work)|"
    r"what\s+now|"
    r"resume\s+(?:the\s+)?(?:last|previous|current)?\s*work|"
    r"(?:report|summari[sz]e)\s+(?:so\s+far\s+of\s+)?(?:the\s+)?"
    r"(?:progress|work)(?:\s+(?:so\s+far|to\s+date))?"
    r")\s*(?:please\s*)?[?!.]*\s*$"
)

_TASKBOARD_STATUS_RE = re.compile(
    r"(?i)^\s*(?:what(?:'s|\s+is)?\s+)?(?:(?:on|in)\s+(?:the\s+)?taskboard|taskboard)\s*[?!]*\s*$"
)

# Questions about what MO already did, or whether it should report instead of
# repeating prior work, are continuity/status requests even when they contain
# action words such as "investigating", "fix", or "report".  Without this
# distinction, the generic work-pattern selector treats the question itself as
# authorization to start another investigation.
_PRIOR_WORK_STATUS_RE = re.compile(
    r"(?is)(?:"
    r"^\s*(?:(?:so|but|and)\s+)?(?:are|were)\s+you\s+(?:going\s+to|planning\s+to)\b"
    r"[^?\n]{0,180}\b(?:re-?\s?do|repeat|restart|continue|report|summari[sz]e)\b"
    r"|^\s*(?:(?:so|but|and)\s+)?(?:did|have|will)\s+(?:you|we)\b"
    r"[^?\n]{0,160}\b(?:re-?\s?do|repeat|restart|finish|complete|report|summari[sz]e)\b"
    r"|\bwhat\s+(?:did|have)\s+(?:you|we)\s+(?:already\s+)?"
    r"(?:fix|finish|complete|resolve|change|find|do|report)\b"
    r"|\bwhat\s+(?:you|we)\s+(?:already\s+)?"
    r"(?:fixed|finished|completed|resolved|changed|found|did)\b"
    r"|\b(?:is|was|are|were)\s+(?:everything|everthing|it|this|that|"
    r"the\s+(?:work|investigation|audit|review|report))\s+(?:already\s+)?"
    r"(?:fixed|done|finished|complete|completed|resolved)\b"
    r"|\b(?:everything|everthing|it|this|that|the\s+(?:work|investigation|audit|review|report))\s+"
    r"(?:is|was|are|were)\s+(?:already\s+)?(?:fixed|done|finished|complete|completed|resolved)\b"
    r")"
)

_PRIOR_WORK_REPORT_REQUEST_RE = re.compile(
    r"(?is)^\s*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:(?:please|just|simply)\s+)*"
    r"(?:(?:proceed|continue)\s+or\s+)?(?:report|summari[sz]e)\b"
)

_PRIOR_WORK_REUSE_RE = re.compile(
    r"(?is)\b(?:"
    r"already\s+(?:investigat(?:e|ed|ing)|analy[sz](?:e|ed|ing)|review(?:ed|ing)?|"
    r"audit(?:ed|ing)?|check(?:ed|ing)?|fix(?:ed|ing)?|finish(?:ed|ing)?|"
    r"complet(?:e|ed|ing)|found|done)"
    r"|(?:no\s+need|do\s+not|don't)\s+(?:(?:for|to)\s+)?"
    r"(?:(?:another|a\s+new)\s+)?(?:pass|investigat(?:e|ion|ing)|review|audit|"
    r"analy[sz](?:e|is|ing)|check|repeat|re-?do|restart)"
    r")\b"
)

_WORK_ACTION_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:review|audit|debug|investigate|diagnose|analy[sz]e|inspect|verify|"
    r"refactor|simplify|implement|migrate|validate|reproduce|repair|address|fix|edit|change|test|check|"
    r"commit|push|deploy|merge|release|publish)\b"
)
_APPROVED_ACTION_REQUEST_RE = re.compile(
    r"(?i)\bapproved\s*:\s*(?:please\s+)?"
    r"(?:create|push|open|run|capture|fix|repair|implement|commit|deploy|merge|update|change|add|remove)\b"
)

_FILE_ARTIFACT_REQUEST_RE = re.compile(
    r"(?i)\b(?:turn|convert|make|put|export|render|compile|dump|save|write)\s+"
    r"(?:this|that|it|these|those|them|everything|the\s+above|all\s+(?:of\s+)?(?:this|that|it))\b"
    r"[^.?!\n]{0,40}?"
    r"\b(?:file|md|markdown|document|report|readme|csv|json|yaml|txt|pdf)\b"
)

_PROCEDURE_SEED_PATTERNS = {
    "clone_adopt",
    "platform_migration",
}


TRIVIAL_GREETINGS = frozenset({
    "hi", "hello", "hey", "yo", "hi mo", "hello mo", "hey mo",
    "thanks", "thank you", "ok", "okay", "yes", "no", "y", "n", "sup", "gm",
})

_PROFILE_SUBJECT_RE = (
    r"(?:profile|name|preferences?|style|projects?|repos?|repositories|servers?|vps|"
    r"deploy(?:ments?|\s+methods?)?|accounts?|paths?|platforms?)"
)
_PROFILE_QUESTION_RE = re.compile(
    r"\b(who\s+am\s+i|about\s+me|know\s+(?:about\s+)?me|remember\s+(?:about\s+)?me"
    rf"|my\s+(?:(?:current|known|saved|configured|all)\s+)?{_PROFILE_SUBJECT_RE}"
    rf"|(?:how\s+many|what|which)\s+{_PROFILE_SUBJECT_RE}\s+"
    r"(?:(?:do\s+)?i\s+(?:have|own)|are\s+mine)"
    rf"|(?:do|did)\s+i\s+(?:have|own)\s+(?:any\s+)?{_PROFILE_SUBJECT_RE}"
    rf"|(?:list|show)\s+(?:the\s+)?{_PROFILE_SUBJECT_RE}\s+i\s+(?:have|own)"
    r"|who\s+are\s+you|about\s+yourself|your\s+(?:identity|maker|creator|profile))\b",
    re.I,
)
_TERM_LOOKUP_RE = re.compile(
    r"\b(what\s+(?:does|do|is)|define|meaning\s+of|remind\s+me)\b.{0,60}"
    r"\b(mean|means|term|shorthand|definition|stand\s+for)\b",
    re.I | re.S,
)
_MEMORY_RECALL_RE = re.compile(
    rf"\b(?:do\s+you\s+(?:{_REMEMBER_WORD_PATTERN})|can\s+you\s+recall|what\s+did\s+i\s+(?:tell|say|mention)"
    r"|what\s+have\s+i\s+(?:told|said|mentioned)|what\s+did\s+we\s+(?:say|discuss|talk\s+about)"
    r"|what\s+(?:was|were)\s+(?:my|our|the)\s+(?:request|message|question|ask)\b"
    r"[^.?!\n]{0,80}\b(?:last|previous|prev|prior|recent)\s+(?:chat|conversation|session)"
    r"|from\s+(?:our|the)\s+(?:earlier|previous|last)\s+(?:chat|conversation|session)"
    r"|(?:earlier|previously)\s+in\s+(?:this|our|the)\s+(?:same\s+)?(?:persisted\s+)?(?:chat|conversation|session)"
    r"|i\s+(?:told|said|mentioned)\s+(?:you\s+)?(?:earlier|before|yesterday|last\s+time))\b",
    re.I | re.S,
)
_DURABLE_MEMORY_WRITE_RE = re.compile(
    r"\b(?:remember|save|store|record|learn|update|forget|remove)\b.{0,80}"
    r"\b(?:this|that|my|preference|profile|fact|memory|about\s+me)\b",
    re.I | re.S,
)
_TOOL_ACTION_RE = re.compile(
    r"\b(?:look\s*up|browse|fetch|open|read|run|execute|calculate|compute|list|show|"
    r"search|find|locate|inspect|check|verify|test|query)\b",
    re.I,
)
_CAPABILITY_STATE_LOOKUP_RE = re.compile(
    r"\b(?:what|which|is|are|show|list|check)\b"
    r"(?=[^?.!\n]{0,140}\b(?:mcps?|connectors?|plugins?|apis?)\b)"
    r"(?=[^?.!\n]{0,140}\b(?:tools?|capabilit(?:y|ies)|status|expos\w*|available|"
    r"configured|enabled|connected|exist(?:s|ing)?)\b)[^?.!\n]{0,140}",
    re.I,
)
_LOCAL_OR_EXTERNAL_TARGET_RE = re.compile(
    r"https?://|www\.\w|(?:^|\s)(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])|"
    r"\b(?:file|folder|directory|repo|repository|codebase|source|function|class|tests|"
    r"test\s+(?:case|file|module|runner|suite)|"
    r"log|config|branch|commit|git|server|vps|service|terminal|desktop|screen|window|image|screenshot|"
    r"attachment|mcps?|connectors?|plugins?|apis?)\b|"
    r"\b[\w.-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg|csv|log|pdf|docx?|xlsx?|png|jpe?g)\b",
    re.I,
)
_WORK_ACTION_WORD_PATTERN = (
    r"(?:add\w*|build\w*|chang\w*|clean\w*|cod\w*|configur\w*|creat\w*|"
    r"debug\w*|delet\w*|deploy\w*|edit\w*|execut\w*|fix\w*|implement\w*|"
    r"integrat\w*|merg\w*|modif\w*|optimi[sz]\w*|push\w*|refactor\w*|"
    r"remov\w*|renam\w*|repair\w*|replac\w*|rewrit\w*|run\w*|simplif\w*|"
    r"test\w*|touch\w*|updat\w*|use|wir\w*|writ\w*)"
)
_NEGATED_WORK_ACTION_CLAUSE_RE = re.compile(
    r"\b(?:do\s+not|don['’]?t|never|without)\s+(?:please\s+)?"
    rf"{_WORK_ACTION_WORD_PATTERN}\b"
    r"[^.;\n]*?(?=\b(?:but|instead|then)\b|"
    rf",\s*(?=(?:please\s+)?{_WORK_ACTION_WORD_PATTERN}\b)|[.;\n]|$)",
    re.I,
)

_RECENT_SESSION_FOLLOWUP_RE = re.compile(
    r"(?i)\bcheck\b[^.?!\n]{0,24}\bwhat\s+was\s+it\b[^.?!\n]{0,48}"
    r"\bour\s+(?:intents?|intentions?|goals?|focus|itendes)\b"
)

_PRIOR_ISSUE_VERIFICATION_RE = re.compile(
    r"(?i)^\s*(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:check|verify|confirm|test)\b[^.?!\n]{0,32}"
    r"\b(?:the|this|that|these|those|same|prior|earlier|previous|reported)\s+"
    r"(?:issues?|problems?|bugs?|failures?|defects?)\b"
    r"(?:\s+(?:still\s+)?(?:exists?|remains?|occurs?|happens?)\b"
    r"|\s+(?:is|are|was|were)\s+(?:still\s+)?(?:present|fixed|resolved|gone)\b"
    r"|\s+(?:fixed|resolved)\b)"
)
from .capability_routing import (
    capability_hints_for,
    is_capability_question,
    looks_like_current_fact_request,
)

_BOARD_OPEN_REQUEST_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|(?:^|[.!?;]\s+|\b(?:please|mo|hi|hello|hey)\s+)(?:please\s+)?"
    r")"
    r"(?:open|reopen|launch|start|run|show)\s+(?:the\s+|a\s+|my\s+)?"
    r"(?:(?:shared|drawing|draw|diagram|blank|new|fresh|temporary|disposable|mo)\s+)*"
    r"(?:white\s*)?board\b(?!\.[A-Za-z0-9])"
)
_BOARD_OPEN_NEGATION_RE = re.compile(
    r"(?i)\b(?:"
    r"(?:do\s+not|don['’]?t|dont|never)\s+(?:please\s+)?(?:open|reopen|launch|start|run|show)"
    r"|without\s+(?:opening|reopening|launching|starting|running|showing)"
    r")\b"
)
_BOARD_FOLLOWUP_ACTION_PATTERN = (
    r"(?:draw|sketch|diagram|annotate|label|connect|add|move|resize|"
    r"change|update|refine|erase|delete|remove)"
)
_BOARD_FOLLOWUP_CLAUSE_PATTERN = r"[^.!?;\n]*?"
_BOARD_FOLLOWUP_EXPLICIT_RE = re.compile(
    rf"(?i)(?:"
    rf"\b{_BOARD_FOLLOWUP_ACTION_PATTERN}\b{_BOARD_FOLLOWUP_CLAUSE_PATTERN}"
    rf"\b(?:board|white\s*board|canvas|diagram|drawing)\b|"
    rf"\b(?:board|white\s*board|canvas|diagram|drawing)\b"
    rf"{_BOARD_FOLLOWUP_CLAUSE_PATTERN}\b{_BOARD_FOLLOWUP_ACTION_PATTERN}\b"
    rf")"
)
_BOARD_FOLLOWUP_RELATIVE_RE = re.compile(
    rf"(?i)(?:"
    rf"\b{_BOARD_FOLLOWUP_ACTION_PATTERN}\b{_BOARD_FOLLOWUP_CLAUSE_PATTERN}"
    rf"\b(?:on|in|from|to)\s+(?:it|there)\b|"
    rf"\b(?:on|in|from|to)\s+(?:it|there)\b"
    rf"{_BOARD_FOLLOWUP_CLAUSE_PATTERN}\b{_BOARD_FOLLOWUP_ACTION_PATTERN}\b"
    rf")"
)
_BOARD_DIRECT_DRAW_RE = re.compile(
    r"(?i)^\s*(?:(?:please|mo)\s+)*(?:draw(?!\s+conclusions?\b)|sketch|diagram)\b"
)
_BOARD_FOLLOWUP_NEGATION_RE = re.compile(
    r"(?i)\b(?:do\s+not|don['’]?t|dont|never)\s+(?:please\s+)?"
    rf"{_BOARD_FOLLOWUP_ACTION_PATTERN}\b"
)
_BOARD_CURRENT_VISUAL_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:current|actual|visible|open)\s+(?:terminal|screen|window|interface|ui)\b|"
    r"\b(?:terminal|screen|window|interface|ui)\s+(?:currently\s+)?(?:open|visible)\b|"
    r"\bwhat\s+(?:i|we)\s+(?:can\s+)?see\b"
    r")"
)


def looks_like_trivial_greeting(text: str) -> bool:
    """Return True only for a bare greeting or acknowledgement.

    This is the shared runtime contract used by context, skills,
    code orientation, and provider-tool scoping. Keeping one exact detector
    prevents a greeting variant such as ``hi mo`` from becoming expensive in
    one subsystem even though the rest of the turn already treats it as tiny.
    """
    return str(text or "").strip().lower().strip("!.?") in TRIVIAL_GREETINGS


def looks_like_explicit_board_open_request(text: str) -> bool:
    """Return True only for a direct request to open MO's drawing Board.

    Keep discussion, diagnosis, and how-to questions out of this route.  A
    positive match is deliberately imperative (or ``can/could you``) so merely
    mentioning that a Board should open does not actuate a window.
    """
    value = str(text or "").strip()
    if not value or _BOARD_OPEN_NEGATION_RE.search(value):
        return False
    return bool(_BOARD_OPEN_REQUEST_RE.search(value))


def looks_like_board_followup_request(
    text: str,
    *,
    allow_relative_target: bool = False,
) -> bool:
    """Return True for a drawing mutation that can continue an active Board.

    The runtime must still prove an exact live terminal binding before this
    detector changes tool exposure. Relative targets such as ``to it`` are only
    admitted when that caller is prepared to make the stateful proof; explicit
    Board targets remain safe for stateless intent classification.
    """
    value = str(text or "").strip()
    if not value or _BOARD_OPEN_NEGATION_RE.search(value) or _BOARD_FOLLOWUP_NEGATION_RE.search(value):
        return False
    return bool(
        _BOARD_DIRECT_DRAW_RE.search(value)
        or _BOARD_FOLLOWUP_EXPLICIT_RE.search(value)
        or (allow_relative_target and _BOARD_FOLLOWUP_RELATIVE_RE.search(value))
    )


def looks_like_current_visual_board_request(text: str) -> bool:
    """Return True when a Board drawing explicitly depends on current pixels."""
    value = str(text or "").strip()
    return bool(
        looks_like_board_followup_request(value, allow_relative_target=True)
        and _BOARD_CURRENT_VISUAL_RE.search(value)
    )


def looks_like_profile_question(text: str) -> bool:
    """Return True when private operator/profile context owns the answer."""
    value = str(text or "").strip()
    return bool(value and (_PROFILE_QUESTION_RE.search(value) or _TERM_LOOKUP_RE.search(value)))


def _managed_workflow_active(text: str) -> bool:
    """True when the input activates an admitted managed workflow.

    Deferred import keeps this module side-effect free; any failure means no
    extension, which must classify exactly like an empty profile."""
    try:
        from .. import local_extensions

        return bool(local_extensions.is_active(text))
    except Exception:
        return False


def looks_like_memory_recall(text: str) -> bool:
    """Return True for an explicit request to recall prior conversation."""
    return bool(_MEMORY_RECALL_RE.search(str(text or "")))


def looks_like_bounded_tool_lookup(text: str) -> bool:
    """Return True when a boardless turn still needs current/tool evidence.

    The test is deliberately capability-shaped rather than topic-specific: an
    explicit tool action, current fact, local/external target, or durable memory
    mutation keeps tools available. Stable explanation and social/creative chat
    remain provider-only.
    """
    value = str(text or "").strip()
    if not value:
        return False
    if looks_like_descriptive_implementation_question(value):
        return True
    if _DURABLE_MEMORY_WRITE_RE.search(value):
        return True
    if _CAPABILITY_STATE_LOOKUP_RE.search(value):
        return True
    if looks_like_current_fact_request(value):
        return True
    target = bool(_LOCAL_OR_EXTERNAL_TARGET_RE.search(value))
    action = bool(_TOOL_ACTION_RE.search(value))
    if target and action:
        return True
    # An explicit operational action is enough even when the target is implicit
    # ("calculate 928 * 47", "run this command", "look it up"). Pure "show me
    # a joke/story" is excluded because show alone has no evidence target.
    return bool(action and re.search(
        r"\b(?:look\s*up|browse|fetch|run|execute|calculate|compute|search|find|locate|inspect|check|verify|query)\b",
        value,
        re.I,
    ))


def _without_negated_work_actions(text: str) -> str:
    """Hide explicit prohibitions from positive work-pattern detection.

    A request such as ``read X; do not create or modify files`` is a bounded
    lookup, not build work. Positive clauses remain intact, so ``fix X; do not
    touch other files`` still routes as work.
    """
    return _NEGATED_WORK_ACTION_CLAUSE_RE.sub(" ", str(text or ""))


def _has_explicit_work_request(text: str) -> bool:
    """Keep direct work and approved action blocks above capability lookup."""
    positive = _without_negated_work_actions(text)
    return bool(
        _WORK_ACTION_PREFIX_RE.search(positive)
        or _APPROVED_ACTION_REQUEST_RE.search(positive)
    )


@dataclass(frozen=True)
class TurnIntent:
    kind: str
    context_policy: str
    board_policy: str
    template: str = "simple_chat"
    work_pattern: str = ""
    work_complexity: str = "simple"
    procedure_name: str = ""
    reason: str = ""
    profile_required: bool = False
    memory_requested: bool = False
    capability_hints: frozenset[str] = field(default_factory=frozenset)

    @property
    def include_continuity(self) -> bool:
        return self.context_policy == CONTEXT_RUNTIME_STATUS or self.reason in {
            "continuity_verification", "continuity_review_lookup",
        }

    @property
    def include_orientation_context(self) -> bool:
        # Actuation needs tools and execution safeguards, not a repository
        # investigation before the provider can open a window or run a command.
        # Keep project rules independent of this optional orientation bundle.
        if self.context_policy == CONTEXT_WORK:
            return self.work_pattern != "execution" and not (
                self.work_pattern == "desktop_task" and self.template == "simple_chat"
            )
        return (
            self.context_policy == CONTEXT_LOOKUP and self.template == "deep_review"
        )

    @property
    def include_profile_context(self) -> bool:
        # Status needs the same bounded, query-matched operational sources as
        # lookup; an empty session must not hide the project's profile record.
        return True

    @property
    def include_full_profile_context(self) -> bool:
        return self.profile_required or self.context_policy in {CONTEXT_PROFILE, CONTEXT_MEMORY, CONTEXT_WORK}

    @property
    def include_memory_context(self) -> bool:
        # Continuity already supplies the exact same-surface conversation that
        # owns a prior-issue recheck. Episodic recall is older orientation; mixing
        # it into that turn can widen a narrow verification back into stale work.
        return (
            self.context_policy == CONTEXT_MEMORY or self.memory_requested
            or (self.context_policy == CONTEXT_WORK and self.include_orientation_context)
        ) and not self.include_continuity

    @property
    def include_workspace_context(self) -> bool:
        return self.context_policy == CONTEXT_WORK and self.include_orientation_context

    @property
    def include_project_context(self) -> bool:
        return self.context_policy == CONTEXT_WORK or self.include_orientation_context

    @property
    def include_mo_control_context(self) -> bool:
        return self.include_orientation_context

    @property
    def include_code_graph_context(self) -> bool:
        return self.include_orientation_context

    @property
    def include_skills_context(self) -> bool:
        # Task-triggered profile skills can own ordinary conversation and
        # bounded lookups too (for example a music request).  Selection is a
        # local relevance check and injects no text when nothing matches.
        # Direct profile questions are already owned by the full profile; loading
        # unrelated task packs there adds weight and can trigger an optional
        # semantic backend for no benefit. Greetings and runtime-status answers
        # stay deliberately minimal too.
        return self.context_policy not in {CONTEXT_GREETING, CONTEXT_PROFILE, CONTEXT_RUNTIME_STATUS}

    @property
    def include_conventions_context(self) -> bool:
        """Whether file/location-scoped code conventions are relevant."""
        return self.include_orientation_context

    @property
    def requires_tools(self) -> bool:
        # Profile capsules are deliberately bounded. Direct profile questions
        # retain a tiny read-only catalog so the model can verify omitted detail
        # instead of turning truncation into a false absence claim.
        return bool(self.capability_hints) or self.context_policy in {
            CONTEXT_LOOKUP,
            CONTEXT_PROFILE,
            CONTEXT_WORK,
        }

    @property
    def lightweight_conversation(self) -> bool:
        return self.context_policy in {CONTEXT_CHAT, CONTEXT_GREETING}


def looks_like_continuity_request(user_input: str) -> bool:
    """Return True for requests whose answer must use live continuity state."""
    text = str(user_input or "")
    if _RECENT_SESSION_FOLLOWUP_RE.search(text):
        return True
    if _WORK_ACTION_PREFIX_RE.search(normalize_work_action_words(text)):
        return False
    return bool(
        _CONTINUITY_RE.search(text)
        or _BARE_CONTINUITY_RE.search(text)
        or _TASKBOARD_STATUS_RE.search(text)
    )


def looks_like_prior_work_status_request(user_input: str) -> bool:
    """Return True for report/status questions about work already attempted."""
    text = str(user_input or "")
    if not text or _VERIFICATION_CHALLENGE_RE.search(text):
        return False
    # A direct action remains work: "investigate what you fixed" must not be
    # demoted merely because its object refers to prior changes.
    if _WORK_ACTION_PREFIX_RE.search(normalize_work_action_words(text)):
        return False
    return bool(
        _PRIOR_WORK_STATUS_RE.search(text)
        or (
            _PRIOR_WORK_REPORT_REQUEST_RE.search(text)
            and _PRIOR_WORK_REUSE_RE.search(text)
        )
    )


def looks_like_surface_lineage_request(user_input: str) -> bool:
    """Return True when the user is asking what happened on the selected thread."""
    return bool(_SURFACE_LINEAGE_RE.search(str(user_input or "")))


def _looks_like_standalone_work_approval(user_input: str) -> bool:
    """Route an explicit approval to work without treating a bare ``yes`` as work."""
    from ..tasking.task_evidence import manual_approval_evidence

    normalized = normalized_operator_text(user_input).strip(" .!?")
    return normalized != "yes" and bool(manual_approval_evidence(user_input))


def _classify_turn_without_capabilities(
    user_input: str,
    *,
    pending_resume: bool = False,
    resumable_available: bool = False,
    queued_request: bool = False,
) -> TurnIntent:
    """Classify a user turn into context and board policies."""
    text = str(user_input or "")
    normalized = normalized_operator_text(text)
    resume_request = looks_like_interrupted_resume_request(text)
    work_start_request = bool(
        looks_like_explicit_work_start_request(text)
        or _looks_like_standalone_work_approval(text)
    )
    taskboard_repair = looks_like_taskboard_repair_request(text)
    corrective_work_feedback = looks_like_corrective_work_feedback(text)
    continuity_request = looks_like_continuity_request(text)
    prior_work_status = looks_like_prior_work_status_request(text)

    if pending_resume or (
        resumable_available
        and (
            resume_request
            or work_start_request
            # A taskboard question queued during an earlier turn describes the
            # state visible when it was submitted.  By the time it is promoted,
            # that board may already be corrected; only an explicit resume
            # clause may carry execution authority across that queue boundary.
            or (taskboard_repair and not queued_request)
        )
    ):
        return TurnIntent(
            kind=KIND_RESUME,
            # Resuming is an execution turn, not a status answer. It needs the
            # normal work catalog and project safeguards on the very first
            # provider request; the preserved board supplies the exact phase.
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_RESUME,
            reason="resume_available",
        )

    if _PRIOR_ISSUE_VERIFICATION_RE.search(text):
        # This is evidence work about the issue named by the immediately prior
        # conversation, not a generic current-fact lookup. It needs that bounded
        # continuity record plus normal verification tools (including tests),
        # while the narrow scope does not justify a taskboard by itself.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_NONE,
            reason="continuity_verification",
        )

    if _managed_workflow_active(text):
        # An admitted managed-work turn is work by definition:
        # the schema layer already keeps tools visible for activation, and the
        # extension owns board rows at build time. Classified as lookup, the
        # dispatch read-only guard blocks the very shells the schemas offered
        # and the board-driven workflow runs boardless. Empty profiles never
        # activate, so this is inert for ordinary users.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            reason="managed_workflow",
        )

    if looks_like_explicit_board_open_request(text):
        # Opening the clarification Board is real local actuation, but it does
        # not create a project taskboard.  Classify it before the greeting seam
        # so "hi mo open board" cannot lose its tool catalog.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_NONE,
            template="problem_solving",
            work_pattern="board_open",
            work_complexity="simple",
            reason="board_open_request",
        )

    if looks_like_board_followup_request(text):
        # A drawing mutation is work even before the stateful Agent proves that
        # an exact linked Board exists.  Only the latter proof narrows the tool
        # catalog to Board operations, so ordinary image/design requests retain
        # their existing owner.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_NONE,
            template="problem_solving",
            work_pattern="board_followup",
            work_complexity="simple",
            reason="board_followup_request",
        )

    if looks_like_mo_trace_reference(text):
        # A MO trace ID is a runtime diagnostic artifact. A bare trace line is
        # an issue-intake shorthand, not stable chat; start with project context
        # and a model-owned board so evidence, fixes, and verification are gated
        # from the first provider request.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            work_pattern="fix_verify",
            work_complexity="moderate",
            reason="trace_diagnostic_reference",
        )

    if looks_like_trivial_greeting(text):
        return TurnIntent(
            kind=KIND_CHAT,
            context_policy=CONTEXT_GREETING,
            board_policy=BOARD_NONE,
            reason="trivial_greeting",
        )

    if is_capability_question(text) and not _has_explicit_work_request(text):
        # Describing an existing capability is a bounded catalog lookup, not a
        # request to exercise that capability or audit its implementation. A
        # direct imperative or explicit approved action block still routes as
        # work even when the surrounding message also discusses capabilities.
        return TurnIntent(
            kind=KIND_LOOKUP,
            context_policy=CONTEXT_LOOKUP,
            board_policy=BOARD_NONE,
            reason="capability_question",
        )

    if looks_like_explicit_install_request(text):
        # Installing an external tool/package is execution work. It needs action
        # tools and evidence, but repository graph/history orientation only adds
        # unrelated context and makes a direct install look like a code review.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            work_pattern="execution",
            work_complexity="simple",
            reason="explicit_install",
        )

    if prior_work_status:
        return TurnIntent(
            kind=KIND_STATUS,
            context_policy=CONTEXT_RUNTIME_STATUS,
            board_policy=BOARD_NONE,
            reason="prior_work_status",
        )

    # A complete request can already identify itself as a bounded read-only
    # lookup through its ``do not edit/modify`` clause.  Removing that clause
    # first made exact source inspections look like procedure work and created a
    # fake one-row board.  Keep the sanitized shape for positive work with a
    # narrow negative scope (``fix X; do not touch unrelated files``), but never
    # let it override the complete request's read-only classification.
    raw_pattern_name = ""
    raw_complexity = "simple"
    if _NEGATED_WORK_ACTION_CLAUSE_RE.search(text):
        _, raw_pattern_name, raw_complexity, _ = _work_shape(text)
    readonly_review = raw_pattern_name == "review_evidence" and raw_complexity == "simple"
    bounded_readonly = bool(
        _NEGATED_WORK_ACTION_CLAUSE_RE.search(text)
        and (not raw_pattern_name or readonly_review)
        and (looks_like_bounded_tool_lookup(text) or readonly_review)
    )
    template, pattern_name, complexity, procedure_name = _work_shape(
        _without_negated_work_actions(text)
    )
    continuity_review = bool(
        pattern_name == "review_evidence"
        and not bounded_readonly
        and (
            continuity_request
            or (
                complexity == "simple"
                and looks_like_surface_lineage_request(text)
            )
        )
    )
    if pattern_name and not bounded_readonly and not continuity_review:
        board_policy = _board_policy_for_work(pattern_name, procedure_name)
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=board_policy,
            template=template,
            work_pattern=pattern_name,
            work_complexity=complexity,
            procedure_name=procedure_name,
            reason="work_pattern",
        )

    if bounded_readonly or continuity_review:
        # A report-only prior-work review keeps project/graph/continuity context
        # while using the bounded read catalog. set_plan is its semantic escape
        # hatch if the provider finds genuinely broader work.
        return TurnIntent(
            kind=KIND_LOOKUP,
            context_policy=CONTEXT_WORK if continuity_review else CONTEXT_LOOKUP,
            board_policy=BOARD_NONE,
            template=template,
            work_pattern=pattern_name if continuity_review else "",
            work_complexity=complexity,
            reason=(
                "continuity_review_lookup"
                if continuity_review
                else "bounded_readonly_lookup"
            ),
        )

    if corrective_work_feedback:
        # Corrective implementation feedback often names the bad result without
        # repeating an imperative. It is work-shaped, but model-owned planning
        # keeps a board optional when the correction is genuinely small.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            work_pattern="fix_verify",
            work_complexity="moderate",
            reason="corrective_work_feedback",
        )

    if work_start_request and not pattern_name and looks_like_bounded_tool_lookup(text):
        # Approval/reassertion must not inflate a concrete one-artifact fact
        # check into a taskboard. The bounded lookup still receives its current
        # evidence tools and can act immediately.
        return TurnIntent(
            kind=KIND_LOOKUP,
            context_policy=CONTEXT_LOOKUP,
            board_policy=BOARD_NONE,
            template=template,
            reason="bounded_tool_lookup",
        )

    if work_start_request:
        # A start/restart phrase supplies execution authority, not the task's
        # shape. Specific work and bounded lookups above retain their own route;
        # this fallback owns only a standalone approval with no clearer shape.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            work_pattern="explicit_work_start",
            work_complexity="simple",
            reason="explicit_work_start",
        )

    # A request to investigate or change continuity/taskboard state is work;
    # only a passive question about that state belongs to the deterministic
    # status lane.  Work-shape classification therefore precedes these two
    # status fallbacks.
    if taskboard_repair:
        return TurnIntent(
            kind=KIND_TASKBOARD_REPAIR,
            context_policy=CONTEXT_RUNTIME_STATUS,
            board_policy=BOARD_NONE,
            reason="taskboard_repair_status",
        )

    # A challenge to MO's own claim outranks continuity phrasing: the status lane
    # answers from injected continuity context with no tools, which is right for
    # "what were we doing" and wrong the moment the operator says that answer is
    # incorrect. Falls through to the verification-challenge branch below, which
    # owns the bounded evidence catalog.
    if (continuity_request or (resume_request and normalized)) and not _VERIFICATION_CHALLENGE_RE.search(text):
        return TurnIntent(
            kind=KIND_STATUS,
            context_policy=CONTEXT_RUNTIME_STATUS,
            board_policy=BOARD_NONE,
            reason="continuity_status",
        )

    if looks_like_explicit_execution_request(_without_negated_work_actions(text)):
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            work_pattern="execution",
            work_complexity="simple",
            reason="explicit_execution",
        )

    if _FILE_ARTIFACT_REQUEST_RE.search(text):
        # "turn this into an md file" asks for a file, so it needs the write
        # tools. The generic work shape misses it (the verb governs the prior
        # answer, not a code target) and it can otherwise fall through to the
        # toolless chat lane, announce the write without tools, and fabricate a
        # path in the final answer.
        return TurnIntent(
            kind=KIND_WORK,
            context_policy=CONTEXT_WORK,
            board_policy=BOARD_MODEL_PLAN,
            template="problem_solving",
            work_pattern="file_artifact",
            work_complexity="simple",
            reason="file_artifact_request",
        )

    if _VERIFICATION_CHALLENGE_RE.search(text):
        # A verification challenge demands re-checking claims against live
        # state, so it needs the bounded read/evidence catalog — the toolless
        # chat lane would contradict the re-verification contract. Work-shaped
        # requests still win above; this only catches the bare challenge that
        # would otherwise fall to chat.
        return TurnIntent(
            kind=KIND_LOOKUP,
            context_policy=CONTEXT_LOOKUP,
            board_policy=BOARD_NONE,
            template=template,
            reason="verification_challenge",
        )

    if looks_like_memory_recall(text):
        return TurnIntent(
            kind=KIND_CHAT,
            context_policy=CONTEXT_MEMORY,
            board_policy=BOARD_NONE,
            template=template,
            reason="memory_recall",
        )

    profile_question = looks_like_profile_question(text)
    if looks_like_bounded_tool_lookup(text):
        return TurnIntent(
            kind=KIND_LOOKUP,
            context_policy=CONTEXT_LOOKUP,
            board_policy=BOARD_NONE,
            template=template,
            reason="bounded_tool_lookup",
            profile_required=bool(profile_question or _DURABLE_MEMORY_WRITE_RE.search(text)),
        )

    if profile_question:
        return TurnIntent(
            kind=KIND_CHAT,
            context_policy=CONTEXT_PROFILE,
            board_policy=BOARD_NONE,
            template=template,
            reason="profile_question",
        )

    return TurnIntent(
        kind=KIND_CHAT,
        context_policy=CONTEXT_CHAT,
        board_policy=BOARD_NONE,
        template=template,
        reason="simple_chat",
    )


def classify_turn(
    user_input: str,
    *,
    pending_resume: bool = False,
    resumable_available: bool = False,
    queued_request: bool = False,
) -> TurnIntent:
    """Classify the turn and attach pure, permission-free capability hints."""
    intent = _classify_turn_without_capabilities(
        user_input,
        pending_resume=pending_resume,
        resumable_available=resumable_available,
        queued_request=queued_request,
    )
    capability_hints = capability_hints_for(user_input)
    if intent.reason in {"continuity_status", "prior_work_status"}:
        # The deterministic snapshot owns this lane. Words such as "remember"
        # and "recent" can activate generic capability hints, but offering tools
        # recreates the guessed-file search this status route exists to avoid.
        capability_hints = frozenset()
    return replace(
        intent, capability_hints=capability_hints,
        memory_requested=looks_like_memory_recall(user_input),
    )


def _work_shape(user_input: str) -> tuple[str, str, str, str]:
    try:
        from ..context.gateway_helpers import select_template
        from ..context.work_patterns import select_work_pattern
        from ..tasking.procedure import work_procedure_for

        template = select_template(user_input)
        pattern = select_work_pattern(user_input)
        pattern_name = str(getattr(pattern, "name", "") or "")
        complexity = str(getattr(pattern, "complexity", "") or "simple")
        procedure = work_procedure_for(pattern_name) if pattern_name else None
        procedure_name = str(getattr(procedure, "name", "") or "")
        return template, pattern_name, complexity, procedure_name
    except Exception:
        return "simple_chat", "", "simple", ""


def _should_seed_procedure(pattern_name: str, procedure_name: str) -> bool:
    if not procedure_name:
        return False
    # Only replay procedures whose shape is itself the product contract
    # (migration and clone/adopt). Ordinary review/build/design/fix/audit work is
    # user-specific even when the wording is long: complexity must not replace a
    # concrete requested sequence with generic inspect -> edit -> verify rows.
    # Those turns remain provider-authored; a taskboard is available when it is
    # useful, but planning is not a prerequisite for ordinary tool execution.
    return pattern_name in _PROCEDURE_SEED_PATTERNS


def _board_policy_for_work(pattern_name: str, procedure_name: str) -> str:
    if pattern_name in {"reference_comparison", "prd_planning"}:
        return BOARD_NONE
    if _should_seed_procedure(pattern_name, procedure_name):
        return BOARD_PROCEDURE
    return BOARD_MODEL_PLAN
