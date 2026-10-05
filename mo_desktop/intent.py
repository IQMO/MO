"""Typed, request-local MO Desktop action admission."""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Mapping

from core.runtime.capability_ids import (
    CAP_CODE_GRAPH,
    CAP_COMPUTER_CONTROL,
    CAP_DESIGN,
    CAP_FILES,
    CAP_MCP,
    CAP_PHONE,
    CAP_SCHEDULING,
    CAP_SCREEN_OBSERVATION,
    CAP_SYSTEMCARE,
    CAP_TRANSFER,
    CAP_WEB,
)
from core.runtime.capability_routing import (
    looks_like_computer_action_request,
    looks_like_screen_observation_request,
)
from core.runtime.turn_intent import classify_turn
from core.runtime.work_signals import (
    looks_like_declined_work_start_request,
    looks_like_explicit_work_start_request,
)
from core.tasking.task_board import normalize_pending_action


_PROJECT_IMPLEMENTATION_ACTION_RE = re.compile(
    r"(?is)(?:^\s*|[.?!;,]\s*|\b(?:also|and|but|then)\s+)"
    r"(?:(?:please|now)\s+|(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i|we)\s+(?:want|need)\s+you\s+to\s+|help\s+me\s+(?:to\s+)?)?"
    r"(?P<action>add\w*|address\w*|appl(?:y|ies|ied|ying)|build\w*|chang\w*|"
    r"clean\w*|commit\w*|configur\w*|correct\w*|creat\w*|debug\w*|delet\w*|"
    r"deploy\w*|develop\w*|edit\w*|ensure\w*|fix\w*|implement\w*|install\w*|"
    r"integrat\w*|mak(?:e|es|ing)|made|merg\w*|migrat\w*|modif\w*|"
    r"optimi[sz]\w*|patch\w*|program\w*|push\w*|refactor\w*|remov\w*|"
    r"renam\w*|repair\w*|replac\w*|resolv\w*|run\w*|set\s+up|ship\w*|"
    r"simplif\w*|solv\w*|test\w*|updat\w*|upgrad\w*|wir\w*|writ\w*)\b",
)
_PROJECT_IMPLEMENTATION_SOURCE_TARGET_RE = re.compile(
    r"\b(?:code(?:base)?|repo(?:sitory)?|source|function|method|class|module|package|"
    r"dependenc(?:y|ies)|library|sdk|api|backend|front[ -]?end|database|schema|"
    r"migration|tests?|bug|feature|website|webapp|game|script|cli|project)\b|"
    r"(?<![\w.])[\w.-]+\.(?:py|pyi|rs|go|java|kt|swift|c|cc|cpp|h|hpp|cs|rb|php|"
    r"ts|tsx|js|jsx|vue|svelte|html|css|scss|sql|toml|ya?ml|json|md)\b",
    re.I,
)
_PROJECT_IMPLEMENTATION_AMBIGUOUS_TARGET_RE = re.compile(
    r"\b(?:app|application|service|software)\b",
    re.I,
)
_PROJECT_IMPLEMENTATION_PRODUCT_BUILD_RE = re.compile(
    r"^(?:build|creat|debug|develop|fix|implement|mak|made|optimi[sz]|program|"
    r"refactor|repair|writ)",
    re.I,
)
_PROJECT_IMPLEMENTATION_COMPANION_TARGET_RE = re.compile(
    r"\b(?:workout|diet|fitness|habit|personal\s+routine|newsletter|presentation|"
    r"slide\s*deck|spreadsheet|workbook|document|recipe)\b",
    re.I,
)


