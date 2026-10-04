"""Presentation and validation for controls backed by existing Desktop settings.

Defaults and effective values are loaded from settings/voice owners, never here.
Bounds match the supported live setters and canonical normalization.
"""
from __future__ import annotations

import math
import re
from typing import Any

# key, page, group, label, detail, control, constraints
FIELDS = (
    ("character.size", "appearance", "Cube", "Size", "The original floating cube cluster.", "range", (56, 140, 2, "px")),
    ("character.glow", "appearance", "Cube", "Glow", "A soft light around the cubes.", "range", (0, 1, .05, "")),
    ("character.corner_radius", "appearance", "Cube", "Cube corners", "Shared by the character and its expanded faces.", "range", (0, .5, .02, "")),
    ("character.cube_count", "appearance", "Cube", "Formation", "Choose the existing four or five cube formation.", "select", ((4, "Four cubes"), (5, "Five cubes"))),
    ("character.color_mode", "appearance", "Cube", "Cube color", "Follow the skin, or choose a custom color.", "color", ()),
    ("panel.window_effect", "appearance", "Windows & controls", "Window effect", "One effect across MO windows.", "select", (("none", "None"), ("shadow", "Shadow"), ("glow", "Glow"), ("hybrid", "Shadow & glow"))),
    ("panel.window_effect_intensity", "appearance", "Windows & controls", "Effect strength", "Preview changes on open MO windows.", "range", (0, 100, 5, "%")),
    ("panel.padding", "appearance", "Windows & controls", "Panel spacing", "Room around panel content.", "range", (8, 28, 1, "px")),
    ("panel.corner_radius", "appearance", "Windows & controls", "Panel corners", "The same radius across MO surfaces.", "range", (0, 30, 1, "px")),
    ("panel.button_padding", "appearance", "Windows & controls", "Button spacing", "Room inside shared controls.", "range", (4, 16, 1, "px")),
    ("panel.button_corner_radius", "appearance", "Windows & controls", "Button corners", "Shared buttons and fields.", "range", (0, 16, 1, "px")),
    ("behavior.default_mode", "general", "Movement", "Default movement", "Free placement or follow the pointer.", "select", (("free", "Free"), ("lock", "Chase"))),
    ("behavior.follow_distance", "general", "Movement", "Follow distance", "Space between MO and the pointer.", "range", (20, 140, 2, "px")),
    ("behavior.follow_ease", "general", "Movement", "Follow response", "Lower values follow more gently.", "range", (.04, .4, .02, "")),
    ("behavior.keep_above_apps", "general", "Movement", "Keep above selected apps", "Exact executable names, separated by commas.", "text", ()),
    ("voice.stt_enabled", "voice", "Listening & speech", "Hold to talk", "Press Alt, then hold Alt to speak.", "switch", ()),
    ("voice.chat_enabled", "voice", "Listening & speech", "Continuous voice chat", "Listen again after each spoken reply.", "switch", ()),
    ("voice.tts_enabled", "voice", "Listening & speech", "Speak typed replies", "Read replies to your typed messages aloud.", "switch", ()),
    ("voice.speech_rate", "voice", "Listening & speech", "Speaking pace", "Preview the voice at your preferred pace.", "range", (.5, 2, .05, "×")),
    ("voice.output_device", "voice", "Listening & speech", "Audio output", "Use the system output or a connected device.", "device", ()),
    ("voice.conversation_provider", "voice", "Conversation", "Spoken replies", "A fast model answers when you talk; tasks still run on MO's model.", "provider", ()),
    ("voice.role", "voice", "Conversation", "Role", "An existing role, Default, or a custom persona.", "role", ()),
    ("voice.role_active", "voice", "Conversation", "Use selected role", "Applies to text and voice conversations.", "switch", ()),
)
BY_ID = {row[0]: row for row in FIELDS}

