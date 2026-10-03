"""Native dry-run-first setup and status surface for MO Everywhere."""
from __future__ import annotations

import sqlite3
import shutil
import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_json
from ..runtime.lock import runtime_lock_owner
from .continuity_events import LOCAL_CONTINUITY_PATH, continuity_status_from_db
from .everywhere_coordinator import CoordinatorSettings, read_coordinator_status
from .everywhere_readiness import (
    ANDROID_PUBLIC_DISTRIBUTION,
    DISABLE_PATH,
    build_everywhere_readiness,
    dependency_readiness,
    everywhere_authority,
)
from .paths import mo_home, resolve_state_path
from .profile_reconcile import compare_profiles, configured_peer, render_reconcile_plan, snapshot_profile
from .sync import GitStateSync


def hub_dependency_status() -> tuple[bool, str]:
    """Return serving readiness without importing optional web packages."""
    state = dependency_readiness()
    return state.pairing_ready, state.render()


def locally_disabled(config: dict[str, Any] | None = None) -> bool:
    return Path(resolve_state_path(DISABLE_PATH, config or {})).is_file()


def set_locally_disabled(config: dict[str, Any] | None = None, *, disabled: bool) -> bool:
    path = Path(resolve_state_path(DISABLE_PATH, config or {}))
    if disabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"disabled": True, "created_at": time.time()}, indent=2, ensure_ascii=False)
        return True
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return False