_ACTION_INTENT_PATTERN = (
    r"click|double[- ]?click|press|type|input|write|enter|paste|copy|scroll|drag|drop|"
    r"open|search|launch|start|run(?=\s+(?:the\s+)?workspace\b)|close|minimi[sz]e|maximi[sz]e|restore|switch(?:\s+to)?|focus|"
    r"move|resize|select|set|choose|navigate|submit|send|route|invoke|play|pause|"
    r"draw|save|undo|redo|erase"
)
_ACTION_INTENT_RE = re.compile(rf"\b({_ACTION_INTENT_PATTERN})\b", re.I)
_DIRECT_NATIVE_ACTION_RE = re.compile(
    r"(?:^|[.!?;:]\s+|\b(?:and|but|so|then)\s+)\s*"
    r"(?:if\b[^.!?;,\n]{1,120},\s*)?"
    r"(?:(?:hi|hey)\s+mo[,!]?\s+)?"
    r"(?:(?:yes|ok(?:ay)?|sure|now|then|also|so(?:\s+now)?)[,!.]?\s+|go\s+ahead\s+and\s+)?"
    r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:(?:please|just)\s+)*|"
    r"(?:i|we)\s+(?:want|need)\s+you\s+to\s+)?"
    rf"(?P<action>{_ACTION_INTENT_PATTERN})\b",
    re.I,
)
_NON_UI_DIRECT_ACTION_RE = re.compile(
    r"^\s*(?:open\s+(?:(?:the|any|all)\s+)?"
    r"(?:(?:current|existing|unresolved|remaining|outstanding|pending|open)\s+)*"
    r"(?:issues?|items?|tasks?|work|questions?|findings?|pull\s+requests?|prs?)\b|"
    r"close\s+to\b)",
    re.I,
)
_NATIVE_COMPUTER_ACTIONS = frozenset({
    "click", "press", "type", "input", "enter", "paste", "copy", "scroll",
    "drag", "drop", "open", "search", "launch", "start", "run", "close", "minimize", "maximize",
    "restore", "switch", "focus", "move", "resize", "select", "choose", "navigate",
    "submit", "send", "route", "play", "pause", "resume", "draw", "save", "undo", "redo", "erase",
})
_ACTIVE_TARGET_FOLLOWUP_ACTIONS = _NATIVE_COMPUTER_ACTIONS - {
    "open", "launch", "start", "run", "send", "route",
}
_EXPLICIT_DESIGN_ARTIFACT_RE = re.compile(
    r"\b(?:diagram|flowchart|logo|illustration|image|icon|poster|banner|mockup|"
    r"wireframe|graphic|artwork)\b",
    re.I,
)
_NATIVE_TARGET_REQUIRED_ACTIONS = frozenset({
    "move", "switch", "focus", "select", "choose", "run", "send", "route", "play", "pause",
})
_CORRECTIVE_ACTION_RE = re.compile(
    r"\byou\b.{0,80}\b(?:did(?:n['’]?t|\s+not)|have\s+not|haven['’]?t|failed\s+to|"
    r"forgot\s+to|should(?:['’]?ve|\s+have)|were\s+supposed\s+to|needed\s+to)\b"
    r".{0,180}\b(?:click(?:ed)?|press(?:ed)?|typ(?:e|ed)|input(?:ted)?|enter(?:ed)?|"
    r"past(?:e|ed)|cop(?:y|ied)|drag(?:ged)?|drop(?:ped)?|scroll(?:ed)?|open(?:ed)?|"
    r"launch(?:ed)?|start(?:ed)?|clos(?:e|ed)|minimi[sz](?:e|ed)|maximi[sz](?:e|ed)|"
    r"restor(?:e|ed)|switch(?:ed)?|focus(?:ed)?|mov(?:e|ed)|resi[sz](?:e|ed)|"
    r"select(?:ed)?|set|chos(?:e|en)|navigat(?:e|ed)|submit(?:ted)?|send|sent|"
    r"invok(?:e|ed)|play(?:ed)?|paus(?:e|ed)|resum(?:e|ed))\b",
    re.I | re.S,
)
_RESUME_ACTUATION_RE = re.compile(
    r"\bresume\b[^.?!]{0,60}\b(?:it|playback|video|audio|music|song|track|media|player|"
    r"download|app|application|window|tab|page)\b|"
    r"\b(?:playback|video|audio|music|song|track|media|player|download|app|application|"
    r"window|tab|page)\b[^.?!]{0,60}\bresume\b",
    re.I,
)
_VISIBLE_OPEN_RE = re.compile(r"\b(?:show\s+me|pull\s+(?:it\s+)?up)\b(?!\s+(?:how|where|around)\b)", re.I)
_DELIVERABLE_INTENT_RE = re.compile(
    r"\b(?:create|make|generate|save|export|write|build)\b[^.?!]{0,100}(?:\.)?"
    r"\b(?:pdf|file|document|doc|docx|xlsx|csv|markdown|md|txt|report)\b|"
    r"\b(?:turn|convert)\b[^.?!]{0,80}\b(?:into|to|as)\b[^.?!]{0,60}(?:\.)?"
    r"\b(?:pdf|file|document|doc|docx|xlsx|csv|markdown|md|txt|report)\b|"
    r"\b(?:summ(?:ari|uri)[sz](?:e|ed|ing|ation)|summary)\b[^.?!]{0,100}"
    r"\b(?:into|to|as|and\s+to)\b[^.?!]{0,60}(?:\.)?"
    r"\b(?:pdf|file|document|doc|docx|report)\b",
    re.I,
)
_PAIRING_REQUEST_RE = re.compile(r"\b(?:show|give|create|generate|display|present|make|get|send|want|need|pair|connect|link)\b", re.I)
_PAIRING_OPERATION_RE = re.compile(r"\b(?:pair|pairing|paired)\b", re.I)
_PAIRING_TARGET_RE = re.compile(r"\b(?:android|phone)\b|\bmo\s+(?:app|everywhere)\b", re.I)
_MO_PAIRING_TARGET_RE = re.compile(r"\bmo\s+(?:app|everywhere)\b|\bandroid\s+app\b|\bwith\s+mo\b|\bto\s+mo\b", re.I)
_QR_RE = re.compile(r"\bqr\b", re.I)
_PHONE_CONTROL_PAIRING_RE = re.compile(r"\b(?:phone[- ]control|remote[- _]?host)\b", re.I)
_WALKTHROUGH_RE = re.compile(
    r"\b(?:walk\s*(?:me\s*)?through\b|guide\s+me\b|show\s+me\s+around\b)",
    re.I,
)
_WALKTHROUGH_TYPO_RE = re.compile(
    r"\bwalk\s*(?:me\s*)?(?:thorugh|throuhg|thorough)\b",
    re.I,
)
_WALKTHROUGH_CONTINUATION_RE = re.compile(
    r"^\s*(?:(?:please\s+)?(?:explain|describe)\s+(?:it|this|that)(?:\s+for\s+me)?|"
    r"(?:please\s+)?(?:continue|resume)(?:\s+(?:it|this|that|the\s+walkthrough))?)"
    r"[.!?\s]*$",
    re.I,
)
_POINT_REQUEST_RE = re.compile(
    r"\b(?:show\s+me\s+where(?:\s+to)?\s+|where\s+(?:do|should|can)\s+(?:i|we)\s+)"
    r"(?:click|tap|press)\b|"
    r"\bshow\s+me\s+where\b[^.?!]{0,80}\bqr(?:\s+code)?\b[^.?!]{0,30}\b(?:is|appears?)\b",
    re.I,
)
_EXPLAIN_ONLY_RE = re.compile(
    r"\b(how\s+(?:do|can|to)\b|where\s+(?:do|should|is|are)\b|which\b|what\b|why\b|"
    r"explain\b|describe\b|show\s+me\s+where\b|point\s+(?:to|at)\b|"
    r"walk(?:\s+me)?\s+through\b|walkthrough\b|guide\s+me\b|show\s+me\s+around\b)",
    re.I,
)
_SCREEN_OFFER_RE = re.compile(r"\bwant\s+me\s+to\s+show\s+you\b[^.?!]{0,40}\bon[- ]screen\b", re.I)
_SCREEN_OFFER_ACCEPT_RE = re.compile(
    r"\s*(?:yes|yeah|yep|ok(?:ay)?|sure|please|do\s+it|go\s+ahead)[.! ]*\s*",
    re.I,
)
_CONTINUATION_RE = re.compile(
    r"\b(?:again|same\s+(?:thing|one)|continue|carry\s+on|do\s+it|do\s+that|resume|"
    r"try\s+(?:it|that)|redo(?:\s+(?:it|that))?)\b",
    re.I,
)
# One whole sentence that only accepts or retries the last attempt ("Yes, fine.
# Try that."), wherever it sits in a longer message.
_STANDALONE_CONTINUATION_RE = re.compile(
    r"(?:(?:yes|yeah|ok(?:ay)?|sure|fine|please)[,.! ]+)*"
    r"(?:again|same\s+(?:thing|one)|continue|carry\s+on|do\s+it|do\s+that|"
    r"try\s+(?:it|that)(?:\s+again)?|redo(?:\s+(?:it|that))?)[.!? ]*",
    re.I,
)
_TARGET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", re.compile(r"\b(?:phone|android|mobile|handset)\b", re.I)),
    ("browser", re.compile(
        r"\b(?:browser|chrome|website|site|web|page|tab|url)\b|"
        r"\bmo\s+connect(?:ed)?(?:\s+(?:tab|chrome|browser|live))?\b",
        re.I,
    )),
    ("media", re.compile(r"\b(?:music|song|track|audio|video|playback|media|player|spotify|youtube)\b", re.I)),
    ("file", re.compile(r"\b(?:file|folder|document|pdf|docx|xlsx|csv|report)\b", re.I)),
    ("design", re.compile(r"\b(?:board|draw|drawing|diagram|design|canvas|sketch)\b", re.I)),
    ("systemcare", re.compile(r"\b(?:system\s*care|cleanup|startup\s+apps?)\b", re.I)),
    ("schedule", re.compile(r"\b(?:schedul\w*|remind\w*)\b", re.I)),
    ("application", re.compile(
        r"\b(?:app|application|window|computer|keyboard|mouse|workspace)\b|"
        r"\b(?:to|into)\s+(?:the\s+)?(?:(?:current|selected|main)\s+)?mo(?:\s+(?:terminal|session|window))?\b",
        re.I,
    )),
)
_CAPABILITY_ORDER = (
    CAP_COMPUTER_CONTROL, CAP_PHONE, CAP_DESIGN, CAP_TRANSFER, CAP_FILES,
    CAP_SCHEDULING, CAP_SYSTEMCARE, CAP_MCP, CAP_WEB, CAP_SCREEN_OBSERVATION,
    CAP_CODE_GRAPH,
)


