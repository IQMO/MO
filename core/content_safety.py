"""Content-safety classifier — refuse clearly-malicious code generation.

A provider-agnostic deterministic pre-filter (runs before any provider call) so MO
declines to BUILD malware / attack tooling regardless of the underlying model, while
ALLOWING authorized security work: pentests, CTFs, the operator's own systems, and
defensive / detection / analysis research. This mirrors MO's charter — assist
authorized security testing, refuse offensive tooling for malicious use.

Conservative by design: default ALLOW. It refuses only when a generation verb meets an
unambiguously-malicious artifact/action AND no legitimate framing is present. The
operator can disable it entirely via config (agent.block_malicious_code). The goal is a
baseline floor for the shipped product, never an obstacle to the trusted operator's
real work — the false-positive bar is deliberately high.
"""
from __future__ import annotations

import re
from typing import Any

# Generation intent — produce the artifact, not discuss it.
_BUILD_VERB = re.compile(
    r"\b(write|create|build|make|develop|generate|code|implement|program|"
    r"give\s+me|need|help\s+me\s+(?:write|build|make|create|develop|code))\b",
    re.IGNORECASE,
)

# Unambiguously-malicious artifacts (offensive by nature).
_MALICIOUS_ARTIFACT = re.compile(
    r"\b(ransomware|keylogger|spyware|rootkit|botnet|infostealer|cryptojack\w*|"
    r"trojan|computer\s+virus|self[-\s]?propagat\w+|credential\s+(?:stealer|harvester)|"
    r"password\s+stealer|ddos\s+(?:tool|script|attack|bot)|denial[-\s]of[-\s]service\s+attack|"
    r"phishing\s+(?:kit|page|site))\b",
    re.IGNORECASE,
)

# Malicious verb+object actions ("steal passwords", "exfiltrate credentials").
_MALICIOUS_ACTION = re.compile(
    r"\b(steal|exfiltrate|harvest|siphon)\b[^.\n]{0,40}\b(password|credential|"
    r"login|session\s+token|private\s+key|wallet|seed\s+phrase)s?\b",
    re.IGNORECASE,
)

# Malware anti-detection ("evade antivirus/EDR/detection").
_EVASION = re.compile(
    r"\b(evade|bypass|defeat|disable|avoid)\b[^.\n]{0,30}\b(antivirus|\bav\b|edr|"
    r"defender|endpoint\s+detection|sandbox\s+detection)\b",
    re.IGNORECASE,
)

# Legitimate / authorized framing → allow (trust the operator's stated context).
_LEGIT_CONTEXT = re.compile(
    r"\b(authoriz\w+|permission|consent|my\s+own|our\s+own|pentest|penetration\s+test\w*|"
    r"red\s+team|blue\s+team|ctf|capture\s+the\s+flag|lab|research|academic|class|course|"
    r"homework|assignment|defensive|defen[cs]e|detect\w*|analy[sz]\w+|reverse[-\s]engineer\w*|"
    r"honeypot|sandbox|test\s+environment|vulnerability\s+(?:scan|assessment|research)|"
    r"bug\s+bounty|how\s+(?:does|do|to\s+detect))\b",
    re.IGNORECASE,
)


def classify_harmful_coding_request(text: Any, config: dict[str, Any] | None = None) -> str | None:
    """Return a refusal message for a clearly-malicious build request, else None."""
    cfg = config if isinstance(config, dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    if not agent_cfg.get("block_malicious_code", True):
        return None
    s = str(text or "")
    if not s.strip():
        return None
    # Authorized/defensive/educational framing → trust it and proceed.
    if _LEGIT_CONTEXT.search(s):
        return None
    if not _BUILD_VERB.search(s):
        return None
    if _MALICIOUS_ARTIFACT.search(s) or _MALICIOUS_ACTION.search(s) or _EVASION.search(s):
        # State the principle, keep a conversational tone, no bullets (Fable L46/L60/L90).
        return (
            "I won't build that. Creating malware or attack tooling for offensive use "
            "isn't something I'll help with. If this is legitimate security work — an "
            "authorized engagement, a CTF, your own systems, or defensive/detection "
            "research — frame it that way and I'm glad to help."
        )
    return None