def _everywhere_sections(
    config: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Return the normalized Everywhere root and its mapping-valued sections."""
    cfg = config or {}
    block = cfg.get("consistent_everywhere") if isinstance(cfg.get("consistent_everywhere"), dict) else {}
    sections = {
        name: block.get(name) if isinstance(block.get(name), dict) else {}
        for name in ("continuity", "sync", "api")
    }
    return cfg, block, sections


def render_everywhere_status(config: dict[str, Any] | None = None) -> str:
    cfg, block, sections = _everywhere_sections(config)
    continuity = sections["continuity"]
    api = sections["api"]
    readiness = build_everywhere_readiness(cfg)
    authority = readiness.authority
    sync = GitStateSync(cfg).status()
    journal = _local_journal_status(cfg)
    coordinator = read_coordinator_status(cfg)
    latest = coordinator.get("created_at") if isinstance(coordinator, dict) else None
    coordinator_owner = runtime_lock_owner("mo-everywhere-coordinator.lock")
    coordinator_age = max(0.0, time.time() - float(latest)) if latest else None
    settings = CoordinatorSettings.from_config(cfg)
    coordinator_required = bool(
        settings.enabled
        and not locally_disabled(cfg)
        and (settings.continuity_auto_sync or settings.profile_auto_sync)
    )
    stale_after = 120.0 if settings.continuity_auto_sync else max(600.0, settings.profile_interval_seconds * 2.0)
    coordinator_stale = bool(coordinator_owner and coordinator_age is not None and coordinator_age > stale_after)
    coordinator_offline = coordinator_required and not coordinator_owner
    lines = [
        "MO Everywhere status:",
        f"  enabled/config gate: {authority.enabled}/{not readiness.locally_disabled}",
        f"  role/authority:      {authority.role}{'' if authority.role_explicit else ' (inferred; configure explicitly)'} · hub-owner={authority.hub_owner}",
        f"  continuity:          enabled={continuity.get('enabled', True) is True} auto={continuity.get('auto_sync') is True} hub-local={authority.hub_owner}",
        f"  local journal:       {journal['outbound']} outbound, {journal['inbound']} inbound, {journal['pending']} pending, {journal['bindings']} bindings",
        f"  profile sync:        configured={sync['configured']} initialized={sync['initialized']} baseline={sync['baseline_established']} auto={bool((block.get('sync') or {}).get('auto_sync')) if isinstance(block.get('sync'), dict) else False}",
        f"  Native API:          enabled={api.get('enabled') is True} allowed={authority.api_allowed} loopback={readiness.api_loopback} health={readiness.endpoint_state}",
        f"  credentials:         provider={readiness.credentials.provider} device={readiness.credentials.primary} live-host={readiness.credentials.live_host}",
        f"  hub registry:        {readiness.registry_state}",
        f"  Android distribution: {ANDROID_PUBLIC_DISTRIBUTION} (private Direct builds are owner-device tooling)",
        f"  coordinator status:  {'never run' if not latest else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(latest)))}"
        + (f" · STALE {int(coordinator_age or 0)}s (resident pid {coordinator_owner})" if coordinator_stale else "")
        + (" · OFFLINE (start MO Desktop or the headless service)" if coordinator_offline else ""),
    ]
    if coordinator:
        continuity_state = coordinator.get("continuity") if isinstance(coordinator.get("continuity"), dict) else {}
        profile_state = coordinator.get("profile") if isinstance(coordinator.get("profile"), dict) else {}
        lines.append(f"  last lanes:          continuity={continuity_state.get('state', 'unknown')} profile={profile_state.get('state', 'unknown')}")
    if readiness.issues:
        lines.append("  blockers:            " + "; ".join(issue.message for issue in readiness.issues))
    lines.append(f"  next action:         {readiness.next_action}")
    return "\n".join(lines)


def _local_journal_status(config: dict[str, Any]) -> dict[str, int]:
    """Read journal counts without initializing or mutating the database."""
    path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, config))
    empty = {"outbound": 0, "inbound": 0, "pending": 0, "bindings": 0}
    if not path.is_file():
        return empty
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
            return continuity_status_from_db(db)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return empty


def render_setup_plan(config: dict[str, Any] | None = None) -> str:
    cfg, block, sections = _everywhere_sections(config)
    continuity = sections["continuity"]
    sync = sections["sync"]
    api = sections["api"]
    readiness = build_everywhere_readiness(cfg)
    role = readiness.authority.role
    home = mo_home(cfg)
    snapshot = snapshot_profile(home)
    credential_state = readiness.credentials.primary
    git_ok = shutil.which("git") is not None
    ssh_ok = shutil.which("ssh") is not None
    hub_deps = readiness.dependencies.pairing_ready
    hub_deps_detail = readiness.dependencies.render()
    api_enabled = api.get("enabled") is True
    api_loopback = readiness.api_loopback
    endpoint_state = readiness.endpoint_state
    disabled = readiness.locally_disabled
    coordinator_owner = runtime_lock_owner("mo-everywhere-coordinator.lock")
    service_owner = runtime_lock_owner("mo-service.lock")
    desktop_owner = runtime_lock_owner("mo-desktop.lock")
    desktop_cfg = cfg.get("mo_desktop") if isinstance(cfg.get("mo_desktop"), dict) else {}
    registry_state = readiness.registry_state
    profile_baseline = GitStateSync(cfg).baseline_established()
    reconcile_state = _reconcile_status(cfg, baseline_established=profile_baseline)
    blockers: list[str] = [issue.message for issue in readiness.issues]
    blockers.extend(_profile_setup_blockers(cfg))
    local_server_bootstrap = disabled and role == "server" and api_enabled
    lines = [
        "MO Everywhere setup (read-only dry run):",
        f"  effective role:      {role}{'' if readiness.authority.role_explicit else ' (configure explicitly)'}",
        f"  hub authority:       {readiness.authority.hub_owner}",
        f"  config gate:         {block.get('enabled') is True}",
        f"  local disable gate:  {disabled}",
        f"  hub dependencies:    {hub_deps} ({hub_deps_detail}; required only on the serving host)",
        f"  Git/OpenSSH:         {git_ok}/{ssh_ok}",
        f"  profile remote:      {bool(str(sync.get('git_remote') or '').strip())}",
        f"  device credential:   {credential_state}",
        f"  curated candidates:  {len(snapshot.files)} valid / {len(snapshot.invalid)} invalid",
        f"  profile reconcile:   {reconcile_state}",
        f"  API configured:      {api_enabled} (loopback={api_loopback})",
        f"  provider credential: {readiness.credentials.provider}",
        f"  live-host credential:{readiness.credentials.live_host}",
        f"  TLS hub health:      {endpoint_state}{' (recheck after local gate release/restart)' if local_server_bootstrap and endpoint_state != 'reachable' else ''}",
        f"  hub registry:        {registry_state}",
        f"  headless service:    {'running' if service_owner else 'not running'}",
        f"  Desktop companion:   configured={desktop_cfg.get('enabled') is True} running={bool(desktop_owner)}",
        f"  coordinator owner:   {'running' if coordinator_owner else 'not running'}",
        f"  Android distribution: {ANDROID_PUBLIC_DISTRIBUTION} (private Direct builds are owner-device tooling)",
        "  changes applied:     none",
    ]
    if blockers:
        lines.append("Blocked before activation:")
        lines.extend(f"- {item}" for item in blockers)
    elif block.get("enabled") is True and not disabled:
        resident_required = bool(
            (continuity.get("enabled", True) is True and continuity.get("auto_sync") is True)
            or (sync.get("enabled", True) is True and sync.get("auto_sync") is True)
        )
        if resident_required and not coordinator_owner:
            lines.append(
                "Configuration is active, but automatic delivery is paused until MO Desktop or the headless service is running."
            )
            if role == "workstation" and desktop_cfg.get("enabled") is True:
                lines.append("For reboot continuity, enable MO Desktop's Run at Startup tray option.")
        else:
            lines.append("Already active; no activation changes required.")
    else:
        lines.append("Ready for explicit activation.")
        if sync.get("auto_sync") is True:
            lines.append("Run `/everywhere reconcile` first; after it is clean, `/everywhere setup --confirm` releases the local disable gate.")
        else:
            lines.append("After reviewing this plan, `/everywhere setup --confirm` releases the local disable gate.")
    lines.extend([
        "Guided path: hub role/provider/dependencies -> HTTPS health -> verified app install -> one-use pairing -> resident permissions -> optional Live Control.",
        f"Next action: {readiness.next_action}",
    ])
    return "\n".join(lines)


def _reconcile_status(
    config: dict[str, Any],
    *,
    baseline_established: bool | None = None,
) -> str:
    # The trusted peer snapshot is a first-attach gate only. Once the private
    # profile repository has a committed baseline, routine Git/OpenSSH sync
    # owns current
    # divergence. Re-comparing the retained bootstrap snapshot here makes a
    # healthy activated device look blocked as soon as either profile evolves.
    if baseline_established is None:
        baseline_established = GitStateSync(config).baseline_established()
    if baseline_established:
        return "baseline established"
    peer = configured_peer(config)
    if peer is None:
        return "peer snapshot not configured"
    if peer == mo_home(config):
        return "blocked: peer snapshot must be distinct from this device profile"
    if not peer.is_dir():
        return "blocked: configured peer snapshot is unavailable"
    plan = compare_profiles(mo_home(config), peer)
    invalid = len(plan.local.invalid) + len(plan.peer.invalid)
    if invalid:
        return f"blocked: {invalid} invalid curated profile candidate(s)"
    if plan.different:
        return f"blocked: {len(plan.different)} same-file conflict(s) require review"
    if plan.local_only or plan.peer_only:
        return f"pending safe union ({len(plan.local_only)} local / {len(plan.peer_only)} peer only)"
    return "clean"


def setup_confirm(config: dict[str, Any] | None = None) -> str:
    cfg, block, _sections = _everywhere_sections(config)
    if block.get("enabled") is not True:
        return "MO Everywhere setup blocked: set consistent_everywhere.enabled: true in the private config after reviewing `/everywhere setup`. No change made."
    readiness = build_everywhere_readiness(cfg)
    if readiness.activation_blockers or _profile_setup_blockers(cfg):
        return render_setup_plan(cfg)
    if not locally_disabled(cfg):
        return "MO Everywhere is already locally enabled. No change made."
    set_locally_disabled(cfg, disabled=False)
    return "MO Everywhere local gate enabled. Restart the resident MO service/Desktop coordinator, then verify `/everywhere status`."


def render_devices(config: dict[str, Any] | None = None) -> str:
    cfg = config or {}
    authority = everywhere_authority(cfg)
    path = Path(resolve_state_path("memory/surfaces/everywhere.sqlite", cfg))
    if not authority.registry_admin_allowed:
        try:
            from mo_everywhere.client import load_credentials

            credentials = load_credentials(cfg)
        except Exception:
            suffix = " A local registry file is ignored because this device is not the hub owner." if path.is_file() else ""
            return "MO Everywhere devices: this device is not paired with the serving hub." + suffix
        return (
            "MO Everywhere devices:\n"
            f"- this device · {credentials.device_id[:12]} · paired to the remote hub\n"
            "Registry administration remains on the serving host; no second hub database is created here."
        )
    if not path.is_file():
        return "MO Everywhere devices: the serving-hub registry is not initialized."
    devices = _read_devices(path)
    if not devices:
        return "MO Everywhere devices: none paired."
    lines = ["MO Everywhere devices:"]
    for item in devices:
        state = "revoked" if item.get("revoked_at") else "active"
        scopes = ",".join(item.get("scopes") or []) or "none"
        lines.append(f"- {str(item.get('device_id') or '')[:12]} · {item.get('label') or 'device'} · {item.get('capability')} · scopes={scopes} · {state}")
    return "\n".join(lines)


def dispatch_everywhere_command(config: dict[str, Any] | None, rest: str) -> str:
    text = str(rest or "").strip()
    action, _, tail = text.partition(" ")
    action = action.lower() or "status"
    confirm = "--confirm" in tail.lower().split()
    if action == "status":
        return render_everywhere_status(config)
    if action == "setup":
        return setup_confirm(config) if confirm else render_setup_plan(config)
    if action == "reconcile":
        return render_reconcile_plan(config, confirm=confirm)
    if action == "devices":
        return render_devices(config)
    if action == "trace":
        from ..diagnostics.surface_trace import build_everywhere_trace_report

        return build_everywhere_trace_report(config)
    if action == "pair":
        pair_args = tail.strip().lower().split()
        if pair_args not in (["android"], ["android", "--phone-control"]):
            return "Use: /everywhere pair android [--phone-control]"
        phone_control = pair_args == ["android", "--phone-control"]
        cfg = config or {}
        readiness = build_everywhere_readiness(cfg)
        role = readiness.authority.role
        if not readiness.authority.registry_admin_allowed:
            return (
                "MO Everywhere Android pairing must be created on the serving hub; "
                f"this device is {role}. Open MO on the hub and run `/everywhere pair android`. "
                "A workstation must not create a second local hub registry."
            )
        if not readiness.can_pair_android:
            return (
                "MO Everywhere Android pairing blocked: "
                + "; ".join(readiness.pairing_blockers)
                + f". Effective role: {role}. Next: {readiness.next_action}"
            )
        from mo_everywhere.cli import render_pairing_qr
        from mo_everywhere.pairing_qr import (
            ANDROID_PAIRING_SCOPES,
            ANDROID_PHONE_CONTROL_SCOPES,
            PairingQrError,
        )
        from mo_everywhere.registry import DeviceRegistry, RegistryError

        try:
            return render_pairing_qr(
                DeviceRegistry(cfg),
                cfg,
                capability="control",
                scopes=ANDROID_PHONE_CONTROL_SCOPES if phone_control else ANDROID_PAIRING_SCOPES,
            )
        except (PairingQrError, RegistryError) as exc:
            return f"MO Everywhere Android pairing blocked: {exc}"
    if action == "disable":
        set_locally_disabled(config, disabled=True)
        return "MO Everywhere locally disabled. API requests are blocked and resident continuity/profile cycles stop work immediately; paired devices remain revocable on the hub."
    return "Use: /everywhere [status|setup [--confirm]|reconcile [--confirm]|pair android [--phone-control]|devices|trace|disable]"


def _profile_setup_blockers(config: dict[str, Any]) -> list[str]:
    _cfg, _block, sections = _everywhere_sections(config)
    sync = sections["sync"]
    snapshot = snapshot_profile(mo_home(config))
    git_ok = shutil.which("git") is not None
    ssh_ok = shutil.which("ssh") is not None
    baseline = GitStateSync(config).baseline_established()
    reconcile_state = _reconcile_status(config, baseline_established=baseline)
    blockers: list[str] = []
    if sync.get("auto_sync") is True and (not git_ok or not ssh_ok or not str(sync.get("git_remote") or "").strip()):
        blockers.append("profile auto-sync needs Git, OpenSSH, and a private Git-over-SSH remote")
    if sync.get("auto_sync") is True and not baseline and configured_peer(config) is None:
        blockers.append("profile auto-sync needs a trusted reconcile_peer_path for first-attach comparison")
    if snapshot.invalid:
        blockers.append(f"{len(snapshot.invalid)} curated profile candidate(s) are invalid")
    if reconcile_state.startswith("blocked"):
        blockers.append(reconcile_state)
    return blockers


def _read_devices(path: Path) -> list[dict[str, Any]]:
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
            rows = db.execute(
                "SELECT device_id,label,capability,revoked_at FROM device ORDER BY created_at"
            ).fetchall()
            scope_rows = db.execute(
                "SELECT device_id,scope FROM device_scope ORDER BY device_id,scope"
            ).fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return []
    scopes: dict[str, list[str]] = {}
    for device_id, scope in scope_rows:
        scopes.setdefault(str(device_id), []).append(str(scope))
    return [
        {
            "device_id": str(device_id),
            "label": str(label),
            "capability": str(capability),
            "revoked_at": revoked_at,
            "scopes": scopes.get(str(device_id), []),
        }
        for device_id, label, capability, revoked_at in rows
    ]