@dataclass(frozen=True)
class DesktopActionAdmission:
    """One request-local safety-lane selection; never a permission bypass."""

    kind: str
    capability: str = "conversation"
    action: str = "none"
    target: str = "none"
    source: str = "explicit"
    reason: str = "conversation"
    selected_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"explain", "point", "act", "clarify"}:
            raise ValueError(f"unknown Desktop admission kind: {self.kind!r}")
        if self.source not in {
            "explicit", "selected_option", "continued_receipt", "active_target", "pending_task",
        }:
            raise ValueError(f"unknown Desktop admission source: {self.source!r}")
        for field_name in ("capability", "action", "target", "reason"):
            if len(str(getattr(self, field_name) or "")) > 80:
                raise ValueError(f"Desktop admission {field_name} is not bounded")
        if len(self.selected_options) > 6 or any(len(str(value)) > 80 for value in self.selected_options):
            raise ValueError("Desktop selected options are not bounded")

    @property
    def permits_action(self) -> bool:
        return self.kind == "act"


def admission_from_pending_action(value: object) -> DesktopActionAdmission | None:
    """Restore one typed action contract without interpreting conversation text."""
    pending = normalize_pending_action(value)
    if not pending:
        return None
    return DesktopActionAdmission(
        "act",
        pending["capability"],
        pending["action"],
        pending["target"],
        "pending_task",
        "typed_pending_action",
    )


