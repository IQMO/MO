"""Validated Android release artifacts served through the paired hub."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


MAX_ANDROID_APK_BYTES = 256 * 1024 * 1024
MAX_ANDROID_RELEASE_MANIFEST_BYTES = 64 * 1024
ANDROID_PACKAGE_NAME = "app.moagent.mobile"
ANDROID_UPDATE_SCHEMA_VERSION = 2
ANDROID_RELEASE_MANIFEST_SCHEMA_VERSION = 1
ANDROID_UPDATE_STATUS_TTL_SECONDS = 300
_VERSION_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,191}\.apk")
_MANIFEST_FIELDS = {
    "schema_version",
    "package_name",
    "version_code",
    "version_name",
    "artifact",
    "sha256",
    "size_bytes",
    "signer_sha256",
    "min_supported_version_code",
    "published_at",
    "release_notes",
}


class AndroidUpdateConfigurationError(RuntimeError):
    """The operator supplied a partial or inconsistent Android release."""


@dataclass(frozen=True)
class AndroidRelease:
    path: Path
    version_code: int
    version_name: str
    sha256: str
    size_bytes: int
    signer_sha256: str
    min_supported_version_code: int
    published_at: int
    release_notes: str

    def public(self) -> dict[str, object]:
        return {
            "schema_version": ANDROID_UPDATE_SCHEMA_VERSION,
            "package_name": ANDROID_PACKAGE_NAME,
            "available": True,
            "version_code": self.version_code,
            "version_name": self.version_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "signer_sha256": self.signer_sha256,
            "min_supported_version_code": self.min_supported_version_code,
            "published_at": self.published_at,
            "expires_at": int(time.time()) + ANDROID_UPDATE_STATUS_TTL_SECONDS,
            "release_notes": self.release_notes,
            "download_path": "/api/mo/android/update/apk",
            "default_install_requires_user_action": True,
            "privileged_install_supported": True,
        }


class AndroidReleaseStore:
    """Resolve one exact operator-packaged APK without exposing its host path."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
    ):
        self._environ = os.environ if environ is None else environ
        self._cache_key: tuple | None = None
        self._cached: AndroidRelease | None = None

    def status(self) -> dict[str, object]:
        release = self.release()
        if release is None:
            return {
                "schema_version": ANDROID_UPDATE_SCHEMA_VERSION,
                "package_name": ANDROID_PACKAGE_NAME,
                "available": False,
                "version_code": 0,
                "version_name": "",
                "sha256": "",
                "size_bytes": 0,
                "signer_sha256": "",
                "min_supported_version_code": 0,
                "published_at": 0,
                "expires_at": int(time.time()) + ANDROID_UPDATE_STATUS_TTL_SECONDS,
                "release_notes": "",
                "download_path": "",
                "default_install_requires_user_action": True,
                "privileged_install_supported": True,
            }
        return release.public()

    def release(self) -> AndroidRelease | None:
        manifest_path = str(
            self._environ.get("MO_ANDROID_UPDATE_MANIFEST") or ""
        ).strip()
        # COMPAT(android-update-legacy-env): replaced-by MO_ANDROID_UPDATE_MANIFEST; remove-when all maintained deployments use generated manifests
        raw = {
            "path": str(self._environ.get("MO_ANDROID_UPDATE_APK") or "").strip(),
            "version_code": str(
                self._environ.get("MO_ANDROID_UPDATE_VERSION_CODE") or ""
            ).strip(),
            "version_name": str(
                self._environ.get("MO_ANDROID_UPDATE_VERSION_NAME") or ""
            ).strip(),
            "sha256": str(
                self._environ.get("MO_ANDROID_UPDATE_SHA256") or ""
            ).strip().lower(),
            "signer_sha256": str(
                self._environ.get("MO_ANDROID_UPDATE_SIGNER_SHA256") or ""
            ).strip().lower(),
            "min_supported_version_code": str(
                self._environ.get("MO_ANDROID_UPDATE_MIN_VERSION_CODE") or ""
            ).strip(),
            "published_at": str(
                self._environ.get("MO_ANDROID_UPDATE_PUBLISHED_AT") or ""
            ).strip(),
        }
        release_notes = " ".join(
            str(self._environ.get("MO_ANDROID_UPDATE_RELEASE_NOTES") or "").split()
        )[:1000]
        if manifest_path:
            if any(raw.values()) or release_notes:
                raise AndroidUpdateConfigurationError(
                    "Android update manifest and legacy packaging cannot be mixed"
                )
            return self._from_manifest(manifest_path)
        return self._build(raw, release_notes)

    def _from_manifest(self, configured_path: str) -> AndroidRelease:
        try:
            manifest_path = Path(configured_path).expanduser().resolve(strict=True)
            manifest_stat = manifest_path.stat()
        except (OSError, RuntimeError):
            raise AndroidUpdateConfigurationError(
                "Android update manifest is unavailable"
            ) from None
        if not manifest_path.is_file() or manifest_path.suffix.lower() != ".json":
            raise AndroidUpdateConfigurationError(
                "Android update manifest is not a JSON file"
            )
        if not 1 <= manifest_stat.st_size <= MAX_ANDROID_RELEASE_MANIFEST_BYTES:
            raise AndroidUpdateConfigurationError(
                "Android update manifest is outside the size bound"
            )
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise AndroidUpdateConfigurationError(
                "Android update manifest is invalid JSON"
            ) from None
        if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
            raise AndroidUpdateConfigurationError(
                "Android update manifest fields are invalid"
            )
        if _manifest_int(payload, "schema_version") != ANDROID_RELEASE_MANIFEST_SCHEMA_VERSION:
            raise AndroidUpdateConfigurationError(
                "Android update manifest schema is unsupported"
            )
        if payload["package_name"] != ANDROID_PACKAGE_NAME:
            raise AndroidUpdateConfigurationError(
                "Android update manifest package identity is invalid"
            )
        artifact_name = payload["artifact"]
        if not isinstance(artifact_name, str) or _ARTIFACT_NAME.fullmatch(artifact_name) is None:
            raise AndroidUpdateConfigurationError(
                "Android update manifest artifact name is invalid"
            )
        try:
            release_path = (manifest_path.parent / artifact_name).resolve(strict=True)
        except (OSError, RuntimeError):
            raise AndroidUpdateConfigurationError(
                "Android update manifest artifact is unavailable"
            ) from None
        if release_path.parent != manifest_path.parent:
            raise AndroidUpdateConfigurationError(
                "Android update manifest artifact escapes its release directory"
            )
        version_name = payload["version_name"]
        sha256 = payload["sha256"]
        signer_sha256 = payload["signer_sha256"]
        release_notes = payload["release_notes"]
        if not all(
            isinstance(value, str)
            for value in (version_name, sha256, signer_sha256, release_notes)
        ):
            raise AndroidUpdateConfigurationError(
                "Android update manifest text fields are invalid"
            )
        raw = {
            "path": str(release_path),
            "version_code": str(_manifest_int(payload, "version_code")),
            "version_name": version_name,
            "sha256": sha256.lower(),
            "signer_sha256": signer_sha256.lower(),
            "min_supported_version_code": str(
                _manifest_int(payload, "min_supported_version_code")
            ),
            "published_at": str(_manifest_int(payload, "published_at")),
        }
        return self._build(
            raw,
            " ".join(release_notes.split())[:1000],
            expected_size=_manifest_int(payload, "size_bytes"),
            cache_context=(
                str(manifest_path),
                int(manifest_stat.st_mtime_ns),
                int(manifest_stat.st_size),
            ),
        )

    def _build(
        self,
        raw: dict[str, str],
        release_notes: str,
        *,
        expected_size: int | None = None,
        cache_context: tuple = (),
    ) -> "AndroidRelease | None":
        if not any(raw.values()):
            self._cache_key = None
            self._cached = None
            return None
        if not all(raw.values()):
            raise AndroidUpdateConfigurationError(
                "Android update packaging is only partially configured"
            )
        try:
            version_code = int(raw["version_code"])
            min_supported_version_code = int(raw["min_supported_version_code"])
            published_at = int(raw["published_at"])
        except ValueError:
            raise AndroidUpdateConfigurationError(
                "Android update version metadata is invalid"
            ) from None
        if not 1 <= version_code <= 2_100_000_000:
            raise AndroidUpdateConfigurationError(
                "Android update version code is invalid"
            )
        if not 1 <= min_supported_version_code <= version_code:
            raise AndroidUpdateConfigurationError(
                "Android update minimum supported version is invalid"
            )
        current = int(time.time())
        if not 1_600_000_000 <= published_at <= current + 300:
            raise AndroidUpdateConfigurationError(
                "Android update publication time is invalid"
            )
        if _VERSION_NAME.fullmatch(raw["version_name"]) is None:
            raise AndroidUpdateConfigurationError(
                "Android update version name is invalid"
            )
        if _SHA256.fullmatch(raw["sha256"]) is None:
            raise AndroidUpdateConfigurationError(
                "Android update SHA-256 is invalid"
            )
        if _SHA256.fullmatch(raw["signer_sha256"]) is None:
            raise AndroidUpdateConfigurationError(
                "Android update signer SHA-256 is invalid"
            )
        try:
            path = Path(raw["path"]).expanduser().resolve(strict=True)
            stat = path.stat()
        except (OSError, RuntimeError):
            raise AndroidUpdateConfigurationError(
                "Android update APK is unavailable"
            ) from None
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise AndroidUpdateConfigurationError(
                "Android update artifact is not an APK"
            )
        size = int(stat.st_size)
        if not 1 <= size <= MAX_ANDROID_APK_BYTES:
            raise AndroidUpdateConfigurationError(
                "Android update APK is outside the size bound"
            )
        if expected_size is not None and size != expected_size:
            raise AndroidUpdateConfigurationError(
                "Android update APK does not match its manifest size"
            )
        cache_key = (
            *cache_context,
            str(path),
            int(stat.st_mtime_ns),
            size,
            raw["sha256"],
            version_code,
            raw["version_name"],
            raw["signer_sha256"],
            min_supported_version_code,
            published_at,
            release_notes,
        )
        if cache_key == self._cache_key and self._cached is not None:
            return self._cached
        digest = _sha256(path)
        if digest != raw["sha256"]:
            raise AndroidUpdateConfigurationError(
                "Android update APK does not match its configured SHA-256"
            )
        release = AndroidRelease(
            path=path,
            version_code=version_code,
            version_name=raw["version_name"],
            sha256=digest,
            size_bytes=size,
            signer_sha256=raw["signer_sha256"],
            min_supported_version_code=min_supported_version_code,
            published_at=published_at,
            release_notes=release_notes,
        )
        self._cache_key = cache_key
        self._cached = release
        return release


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AndroidUpdateConfigurationError(
            "Android update manifest numeric fields are invalid"
        )
    return value
