"""Lightweight MO Desktop feature knowledge for the current surface policy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesktopCapability:
    name: str
    actions: str
    limits: str
    owner: str


CAPABILITIES = (
    DesktopCapability("Conversation", "chat, choices, attachments, history; image drops preview locally, with explicit Send to MO and one row of image actions", "isolated Desktop session; no Terminal taskboard", "mo_desktop/companion.py"),
    DesktopCapability("Project implementation", "hand an explicit software change to a live same-project MO Terminal, open one when absent, or continue through an explicitly active Project Architect", "the ordinary companion stays available and does not implement project source", "mo_desktop/companion.py and core/design/terminal_handoff.py"),
    DesktopCapability("Screen help", "observe, explain, point, bounded walkthroughs, and launch one unambiguous installed application by name in a single action", "fresh target evidence and verification required; ambiguous app names launch nothing and dispatch alone is not success", "core/desktop/ and tools/computer.py"),
    DesktopCapability("Screen capture", "hold the bottom-left cube for two seconds as it brightens, drag a rectangle, preview and share the PNG to a running MO terminal", "saved in private profile media; Share prepares an unsent path in the exact conversation", "mo_desktop/cube_interaction.py and mo_desktop/companion.py"),
    DesktopCapability("Connected Chrome", "discover ordinary tabs and attach the selected tab on demand for inspection and actions", "requires the configured extension/native bridge; stopped, private, protected and other-debugger tabs stay excluded", "core/browser_bridge.py and clients/chrome/"),
    DesktopCapability("Dashboard", "cube launcher opens the full Dashboard using the resident Agent without starting a terminal; cube right-click opens compact Home, Work, You, Systems with Project checks; Settings owns project LSP preferences", "delegates to canonical owners; configuration is not verification", "core/dashboard/ and mo_desktop/"),
    DesktopCapability("MO app launcher", "cube double-click opens four grouped app tiles; one gear owns Edit, Add file, Add folder, Reset and Appearance; holding an app enters Edit with removal and animated drag; headings move groups outside Edit; idle buzz at 10s and close at 15s", "hover never launches an app; removing a shortcut preserves its target; colors follow the active Desktop skin", "mo_desktop/tray.py and mo_desktop/companion.py"),
    DesktopCapability("Focus mode", "freely movable lower-right cube expansion, window switch/minimize/close, side previews with wheel cycling, installed-app and indexed-file search, actual taskbar pins, combined Explorer tray with native icon actions, clock/calendar, inline dimming, compact power controls with optional cancellable countdown, aligned composer width and reversible taskbar hiding", "Windows DWM; single-click folds to time/window count and normal cube motion, double-click exits; file coverage follows Windows indexing; modern Explorer retains its native flyout; power timer off by default; no duplicate cube, AI turn or saved mode", "mo_desktop/tray.py and mo_desktop/focus.py; switching reuses mo_desktop/phone/trackpad.py"),
    DesktopCapability("MO Design", "open, inspect, refine, and hand off visual concepts", "read-only project inspection until explicit implementation", "core/design/ and mo_desktop/design_studio/"),
    DesktopCapability("Mologrthim", "open the project workroom from the Work cube and inspect existing specialists, assignments and evidence", "opening the view does not activate a role or dispatch workers; execution remains with existing role and worker owners", "mo_desktop/mologrthim/"),
    DesktopCapability("Explainer video", "produce narrated explainers and product videos through mo --explainer; read core/explainer/README.md for its existing workflow", "local shell/artifact access and optional render/voice dependencies; verify final media", "core/explainer/ and core/skills/seeds/explainer-video/SKILL.md"),
    DesktopCapability("MO Files", "browse approved sources and guarded transfers", "path, source, phone, and mutation boundaries apply", "core/files/ and mo_desktop/files/"),
    DesktopCapability("MO SystemCare", "inspect machine, MO, configured hosts and selected projects; review exact maintenance plans and receipts", "read-only inspection; selected native actions require current evidence, confirmation and applicable Windows permission; only eligible exact registry values are repairable", "core/systemcare/ and mo_desktop/systemcare/"),
    DesktopCapability("MO Phone", "open the native device workspace, check Hub/setup and the independent Desktop host, and create an inline one-use companion pairing QR", "QR requires a reachable Hub and authenticated coordinator; Android grants remain phone-owned; ordinary pairing does not grant phone-host control", "mo_desktop/phone/"),
    DesktopCapability("Phone Trackpad", "use a paired Android phone (Google Play or Direct build) for cursor, typing, window switching, zoom and Board pen input", "explicit pairing, foreground target and device consent", "mo_desktop/phone/trackpad.py"),
    DesktopCapability("MO Shell", "launch independent floating Shell instances and focus each through running-app rows", "one canonical terminal and one explicit window attachment per Shell; its title is not observed window content", "mo_shell/ and mo_desktop/companion.py"),
    DesktopCapability("Voice", "double-Alt hold-to-talk and optional local speech output", "optional local runtime and explicit activation", "mo_desktop/voice/"),
    DesktopCapability("Window appearance", "select None, Shadow, Glow or Hybrid and shared intensity in Settings", "appearance applies to MO-owned windows; it grants no control", "mo_desktop/settings_panel.py"),
    DesktopCapability("Live Control", "share the running primary display with a paired Android controller", "exact device scopes, remote-host identity and renewable short leases", "mo_everywhere/live_control.py and mo_desktop/live_control.py"),
    DesktopCapability("Continuity", "follow a terminal and show bounded Everywhere state", "current runtime evidence only; no transcript merging", "core/runtime/continuity.py and mo_desktop/"),
)


def render_capability_manifest() -> str:
    lines = ["Verified MO Desktop features (current product contract):"]
    for item in CAPABILITIES:
        lines.append(f"- {item.name}: {item.actions}. Limits: {item.limits}. Owner: {item.owner}.")
    return "\n".join(lines)