@dataclass(frozen=True)
class DesktopActionReceipt:
    """Bounded same-session continuation evidence with no raw arguments/content."""

    capability: str
    action: str
    target: str
    outcome: str
    evidence_id: str
    created_at: float

    def is_usable(self, *, now: float | None = None, max_age_seconds: float = 300.0) -> bool:
        current = time.time() if now is None else float(now)
        age = current - float(self.created_at or 0.0)
        return bool(
            self.outcome == "success"
            and self.evidence_id
            and 0.0 <= age <= max(1.0, float(max_age_seconds))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability[:80],
            "action": self.action[:80],
            "target": self.target[:80],
            "outcome": self.outcome[:20],
            "evidence_id": self.evidence_id[:80],
            "created_at": float(self.created_at),
        }

    @classmethod
    def from_mapping(cls, value: object) -> "DesktopActionReceipt | None":
        if not isinstance(value, Mapping):
            return None
        try:
            receipt = cls(
                capability=str(value.get("capability") or "")[:80],
                action=str(value.get("action") or "")[:80],
                target=str(value.get("target") or "")[:80],
                outcome=str(value.get("outcome") or "")[:20],
                evidence_id=str(value.get("evidence_id") or "")[:80],
                created_at=float(value.get("created_at") or 0.0),
            )
        except (TypeError, ValueError):
            return None
        return receipt if all((receipt.capability, receipt.action, receipt.target, receipt.outcome)) else None


