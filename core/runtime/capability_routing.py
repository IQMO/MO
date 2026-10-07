"""Pure action-and-target capability hints for one admitted user turn.

Hints describe which existing tool families may be relevant. They grant no
permission and own no provider, sandbox, task, or execution policy.
"""

from __future__ import annotations

import re

from .capability_ids import (
    CAP_CODE_GRAPH,
    CAP_COMPUTER_CONTROL,
    CAP_CURRENT_FACTS,
    CAP_DESIGN,
    CAP_FILE_ORGANIZATION,
    CAP_FILES,
    CAP_GITHUB_REPO,
    CAP_LIFE,
    CAP_MCP,
    CAP_MIGRATION,
    CAP_PERCEPTION,
    CAP_PHONE,
    CAP_PROFILE,
    CAP_SCHEDULING,
    CAP_SCREEN_OBSERVATION,
    CAP_SYSTEMCARE,
    CAP_TRANSFER,
    CAP_VISUALIZATION,
    CAP_WEB,
)


_FILE_TARGET_RE = re.compile(
    r"(?:^|\s)(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])|"
    r"\b(?:files?|folders?|director(?:y|ies)|paths?|documents?|artifacts?|repo(?:sitory)?|"
    r"codebase|source|configs?|logs?)\b|"
    r"\b[\w.-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg|csv|log|pdf|docx?|xlsx?|png|jpe?g)\b",
    re.I,
)
_FILE_ACTION_RE = re.compile(
    r"\b(?:read|open|inspect|find|search|list|write|edit|change|create|save|copy|move|"
    r"rename|delete|remove|organ(?:ize|ise)|sort|group|scan|audit|review|fix|maintain(?!er))\w*\b",
    re.I,
)
_ORGANIZE_RE = re.compile(
    r"\b(?:organ(?:ize|ise|izing|ising)|sort|group|copy|move|rename)\w*\b"
    r"[^.?!\n]{0,100}\b(?:files?|folders?|photos?|images?|pictures?|documents?)\b|"
    r"\b(?:files?|folders?|photos?|images?|pictures?|documents?)\b"
    r"[^.?!\n]{0,100}\b(?:organ(?:ize|ise)|sort|group|copy|move|rename)\w*\b",
    re.I,
)
_TRANSFER_RE = re.compile(
    r"\b(?:send|share|transfer|deliver|upload|download|sync)\w*\b"
    r"[^.?!\n]{0,100}\b(?:files?|folders?|attachments?|photos?|images?|documents?|phone|android|device)\b|"
    r"\b(?:files?|folders?|attachments?|photos?|images?|documents?)\b"
    r"[^.?!\n]{0,100}\b(?:phone|android|device)\b",
    re.I,
)
_SCREEN_TARGET_PATTERN = (
    r"screens?|displays?|monitors?|desktops?|windows?|tabs?|pages?|buttons?|icons?|menus?|toolbars?|dialogs?|panels?|pixels?|"
    r"(?:visible|on[- ]screen|displayed)\s+(?:ui|interface|controls?)"
)
_SCREEN_OBSERVATION_RE = re.compile(
    rf"\b(?:look\s+at|see|show|observe|inspect|read|diagnose|check|review|report|explain)\b"
    rf"[^.?!\n]{{0,100}}\b(?:{_SCREEN_TARGET_PATTERN})\b|"
    rf"\b(?:{_SCREEN_TARGET_PATTERN})\b"
    rf"[^.?!\n]{{0,100}}\b(?:visible|open|showing|currently|now)\b|"
    rf"\bwhere\s+(?:is|are|do|should|can)\b"
    rf"[^.?!\n]{{0,100}}\b(?:{_SCREEN_TARGET_PATTERN})\b|"
    r"\bwhere\s+(?:do|should|can)\s+(?:i|we|you)\s+(?:click|tap|press)\b|"
    rf"\b(?:point\s+(?:to|at)|show\s+me\s+where|high\s*light)\b"
    rf"[^.?!\n]{{0,100}}\b(?:{_SCREEN_TARGET_PATTERN}|it|this|that)\b|"
    rf"\bwhat(?:'s|\s+is)\s+on\b"
    rf"[^.?!\n]{{0,100}}\b(?:{_SCREEN_TARGET_PATTERN})\b|"
    r"\bwhat\s+am\s+i\s+looking\s+at\b",
    re.I,
)
_CURRENT_FACT_RE = re.compile(
    r"\b(?:current|latest|today|tonight|now|live|recent|time|date|weather|forecast|news|"
    r"price|exchange\s+rate|stock|ticker|market|trading|coin|crypto|portfolio|order|score|"
    r"schedule|status|health|president|prime\s+minister|ceo|law|regulation|release|version)\b",
    re.I,
)
_CURRENT_FACT_TOPIC_RE = re.compile(
    r"\b(?:time|date|weather|forecast|news|headlines?|updates?|events?|price|"
    r"exchange\s+rate|stock|ticker|market|trading|coin|crypto|portfolio|order|score|"
    r"schedule|status|health|president|prime\s+minister|ceo|law|regulation|release|version)\b",
    re.I,
)
_CURRENT_FACT_INSPECTION_RE = re.compile(r"\b(?:check|verify|inspect)\w*\b", re.I)
_SCHEDULE_RE = re.compile(
    r"\b(?:schedul\w*|remind\w*|cron|recurring|every\s+(?:day|week|month)|at\s+\d{1,2}(?::\d{2})?)\b",
    re.I,
)
_MCP_RE = re.compile(
    r"\b(?:mcp|connector|plugin|github|gitlab|trading|portfolio|exchange|coin|crypto|order|market)\b",
    re.I,
)
_CODE_TARGET_RE = re.compile(
    r"\b(?:code|codebase|repo(?:sitory)?|source|function|class|symbol|callers?|callees?|"
    r"dependency|dependencies|graph|lsp|tests|"
    r"test\s+(?:(?:the|this|that)\s+)?(?:case|file|module|runner|suite|workflow)|"
    r"debug|refactor|implementation|mapthis)\b",
    re.I,
)
_CODE_ACTION_RE = re.compile(
    r"\b(?:inspect|search|find|review|audit|debug|fix|refactor|change|edit|implement|"
    r"maintain(?!er)|trace|map|build|test|check|investigate|callers?|callees?|lsp|mapthis)\w*\b",
    re.I,
)
_CODE_EXPLAIN_PROJECT_RE = re.compile(
    r"\bexplain\w*\b[^.?!\n]{0,100}\b(?:this\s+)?(?:code|codebase|repo(?:sitory)?|source|"
    r"function|class|symbol|implementation|graph|lsp)\b",
    re.I,
)
_PERCEPTION_RE = re.compile(
    r"\b(?:image|photo|picture|screenshot|pdf)\b"
    r"[^.?!\n]{0,100}\b(?:look|see|read|inspect|check|analy[sz]e|describe|extract|show)\w*\b|"
    r"\b(?:look|see|read|inspect|check|analy[sz]e|describe|extract|show)\w*\b"
    r"[^.?!\n]{0,100}\b(?:image|photo|picture|screenshot|pdf)\b",
    re.I,
)
_DESIGN_RE = re.compile(
    r"\b(?:design|diagram|drawing|draw|sketch|(?:white\s*)?board|canvas|prototype|wireframe|mockup)\b",
    re.I,
)
_COMPUTER_ACTION_PATTERN = (
    r"(?:click|tap|type|press|scroll|drag|focus|activate|control|operate|switch|"
    r"interact|play|launch|open|search)\w*"
)
_COMPUTER_TARGET_PATTERN = (
    r"(?:screen|window|desktop|browser|chrome|tab|app|application|taskbar|icon|"
    r"start\s+menu|search\s+box|youtube|site|website|music|song|track|audio|"
    r"video|playback|media|player|spotify)"
)
_COMPUTER_DEMONSTRATION_TARGET_PATTERN = (
    rf"(?:{_COMPUTER_TARGET_PATTERN}|computer|"
    r"mo\s+connect(?:ed)?(?:\s+(?:tab|chrome|browser|live))?|"
    r"connected\s+(?:tab|chrome|browser))"
)
_COMPUTER_ACTION_CLAUSE_PATTERN = r"[^.?!;\n]*?"
_COMPUTER_ACT_RE = re.compile(
    rf"(?:\b{_COMPUTER_ACTION_PATTERN}\b{_COMPUTER_ACTION_CLAUSE_PATTERN}"
    rf"\b{_COMPUTER_TARGET_PATTERN}\b|"
    rf"\b{_COMPUTER_TARGET_PATTERN}\b{_COMPUTER_ACTION_CLAUSE_PATTERN}"
    rf"\b{_COMPUTER_ACTION_PATTERN}\b)",
    re.I,
)
_DIRECT_COMPUTER_CONTROL_RE = re.compile(
    r"^\s*(?:(?:yes|ok(?:ay)?|sure)[,!.]?\s+)?"
    r"(?:(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+|"
    r"(?:i|we)\s+(?:want|need)\s+you\s+to\s+)?)"
    r"(?:drive|control|operate|interact\s+with|take\s+over|take\s+control\s+of|use)\b"
    r"[^.?!\n]{0,180}\b(?:computer|keyboard|mouse|screen|desktop|window|app|application|"
    r"terminal|workspace|tui|browser)\b[^.?!\n]*[.?!]*\s*$",
    re.I,
)
_DIRECT_COMPUTER_DEMONSTRATION_RE = re.compile(
    rf"(?:^|[.!?;:]\s+|\b(?:and|then|also)\s+)\s*"
    r"(?:(?:hi|hey)\s+mo[,!]?\s+)?"
    r"(?:(?:yes|ok(?:ay)?|sure|good|now|then|please)[,!.]?\s+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i|we)\s+(?:want|need)\s+(?:you\s+)?to\s+)?"
    rf"(?:(?:do|run|perform|start|resume|go\s+ahead(?:\s+with)?)\s+(?:the\s+)?"
    rf"{_COMPUTER_DEMONSTRATION_TARGET_PATTERN}(?:\s+control)?\s+test\b|"
    rf"(?:test|try|demonstrate|demo)\b(?=[^.?!;\n]{{0,240}}\b"
    rf"{_COMPUTER_DEMONSTRATION_TARGET_PATTERN}\b))",
    re.I,
)
_NON_ACTION_COMPUTER_DEMONSTRATION_RE = re.compile(
    r"\b(?:parser|classifier|classification|router|routing|regex|source(?:\s+code)?|"
    r"codebase|rendering\s+code|test\s+(?:cases?|suite))\b|"
    r"\b(?:using|with)\s+(?:the\s+)?(?:phrase|prompt|request|example)\b|"
    r"\b(?:when|if)\s+(?:a\s+)?user\b|"
    rf"\b(?:do\s+not|don['’]?t|dont|never|without)\b[^,.?!;\n]{{0,80}}"
    rf"\b(?:{_COMPUTER_ACTION_PATTERN}|do|run|perform|start|resume|test|try|demonstrate|demo)\b|"
    r"\bshould\b[^?\n]{0,160}\?",
    re.I,
)
_DIRECT_WORKSPACE_RUN_RE = re.compile(
    r"^\s*(?:(?:yes|ok(?:ay)?|sure)[,!.]?\s+)?"
    r"(?:(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+|"
    r"(?:i|we)\s+(?:want|need)\s+you\s+to\s+)?)"
    r"(?:run|launch|open)\s+(?:the\s+)?workspace\b[^.?!\n]*[.?!]*\s*$",
    re.I,
)
_DIRECT_MO_ROUTE_RE = re.compile(
    r"^\s*(?:(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?)"
    r"(?:send|route)\s+(?:this|that)\s+(?:to|into)\s+(?:the\s+)?(?:current\s+)?"
    r"(?:mo(?:\s+terminal)?|terminal|workspace)\b[^.?!\n]*[.?!]*\s*$",
    re.I,
)
_PHONE_RE = re.compile(r"\b(?:phone(?:_[a-z0-9_]+)?|android|mobile|handset)\b", re.I)
_SYSTEMCARE_RE = re.compile(
    r"\b(?:system\s*care|pc\s+(?:health|cleanup|performance|optimization)|"
    r"windows\s+(?:health|cleanup|startup|performance)|startup\s+apps?|gaming\s+baseline|cache\s+cleanup|"
    r"(?:computer|pc|laptop)\s+(?:(?:is|runs?)\s+)?(?:slow|sluggish)|"
    r"(?:slow|sluggish)\s+(?:computer|pc|laptop))\b",
    re.I,
)
_WEB_RE = re.compile(
    r"https?://|www\.|\b(?:web|website|browser|browse|online|internet|search\s+the\s+web|fetch)\b",
    re.I,
)
_GITHUB_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:[/?#]|$)|"
    r"^\s*[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?\s*[?!.]*\s*$",
    re.I,
)
_EXTERNAL_REPOSITORY_RE = re.compile(
    r"\b(?:official|offical|upstream|remote|public|github|gitlab)\s+"
    r"(?:repo(?:sitory)?|source)\b|"
    r"\b(?:repo(?:sitory)?)\s+(?:from|on)\s+(?:github|gitlab|the\s+web)\b",
    re.I,
)
_LOCAL_PROJECT_TARGET_RE = re.compile(
    r"(?:^|\s)(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])|"
    r"\b(?:this|current|local|my|our|checked[- ]out)\s+"
    r"(?:repo(?:sitory)?|project|codebase|source|files?|folder|directory)\b|"
    r"\b[\w.-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg)\b",
    re.I,
)
_PROFILE_RE = re.compile(
    r"\b(?:remember|forget|profile|preference|about\s+me|who\s+am\s+i|record\s+this)\b",
    re.I,
)
_LIFE_RE = re.compile(
    r"\b(?:track|record|add|list|show|update|mark|complete|forget|remove|delete|manage|"
    r"remember|pay|owe|due|open|resolve|summarize|toon|volg|bewaar|markeer|vergeet|"
    r"verwijder|betalen|openstaand)\b[^.?!\n]{0,100}"
    r"\b(?:payments?|bills?|invoices?|subscriptions?|appointments?|commitments?|"
    r"life items?|cases?|paperwork|documents?|money|finances?|income|expenses?|spending|uitgaven?|inkomsten?|dossiers?|papierwerk|"
    r"betalingen?|facturen?|abonnementen?|afspraken?|termijnen?)\b|"
    r"\b(?:payments?|bills?|invoices?|subscriptions?|appointments?|commitments?|"
    r"life items?|cases?|paperwork|documents?|money|finances?|income|expenses?|spending|uitgaven?|inkomsten?|dossiers?|papierwerk|"
    r"betalingen?|facturen?|abonnementen?|afspraken?|termijnen?)\b"
    r"[^.?!\n]{0,100}\b(?:track|record|list|show|update|mark|complete|forget|"
    r"remove|delete|manage|due|open|resolve|summarize|toon|volg|bewaar|markeer|"
    r"vergeet|verwijder|betalen|openstaand)\b",
    re.I,
)
_LIFE_MONEY_QUESTION_RE = re.compile(
    r"\b(?:what|which|how\s+much|wat|welke|hoeveel)\b[^.?!\n]{0,100}"
    r"\b(?:my|our|mijn|onze)\b[^.?!\n]{0,100}"
    r"\b(?:money|finances?|income|expenses?|spending|uitgaven?|inkomsten?)\b",
    re.I,
)
_LIFE_CASE_QUESTION_RE = re.compile(
    r"\b(?:my|our|mijn|onze)\b[^.?!\n]{0,80}"
    r"\b(?:cases?|paperwork|dossiers?|papierwerk)\b",
    re.I,
)
_MIGRATION_RE = re.compile(r"\b(?:migrate|migration|adopt|import\s+(?:a\s+)?project|clone\s+and)\b", re.I)
_VISUALIZATION_RE = re.compile(
    r"\b(?:visuali[sz](?:e|ation)|chart|plot|graph\s+this|interactive\s+(?:tool|view|lab)|suno|seedance|seedream|kie)\b|"
    r"\b(?:generate|create|make|cover|extend|continue)\w*\b[^.?!\n]{0,100}\b(?:song|music|track|video|clip)\b",
    re.I,
)
_IMAGE_TOOL_RE = re.compile(
    r"\b(?:generate|create|make|resize|crop|rotate|flip|convert|transform|edit|show|display)\w*\b"
    r"[^.?!\n]{0,100}\b(?:images?|photos?|pictures?)\b|"
    r"\b(?:images?|photos?|pictures?)\b"
    r"[^.?!\n]{0,100}\b(?:resize|crop|rotate|flip|convert|transform|edit|show|display)\w*\b",
    re.I,
)
_NO_TOOLS_RE = re.compile(
    r"\b(?:do\s+not|don't|without|no)\s+(?:use|using|call|calling|run|running)?\s*tools?\b",
    re.I,
)
_CAPABILITY_MODAL_PATTERN = r"(?:can|could|does|do|is|are|will|would)"
_CAPABILITY_SUBJECT_PATTERN = (
    r"(?:mo(?:\s+desktop)?|desktop|companion|this\s+(?:app|surface|session))"
)
_CAPABILITY_ATTRIBUTE_PATTERN = (
    r"(?:have|offer|support|capab|able|read[- ]?only|guide[- ]?only|tools?|"
    r"act(?!ual)|control|do)\w*"
)
_CAPABILITY_QUESTION_RE = re.compile(
    r"\b(?:what|which)\s+(?:can|could)\s+you\s+(?:do|use|access|control)\b|"
    r"\b(?:what|which|describe|explain|list|show)\b[^.?!\n]{0,120}"
    r"\byour\s+(?:capabilit(?:y|ies)|features?|tools?)\b|"
    r"\b(?:what|which|describe|explain|list)\b[^.?!\n]{0,120}"
    r"\b(?:capabilit(?:y|ies)|features?|tools?)\b[^.?!\n]{0,100}"
    r"\b(?:available|supported|enabled|have|offer|use|can)\b|"
    rf"\b{_CAPABILITY_MODAL_PATTERN}\b\s+(?:the\s+)?"
    rf"\b{_CAPABILITY_SUBJECT_PATTERN}\b[^.?!\n]*?"
    rf"\b{_CAPABILITY_ATTRIBUTE_PATTERN}\b|"
    rf"\b{_CAPABILITY_MODAL_PATTERN}\b\s+(?:there\s+|any\s+)?"
    rf"\b{_CAPABILITY_ATTRIBUTE_PATTERN}\b[^.?!\n]*?"
    rf"\b(?:in|on|for|with|by)\s+(?:the\s+)?{_CAPABILITY_SUBJECT_PATTERN}\b",
    re.I,
)


