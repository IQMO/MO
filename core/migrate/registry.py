"""Read-only registry of source-agent footprints for MO Migration.

Encodes WHERE peer agents (OpenClaw, Hermes) keep the artifacts a user would
move into MO, WHERE each maps, and enough routing metadata (``kind``/``target``)
for the write layer. Pure data + resolution — it reads no user files and writes
nothing. Access to a user's source data happens only after they opt in and name
the source (see ``core.migrate.inspect`` / ``core.migrate.apply``); MO never
scans the home directory unprompted.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceFile:
    """A readable, non-secret source artifact and where it lands in MO.

    ``kind`` routes the write layer: identity/persona -> a MO profile file,
    memory -> captured facts, rules -> the project AGENTS.md. ``target`` names
    the concrete MO destination. ``maps_to`` is the human description.
    """

    rel: str
    maps_to: str
    kind: str = ""      # identity | persona | memory | rules
    target: str = ""    # operator.md | behavior.md | facts.md | AGENTS.md


@dataclass(frozen=True)
class SourceAgent:
    """One peer agent's on-disk footprint and how it maps into MO."""

    key: str
    label: str
    homes: tuple[str, ...]          # candidate home dirs (env vars / ~ expanded)
    files: tuple[SourceFile, ...]   # readable text artifacts (NEVER secrets)
    reauth: tuple[str, ...]         # credentials that re-auth in MO, never copied
    unsupported: tuple[str, ...]    # assets MO cannot take (declared, not dropped)
    skills_dir: str = ""            # relative dir of user skills, if any
    provider_note: str = ""         # where provider keys live (reference only)

    def resolve_home(self, override: str | None = None) -> Path | None:
        """Return the first existing home dir, or None. Never creates anything."""
        candidates = [override] if override else list(self.homes)
        for raw in candidates:
            if not raw:
                continue
            p = Path(os.path.expandvars(os.path.expanduser(str(raw))))
            if p.is_dir():
                return p
        return None


# Verified footprints (see MO-ADOPT-FROM-AGENTS-PROPOSAL §4c). Only readable,
# non-secret text artifacts are listed under ``files``; credentials and session
# stores are intentionally absent so migration can never read them.
KNOWN_SOURCES: dict[str, SourceAgent] = {
    "openclaw": SourceAgent(
        key="openclaw",
        label="OpenClaw",
        homes=("~/.openclaw",),
        files=(
            SourceFile("workspace/USER.md", "operator.md — who you are, projects, preferences", "identity", "operator.md"),
            SourceFile("workspace/SOUL.md", "behavior.md — the tone / working style you set", "persona", "behavior.md"),
            SourceFile("workspace/MEMORY.md", "captured durable memory facts", "memory", "facts.md"),
            SourceFile("workspace/AGENTS.md", "project AGENTS.md — your working rules", "rules", "AGENTS.md"),
        ),
        reauth=(
            "credentials/ + openclaw.json (API keys, platform logins) — you re-auth in MO; never copied",
        ),
        unsupported=(
            "Discord / Slack / WhatsApp / Signal channel state — not imported; configure MO's supported surfaces separately",
            "raw session history (openclaw-agent.sqlite) — summarized into MO memory, not lifted",
        ),
        skills_dir="skills",
        provider_note="openclaw.json holds provider selection; put the API key in ~/.mo/credentials/providers.env (reference, never copied)",
    ),
    "hermes": SourceAgent(
        key="hermes",
        label="Hermes",
        homes=("~/.hermes", r"%LOCALAPPDATA%\hermes"),
        files=(
            SourceFile("USER.md", "operator.md — who you are, projects, preferences", "identity", "operator.md"),
            SourceFile("MEMORY.md", "captured durable memory facts", "memory", "facts.md"),
        ),
        reauth=(
            "API keys (Telegram/OpenRouter/OpenAI/Anthropic/ElevenLabs) — you re-auth/reference in MO; never copied",
        ),
        unsupported=(
            "Discord / Slack / WhatsApp / Signal channel state — not imported; configure MO's supported surfaces separately",
            "raw session history / FTS5 index — summarized into MO memory, not lifted",
            "Hermes-specific tools (home-assistant, etc.) — no MO equivalent",
        ),
        skills_dir="skills",
        provider_note="Hermes stores provider keys in its config; put each key in ~/.mo/credentials/providers.env (reference, never copied)",
    ),
}


def known_source_keys() -> tuple[str, ...]:
    return tuple(KNOWN_SOURCES.keys())


def resolve_source(name: str) -> SourceAgent | None:
    """Return the SourceAgent for an operator-named source, or None.

    Tolerant of case, spacing, punctuation, and a path-ish name (``~/.openclaw``):
    it normalizes to alphanumerics and matches a known key as a substring.
    """
    norm = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    if not norm:
        return None
    if norm in KNOWN_SOURCES:
        return KNOWN_SOURCES[norm]
    for key, agent in KNOWN_SOURCES.items():
        if key in norm:
            return agent
    return None