def is_desktop_capability_question(user_input: str) -> bool:
    from core.runtime.capability_routing import is_capability_question

    return is_capability_question(user_input)


def normalize_desktop_walkthrough_text(user_input: str) -> str:
    """Normalize only the bounded walkthrough slips observed on Desktop."""
    return _WALKTHROUGH_TYPO_RE.sub("walk me through", str(user_input or ""))


def is_screen_request(user_input: str) -> bool:
    text = normalize_desktop_walkthrough_text(user_input)
    if is_desktop_capability_question(text):
        return False
    return bool(
        _WALKTHROUGH_RE.search(text)
        or _POINT_REQUEST_RE.search(text)
        or looks_like_screen_observation_request(text)
    )


def is_walkthrough_request(user_input: str) -> bool:
    return bool(
        _WALKTHROUGH_RE.search(normalize_desktop_walkthrough_text(user_input))
    )


def is_walkthrough_continuation(user_input: str, prior_user_input: str = "") -> bool:
    """Recognize one immediate referential follow-up to an authored walkthrough."""
    return bool(
        is_walkthrough_request(prior_user_input)
        and _WALKTHROUGH_CONTINUATION_RE.fullmatch(str(user_input or ""))
    )


def _screen_request_action(user_input: str) -> str:
    """Distinguish a requested pointer from a read-only visual observation."""
    text = normalize_desktop_walkthrough_text(user_input)
    if _WALKTHROUGH_RE.search(text):
        return "walkthrough"
    if _POINT_REQUEST_RE.search(text) or re.search(
        r"\b(?:point\s+(?:to|at)|highlight)\b", text, re.I
    ):
        return "point"
    return "observe"


def _has_direct_native_action_request(user_input: str) -> bool:
    """Recognize a requested UI verb by instruction shape, not isolated words."""
    text = str(user_input or "")
    match = _DIRECT_NATIVE_ACTION_RE.search(text)
    return bool(
        match
        and not _NON_UI_DIRECT_ACTION_RE.match(text[match.start("action"):])
    )


def _direct_native_action_clause(user_input: str) -> str:
    """Return the first requested UI-action clause for capability precedence."""
    text = str(user_input or "")
    match = _DIRECT_NATIVE_ACTION_RE.search(text)
    if match is None:
        return ""
    return re.split(
        r"\b(?:and\s+then|and|then)\b|[!?;]|\.(?=\s|$)",
        text[match.start("action"):],
        maxsplit=1,
        flags=re.I,
    )[0].strip()


def desktop_pairing_action(user_input: str) -> str | None:
    """Return the exact explicit Android-pairing action requested on Desktop."""
    text = str(user_input or "").strip()
    if not text or _EXPLAIN_ONLY_RE.search(text):
        return None
    pairing_text = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    pairing_action = (
        _PAIRING_REQUEST_RE.search(pairing_text)
        and (
            (_QR_RE.search(pairing_text) and (_PAIRING_OPERATION_RE.search(pairing_text) or _PAIRING_TARGET_RE.search(pairing_text)))
            or (_PAIRING_OPERATION_RE.search(pairing_text) and _MO_PAIRING_TARGET_RE.search(pairing_text))
        )
    )
    if not pairing_action:
        return None
    return "phone_control" if _PHONE_CONTROL_PAIRING_RE.search(pairing_text) else "companion"


