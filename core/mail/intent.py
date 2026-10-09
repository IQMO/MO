"""Small mail-turn classifier shared by Agent and Hub persistence boundaries."""
from __future__ import annotations

import re


# A generic message can be a chat turn, CLI coordination, or a diagnostic.
# Require mail context before applying the mail-only tools and ephemeral history.
_EXPLICIT_MAIL_REQUEST = re.compile(
    r"(?ix)^/mail(?:\s|$)|"
    r"\b(?:what(?:'s|\s+is)|any|how\s+many)\b.{0,80}\b(?:gmail|outlook|hotmail|live\.com|e-?mails?|inbox|mailbox)\b|"
    r"\b(?:read|review|inspect|look\s+at|search|find|summari[sz]e|show|list|draft|compose|write|reply|send|archive|trash|delete|move|organize|sort|label|tag|mark|check|sync|connect|set\s*up|setup|authori[sz]e|link|sign\s*in|disconnect|status|count|unread|triage|lees|lezen|zoek|zoeken|vind|vinden|toon|bekijk|samenvat\w*|vat|verplaats|verwijder|verstuur|verzend|markeer|organiseer|sorteer|filter|verbind\w*|koppel\w*|aanmeld\w*)\b"
    r".{0,120}\b(?:gmail|outlook|hotmail|live\.com|e-?mails?|inbox|mailbox)\b|"
    r"\b(?:gmail|outlook|hotmail|live\.com|e-?mails?|inbox|mailbox)\b.{0,120}"
    r"\b(?:read|review|inspect|look\s+at|search|find|summari[sz]e|show|list|draft|compose|write|reply|send|archive|trash|delete|move|organize|sort|label|tag|mark|check|sync|connect|set\s*up|setup|authori[sz]e|link|sign\s*in|disconnect|status|count|unread|triage|lees|lezen|zoek|zoeken|vind|vinden|toon|bekijk|samenvat\w*|vat|verplaats|verwijder|verstuur|verzend|markeer|organiseer|sorteer|filter|verbind\w*|koppel\w*|aanmeld\w*)\b"
)
_APPROVAL_REPLY = re.compile(r"\bapprove\s+[0-9a-f]{10}\b", re.I)


def is_mail_sensitive_request(text: str, *, include_approval: bool = False) -> bool:
    value = str(text or "")
    return bool(_EXPLICIT_MAIL_REQUEST.search(value) or (include_approval and _APPROVAL_REPLY.search(value)))
