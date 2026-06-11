"""Answer critic — secrets detection + user-configurable block/warning phrases.

Reads critique/ANSWER.md for user-extensible block/warning phrases.
This is the ONLY post-model output gate. Secrets + ANSWER.md rules only.

No false capability detection, no health claims, no voice scanning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CRITIQUE_PATH = "critique/ANSWER.md"

SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>[A-Za-z0-9_-]*(?:api[_-]?key|x-api-key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth[_-]?token|token|password|client[_-]?secret|secret))\b"
    r"(?P<sep>\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s\"',;}]{8,})(?P=quote)",
    re.IGNORECASE,
)
PRIVATE_KEY_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>private[_\s-]?key)\b(?P<sep>\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s\"',}]{8,})(?P=quote)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"\b(?P<prefix>bearer\s+)(?P<value>[A-Za-z0-9._\-+/=_]{8,})", re.IGNORECASE)
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b", re.IGNORECASE)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN\s+[A-Z0-9 ]*PRIVATE\s+KEY-----.*?-----END\s+[A-Z0-9 ]*PRIVATE\s+KEY-----",
    re.IGNORECASE | re.DOTALL,
)
# SSH / server connection strings — redact user@host to prevent leaking
# access targets. Handles both IP and hostname forms.
SSH_USER_HOST_RE = re.compile(
    r"\b(?P<user>[a-z_][a-z0-9_-]{1,32})@(?P<host>"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"        # user@ip
    r"|"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,}"  # user@hostname
    r")",
    re.IGNORECASE,
)
# Authorization header with credential value (already in sandbox, added here for critic)
AUTH_HEADER_RE = re.compile(
    r"(?i)([\"']?authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\s\"',}]+",
)
# Bare IPv4 addresses — context-sensitive; often docs but high-signal in server output
IPV4_RE = re.compile(r"\b(?<!\d)(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")


@dataclass
class CritiqueResult:
    text: str
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard_failures


class AnswerCritic:
    """Deterministic answer gate. Secrets only. No model call, no token cost."""

    def __init__(self, path: str | Path = DEFAULT_CRITIQUE_PATH):
        self.path = Path(path)
        self.extra_block_phrases: list[str] = []
        self.extra_warning_phrases: list[str] = []
        self.last_result: CritiqueResult | None = None
        self.reload()

    def reload(self):
        self.extra_block_phrases = []
        self.extra_warning_phrases = []
        if not self.path.exists():
            return
        rules_text = self.path.read_text(encoding="utf-8", errors="replace")
        self.extra_block_phrases = self._section_phrases(rules_text, "Extra Block Phrases")
        self.extra_warning_phrases = self._section_phrases(rules_text, "Extra Warning Phrases")

    @staticmethod
    def _section_phrases(rules_text: str, section: str) -> list[str]:
        pattern = re.compile(rf"^##\s+{re.escape(section)}\s*$", re.IGNORECASE | re.MULTILINE)
        match = pattern.search(rules_text)
        if not match:
            return []
        start = match.end()
        next_match = re.search(r"^##\s+", rules_text[start:], re.MULTILINE)
        end = start + next_match.start() if next_match else len(rules_text)
        body = rules_text[start:end]
        phrases = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line.startswith("-"):
                continue
            value = line[1:].strip().strip('"').strip("'")
            if value:
                phrases.append(value)
        return phrases

    @staticmethod
    def _is_placeholder_secret_value(value: str) -> bool:
        clean = str(value or "").strip().strip("`'\"")
        lower = clean.lower()
        if lower in {"[redacted]", "<redacted>", "redacted"}:
            return True
        if lower in {"...", "xxx", "xxxx", "xxxxx", "xxxxxxxx", "changeme", "change_me", "replace_me"}:
            return True
        secret_words = ("key", "token", "password", "secret")
        placeholder_words = ("your", "example", "sample", "dummy", "fake", "test", "placeholder", "replace")
        if any(word in lower for word in secret_words) and any(word in lower for word in placeholder_words):
            return True
        if lower.endswith("_here") and any(word in lower for word in secret_words):
            return True
        return False

    def _redact_secret_material(self, text: str) -> tuple[str, bool]:
        redacted = str(text or "")
        changed = False

        def replace_assignment(match: re.Match[str]) -> str:
            nonlocal changed
            value = match.group("value")
            if self._is_placeholder_secret_value(value):
                return match.group(0)
            changed = True
            quote = match.groupdict().get("quote") or ""
            return f"{match.group('name')}{match.group('sep')}{quote}[redacted]{quote}"

        def replace_bearer(match: re.Match[str]) -> str:
            nonlocal changed
            value = match.group("value")
            if self._is_placeholder_secret_value(value):
                return match.group(0)
            changed = True
            return f"{match.group('prefix')}[redacted]"

        def replace_ssh_user_host(match: re.Match[str]) -> str:
            nonlocal changed
            changed = True
            return f"{match.group('user')}@[redacted-host]"

        def replace_auth_header(match: re.Match[str]) -> str:
            nonlocal changed
            changed = True
            return f"{match.group(1)}[redacted]"

        redacted = PRIVATE_KEY_BLOCK_RE.sub("[redacted private key]", redacted)
        if redacted != text:
            changed = True
        redacted = SECRET_ASSIGNMENT_RE.sub(replace_assignment, redacted)
        redacted = PRIVATE_KEY_ASSIGNMENT_RE.sub(replace_assignment, redacted)
        redacted = BEARER_RE.sub(replace_bearer, redacted)
        before = redacted
        redacted = OPENAI_KEY_RE.sub("sk-[redacted]", redacted)
        if redacted != before:
            changed = True
        # Redact SSH user@host patterns (both IP and hostname)
        before = redacted
        redacted = SSH_USER_HOST_RE.sub(replace_ssh_user_host, redacted)
        if redacted != before:
            changed = True
        # Redact authorization headers with credential values
        before = redacted
        redacted = AUTH_HEADER_RE.sub(replace_auth_header, redacted)
        if redacted != before:
            changed = True
        # Redact bare IPv4 addresses (context-sensitive; only for server/connection output)
        before = redacted
        redacted = IPV4_RE.sub("[redacted-ip]", redacted)
        if redacted != before:
            changed = True
        return redacted, changed

    def review(self, text: str) -> CritiqueResult:
        original = str(text or "")
        cleaned_base, redacted_secret = self._redact_secret_material(original)
        lower = cleaned_base.lower()
        hard: list[str] = []
        warnings: list[str] = []

        if redacted_secret:
            warnings.append("secret material redacted")

        for phrase in self.extra_block_phrases:
            if phrase.lower() in lower:
                hard.append(f"blocked phrase: {phrase}")

        configured_warnings: list[str] = []
        for phrase in self.extra_warning_phrases:
            if phrase.lower() in lower:
                configured_warnings.append("configured warning phrase")

        warnings.extend(configured_warnings)

        if hard:
            reason = "; ".join(hard[:3])
            cleaned = (
                f"[answer held by critique: {reason}]\n"
                "I need to restate this without the blocked claim."
            )
        elif configured_warnings:
            reason = "; ".join(configured_warnings[:3])
            cleaned = (
                f"[answer critique warning: {reason}]\n"
                "Accuracy note: treat conflicting claims below as corrected.\n\n"
                + cleaned_base
            )
        else:
            cleaned = cleaned_base

        result = CritiqueResult(text=cleaned, hard_failures=hard, warnings=warnings)
        self.last_result = result
        return result