def admit_desktop_action(
    user_input: str,
    *,
    selected_options: tuple[str, ...] | list[str] = (),
    prior_receipt: DesktopActionReceipt | None = None,
    active_target: str = "",
    prior_assistant_text: str = "",
    prior_user_input: str = "",
    allow_compound_walkthrough_action: bool = False,
    now: float | None = None,
    receipt_max_age_seconds: float = 300.0,
) -> DesktopActionAdmission:
    """Classify one request from current input and bounded conversation evidence."""
    from core.runtime.capability_routing import (
        capability_hints_for,
        looks_like_direct_computer_control_request,
    )

    text = normalize_desktop_walkthrough_text(user_input).strip()
    prior_user_input = normalize_desktop_walkthrough_text(prior_user_input)
    options = tuple(str(value or "").strip()[:80] for value in selected_options if str(value or "").strip())[:6]
    if options:
        # A selected explanation is still an explanation. Clicking a card does
        # not turn every capability into an actuation request.
        selected = admit_desktop_action(
            text or " ".join(options),
            active_target=active_target,
            prior_user_input=prior_user_input,
            allow_compound_walkthrough_action=allow_compound_walkthrough_action,
        )
        return DesktopActionAdmission(
            selected.kind, selected.capability, selected.action, selected.target,
            "selected_option", selected.reason, options,
        )

    if (
        _SCREEN_OFFER_ACCEPT_RE.fullmatch(text)
        and _SCREEN_OFFER_RE.search(str(prior_assistant_text or ""))
    ):
        return DesktopActionAdmission(
            "point", CAP_SCREEN_OBSERVATION, "walkthrough", "screen",
            "selected_option", "accepted_screen_offer",
        )
    if looks_like_declined_work_start_request(text):
        return DesktopActionAdmission(
            "explain", "conversation", "none", "none", "explicit", "agent_work_start_declined"
        )
    if looks_like_explicit_work_start_request(text):
        # Starting MO's own already-offered work is a Gateway work instruction,
        # not authority to start an application or drive the screen.
        return DesktopActionAdmission(
            "explain", "conversation", "none", "none", "explicit", "agent_work_start"
        )
    route_text = text
    hints = capability_hints_for(route_text)
    capability = next((value for value in _CAPABILITY_ORDER if value in hints), "conversation")
    target = _target_class(route_text, capability)
    if target == "phone" and CAP_PHONE in hints:
        capability = CAP_PHONE
    action = _action_class(route_text)
    direct_computer_control = looks_like_direct_computer_control_request(route_text)
    if direct_computer_control and action == "none":
        action = "control"
    raw_action_match = _ACTION_INTENT_RE.search(route_text)
    raw_action = str(raw_action_match.group(1) if raw_action_match else "").lower()
    direct_native_action = _has_direct_native_action_request(route_text)
    deliverable_intent = bool(_DELIVERABLE_INTENT_RE.search(text))
    direct_clause = _direct_native_action_clause(route_text)
    direct_clause_hints = capability_hints_for(direct_clause) if direct_clause else frozenset()
    if (
        direct_native_action
        and action in {"open", "launch", "start"}
        and capability not in {"conversation", CAP_COMPUTER_CONTROL}
        and capability not in direct_clause_hints
    ):
        # Classify the object of the opening clause, not a later operation. This
        # keeps arbitrary application names usable in compound requests such as
        # "Open Paint and draw ..." while explicit Board/file targets retain
        # their existing owners.
        capability = CAP_COMPUTER_CONTROL
        target = "application"
    if (
        capability == "conversation"
        and (direct_native_action or bool(_CORRECTIVE_ACTION_RE.search(route_text))
             or bool(_RESUME_ACTUATION_RE.search(route_text)))
        and not deliverable_intent
        and action in _NATIVE_COMPUTER_ACTIONS
        and (action not in _NATIVE_TARGET_REQUIRED_ACTIONS or target != "unknown")
        and raw_action != "write"
        and (not _CONTINUATION_RE.search(route_text) or bool(_RESUME_ACTUATION_RE.search(route_text)))
    ):
        # An explicit Desktop UI action with no more specific capability still
        # belongs to the native computer-control route (for example "click
        # Save").  Conversation is not an action capability.
        capability = CAP_COMPUTER_CONTROL
        target = _target_class(route_text, capability)
    native_action_admitted = bool(
        direct_native_action and capability != "conversation"
    )

    if direct_computer_control:
        return DesktopActionAdmission(
            "act", CAP_COMPUTER_CONTROL, action, target, "explicit", "direct_computer_control"
        )

    usable_receipt = bool(
        prior_receipt
        and prior_receipt.is_usable(now=now, max_age_seconds=receipt_max_age_seconds)
    )
    live_target = str(active_target or "").strip().casefold()
    contextual_target = (
        prior_receipt.target
        if usable_receipt and prior_receipt is not None
        else live_target
        if live_target in {"application", "browser", "media", "screen"}
        else ""
    )
    if (
        contextual_target
        and direct_native_action
        and action in _ACTIVE_TARGET_FOLLOWUP_ACTIONS
        and target in {"unknown", "application", "design", "file", contextual_target}
        and not (
            contextual_target == "screen"
            and _EXPLICIT_DESIGN_ARTIFACT_RE.search(text)
        )
        and not (
            deliverable_intent
            and re.search(r"\bsave\s+(?:a|an|new)\b", text, re.I)
        )
    ):
        # A fresh successful receipt or target-bound observation keeps a
        # natural UI follow-up on computer control. A whole-screen observation
        # is only a route hint; the executor must still discover and verify the
        # exact app target before acting.
        return DesktopActionAdmission(
            "act", CAP_COMPUTER_CONTROL, action, contextual_target,
            "continued_receipt" if usable_receipt else "active_target",
            "active_target_followup",
        )

    if is_walkthrough_continuation(text, prior_user_input):
        return DesktopActionAdmission(
            "point", CAP_SCREEN_OBSERVATION, "walkthrough", "screen",
            "explicit", "walkthrough_continuation",
        )

    if (
        allow_compound_walkthrough_action
        and is_walkthrough_request(text)
        and native_action_admitted
    ):
        return DesktopActionAdmission(
            "act", capability, action, target,
            "explicit", "compound_walkthrough_action",
        )

    if is_walkthrough_request(text):
        return DesktopActionAdmission("point", CAP_SCREEN_OBSERVATION, "walkthrough", "screen", "explicit", "walkthrough")

    continuation = _CONTINUATION_RE.search(text)
    short_continuation = len(text.split()) <= 5
    action_continuation = bool(
        _has_direct_native_action_request(text)
        or _RESUME_ACTUATION_RE.search(text)
        or (short_continuation and re.search(r"\b(?:same\s+(?:thing|one)|continue|carry\s+on|do\s+it|do\s+that|resume)\b", text, re.I))
    )
    receipt_continuation = bool(
        action_continuation
        or any(
            _STANDALONE_CONTINUATION_RE.fullmatch(sentence.strip())
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if sentence.strip()
        )
        or (
            short_continuation
            and re.fullmatch(
                r"(?:again|same\s+(?:thing|one)|continue|carry\s+on|do\s+it|do\s+that)[.!? ]*",
                text,
                re.I,
            )
        )
    )
    if continuation and receipt_continuation:
        usable = usable_receipt
        if usable:
            assert prior_receipt is not None
            compatible_target = bool(
                target in {"none", "unknown", "media"}
                or target == prior_receipt.target
                or (
                    target == "browser"
                    and prior_receipt.target == "browser"
                )
            )
            if not compatible_target:
                return DesktopActionAdmission(
                    "clarify", capability, action, target, "continued_receipt", "continuation_target_conflict"
                )
            return DesktopActionAdmission(
                "act", prior_receipt.capability, prior_receipt.action, prior_receipt.target,
                "continued_receipt", "compatible_success_receipt",
            )
        requires_receipt = bool(
            re.search(r"\b(?:again|same\s+(?:thing|one)|continue|carry\s+on|do\s+it|do\s+that)\b", text, re.I)
            or (re.fullmatch(r"resume[.!? ]*", text, re.I) is not None)
        )
        if requires_receipt:
            return DesktopActionAdmission(
                "clarify", capability, action, target, "continued_receipt", "continuation_missing_receipt"
            )

    pairing = desktop_pairing_action(text)
    if pairing is not None:
        return DesktopActionAdmission("act", CAP_PHONE, "pair", "phone", "explicit", f"pair_{pairing}")
    if deliverable_intent:
        return DesktopActionAdmission("act", capability if capability != "conversation" else CAP_FILES, "create", target, "explicit", "deliverable")
    if _CORRECTIVE_ACTION_RE.search(text):
        return DesktopActionAdmission("act", capability, action if action != "none" else "retry", target, "explicit", "corrective_action")
    # Questions that merely contain an action verb are not action authority.
    # Screen questions may observe/point, while other how/what/why questions
    # remain conversational. Explicit deliverables and corrections above remain
    # actionable even when phrased as a question.
    if _EXPLAIN_ONLY_RE.search(text) and not direct_native_action:
        if is_screen_request(text):
            return DesktopActionAdmission(
                "point", CAP_SCREEN_OBSERVATION, _screen_request_action(text), "screen", "explicit", "screen_question"
            )
        return DesktopActionAdmission(
            "explain", capability, "none", target, "explicit", "explanation_question"
        )
    if _VISIBLE_OPEN_RE.search(text) or _RESUME_ACTUATION_RE.search(text) or native_action_admitted:
        return DesktopActionAdmission("act", capability, action, target, "explicit", "explicit_action")
    if is_screen_request(text):
        return DesktopActionAdmission(
            "point", CAP_SCREEN_OBSERVATION, _screen_request_action(text), "screen", "explicit", "screen_request"
        )
    return DesktopActionAdmission("explain", capability, "none", target, "explicit", "conversation")


