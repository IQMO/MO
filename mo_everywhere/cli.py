"""Local administration CLI for pairing, capability, consent, and kill switch."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from core.provider.provider import load_config
from core.state.everywhere_readiness import everywhere_authority

from .client import (
    EverywhereClientError,
    files_client_config,
    join_hub,
    transfer_client_config,
)
from .live_control import set_live_control_locally_disabled
from .live_host import live_host_client_config
from .pairing_qr import (
    PairingQrError,
    ensure_qr_support,
    issue_pairing_payload,
    pairing_origin,
    render_terminal_qr,
)
from .registry import DeviceRegistry, RegistryError


def render_pairing_qr(
    registry: DeviceRegistry,
    config: dict[str, Any],
    *,
    capability: str,
    scopes: list[str] | tuple[str, ...],
    ttl_seconds: int = 300,
) -> str:
    """Create and render one registry-owned grant for a trusted terminal."""
    origin = pairing_origin(config)
    ensure_qr_support()
    payload = issue_pairing_payload(
        registry,
        origin=origin,
        capability=capability,
        scopes=scopes,
        ttl_seconds=ttl_seconds,
    )
    expiry = datetime.fromtimestamp(payload["expires_at"], tz=timezone.utc).isoformat(timespec="seconds")
    return "\n".join((
        "One-use MO pairing QR — trusted terminal only; do not capture or share.",
        render_terminal_qr(payload),
        f"Hub: {origin}",
        f"Capability: {payload['capability']}; scopes: {', '.join(payload['scopes']) or 'none'}",
        f"Expires: {expiry}",
        "On Android: open MO Everywhere, choose Scan trusted QR, review the origin and authority, then confirm.",
        "Manual fallback: create a separate one-time code without --qr.",
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Administer MO Everywhere from the trusted local machine.")
    parser.add_argument("--config", default=None, help="Config path; defaults to MO_CONFIG or ~/.mo/config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair", help="Create a one-time device pairing code")
    pair.add_argument("--capability", choices=("notify", "view", "control"), default="view")
    pair.add_argument(
        "--scope",
        action="append",
        choices=(
            "attachment_upload",
            "continuity_read",
            "continuity_sync",
            "conversation_read",
            "conversation_write",
            "file_browse",
            "file_manage",
            "file_transfer",
            "remote_control",
            "remote_host",
        ),
        default=[],
        help="Exact trusted-device scope; repeat to grant both read and sync",
    )
    pair.add_argument("--ttl", type=int, default=300)
    pair.add_argument(
        "--qr",
        action="store_true",
        help="Render a trusted-terminal QR using the configured verified HTTPS public_url",
    )
    join = commands.add_parser("join", help="Redeem a hub pairing code on this device")
    join.add_argument("--hub", required=True, help="HTTPS hub origin, or loopback HTTP for local testing")
    join.add_argument("--code", required=True, help="One-time code created on the trusted hub")
    join.add_argument("--label", required=True, help="Private label for this device")
    credential_slot = join.add_mutually_exclusive_group()
    credential_slot.add_argument(
        "--as-live-host",
        action="store_true",
        help="Store the grant in the dedicated Live Control host credential slot",
    )
    credential_slot.add_argument(
        "--as-transfer-client",
        action="store_true",
        help="Store the grant in the dedicated file-transfer credential slot",
    )
    credential_slot.add_argument(
        "--as-files-client",
        action="store_true",
        help="Store the grant in the dedicated MO Files controller credential slot",
    )
    commands.add_parser("devices", help="List paired devices without tokens")
    grant = commands.add_parser("grant", help="Change a paired device capability")
    grant.add_argument("device_id")
    grant.add_argument("capability", choices=("notify", "view", "control"))
    scopes = commands.add_parser("grant-scopes", help="Replace exact trusted-device scopes")
    scopes.add_argument("device_id")
    scopes.add_argument(
        "scopes",
        nargs="*",
        choices=(
            "attachment_upload",
            "continuity_read",
            "continuity_sync",
            "conversation_read",
            "conversation_write",
            "file_browse",
            "file_manage",
            "file_transfer",
            "remote_control",
            "remote_host",
        ),
    )
    phone_control = commands.add_parser(
        "grant-phone-control",
        help="Add Android phone-control authority while preserving all existing scopes",
    )
    phone_control.add_argument("device_id")
    revoke = commands.add_parser("revoke", help="Revoke a device and every token")
    revoke.add_argument("device_id")
    commands.add_parser("remote-off", help="Engage the native Live Control kill switch")
    commands.add_parser("remote-on", help="Release the native Live Control kill switch")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "join":
        target_config = config
        kind = "device"
        if args.as_live_host:
            target_config = live_host_client_config(config)
            kind = "Live Control host"
        elif args.as_transfer_client:
            target_config = transfer_client_config(config)
            kind = "transfer client"
        elif args.as_files_client:
            target_config = files_client_config(config)
            kind = "MO Files controller"
        try:
            credentials = join_hub(args.hub, args.code, args.label, target_config)
        except EverywhereClientError as exc:
            print(f"Blocked: {exc}")
            return 2
        print(f"Paired this {kind} with MO Everywhere ({credentials.device_id[:12]}).")
        return 0
    if args.command in {"remote-off", "remote-on"}:
        disabled = args.command == "remote-off"
        set_live_control_locally_disabled(config, disabled=disabled)
        print(
            "Native Live Control locally disabled."
            if disabled
            else "Native Live Control local kill switch released; config and fresh approval still apply."
        )
        return 0
    authority = everywhere_authority(config)
    if not authority.registry_admin_allowed:
        detail = "; ".join(authority.conflicts) or "run registry administration only on explicit device_role: server"
        print(f"Blocked: {detail}. No local hub registry was created.")
        return 2
    if args.command == "pair" and not authority.enabled:
        print("Blocked: consistent_everywhere.enabled must be true before issuing new authority.")
        return 2
    registry = DeviceRegistry(config)
    try:
        return _dispatch(args, registry, config)
    except (RegistryError, PairingQrError) as exc:
        print(f"Blocked: {exc}")
        return 2


def _dispatch(
    args: Any,
    registry: DeviceRegistry,
    config: dict[str, Any] | None = None,
) -> int:
    if args.command == "pair":
        if not getattr(args, "qr", False):
            print(registry.create_pairing(args.capability, ttl_seconds=args.ttl, scopes=args.scope))
            return 0
        print(render_pairing_qr(
            registry,
            config or {},
            capability=args.capability,
            scopes=args.scope,
            ttl_seconds=args.ttl,
        ))
        return 0
    if args.command == "devices":
        print(json.dumps(registry.list_devices(), indent=2))
        return 0
    if args.command == "grant":
        if not registry.set_capability(args.device_id, args.capability):
            raise RegistryError("device was not found or is revoked")
        print(f"Updated {args.device_id}: {args.capability}")
        return 0
    if args.command == "grant-scopes":
        if not registry.set_scopes(args.device_id, args.scopes):
            raise RegistryError("device was not found or is revoked")
        print(f"Updated {args.device_id} scopes: {', '.join(sorted(args.scopes)) or 'none'}")
        return 0
    if args.command == "grant-phone-control":
        principal = registry.grant_phone_control(args.device_id)
        if principal is None:
            raise RegistryError("device was not found or is revoked")
        print(
            f"Enabled phone control for {principal.device_id}; existing pairing and scopes were preserved: "
            f"{', '.join(sorted(principal.scopes))}"
        )
        return 0
    if args.command == "revoke":
        if not registry.revoke_device(args.device_id):
            raise RegistryError("device was not found or is already revoked")
        print(f"Revoked {args.device_id}")
        return 0
    raise RegistryError("unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
