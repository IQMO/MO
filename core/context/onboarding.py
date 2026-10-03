"""First-contact nudge — the MODEL side of MO's cold-start onboarding.

MO already invites the operator's NAME two ways, so this nudge must NOT repeat them:
  * the TUI banner (``interface.tui_app``) shows "MO doesn't know you yet ..." while
    the profile has no name, and
  * ``profile.capture_operator_name`` auto-captures the name from a
    self-introduction ("I'm <Name>").

What those passive paths do NOT do is proactively learn the richer context that makes
MO personalized — the operator's projects and how they like MO to work. This nudge
drives exactly that, once, and persists it through the existing ``record_profile_fact``
tool. It never re-asks for the name and never blocks the user's task.
"""
from __future__ import annotations

from typing import Any


def build_onboarding_context(profile: Any = None) -> str:
    """Return the one-time first-contact nudge (projects + working style only).

    Pure text: the caller gates inclusion via ``Profile.needs_onboarding()`` and marks
    it offered so it fires once.
    """
    return (
        "This is a NEW operator MO hasn't learned yet. MO is a personalized agent, so use early "
        "contact to start modeling them. Their NAME is already handled — it auto-captures when they "
        "introduce themselves and the interface already invites it, so do NOT ask for their name again.\n"
        "- When it fits naturally (not mid-task), warmly invite ONCE the part the passive hints can't "
        "get: what they're working on (their projects/repos) and how they like MO to work (tone, "
        "verbosity, verify-first, autonomy). One or two sentences, optional — not a form.\n"
        "- Persist durable things they share with the record_profile_fact tool (projects, repos, deploy "
        "method, credential LOCATIONS, stated preferences) — never secret values.\n"
        "- Coming from another agent? Mention ONCE, lightly, that MO can migrate their setup — they just "
        "name it (OpenClaw, Hermes, …). If they accept, call migrate with action=inspect and that source to "
        "read their existing profile/memory READ-ONLY and adapt to them right away (Tier 1: nothing is "
        "moved, fully reversible). Never say you detected another agent — MO has no access until they opt in.\n"
        "- If their first message is a real task, do the task first; never block work to onboard. If they "
        "decline or it's a scripted one-shot, drop it — MO keeps capturing facts autonomously as they surface.\n"
        "Offer only once; don't repeat on later turns."
    )