def is_desktop_action_request(
    user_input: str,
    *,
    admission: DesktopActionAdmission | None = None,
) -> bool:
    from core.desktop.policy import computer_action_request_authorized

    decided = admission or admit_desktop_action(user_input)
    return computer_action_request_authorized(decided, None)


def looks_like_project_implementation_request(user_input: str) -> bool:
    """Return whether an explicit software change belongs to an engineering surface.

    Canonical turn intent identifies clone/adopt work. This Desktop boundary uses a
    direct-request matcher for other software changes so diagnostic questions, screen
    actions, generated artifacts, and design discussion stay with the companion. It
    grants no mutation authority.
    """
    text = str(user_input or "").strip()
    if not text or looks_like_computer_action_request(text):
        return False
    intent = classify_turn(text)
    pattern = str(getattr(intent, "work_pattern", "") or "")
    if pattern == "clone_adopt":
        return True
    action_match = _PROJECT_IMPLEMENTATION_ACTION_RE.search(text)
    if action_match is None:
        return False
    project_target = bool(_PROJECT_IMPLEMENTATION_SOURCE_TARGET_RE.search(text))
    ambiguous_target = bool(_PROJECT_IMPLEMENTATION_AMBIGUOUS_TARGET_RE.search(text))
    action = str(action_match.group("action") or "").casefold()
    if action.startswith("implement"):
        return bool(
            project_target or ambiguous_target
            or not _PROJECT_IMPLEMENTATION_COMPANION_TARGET_RE.search(text)
        )
    return bool(
        project_target
        or (
            ambiguous_target
            and _PROJECT_IMPLEMENTATION_PRODUCT_BUILD_RE.match(action)
        )
    )