# Authored preference editors. Runtime owners supply defaults; an omitted
# value stays omitted until explicitly overridden here.
CONFIGURATION_GROUPS = (
    ("general", "Desktop startup & updates", "These options apply when their owning process starts.", (
        ("mo_desktop.enabled", "Start Desktop with MO", "bool"),
        ("mo_desktop.tray_enabled", "Show Desktop tray icon", "bool"),
        ("update.check", "Check for updates", "bool"),
    )),
    ("models", "Agent budgets", "Saved configuration; provider support determines which request limits are sent. Open terminals reload configuration separately.", (
        ("agent.temperature", "Temperature", "number"),
        ("agent.max_tokens", "Requested output tokens", "number"),
        ("agent.max_provider_requests_per_turn", "Provider requests per turn · 0 is unlimited", "number"),
        ("agent.context_budget_tokens", "Context token budget", "number_auto"),
        ("agent.context_reserve_tokens", "Context reserve", "number"),
        ("agent.context_handoff_enabled", "Context handoff", "bool"),
    )),
    ("voice", "Speech engines", "Local recognition and speech workers use these options. Restart Desktop after editing engine configuration.", (
        ("mo_desktop.voice.stt_engine", "Recognition engine", ("whisper", "windows")),
        ("mo_desktop.voice.stt_model", "Recognition model", ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")),
        ("mo_desktop.voice.stt_device", "Recognition device", ("cpu", "cuda", "auto")),
        ("mo_desktop.voice.stt_beam_size", "Recognition beam size", "number"),
        ("mo_desktop.voice.stt_idle_seconds", "Recognition worker idle seconds", "number"),
    )),
    ("connections", "MCP tools", "Servers and exact tool allowlists are authored in configuration. Enabling MCP alone starts no servers; tools load on demand.", (
        ("mcp.enabled", "MCP admission", "bool"),
        ("local_extensions.enabled", "Profile extensions", "bool"),
    )),
    ("connections", "Image generation", "Generation is separate from reading images. Availability also depends on the configured backend and credentials.", (
        ("image.backend", "Generation backend", ("auto", "codex", "openai_compatible", "off")),
    )),
    ("connections", "Telegram", "Bot setup and pairing use MO’s existing Telegram controls. Credentials remain in their private service file.", (
        ("telegram.enabled", "Telegram service", "bool"),
        ("telegram.dm_policy", "Direct-message policy", ("pairing", "allowlist", "disabled")),
    )),
    ("projects", "Diagnostic defaults", "Per-project choices below override this default. Configured servers start on demand; they are not evidence of a clean check.", (
        ("lsp.enabled", "Enable configured language servers", "bool"),
        ("lsp.timeout", "Server timeout seconds", "number"),
    )),
    ("permissions", "Filesystem & safeguards", "Saved policy preferences. Roles, tool gates and exact-request confirmations still apply.", (
        ("access.mode", "Filesystem access", ("project", "full")),
        ("sandbox.enabled", "Tool safeguards", "bool"),
        ("sandbox.clean_env", "Clean child environment", "bool"),
        ("sandbox.block_shell_escape", "Block shell escape", "bool"),
        ("sandbox.block_write_secrets", "Block literal secrets in edits", "bool"),
        ("agent.block_malicious_code", "Malicious-code safeguard", "bool"),
    )),
    ("permissions", "Network & screen access", "Screen capture and sending pixels to a provider are separate controls. Host allowlists and exact provider routes stay in configuration.", (
        ("sandbox.shell_network_enabled", "Shell network access", "bool"),
        ("sandbox.web_fetch_enabled", "Web tools", "bool"),
        ("sandbox.screen_capture_enabled", "Screen capture", "bool"),
        ("mo_desktop.computer_use.pixel_policy", "Desktop image routing", ("local_only", "configured_providers", "confirm_cloud")),
    )),
    ("memory", "Learning", "Automatic promotion applies only to eligible repeated guidance. Turning it off does not undo accepted learning; review and Undo remain in Dashboard → Learning.", (
        ("learning.auto_promote", "Promote eligible guidance automatically", "bool"),
        ("learning.materialize_packs", "Create skill packs from automatic learning", "bool"),
        ("learning.capture_nudge", "Prompt MO to record durable guidance", "bool"),
    )),
    ("memory", "Skills & conventions", "Task skills and authored conversation roles have different delivery rules. Importing, reviewing and promoting a skill remain explicit actions.", (
        ("skills.enabled", "Task skill delivery", "bool"),
        ("skills.project_local", "Allow project-local skills", "bool"),
        ("skills.semantic_match", "Match skills by meaning", "bool"),
        ("skills.decay_days", "Relevance decay days", "number"),
    )),
    ("memory", "Memory recall", "Keyword recall remains available when semantic recall is off or unavailable. Semantic skill matching is a separate option above.", (
        ("embeddings.enabled", "Semantic recall", "bool"),
        ("embeddings.backend", "Embedding backend", ("api", "local")),
        ("embeddings.local_shared_worker", "Share local embedding worker", "bool"),
    )),
    ("automation", "Scheduler", "The service runs reminders and scheduled work. Job creation, pause, resume and cancellation belong in Dashboard → Life; a configured service may not be running.", (
        ("scheduler.enabled", "Start scheduler service", "bool"),
        ("scheduler.tick_seconds", "Scheduler interval seconds", "number"),
        ("agent.background_workers_max", "Background worker limit", "number"),
    )),
    ("devices", "Presence & continuity", "Presence records which MO instances are available. Conversation continuity and curated profile sync are separate lanes; neither means devices are paired or authorized.", (
        ("heartbeat.enabled", "Instance presence heartbeat", "bool"),
        ("heartbeat.interval_seconds", "Heartbeat interval seconds", "number"),
        ("consistent_everywhere.enabled", "Everywhere configuration", "bool"),
        ("consistent_everywhere.device_role", "Machine role", ("workstation", "server", "mobile")),
        ("consistent_everywhere.continuity.auto_sync", "Sync conversation continuity automatically", "bool"),
        ("consistent_everywhere.sync.auto_sync", "Sync curated profile automatically", "bool"),
    )),
    ("devices", "Live Control & files", "Device grants remain explicit in Phone and Everywhere. Host controls need exact scopes and local consent; transfer acceptance never grants unrestricted file access.", (
        ("consistent_everywhere.live_control.enabled", "Live Control configuration", "bool"),
        ("consistent_everywhere.live_control.host.enabled", "Allow this host to publish", "bool"),
        ("file_transfer.enabled", "File cargo", "bool"),
        ("file_transfer.auto_accept", "Receive catalog cargo automatically", "bool"),
        ("file_transfer.auto_accept_named_paths", "Accept named destinations automatically", "bool"),
        ("file_manager.enabled", "Cross-machine file browsing", "bool"),
    )),
)


def configuration_overview(config: dict[str, Any], *, current: dict | None = None,
                           defaults: dict | None = None, effective: dict | None = None) -> list[dict[str, Any]]:
    """Bounded discovery of authored values, without reading private content.

    Missing keys are not treated as disabled. Wrong types and unknown strings
    are not echoed, so even a malformed config cannot leak arbitrary values.
    """
    from core.state.configuration_defaults import DEFAULT_PREFERENCES
    from core.state.everywhere_coordinator import CoordinatorSettings
    from core.state.everywhere_readiness import everywhere_authority
    from core.transfer.model import TransferSettings
    from mo_desktop.settings import load_settings
    from mo_everywhere.live_control import LiveControlSettings

    def owned_values(source):
        desktop = load_settings(source)
        coordinator = CoordinatorSettings.from_config(source)
        transfer = TransferSettings.from_config(source)
        return {
            "mo_desktop.enabled": desktop.enabled,
            "mo_desktop.tray_enabled": desktop.tray_enabled,
            "consistent_everywhere.enabled": coordinator.enabled,
            "consistent_everywhere.device_role": everywhere_authority(source).role,
            "consistent_everywhere.continuity.auto_sync": coordinator.continuity_auto_sync,
            "consistent_everywhere.sync.auto_sync": coordinator.profile_auto_sync,
            "consistent_everywhere.live_control.enabled": LiveControlSettings.from_config(source).enabled,
            "file_transfer.enabled": transfer.enabled,
            "file_transfer.auto_accept": transfer.auto_accept,
            "file_transfer.auto_accept_named_paths": transfer.auto_accept_named_paths,
        }

    baseline = {**DEFAULT_PREFERENCES, **owned_values({}),
                "access.mode": "project", "consistent_everywhere.live_control.host.enabled": False,
                **(defaults or {})}
    current = config if current is None else current
    effective = {**owned_values(current), **(effective or {})}
    def read(source, path):
        value = source
        for key in path.split('.'):
            if not isinstance(value, dict) or key not in value:
                return baseline.get(path)
            value = value[key]
        return value
    def display(path, value):
        if value is None:
            return "Not set"
        try:
            value = validate_configuration(path, value)
        except ValueError:
            return "Custom configuration"
        if isinstance(value, bool):
            return "On" if value else "Off"
        return "Automatic" if value == "auto" else str(value).replace('_', ' ')
    groups = []
    for page, label, detail, fields in CONFIGURATION_GROUPS:
        values = []
        for path, title, kind in fields:
            value: Any = config
            present = True
            for key in path.split("."):
                if not isinstance(value, dict) or key not in value:
                    present = False
                    break
                value = value[key]
            shown = "Runtime default · not overridden"
            if present:
                shown = "Review in configuration"
                if kind == "bool" and isinstance(value, bool):
                    shown = "On" if value else "Off"
                elif kind in ("number", "number_auto") and type(value) in (int, float) and math.isfinite(value):
                    shown = str(value)
                elif kind == "number_auto" and value == "auto":
                    shown = "Automatic"
                elif isinstance(kind, tuple) and isinstance(value, str) and value in kind:
                    shown = value.replace("_", " ")
            try:
                admitted = validate_configuration(path, value) if present else None
            except ValueError:
                admitted = None
            values.append({"key": path, "label": title, "value": shown, "kind": kind,
                           "configured": present, "selection": admitted,
                           "default_value": display(path, baseline.get(path)),
                           "current": display(path, effective.get(path, read(current, path))),
                           "pending": read(config, path) != read(current, path),
                           "detail": CONFIGURATION_HELP[path],
                           "limits": CONFIGURATION_LIMITS.get(path)})
        groups.append({"page": page, "label": label, "detail": detail, "values": values})
    return groups


CONFIGURATION_FIELDS = {path: kind for _, _, _, fields in CONFIGURATION_GROUPS for path, _, kind in fields}
CONFIGURATION_HELP = {
    "mo_desktop.enabled": "Starts the floating assistant with MO. Off keeps the Terminal available without the Desktop process.",
    "mo_desktop.tray_enabled": "Keeps quick access to Desktop controls in the system tray. Off removes that entry point.",
    "update.check": "Shows when an update is available. Checks use occasional network access; they do not install updates.",
    "agent.temperature": "Controls response variation where the provider supports it. Higher values can make results less predictable.",
    "agent.max_tokens": "Limits requested output per response. Larger limits can increase generation time and cost; providers may impose a lower limit.",
    "agent.max_provider_requests_per_turn": "Caps model requests in one turn. A lower limit bounds work and cost but may interrupt a long task; 0 removes this cap.",
    "agent.context_budget_tokens": "Controls how much context a turn can use. Automatic follows model capacity; larger budgets can increase latency and cost.",
    "agent.context_reserve_tokens": "Leaves room for output and tool results. Too little reserve can cause context pressure; more leaves less space for history.",
    "agent.context_handoff_enabled": "Carries work forward when context fills. Off can leave long tasks unable to continue in a fresh context.",
    "mo_desktop.voice.stt_engine": "Chooses local speech recognition. Whisper is offline; Windows uses the installed OS recognizer. Changing this restarts neither engine immediately.",
    "mo_desktop.voice.stt_model": "Larger speech models may improve recognition but need more memory and processing time, and may download on first use.",
    "mo_desktop.voice.stt_device": "CPU works without a supported GPU. CUDA can be faster but needs compatible hardware and available GPU memory.",
    "mo_desktop.voice.stt_beam_size": "Tries more candidate transcriptions. Higher values can improve recognition but make it slower.",
    "mo_desktop.voice.stt_idle_seconds": "Keeps recognition warm between requests. Longer waits reduce cold starts but retain memory; the worker also allows time for a complete recording.",
    "mcp.enabled": "Admits configured external tool servers on demand. Off removes those tools; On alone does not configure or authorize a server.",
    "local_extensions.enabled": "Loads extensions supplied by this profile. They run local code and can add private capabilities; enable only for a trusted profile.",
    "image.backend": "Chooses the image-generation route. A cloud route may send the prompt or reference image and incur provider charges; Off disables generation.",
    "telegram.enabled": "Allows the Telegram service to start. It needs configured credentials and pairing; enabling it alone does not grant chat access.",
    "telegram.dm_policy": "Controls who can reach MO through Telegram. Pairing requires approval, allowlist uses known users, and disabled blocks DM access.",
    "lsp.enabled": "Allows configured language servers to analyze code. Per-project choices can override this; servers use resources only when needed.",
    "lsp.timeout": "Sets how long MO waits for a language server. A shorter wait returns sooner but can mark a slow diagnostic check unavailable.",
    "access.mode": "Project scope bounds file access. Full scope permits paths outside the project; other role, tool and confirmation gates still apply.",
    "sandbox.enabled": "Keeps execution safeguards active. Turning this off reduces protection around tool execution; it does not grant every action.",
    "sandbox.clean_env": "Limits inherited environment data in child processes. Off can expose more local environment values to executed commands.",
    "sandbox.block_shell_escape": "Blocks shell escape patterns at the execution gate. Off permits command forms that need more careful review.",
    "sandbox.block_write_secrets": "Checks edits for literal secrets. Off increases the risk of writing credentials into files or source history.",
    "agent.block_malicious_code": "Checks generated code for harmful patterns. Off removes this safeguard rather than making such code safe.",
    "sandbox.shell_network_enabled": "Allows admitted shell commands to use the network. Off can prevent installs or remote commands that need connectivity.",
    "sandbox.web_fetch_enabled": "Allows MO's web tools. Off prevents fetching online sources; it is separate from network access in shell commands.",
    "sandbox.screen_capture_enabled": "Allows requested screen observations. Captures can include visible private information; Off prevents screenshot-based help.",
    "mo_desktop.computer_use.pixel_policy": "Local only keeps pixels on local routes. Configured providers permits those routes; confirm cloud asks before sending pixels to a cloud provider.",
    "learning.auto_promote": "Lets eligible repeated guidance become durable learning. Off keeps adoption manual; it does not remove previously accepted learning.",
    "learning.materialize_packs": "Turns automatically accepted learning into reusable skill packs. Off keeps that learning from creating new packs automatically.",
    "learning.capture_nudge": "Prompts MO to retain durable guidance when appropriate. Off reduces those reminders without deleting existing memory.",
    "skills.enabled": "Adds relevant task skills to turn context. Off reduces that guidance and context use; explicitly selected roles have their own admission path.",
    "skills.project_local": "Allows skills supplied by the current project. Useful for project conventions; enable only where you trust those instructions.",
    "skills.semantic_match": "Finds skills by meaning as well as words. It needs a usable embedding route and can add processing or provider cost.",
    "skills.decay_days": "Controls retirement of stale generated skills. A shorter period retires unused guidance sooner; authored skills are preserved.",
    "embeddings.enabled": "Adds meaning-based memory recall. If off or unavailable, keyword recall remains; an API backend sends text to its configured provider.",
    "embeddings.backend": "API uses a configured provider and may incur network cost. Local keeps embedding computation on this machine but uses local memory and CPU.",
    "embeddings.local_shared_worker": "Lets local embedding requests share a worker to avoid repeated model loads. Its process retains memory until idle shutdown.",
    "scheduler.enabled": "Starts reminders and scheduled work. Off can stop scheduled jobs from running in this process; SystemCare automation may also require the shared scheduler.",
    "scheduler.tick_seconds": "Controls how often due jobs are checked. Shorter intervals react sooner but wake the service more often.",
    "agent.background_workers_max": "Limits simultaneous background workers. More can speed independent tasks but increase memory use, contention and provider cost.",
    "heartbeat.enabled": "Publishes local instance presence used for status and exact handoffs. Off can make a running Terminal unavailable to those selectors.",
    "heartbeat.interval_seconds": "Controls presence refresh frequency. Shorter intervals improve freshness but create more local writes.",
    "consistent_everywhere.enabled": "Enables configured cross-device coordination. Pairing, credentials and exact grants are still required.",
    "consistent_everywhere.device_role": "Identifies this machine as a workstation, server or mobile client. Server ownership must be explicit; choosing it alone does not deploy a hub.",
    "consistent_everywhere.continuity.auto_sync": "Syncs bounded conversation continuity through the configured hub. Requires pairing and sends selected conversation state to that hub.",
    "consistent_everywhere.sync.auto_sync": "Syncs the curated profile through its configured private Git route. Requires setup; credentials and raw session stores remain outside that lane.",
    "consistent_everywhere.live_control.enabled": "Admits Live Control where configured. Sessions still need exact device scopes and consent; it does not create an unrestricted remote-control grant.",
    "consistent_everywhere.live_control.host.enabled": "Allows this machine to publish as a control host when the connection is ready. Remote control still requires the existing session and consent checks.",
    "file_transfer.enabled": "Enables cross-surface file cargo. Transfers still require their existing device permissions and path bounds.",
    "file_transfer.auto_accept": "Accepts permitted catalog cargo without an extra receive prompt. Off keeps receiving manual; this does not allow arbitrary destination paths.",
    "file_transfer.auto_accept_named_paths": "Automatically accepts permitted named destinations. This reduces receive review for those paths; existing overwrite and path rules still apply.",
    "file_manager.enabled": "Enables configured cross-machine file browsing. Locations and device grants still bound access; Off removes this file-management route.",
}
CONFIGURATION_LIMITS = {
    "agent.temperature": (0, 2, .05), "agent.max_tokens": (1, 2000000, 1),
    "agent.max_provider_requests_per_turn": (0, 100000, 1),
    "agent.context_budget_tokens": (1, 2000000, 1), "agent.context_reserve_tokens": (1, 2000000, 1),
    "agent.background_workers_max": (1, 32, 1), "skills.decay_days": (1, 36500, 1),
    "mo_desktop.voice.stt_beam_size": (1, 10, 1),
    "mo_desktop.voice.stt_idle_seconds": (15, 900, 1),
    "lsp.timeout": (1, 600, 1), "scheduler.tick_seconds": (5, 3600, 1),
    "heartbeat.interval_seconds": (5, 3600, 1),
}


def validate_configuration(key: str, value: Any) -> Any:
    """Only declared non-secret preferences cross the native bridge."""
    kind = CONFIGURATION_FIELDS.get(key)
    if kind is None:
        raise ValueError("Unknown configuration preference")
    if value is None:
        return None  # remove the authored override; owner default applies
    if kind == "bool" and isinstance(value, bool):
        return value
    if isinstance(kind, tuple) and isinstance(value, str) and value in kind:
        return value
    if kind == "number_auto" and value == "auto":
        return value
    if kind in ("number", "number_auto") and type(value) in (float, int) and math.isfinite(value):
        low, high, step = CONFIGURATION_LIMITS[key]
        if low <= value <= high and (step != 1 or int(value) == value):
            return int(value) if step == 1 else float(value)
        raise ValueError(f"Choose {'a whole number' if step == 1 else 'a number'} from {low} to {high}")
    raise ValueError("Choose an available value")


def validate_value(key: str, value: Any) -> Any:
    row = BY_ID.get(key)
    if row is None:
        raise ValueError("Unknown setting")
    kind, limits = row[5:]
    if kind == "switch":
        if not isinstance(value, bool):
            raise ValueError("Choose on or off")
    elif kind == "range":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Enter a finite number")
        low, high, step, _ = limits
        if not low <= value <= high:
            raise ValueError(f"Choose a value from {low} to {high}")
        value = round(round(value / step) * step, 4)
        value = int(value) if isinstance(step, int) else value
    elif kind == "select":
        if isinstance(value, bool) or value not in [item[0] for item in limits]:
            raise ValueError("Choose an available option")
    elif key == "behavior.keep_above_apps":
        from mo_desktop.settings import normalize_keep_above_apps
        if not isinstance(value, (str, list)):
            raise ValueError("Enter executable names")
        value = normalize_keep_above_apps(value)
    elif kind == "color":
        if not isinstance(value, str) or (value != "skin" and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None):
            raise ValueError("Choose a color or follow the skin")
    else:
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError("Enter at most 500 characters")
        value = value.strip()
    return value


def controls(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(id=key, page=page, group=group, label=label, detail=detail,
                 kind=kind, constraints=limits, value=values.get(key))
            for key, page, group, label, detail, kind, limits in FIELDS]