def looks_like_screen_observation_request(text: object) -> bool:
    """Recognize an explicit request to inspect or point at a visible target.

    This shared predicate is a routing signal, never screen-access authority.
    Requiring a visible target keeps incidental language such as ``where can``
    in source or profile discussions from being treated as a screen request.
    """
    return bool(_SCREEN_OBSERVATION_RE.search(str(text or "")))


def looks_like_current_fact_request(text: object) -> bool:
    """Return True when current evidence has a subject or inspection signal.

    Temporal modifiers alone do not own routing: ``now`` and ``today`` also
    describe ordinary conversation and profile questions. Dynamic subjects and
    explicit checks keep real live-state lookups tool-backed.
    """
    value = str(text or "").strip()
    return bool(
        value
        and _CURRENT_FACT_RE.search(value)
        and (
            _CURRENT_FACT_TOPIC_RE.search(value)
            or _CURRENT_FACT_INSPECTION_RE.search(value)
        )
    )


def capability_hints_for(text: object) -> frozenset[str]:
    """Return bounded capability-family hints from action plus target signals."""
    value = str(text or "").strip()
    if not value:
        return frozenset()
    if _NO_TOOLS_RE.search(value):
        return frozenset()
    hints: set[str] = set()
    github_repo_reference = bool(_GITHUB_REPO_RE.search(value))
    external_repository = bool(_EXTERNAL_REPOSITORY_RE.search(value))
    external_repository_only = bool(
        external_repository and not _LOCAL_PROJECT_TARGET_RE.search(value)
    )
    if _FILE_TARGET_RE.search(value) and _FILE_ACTION_RE.search(value) and not external_repository_only:
        hints.add(CAP_FILES)
    if _ORGANIZE_RE.search(value):
        hints.update({CAP_FILES, CAP_FILE_ORGANIZATION})
    if _TRANSFER_RE.search(value):
        hints.add(CAP_TRANSFER)
    if looks_like_screen_observation_request(value):
        hints.add(CAP_SCREEN_OBSERVATION)
    if looks_like_current_fact_request(value):
        hints.add(CAP_CURRENT_FACTS)
    if _SCHEDULE_RE.search(value):
        hints.add(CAP_SCHEDULING)
    mcp_topics = {match.group(0).casefold() for match in _MCP_RE.finditer(value)}
    if mcp_topics - {"github"} or (mcp_topics and not github_repo_reference):
        hints.add(CAP_MCP)
    if not external_repository_only and _CODE_TARGET_RE.search(value) and (
        _CODE_ACTION_RE.search(value) or _CODE_EXPLAIN_PROJECT_RE.search(value)
    ):
        hints.add(CAP_CODE_GRAPH)
    if _PERCEPTION_RE.search(value):
        hints.add(CAP_PERCEPTION)
    if _DESIGN_RE.search(value):
        hints.add(CAP_DESIGN)
    if looks_like_computer_action_request(value):
        hints.add(CAP_COMPUTER_CONTROL)
    if _PHONE_RE.search(value):
        hints.add(CAP_PHONE)
    if _SYSTEMCARE_RE.search(value):
        hints.add(CAP_SYSTEMCARE)
    if _WEB_RE.search(value) or external_repository:
        hints.add(CAP_WEB)
    if github_repo_reference:
        hints.add(CAP_GITHUB_REPO)
    if _PROFILE_RE.search(value):
        hints.add(CAP_PROFILE)
    if _LIFE_RE.search(value) or _LIFE_MONEY_QUESTION_RE.search(value) or _LIFE_CASE_QUESTION_RE.search(value):
        hints.add(CAP_LIFE)
    if _MIGRATION_RE.search(value):
        hints.add(CAP_MIGRATION)
    if _VISUALIZATION_RE.search(value) or _IMAGE_TOOL_RE.search(value):
        hints.add(CAP_VISUALIZATION)
    return frozenset(hints)