def _target_class(text: str, capability: str) -> str:
    for name, pattern in _TARGET_PATTERNS:
        if pattern.search(str(text or "")):
            return name
    return {
        CAP_PHONE: "phone",
        CAP_DESIGN: "design",
        CAP_FILES: "file",
        CAP_TRANSFER: "file",
        CAP_SCREEN_OBSERVATION: "screen",
        CAP_COMPUTER_CONTROL: "application",
        CAP_WEB: "browser",
        CAP_SYSTEMCARE: "systemcare",
        CAP_SCHEDULING: "schedule",
    }.get(capability, "unknown")


def _action_class(text: str) -> str:
    match = _ACTION_INTENT_RE.search(str(text or ""))
    if not match:
        if _RESUME_ACTUATION_RE.search(str(text or "")):
            return "resume"
        if _VISIBLE_OPEN_RE.search(str(text or "")):
            return "open"
        if _CORRECTIVE_ACTION_RE.search(str(text or "")):
            return "retry"
        return "none"
    action = match.group(1).lower().replace(" ", "-")
    if action.startswith("double"):
        return "click"
    if action in {"launch", "start", "show"}:
        return "open"
    if action in {"input", "write", "enter", "paste"}:
        return "type"
    if action in {"choose", "select"}:
        return "select"
    return action[:40]


__all__ = [
    "DesktopActionAdmission",
    "DesktopActionReceipt",
    "admission_from_pending_action",
    "admit_desktop_action",
    "desktop_pairing_action",
    "is_desktop_action_request",
    "is_desktop_capability_question",
    "looks_like_project_implementation_request",
    "is_screen_request",
    "is_walkthrough_continuation",
    "is_walkthrough_request",
    "normalize_desktop_walkthrough_text",
]
