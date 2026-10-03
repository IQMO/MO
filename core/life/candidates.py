"""Conservative, local wording signals for the current mail result list.

These labels are navigation hints, never a semantic message summary or an
automatic decision to move, delete, learn, or track a message.
"""
from __future__ import annotations

import re


_GROUPS = (
    ("payment", re.compile(r"\b(?:betaling(?:en)?|betaal(?:verzoek)?|factu(?:ur|ren)|invoice|termijn(?:en)?|aanmaning|incasso|payment|bill(?:ing)?)\b", re.I)),
    ("subscription", re.compile(r"\b(?:abonnement(?:en)?|subscription|renewal|verlenging|lidmaatschap|membership)\b", re.I)),
    ("appointment", re.compile(r"\b(?:afspraak|appointment|reservering|booking|consult(?:ation)?|bezoek)\b", re.I)),
    ("issue", re.compile(r"\b(?:probleem|klacht|storing|problem|failed|mislukt|opgelost|support)\b", re.I)),
    ("case", re.compile(r"\b(?:case|paperwork|documents?|documentation)\b", re.I)),
)
_TIME_WORDS = re.compile(r"\b(?:aanmaning|achterstand|vervallen|overdue|past due|laatste herinnering|urgent|dringend)\b", re.I)


def visible_mail_signals(visible_text: str) -> dict[str, str]:
    """Describe literal row/subject matches without inferring message meaning."""
    text = " ".join(str(visible_text or "").split())[:500]
    group = next((name for name, pattern in _GROUPS if pattern.search(text)), "other")
    return {"candidate_group": group,
            "candidate_signal": "time wording" if _TIME_WORDS.search(text) else ""}