def is_capability_question(text: object) -> bool:
    """Return True for questions about capability, not requests to exercise it."""
    return bool(_CAPABILITY_QUESTION_RE.search(str(text or "")))


def looks_like_computer_action_request(text: object) -> bool:
    """Recognize explicit computer actions for routing, never authorization."""
    value = str(text or "")
    demonstration_discussion = bool(
        re.search(r"\b(?:test|try|demonstrate|demo)\b", value, re.I)
        and _NON_ACTION_COMPUTER_DEMONSTRATION_RE.search(value)
    )
    return bool(
        (_COMPUTER_ACT_RE.search(value) and not demonstration_discussion)
        or _DIRECT_COMPUTER_CONTROL_RE.search(value)
        or _looks_like_direct_computer_demonstration_request(value)
        or _DIRECT_WORKSPACE_RUN_RE.search(value)
        or _DIRECT_MO_ROUTE_RE.search(value)
    )


def _looks_like_direct_computer_demonstration_request(text: object) -> bool:
    """Recognize a natural request to exercise computer control, not discuss it."""
    value = str(text or "")
    return bool(
        _DIRECT_COMPUTER_DEMONSTRATION_RE.search(value)
        and not _NON_ACTION_COMPUTER_DEMONSTRATION_RE.search(value)
    )


def looks_like_direct_computer_control_request(text: object) -> bool:
    """Recognize a direct natural request to drive a named computer surface.

    This is a routing hint, never a permission or confirmation boundary. The
    anchored instruction shape keeps review prose such as ``do not drive the
    computer`` out while accepting ordinary requests such as ``drive the live
    workspace`` without requiring a magic action verb.
    """
    value = str(text or "")
    return bool(
        _DIRECT_COMPUTER_CONTROL_RE.search(value)
        or _looks_like_direct_computer_demonstration_request(value)
    )


__all__ = [name for name in globals() if name.startswith("CAP_")] + [
    "capability_hints_for",
    "is_capability_question",
    "looks_like_computer_action_request",
    "looks_like_direct_computer_control_request",
    "looks_like_screen_observation_request",
]
