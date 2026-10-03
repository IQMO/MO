"""Immutable SystemCare rule catalog.

Rules name exact observation and action owners. They never accept a user/model
supplied shell command, registry root, service name or filesystem root.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Domain, RiskLevel, ScanMode, UndoQuality


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    domain: Domain
    title: str
    description: str
    modes: frozenset[ScanMode]
    weight: int
    scanner: str
    action: str = "review"
    selectable: bool = False
    default_selected: bool = False
    risk: RiskLevel = RiskLevel.NONE
    undo: UndoQuality = UndoQuality.NOT_APPLICABLE
    requires_elevation: bool = False
    requires_restart: bool = False
    root_key: str = ""
    relative_root: str = ""
    patterns: tuple[str, ...] = ()
    minimum_age_days: int = 0
    recursive: bool = False
    max_candidates: int = 5_000
    owner_processes: tuple[str, ...] = ()


SAFE_AND_ADVANCED = frozenset({ScanMode.SAFE, ScanMode.ADVANCED})
ADVANCED_ONLY = frozenset({ScanMode.ADVANCED})


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        "storage.user_temp_old",
        Domain.STORAGE,
        "Old temporary files",
        "Files in the current user's Windows temporary root that are old enough and not reparse points.",
        SAFE_AND_ADVANCED,
        14,
        "file_inventory",
        action="delete_files",
        selectable=True,
        default_selected=True,
        risk=RiskLevel.LOW,
        undo=UndoQuality.NONE,
        root_key="temp",
        patterns=("*",),
        minimum_age_days=7,
        recursive=True,
    ),
    RuleSpec(
        "storage.thumbnail_cache",
        Domain.STORAGE,
        "Rebuildable thumbnail cache",
        "Closed, old thumbnail and icon cache files owned by Windows Explorer.",
        SAFE_AND_ADVANCED,
        9,
        "file_inventory",
        action="delete_files",
        selectable=True,
        default_selected=True,
        risk=RiskLevel.LOW,
        undo=UndoQuality.NONE,
        root_key="local_appdata",
        relative_root="Microsoft/Windows/Explorer",
        patterns=("thumbcache_*.db", "iconcache_*.db"),
        minimum_age_days=1,
    ),
    RuleSpec(
        "storage.wer_old",
        Domain.STORAGE,
        "Old completed error reports",
        "Old user-owned Windows Error Reporting files; active queues and system logs are excluded.",
        SAFE_AND_ADVANCED,
        8,
        "file_inventory",
        action="delete_files",
        selectable=True,
        default_selected=False,
        risk=RiskLevel.LOW,
        undo=UndoQuality.NONE,
        root_key="local_appdata",
        relative_root="Microsoft/Windows/WER/ReportArchive",
        patterns=("*",),
        minimum_age_days=30,
        recursive=True,
    ),
    RuleSpec(
        "storage.directx_shader_cache",
        Domain.STORAGE,
        "Old DirectX shader cache",
        "Old user-owned DirectX shader cache files that Windows and graphics drivers can rebuild.",
        SAFE_AND_ADVANCED,
        7,
        "file_inventory",
        action="delete_files",
        selectable=True,
        default_selected=True,
        risk=RiskLevel.LOW,
        undo=UndoQuality.NONE,
        root_key="local_appdata",
        relative_root="D3DSCache",
        patterns=("*",),
        minimum_age_days=7,
        recursive=True,
    ),
    RuleSpec(
        "storage.crash_dumps",
        Domain.STORAGE,
        "Old application crash dumps",
        "Old user-owned application crash dumps; recent diagnostic evidence remains excluded.",
        SAFE_AND_ADVANCED,
        7,
        "file_inventory",
        action="delete_files",
        selectable=True,
        default_selected=False,
        risk=RiskLevel.LOW,
        undo=UndoQuality.NONE,
        root_key="local_appdata",
        relative_root="CrashDumps",
        patterns=("*.dmp",),
        minimum_age_days=14,
        recursive=True,
    ),
    RuleSpec(
        "storage.disk_pressure",
        Domain.STORAGE,
        "Drive capacity",
        "Current total and free space for calibrated fixed volumes.",
        SAFE_AND_ADVANCED,
        10,
        "disk_pressure",
    ),
    RuleSpec(
        "startup.registered_apps",
        Domain.STARTUP,
        "Startup applications",
        "Current-user and all-user Run keys plus Startup folders, without changing them.",
        SAFE_AND_ADVANCED,
        12,
        "startup_inventory",
    ),
    RuleSpec(
        "health.restart",
        Domain.HEALTH,
        "Restart and servicing state",
        "Known Windows servicing and pending-restart signals.",
        SAFE_AND_ADVANCED,
        8,
        "restart_state",
    ),
    RuleSpec(
        "health.storage_sense",
        Domain.HEALTH,
        "Storage Sense policy",
        "Current Windows Storage Sense enablement without changing cleanup schedules or personal-folder choices.",
        SAFE_AND_ADVANCED,
        5,
        "storage_sense",
    ),
    RuleSpec(
        "health.services",
        Domain.HEALTH,
        "Update and protection services",
        "Presence and current state of exact Windows health services.",
        SAFE_AND_ADVANCED,
        8,
        "health_services",
    ),
    RuleSpec(
        "performance.power",
        Domain.PERFORMANCE,
        "Power and gaming baseline",
        "Current power scheme, power source and Game Mode capability.",
        SAFE_AND_ADVANCED,
        8,
        "performance_baseline",
    ),
    RuleSpec(
        "health.component_store",
        Domain.HEALTH,
        "Component-store analysis",
        "Availability and protected-read requirements for Windows component analysis.",
        ADVANCED_ONLY,
        8,
        "component_store",
    ),
    RuleSpec(
        "storage.windows_temp_advisory",
        Domain.STORAGE,
        "Windows temporary storage",
        "Old files visible under the Windows temporary owner; protected or active files remain unavailable and no cleanup action is enabled.",
        ADVANCED_ONLY,
        8,
        "file_inventory",
        root_key="system_root",
        relative_root="Temp",
        patterns=("*",),
        minimum_age_days=14,
        recursive=True,
        max_candidates=2_000,
    ),
    RuleSpec(
        "storage.delivery_optimization_advisory",
        Domain.STORAGE,
        "Delivery Optimization cache",
        "Bounded visible cache evidence from Windows Delivery Optimization; cleanup remains with the Windows owner.",
        ADVANCED_ONLY,
        6,
        "file_inventory",
        root_key="system_root",
        relative_root="ServiceProfiles/NetworkService/AppData/Local/Microsoft/Windows/DeliveryOptimization/Cache",
        patterns=("*",),
        minimum_age_days=7,
        recursive=True,
        max_candidates=2_000,
    ),
    RuleSpec(
        "health.integrity",
        Domain.HEALTH,
        "Integrity and recovery readiness",
        "Availability of Windows integrity and recovery owners without running repair.",
        ADVANCED_ONLY,
        6,
        "integrity_readiness",
    ),
    RuleSpec(
        "startup.services_advisory",
        Domain.STARTUP,
        "Automatic services advisory",
        "Counts automatic service registrations for review; it never disables a service.",
        ADVANCED_ONLY,
        7,
        "service_inventory",
    ),
    RuleSpec(
        "registry.startup_targets",
        Domain.REGISTRY,
        "Registry startup target health",
        "Exact startup values whose executable target can be evaluated safely; generic registry recursion is absent.",
        ADVANCED_ONLY,
        6,
        "startup_target_health",
    ),
)

def _action_rule(action: str, title: str, domain: Domain, *, undo: UndoQuality = UndoQuality.NONE,
                 elevated: bool = False, restart: bool = False) -> RuleSpec:
    return RuleSpec("action." + action, domain, title, "Explicit reviewed native-owner action",
                    frozenset(), 1, "selected_subject", action=action, selectable=True,
                    risk=RiskLevel.MEDIUM, undo=undo, requires_elevation=elevated,
                    requires_restart=restart)


# Selected actions never join an opening refresh or a scan implicitly.
ACTION_RULES = (
    *(_action_rule("service_" + verb, "Service: " + verb.replace("_", " "), Domain.STARTUP,
                   undo=UndoQuality.FULL, elevated=True)
      for verb in ("start", "stop", "restart", "automatic", "manual", "disabled")),
    _action_rule("startup_disable", "Disable selected startup value", Domain.STARTUP, undo=UndoQuality.FULL),
    _action_rule("startup_shortcut_disable", "Disable selected Startup shortcut", Domain.STARTUP, undo=UndoQuality.PARTIAL),
    *(_action_rule("task_" + verb, "Startup task: " + verb, Domain.STARTUP,
                   undo=UndoQuality.FULL, elevated=True) for verb in ("disable", "enable")),
    _action_rule("registry_remove", "Remove explained registry value", Domain.REGISTRY, undo=UndoQuality.FULL),
    _action_rule("path_remove_duplicate", "Remove selected duplicate PATH entry", Domain.HEALTH, undo=UndoQuality.FULL),
    _action_rule("game_on", "Enable global Game Mode", Domain.PERFORMANCE, undo=UndoQuality.FULL),
    _action_rule("game_off", "Restore Game Mode originals", Domain.PERFORMANCE, undo=UndoQuality.NOT_APPLICABLE),
    _action_rule("desktop_refresh", "Refresh desktop", Domain.HEALTH, undo=UndoQuality.NOT_APPLICABLE),
    _action_rule("integrity_repair", "Repair Windows system files", Domain.HEALTH, elevated=True),
    _action_rule("component_repair", "Repair Windows component store", Domain.HEALTH, elevated=True),
    _action_rule("component_cleanup", "Clean superseded Windows components", Domain.STORAGE, elevated=True),
    _action_rule("disk_optimize", "Optimize selected volume by media type", Domain.STORAGE, elevated=True),
    _action_rule("network_dns", "Clear resolver cache", Domain.HEALTH, undo=UndoQuality.NOT_APPLICABLE),
    _action_rule("app_uninstall", "Uninstall selected app through Windows", Domain.STORAGE),
    _action_rule("browser_history_clear", "Clear selected local browser records", Domain.STORAGE),
    _action_rule("recycle_remove", "Permanently remove selected Recycle Bin item", Domain.STORAGE),
    _action_rule("app_leftover_remove", "Remove selected reviewed app leftover", Domain.STORAGE),
    _action_rule("app_update", "Update selected native package", Domain.HEALTH),
    _action_rule("driver_remove", "Remove selected unused driver package", Domain.STORAGE, elevated=True),
    _action_rule("windows_update", "Install selected Windows-offered update", Domain.HEALTH, elevated=True, restart=True),
)
_BROWSERS = (
    ("chrome", "Chrome", "Google/Chrome/User Data", ("chrome.exe",)),
    ("edge", "Edge", "Microsoft/Edge/User Data", ("msedge.exe",)),
    ("brave", "Brave", "BraveSoftware/Brave-Browser/User Data", ("brave.exe",)),
    ("firefox", "Firefox", "Mozilla/Firefox/Profiles", ("firefox.exe",)),
)
RULES += tuple(RuleSpec("storage.browser_" + key, Domain.STORAGE, title + " rebuildable caches",
                       "Closed-browser cache files; credentials, cookies, history and sessions are excluded.",
                       SAFE_AND_ADVANCED, 5, "browser_cache", action="delete_files", selectable=True,
                       risk=RiskLevel.LOW, undo=UndoQuality.NONE, root_key="local_appdata", relative_root=root,
                       patterns=("*",), minimum_age_days=7, recursive=True, max_candidates=2000,
                       owner_processes=processes) for key, title, root, processes in _BROWSERS)
RULES += tuple(RuleSpec("storage.app_" + key, Domain.STORAGE, title + " generated cache",
                       "Closed application cache owner; databases and configuration remain protected.",
                       SAFE_AND_ADVANCED, 4, "file_inventory", action="delete_files", selectable=True,
                       risk=RiskLevel.LOW, undo=UndoQuality.NONE, root_key="roaming_appdata", relative_root=root,
                       patterns=("*",), minimum_age_days=7, recursive=True, max_candidates=2000,
                       owner_processes=processes)
               for key, title, root, processes in (
                   ("discord", "Discord", "discord/Cache", ("discord.exe",)),
                   ("slack", "Slack", "Slack/Cache", ("slack.exe",)),
               ))
RULES += tuple(RuleSpec("storage." + key, Domain.STORAGE, title, description, SAFE_AND_ADVANCED, 4,
                       "file_inventory", action="delete_files", selectable=True, risk=RiskLevel.LOW,
                       undo=UndoQuality.NONE, root_key="local_appdata", relative_root=root, patterns=("*",),
                       minimum_age_days=14, recursive=True, max_candidates=2000)
               for key, title, description, root in (
                   ("nvidia_shader", "NVIDIA shader cache", "Old generated shader cache, when present.", "NVIDIA/DXCache"),
                   ("amd_shader", "AMD shader cache", "Old generated shader cache, when present.", "AMD/DxCache"),
               ))
# Scan-all projects the existing inspection owners into the same scan ledger.
# Expensive checks remain Advanced; selected roots/volumes stay explicit.
INSPECTION_RULES = tuple(
    RuleSpec("inspect." + section, domain, title, "Existing scoped Windows inspection", modes, 5,
             "inspection:" + section)
    for section, domain, title, modes in (
        ("storage", Domain.STORAGE, "Storage volumes", SAFE_AND_ADVANCED),
        ("browser_data", Domain.STORAGE, "Browser records", SAFE_AND_ADVANCED),
        ("recycle", Domain.STORAGE, "Recycle Bin", SAFE_AND_ADVANCED),
        ("app_leftovers", Domain.STORAGE, "Reviewed app leftovers", SAFE_AND_ADVANCED),
        ("services", Domain.STARTUP, "Services and startup types", SAFE_AND_ADVANCED),
        ("startup", Domain.STARTUP, "Startup apps and tasks", SAFE_AND_ADVANCED),
        ("apps", Domain.STORAGE, "Installed apps", SAFE_AND_ADVANCED),
        ("packaged_apps", Domain.STORAGE, "Windows apps", SAFE_AND_ADVANCED),
        ("drivers", Domain.HEALTH, "Installed drivers", SAFE_AND_ADVANCED),
        ("devices", Domain.HEALTH, "Storage devices", SAFE_AND_ADVANCED),
        ("environment", Domain.HEALTH, "Environment references", SAFE_AND_ADVANCED),
        ("network", Domain.HEALTH, "Network configuration", SAFE_AND_ADVANCED),
        ("protection", Domain.HEALTH, "Windows protection", SAFE_AND_ADVANCED),
        ("desktop", Domain.PERFORMANCE, "Desktop responsiveness", SAFE_AND_ADVANCED),
        ("software_updates", Domain.HEALTH, "Available app updates", ADVANCED_ONLY),
        ("updates", Domain.HEALTH, "Windows Update", ADVANCED_ONLY),
        ("driver_updates", Domain.HEALTH, "Available driver updates", ADVANCED_ONLY),
        ("driver_packages", Domain.HEALTH, "Driver packages", ADVANCED_ONLY),
        ("integrity", Domain.HEALTH, "System-file verification", ADVANCED_ONLY),
        ("component_store", Domain.HEALTH, "Component-store analysis", ADVANCED_ONLY),
        ("registry", Domain.REGISTRY, "Registry references", ADVANCED_ONLY),
    )
)
RULE_BY_ID = {rule.rule_id: rule for rule in (*RULES, *INSPECTION_RULES, *ACTION_RULES)}


def rules_for_mode(mode: ScanMode, *, advanced_domains: set[str] | None = None,
                   all_checks: bool = False) -> tuple[RuleSpec, ...]:
    selected = []
    enabled_domains = (
        {str(item).strip().lower() for item in advanced_domains if str(item).strip()}
        if advanced_domains is not None
        else None
    )
    for rule in (*RULES, *(INSPECTION_RULES if all_checks else ())):
        if mode not in rule.modes:
            continue
        if mode == ScanMode.ADVANCED and rule.modes == ADVANCED_ONLY and enabled_domains is not None and not rule.scanner.startswith("inspection:"):
            if rule.domain.value not in enabled_domains and rule.rule_id not in enabled_domains:
                continue
        selected.append(rule)
    return tuple(selected)


def rule_for_id(rule_id: str) -> RuleSpec:
    try:
        return RULE_BY_ID[str(rule_id)]
    except KeyError as exc:
        raise ValueError("unknown SystemCare rule") from exc
