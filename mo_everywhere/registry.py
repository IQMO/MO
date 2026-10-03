"""Hub-owned device authentication and capability registry.

Tokens are opaque random values. Only SHA-256 digests are persisted, every
request is revocation checked, refresh tokens rotate, and authority is bounded
by each device's capability and exact scopes.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.state.paths import EVERYWHERE_HUB_DB_PATH, resolve_state_path
from core.state.sqlite import connect_state_db


CAPABILITY_RANK = {"notify": 0, "view": 1, "control": 2}
# The canonical device-capability vocabulary; consumers import this instead of
# restating the triple.
DEVICE_CAPABILITIES = frozenset(CAPABILITY_RANK)
DEVICE_SCOPES = frozenset({
    "attachment_upload",
    "conversation_read",
    "conversation_write",
    "continuity_read",
    "continuity_sync",
    "file_browse",
    "file_manage",
    "file_transfer",
    "remote_control",
    "remote_host",
})
DEVICE_SURFACE_KINDS = frozenset({"unknown", "android"})
DEFAULT_ACCESS_TTL = 15 * 60
DEFAULT_REFRESH_TTL = 30 * 24 * 60 * 60
DEFAULT_REFRESH_GRACE_SECONDS = 30


class RegistryError(RuntimeError):
    """Safe registry error suitable for an API status response."""


@dataclass(frozen=True)
class DevicePrincipal:
    device_id: str
    label: str
    capability: str
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    access_expires_at: float
    refresh_expires_at: float
    principal: DevicePrincipal


@dataclass(frozen=True)
class IssuedPairing:
    code: str
    expires_at: float
    capability: str
    scopes: frozenset[str]


class DeviceRegistry:
    """Small single-hub SQLite registry; no token or pairing secret is stored raw."""

    def __init__(self, config: dict[str, Any] | None = None, *, path: str | Path | None = None,
                 read_only: bool = False):
        self.config = config or {}
        configured = path or resolve_state_path(EVERYWHERE_HUB_DB_PATH, self.config)
        self.path = Path(configured).expanduser().resolve(strict=False)
        self._read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def create_pairing(
        self,
        capability: str = "view",
        *,
        ttl_seconds: int = 300,
        scopes: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    ) -> str:
        return self.create_pairing_grant(
            capability,
            ttl_seconds=ttl_seconds,
            scopes=scopes,
        ).code

    def create_pairing_grant(
        self,
        capability: str = "view",
        *,
        ttl_seconds: int = 300,
        scopes: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    ) -> IssuedPairing:
        capability = _capability(capability)
        clean_scopes = _scopes(scopes or ())
        token = "mo_pair_" + secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + max(30, min(900, int(ttl_seconds)))
        with self._connect() as db:
            db.execute(
                "INSERT INTO pairing_code(token_hash, capability, created_at, expires_at, used_at) VALUES(?,?,?,?,NULL)",
                (_digest(token), capability, now, expires_at),
            )
            db.executemany(
                "INSERT INTO pairing_scope(token_hash,scope) VALUES(?,?)",
                [(_digest(token), scope) for scope in sorted(clean_scopes)],
            )
        return IssuedPairing(token, expires_at, capability, clean_scopes)

    def redeem_pairing(
        self,
        token: str,
        label: str,
        *,
        surface_kind: str = "unknown",
    ) -> IssuedTokens:
        digest = _digest(_required_token(token))
        clean_surface_kind = _surface_kind(surface_kind)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT capability, expires_at, used_at FROM pairing_code WHERE token_hash=?",
                (digest,),
            ).fetchone()
            if not row or row[2] is not None or float(row[1]) <= now:
                raise RegistryError("pairing code is invalid or expired")
            updated = db.execute(
                "UPDATE pairing_code SET used_at=? WHERE token_hash=? AND used_at IS NULL",
                (now, digest),
            ).rowcount
            if updated != 1:
                raise RegistryError("pairing code was already used")
            scopes = [str(item[0]) for item in db.execute(
                "SELECT scope FROM pairing_scope WHERE token_hash=? ORDER BY scope",
                (digest,),
            ).fetchall()]
            device_id = uuid.uuid4().hex
            clean_label = _label(label)
            db.execute(
                "INSERT INTO device(device_id,label,capability,surface_kind,created_at,last_seen_at,revoked_at) "
                "VALUES(?,?,?,?,?,?,NULL)",
                (device_id, clean_label, str(row[0]), clean_surface_kind, now, now),
            )
            db.executemany(
                "INSERT INTO device_scope(device_id,scope) VALUES(?,?)",
                [(device_id, scope) for scope in scopes],
            )
            return self._issue_tokens(db, device_id, now=now)

    def authenticate(
        self,
        token: str,
        required: str = "view",
        *,
        touch: bool = True,
    ) -> DevicePrincipal:
        required = _capability(required)
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                """
                SELECT d.device_id,d.label,d.capability,t.expires_at,t.revoked_at,d.revoked_at,
                       d.surface_kind
                FROM auth_token t JOIN device d ON d.device_id=t.device_id
                WHERE t.token_hash=? AND t.kind='access'
                """,
                (_digest(_required_token(token)),),
            ).fetchone()
            if row and not _surface_kind_supported(row[6]):
                row = None
            principal = _principal_from_token_row(row, required, now)
            principal = _principal_with_scopes(db, principal)
            if touch:
                db.execute("UPDATE device SET last_seen_at=? WHERE device_id=?", (now, principal.device_id))
            return principal

    def authenticate_scope(self, token: str, scope: str, *, touch: bool = True) -> DevicePrincipal:
        required_scope = _scope(scope)
        principal = self.authenticate(token, "notify", touch=touch)
        if required_scope not in principal.scopes:
            raise RegistryError("device scope is insufficient")
        return principal

    def revalidate_connected_device(
        self,
        device_id: str,
        required: str = "notify",
        *,
        scope: str = "",
        touch: bool = True,
    ) -> DevicePrincipal:
        """Revalidate an already authenticated long-lived connection.

        Access-token expiry limits new handshakes and HTTP requests. Once a WebSocket
        has authenticated, its fixed device identity remains valid until the device
        is revoked or loses the required capability/scope. This check preserves that
        live revocation boundary without turning the 15-minute bearer TTL into a
        forced disconnect.
        """
        clean_id = str(device_id or "").strip()
        required_capability = _capability(required)
        required_scope = _scope(scope) if str(scope or "").strip() else ""
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT device_id,label,capability,revoked_at,surface_kind FROM device WHERE device_id=?",
                (clean_id,),
            ).fetchone()
            if not row or row[3] is not None or not _surface_kind_supported(row[4]):
                raise RegistryError("device is unavailable")
            capability = str(row[2])
            if CAPABILITY_RANK.get(capability, -1) < CAPABILITY_RANK[required_capability]:
                raise RegistryError("device capability is insufficient")
            principal = _principal_with_scopes(
                db,
                DevicePrincipal(str(row[0]), str(row[1]), capability),
            )
            if required_scope and required_scope not in principal.scopes:
                raise RegistryError("device scope is insufficient")
            if touch:
                db.execute(
                    "UPDATE device SET last_seen_at=? WHERE device_id=?",
                    (now, principal.device_id),
                )
            return principal

    def refresh(self, refresh_token: str) -> IssuedTokens:
        digest = _digest(_required_token(refresh_token))
        now = time.time()
        issued: IssuedTokens | None = None
        denied = ""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT d.device_id,d.label,d.capability,t.expires_at,t.revoked_at,d.revoked_at,
                       t.rotation_grace_expires_at,t.rotation_child_set_id,d.surface_kind
                FROM auth_token t JOIN device d ON d.device_id=t.device_id
                WHERE t.token_hash=? AND t.kind='refresh'
                """,
                (digest,),
            ).fetchone()
            if row and not _surface_kind_supported(row[8]):
                principal = None
                denied = "unsupported_client"
            else:
                try:
                    principal = _principal_from_token_row(row, "notify", now)
                except RegistryError:
                    principal = None
                    denied = "invalid_or_expired"
            if principal is None:
                _record_audit_row(
                    db,
                    now,
                    "token_refresh_replay",
                    device_id=str(row[0]) if row else "",
                    outcome="denied",
                    detail=denied,
                )
            else:
                principal = _principal_with_scopes(db, principal)
                grace_expires_at = float(row[6] or 0.0)
                child_set_id = str(row[7] or "")
                retry = grace_expires_at > 0.0
                if retry and now > grace_expires_at:
                    db.execute(
                        "UPDATE auth_token SET revoked_at=COALESCE(revoked_at,?) WHERE token_hash=?",
                        (now, digest),
                    )
                    denied = "grace_expired"
                elif retry:
                    child = db.execute(
                        """
                        SELECT expires_at,revoked_at,rotation_grace_expires_at
                        FROM auth_token
                        WHERE token_set_id=? AND parent_token_hash=? AND kind='refresh'
                        LIMIT 1
                        """,
                        (child_set_id, digest),
                    ).fetchone()
                    if (
                        not child
                        or child[1] is not None
                        or float(child[0] or 0.0) <= now
                        or float(child[2] or 0.0) > 0.0
                    ):
                        db.execute(
                            "UPDATE auth_token SET revoked_at=COALESCE(revoked_at,?) WHERE token_hash=?",
                            (now, digest),
                        )
                        denied = "lineage_advanced"
                    else:
                        db.execute(
                            "UPDATE auth_token SET revoked_at=COALESCE(revoked_at,?) WHERE token_set_id=?",
                            (now, child_set_id),
                        )
                if denied:
                    _record_audit_row(
                        db,
                        now,
                        "token_refresh_replay",
                        device_id=principal.device_id,
                        outcome="denied",
                        detail=denied,
                    )
                else:
                    if not retry:
                        grace_expires_at = now + _refresh_grace_seconds(self.config)
                        db.execute(
                            "UPDATE auth_token SET rotation_grace_expires_at=? WHERE token_hash=?",
                            (grace_expires_at, digest),
                        )
                    next_set_id = uuid.uuid4().hex
                    issued = self._issue_tokens(
                        db,
                        principal.device_id,
                        now=now,
                        parent_token_hash=digest,
                        token_set_id=next_set_id,
                    )
                    db.execute(
                        "UPDATE auth_token SET rotation_child_set_id=? WHERE token_hash=?",
                        (next_set_id, digest),
                    )
                    if retry:
                        _record_audit_row(
                            db,
                            now,
                            "token_refresh_recovery",
                            device_id=principal.device_id,
                            outcome="allowed",
                            detail="parent_retry",
                        )
        if denied or issued is None:
            raise RegistryError("refresh token is invalid or expired")
        return issued

    def revoke_device(self, device_id: str) -> bool:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE device SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                (now, str(device_id or "")),
            ).rowcount
            db.execute(
                "UPDATE auth_token SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL",
                (now, str(device_id or "")),
            )
        return bool(changed)

    def set_capability(self, device_id: str, capability: str) -> bool:
        capability = _capability(capability)
        with self._connect() as db:
            changed = db.execute(
                "UPDATE device SET capability=? WHERE device_id=? AND revoked_at IS NULL",
                (capability, str(device_id or "")),
            ).rowcount
        return bool(changed)

    def set_scopes(self, device_id: str, scopes: list[str] | tuple[str, ...] | set[str] | frozenset[str]) -> bool:
        clean_scopes = _scopes(scopes)
        clean_id = str(device_id or "")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT 1 FROM device WHERE device_id=? AND revoked_at IS NULL",
                (clean_id,),
            ).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM device_scope WHERE device_id=?", (clean_id,))
            db.executemany(
                "INSERT INTO device_scope(device_id,scope) VALUES(?,?)",
                [(clean_id, scope) for scope in sorted(clean_scopes)],
            )
        return True

    def model_preference(self, device_id: str) -> dict[str, str] | None:
        clean_id = str(device_id or "").strip()
        with self._connect() as db:
            row = db.execute(
                """
                SELECT p.source,p.model,p.thinking
                FROM device_model_preference p
                JOIN device d ON d.device_id=p.device_id
                WHERE p.device_id=? AND d.revoked_at IS NULL
                """,
                (clean_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "source": str(row[0]),
            "model": str(row[1]),
            "thinking": str(row[2]),
        }

    def set_model_preference(
        self,
        device_id: str,
        *,
        source: str,
        model: str,
        thinking: str,
    ) -> bool:
        clean_id = str(device_id or "").strip()
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT 1 FROM device WHERE device_id=? AND revoked_at IS NULL",
                (clean_id,),
            ).fetchone()
            if not row:
                return False
            db.execute(
                """
                INSERT INTO device_model_preference(device_id,source,model,thinking,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET
                    source=excluded.source,
                    model=excluded.model,
                    thinking=excluded.thinking,
                    updated_at=excluded.updated_at
                """,
                (clean_id, str(source), str(model), str(thinking), now),
            )
        return True

    def clear_model_preference(self, device_id: str) -> bool:
        with self._connect() as db:
            changed = db.execute(
                "DELETE FROM device_model_preference WHERE device_id=?",
                (str(device_id or "").strip(),),
            ).rowcount
        return bool(changed)

    def grant_phone_control(self, device_id: str) -> DevicePrincipal | None:
        """Add the Android host scope without replacing an existing grant."""
        clean_id = str(device_id or "").strip()
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT device_id,label,capability FROM device WHERE device_id=? AND revoked_at IS NULL",
                (clean_id,),
            ).fetchone()
            if not row:
                return None
            principal = _principal_with_scopes(
                db,
                DevicePrincipal(str(row[0]), str(row[1]), str(row[2])),
            )
            if principal.capability != "control" or "remote_control" not in principal.scopes:
                raise RegistryError("device must already have control capability and remote_control scope")
            db.execute(
                "INSERT OR IGNORE INTO device_scope(device_id,scope) VALUES(?,?)",
                (clean_id, "remote_host"),
            )
            _record_audit_row(
                db,
                now,
                "phone_control_grant",
                device_id=clean_id,
                outcome="allowed",
                detail="preserved_existing_scopes",
            )
            return _principal_with_scopes(
                db,
                DevicePrincipal(principal.device_id, principal.label, principal.capability),
            )

    def list_devices(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT device_id,label,capability,surface_kind,created_at,last_seen_at,revoked_at "
                "FROM device ORDER BY created_at"
            ).fetchall()
            scope_rows = db.execute("SELECT device_id,scope FROM device_scope ORDER BY device_id,scope").fetchall()
        scopes: dict[str, list[str]] = {}
        for row in scope_rows:
            scopes.setdefault(str(row[0]), []).append(str(row[1]))
        result = []
        for row in rows:
            item = dict(row)
            item["scopes"] = scopes.get(str(row["device_id"]), [])
            result.append(item)
        return result

    def list_android_devices(self) -> list[dict[str, Any]]:
        """Public metadata for active Android registrations, never credentials."""
        fields = ("device_id", "label", "capability", "scopes", "last_seen_at")
        return [
            {key: item[key] for key in fields}
            for item in self.list_devices()
            if item["surface_kind"] == "android" and item["revoked_at"] is None
        ]

    def record_audit(self, event: str, *, device_id: str = "", outcome: str = "", detail: str = "") -> None:
        with self._connect() as db:
            _record_audit_row(
                db,
                time.time(),
                event,
                device_id=device_id,
                outcome=outcome,
                detail=detail,
            )

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT created_at,event,device_id,outcome,detail FROM audit ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _issue_tokens(
        self,
        db: sqlite3.Connection,
        device_id: str,
        *,
        now: float,
        parent_token_hash: str = "",
        token_set_id: str = "",
    ) -> IssuedTokens:
        _prune_ephemeral(db, now)
        row = db.execute(
            "SELECT device_id,label,capability,surface_kind FROM device "
            "WHERE device_id=? AND revoked_at IS NULL",
            (device_id,),
        ).fetchone()
        if not row or not _surface_kind_supported(row[3]):
            raise RegistryError("device is unavailable")
        principal = _principal_with_scopes(
            db,
            DevicePrincipal(str(row[0]), str(row[1]), str(row[2])),
        )
        access = "mo_access_" + secrets.token_urlsafe(32)
        refresh = "mo_refresh_" + secrets.token_urlsafe(48)
        access_exp = now + DEFAULT_ACCESS_TTL
        refresh_exp = now + DEFAULT_REFRESH_TTL
        issued_set_id = token_set_id or uuid.uuid4().hex
        db.executemany(
            """
            INSERT INTO auth_token(
              token_hash,device_id,kind,created_at,expires_at,revoked_at,
              token_set_id,parent_token_hash,rotation_grace_expires_at,rotation_child_set_id
            ) VALUES(?,?,?,?,?,NULL,?,?,0,'')
            """,
            [
                (_digest(access), device_id, "access", now, access_exp, issued_set_id, parent_token_hash),
                (_digest(refresh), device_id, "refresh", now, refresh_exp, issued_set_id, parent_token_hash),
            ],
        )
        return IssuedTokens(
            access, refresh, access_exp, refresh_exp,
            principal,
        )

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS device(
                    device_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    surface_kind TEXT NOT NULL DEFAULT 'unknown',
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS auth_token(
                    token_hash TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES device(device_id),
                    kind TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL,
                    token_set_id TEXT NOT NULL DEFAULT '',
                    parent_token_hash TEXT NOT NULL DEFAULT '',
                    rotation_grace_expires_at REAL NOT NULL DEFAULT 0,
                    rotation_child_set_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS auth_token_device_idx ON auth_token(device_id);
                CREATE TABLE IF NOT EXISTS pairing_code(
                    token_hash TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL
                );
                CREATE TABLE IF NOT EXISTS pairing_scope(
                    token_hash TEXT NOT NULL REFERENCES pairing_code(token_hash) ON DELETE CASCADE,
                    scope TEXT NOT NULL,
                    PRIMARY KEY(token_hash,scope)
                );
                CREATE TABLE IF NOT EXISTS device_scope(
                    device_id TEXT NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
                    scope TEXT NOT NULL,
                    PRIMARY KEY(device_id,scope)
                );
                CREATE TABLE IF NOT EXISTS device_model_preference(
                    device_id TEXT PRIMARY KEY REFERENCES device(device_id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    model TEXT NOT NULL,
                    thinking TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                DROP TABLE IF EXISTS control_stepup;
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    event TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                """
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(auth_token)").fetchall()}
            migrations = {
                "token_set_id": "TEXT NOT NULL DEFAULT ''",
                "parent_token_hash": "TEXT NOT NULL DEFAULT ''",
                "rotation_grace_expires_at": "REAL NOT NULL DEFAULT 0",
                "rotation_child_set_id": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE auth_token ADD COLUMN {name} {declaration}")
            device_columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(device)").fetchall()
            }
            if "surface_kind" not in device_columns:
                db.execute(
                    "ALTER TABLE device ADD COLUMN surface_kind TEXT NOT NULL DEFAULT 'unknown'"
                )
            supported_kinds = tuple(sorted(DEVICE_SURFACE_KINDS))
            placeholders = ",".join("?" for _ in supported_kinds)
            db.execute(
                f"UPDATE device SET revoked_at=COALESCE(revoked_at,?) "
                f"WHERE lower(trim(surface_kind)) NOT IN ({placeholders})",
                (time.time(), *supported_kinds),
            )
            db.execute("CREATE INDEX IF NOT EXISTS auth_token_set_idx ON auth_token(token_set_id)")
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        if self._read_only:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
            connection.row_factory = sqlite3.Row
            return connection
        return connect_state_db(self.path, foreign_keys=True)


def _principal_from_token_row(row: sqlite3.Row | None, required: str, now: float) -> DevicePrincipal:
    if not row or row[4] is not None or row[5] is not None or float(row[3]) <= now:
        raise RegistryError("token is invalid or expired")
    capability = str(row[2])
    if CAPABILITY_RANK.get(capability, -1) < CAPABILITY_RANK[required]:
        raise RegistryError("device capability is insufficient")
    return DevicePrincipal(str(row[0]), str(row[1]), capability)


def _principal_with_scopes(db: sqlite3.Connection, principal: DevicePrincipal) -> DevicePrincipal:
    rows = db.execute(
        "SELECT scope FROM device_scope WHERE device_id=? ORDER BY scope",
        (principal.device_id,),
    ).fetchall()
    return DevicePrincipal(principal.device_id, principal.label, principal.capability, frozenset(str(row[0]) for row in rows))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _required_token(token: str) -> str:
    value = str(token or "").strip()
    if len(value) < 24 or len(value) > 256:
        raise RegistryError("token is invalid or expired")
    return value


def _capability(value: str) -> str:
    capability = str(value or "").strip().lower()
    if capability not in CAPABILITY_RANK:
        raise RegistryError("unknown capability")
    return capability


def _surface_kind_supported(value: Any) -> bool:
    return str(value or "").strip().lower() in DEVICE_SURFACE_KINDS


def _surface_kind(value: Any) -> str:
    kind = str(value or "unknown").strip().lower()
    if not _surface_kind_supported(kind):
        raise RegistryError("device surface kind is invalid")
    return kind


def _scope(value: str) -> str:
    scope = str(value or "").strip().lower()
    if scope not in DEVICE_SCOPES:
        raise RegistryError("unknown device scope")
    return scope


def _scopes(values: Any) -> frozenset[str]:
    if isinstance(values, str):
        values = [values]
    try:
        return frozenset(_scope(value) for value in values)
    except TypeError:
        raise RegistryError("device scopes must be a list") from None


def _label(value: str) -> str:
    label = " ".join(str(value or "Mobile device").strip().split())
    return _short(label, 80) or "Mobile device"


def _short(value: str, limit: int) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def _prune_ephemeral(db: sqlite3.Connection, now: float) -> None:
    cutoff = float(now) - 24 * 60 * 60
    db.execute(
        """
        UPDATE auth_token SET revoked_at=COALESCE(revoked_at,?)
        WHERE kind='refresh' AND rotation_grace_expires_at>0 AND rotation_grace_expires_at<=?
        """,
        (now, now),
    )
    db.execute("DELETE FROM auth_token WHERE expires_at<? OR (revoked_at IS NOT NULL AND revoked_at<?)", (now, cutoff))
    db.execute("DELETE FROM pairing_code WHERE expires_at<? OR (used_at IS NOT NULL AND used_at<?)", (now, cutoff))


def _refresh_grace_seconds(config: dict[str, Any]) -> float:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    api = block.get("api") if isinstance(block.get("api"), dict) else {}
    try:
        return max(5.0, min(120.0, float(
            api.get("refresh_rotation_grace_seconds", DEFAULT_REFRESH_GRACE_SECONDS)
            or DEFAULT_REFRESH_GRACE_SECONDS
        )))
    except (TypeError, ValueError):
        return float(DEFAULT_REFRESH_GRACE_SECONDS)


def _record_audit_row(
    db: sqlite3.Connection,
    created_at: float,
    event: str,
    *,
    device_id: str,
    outcome: str,
    detail: str,
) -> None:
    db.execute(
        "INSERT INTO audit(created_at,event,device_id,outcome,detail) VALUES(?,?,?,?,?)",
        (created_at, _short(event, 80), _short(device_id, 64), _short(outcome, 32), _short(detail, 300)),
    )
    db.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 10000)")
