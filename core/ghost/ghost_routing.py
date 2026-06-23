"""Lightweight Ghost routing hints.

This is intentionally deterministic and small. Ghost may suggest where work should
go, but execution still routes through existing MO/Gateway/goal machinery after
explicit user confirmation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


RouteKind = Literal["main", "queue", "background", "steer"]


@dataclass(frozen=True)
class GhostRouteSuggestion:
    route: RouteKind
    objective: str
    reason: str
    risky: bool = False

    def offer_text(self) -> str:
        if self.risky:
            return (
                "Route suggestion: this touches a high-risk boundary, so I should keep it with main MO/Gateway "
                "after explicit approval. Reply yes to send it to MO, or no to ignore."
            )
        if self.route == "background":
            return (
                "Route suggestion: this looks independent and safe for a background MO worker. "
                "Reply yes to start it in the background, or no to ignore."
            )
        if self.route == "steer":
            return (
                "Route suggestion: MO is working right now; this looks like a current-work adjustment. "
                "Reply yes to inject it at the next safe checkpoint without stopping MO, or no to ignore. "
                "If it is urgent enough to interrupt, say: stop MO now."
            )
        if self.route == "queue":
            return (
                "Route suggestion: MO is working on something right now. "
                "Reply yes to send this to MO after the current turn, or no to ignore."
            )
        return "Routing to main MO..."


_CONFIRM_RE = re.compile(r"^\s*(yes|y|ok|okay|sure|go|do it|run it|start it|go ahead|approved|approve)\s*[.!]*\s*$", re.I)
_REJECT_RE = re.compile(r"^\s*(no|n|nope|cancel|ignore|stop|not now)\s*[.!]*\s*$", re.I)

_RISKY_RE = re.compile(
    r"\b(commit|push|force[-\s]?push|deploy|deployment|production|prod|server|credential|credentials|secret|secrets|token|delete|remove|rm\s+-rf|destructive)\b",
    re.I,
)
_WORK_RE = re.compile(
    r"\b(run|start|do|handle|ask|give|assign|route|send|check|inspect|review|investigate|scan|find|fix|build|implement|create|add|enhance|adjust|change|tweak|modify|edit|test|verify|analyze|analyse|deploy|push|commit)\b",
    re.I,
)
_DELIVERABLE_WORK_RE = re.compile(
    r"\b(?:i\s+(?:want|need|would\s+like)|let'?s|make|build|create)\b.{0,120}\b(?:game|prototype|app|page|site|tool|feature|dashboard|cli|script)\b",
    re.I | re.S,
)
_EXPLICIT_ROUTE_RE = re.compile(
    r"\b(ask\s+(?:main\s+)?mo\s+to|send\s+(?:it|this|that)?\s*(?:to\s+)?(?:main\s+)?mo|route\s+(?:it|this|that)?|queue\s+(?:it|this|that)?|start\s+(?:a\s+)?background|background\s+(?:worker|mo)|run\s+(?:it|this|that)\s+in\s+background|run\s+(?:a\s+)?worker|start\s+(?:a\s+)?worker|worker\s+(?:task|route|job))\b",
    re.I,
)
_BACKGROUND_ROUTE_RE = re.compile(
    r"\b(start\s+(?:a\s+)?background|background\s+(?:worker|mo|task|job)|run\s+(?:it|this|that)\s+in\s+background|run\s+(?:a\s+)?worker|start\s+(?:a\s+)?worker|worker\s+(?:task|route|job))\b",
    re.I,
)
_CONVERSATIONAL_RE = re.compile(
    r"^\s*(what|how|why|when|where|who|is|are|was|were|do|does|did|can|could|would|should)\b",
    re.I,
)
_STRATEGY_QUESTION_RE = re.compile(r"^\s*(what|how)\s+(should|would|could|do|does|can)\b", re.I)
_CONTEXT_SENSITIVE_RE = re.compile(
    r"\b(this|that|it|current|same|above|main task|existing task|fix|modify|edit|change|implement|build|add)\b",
    re.I,
)
_CURRENT_TURN_ADJUSTMENT_RE = re.compile(
    r"\b(?:this|that|it|current|same|above|without|instead|keyboard|mouse|playable|unplayable|doesn'?t\s+work|not\s+work(?:ing)?)\b",
    re.I,
)
_INDEPENDENT_RE = re.compile(
    r"\b(independent|separate|background|scan|review|investigate|audit|analyze|analyse|find|check)\b",
    re.I,
)
_POLITE_PREFIX_RE = re.compile(
    r"^\s*(ghost\s*,?\s*)?(can you|could you|would you|please|pls|should we|shall we|i need you to|we need to|ask mo to|ask main mo to|send mo to|route mo to|give (?:him|mo|main mo)|assign (?:him|mo|main mo))\s+",
    re.I,
)


def is_route_confirmation(text: str) -> bool:
    return bool(_CONFIRM_RE.match(str(text or "")))


def is_route_rejection(text: str) -> bool:
    return bool(_REJECT_RE.match(str(text or "")))


def recommend_ghost_route(
    text: str,
    *,
    main_busy: bool = False,
    goal_active: bool = False,
) -> GhostRouteSuggestion | None:
    """Return a route suggestion when the Ghost question appears to ask for work."""
    raw = str(text or "").strip()
    if not raw or not (_WORK_RE.search(raw) or _DELIVERABLE_WORK_RE.search(raw)):
        return None
    explicit_route = bool(_EXPLICIT_ROUTE_RE.search(raw))
    if _STRATEGY_QUESTION_RE.search(raw):
        return None
    if _CONVERSATIONAL_RE.search(raw) and not explicit_route:
        return None

    objective = _clean_objective(raw)
    risky = bool(_RISKY_RE.search(raw))
    if risky:
        return GhostRouteSuggestion(
            route="queue" if main_busy else "main",
            objective=objective,
            reason="high-risk work requires explicit main MO/Gateway handling",
            risky=True,
        )

    if _BACKGROUND_ROUTE_RE.search(raw):
        return GhostRouteSuggestion(
            route="background",
            objective=objective,
            reason="operator explicitly asked for background/worker route",
        )

    if main_busy:
        if not goal_active and _INDEPENDENT_RE.search(raw) and not _CONTEXT_SENSITIVE_RE.search(raw):
            return GhostRouteSuggestion(
                route="background",
                objective=objective,
                reason="main MO is busy and the task appears independent",
            )
        if _CURRENT_TURN_ADJUSTMENT_RE.search(raw):
            return GhostRouteSuggestion(
                route="steer",
                objective=objective,
                reason="main MO is busy and the request adjusts current work",
            )
        return GhostRouteSuggestion(
            route="queue",
            objective=objective,
            reason="main MO is busy and the task should keep main context",
        )

    return GhostRouteSuggestion(
        route="main",
        objective=objective,
        reason="main MO is idle or available",
    )


def enhance_route_objective(original: str, ghost_response: str = "") -> str:
    """Return the prompt Ghost should hand to MO after operator approval."""
    explicit = _extract_suggested_ask(ghost_response)
    if explicit and _suggested_ask_matches_original(original, explicit):
        return explicit[:500]
    # Pass through the original text — Ghost/provider injects methodology, not us
    return str(original or "").strip()[:500]


_ROUTE_CONFIRMATION_WORDS = {
    "yes", "yeah", "yep", "route", "send", "ask", "mo", "main", "it", "this", "that",
    "go", "do", "okay", "ok", "sure", "approved", "approve", "please", "pls",
}


def _suggested_ask_matches_original(original: str, suggested: str) -> bool:
    """Reject stale Ghost suggested asks that drift away from a concrete current request."""
    original_text = str(original or "")
    if not (_WORK_RE.search(original_text) or _DELIVERABLE_WORK_RE.search(original_text)):
        return True
    original_terms = _meaningful_route_terms(original_text)
    if not original_terms:
        return True
    suggested_terms = _meaningful_route_terms(suggested)
    return bool(original_terms & suggested_terms)


def _meaningful_route_terms(text: str) -> set[str]:
    terms = set()
    for word in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if len(word) <= 2 or word in _ROUTE_CONFIRMATION_WORDS:
            continue
        terms.add(word)
    return terms


def _extract_suggested_ask(text: str) -> str:
    lines = str(text or "").splitlines()
    for idx, raw in enumerate(lines):
        line = _clean_suggested_ask_line(raw)
        if not line:
            continue
        match = re.match(r"(?i)^(?:suggested\s+(?:mo\s+)?ask|route\s+objective|send\s+to\s+mo)\s*:\s*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip().strip('"“”')
        if inline:
            return inline
        for candidate_raw in lines[idx + 1: idx + 4]:
            candidate = _clean_suggested_ask_line(candidate_raw).strip('"“”')
            if candidate:
                return candidate
    return ""


def _clean_suggested_ask_line(raw: str) -> str:
    line = str(raw or "").strip().lstrip("-• ").strip()
    line = line.replace("**", "").replace("__", "").strip()
    return line


def _clean_objective(text: str) -> str:
    objective = _POLITE_PREFIX_RE.sub("", str(text or "").strip())
    objective = objective.strip(" .!?\t")
    return objective or str(text or "").strip()
