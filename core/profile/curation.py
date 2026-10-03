"""Lossless lifecycle operations for curated operator profile prose."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state.paths import resolve_state_path
from ..utils.atomic_write import atomic_create_bytes, atomic_write_text
from . import DEFAULT_PROFILE_PATH, TEMPLATE_FILES, profile_transaction_lock

_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*\r?\n?$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_SNAPSHOT_MARKER = "<!-- exact-former-operator-profile-follows -->\n"
_MAX_OPERATOR_PROFILE_BYTES = 1_000_000


def _profile_db_path(profile: Any, config: dict[str, Any] | None) -> Path:
    raw = str(getattr(profile, "_path", "") or "").strip()
    return Path(raw or resolve_state_path(DEFAULT_PROFILE_PATH, config)).expanduser()


def _operator_path(profile_db: Path) -> Path:
    return profile_db.parent / "profile" / "operator.md"


def _read_operator_profile(operator_path: Path) -> str:
    try:
        return operator_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError("operator profile file is missing") from exc


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_replacement(replacement: str) -> str:
    text = str(replacement or "")
    if "\x00" in text:
        raise ValueError("operator profile replacement contains a NUL byte")
    if len(text.encode("utf-8")) > _MAX_OPERATOR_PROFILE_BYTES:
        raise ValueError("operator profile replacement is too large")
    if not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# Operator Profile"):
        raise ValueError("operator profile replacement must start with '# Operator Profile'")
    if not re.search(r"(?m)^- \*\*Name:\*\*\s*\S", text):
        raise ValueError("operator profile replacement must retain a non-empty Name field")
    return text


def _invalidate(profile: Any) -> None:
    if profile is not None:
        profile._profile_cache_text = None
        profile._profile_cache_key = None


def _section_bounds(text: str, heading: str) -> tuple[int, int, str]:
    lines = text.splitlines(keepends=True)
    matches: list[int] = []
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and match.group(1).strip().casefold() == heading.casefold():
            matches.append(index)
    if not matches:
        raise ValueError(f"operator profile section not found: {heading}")
    if len(matches) != 1:
        raise ValueError(f"operator profile section is ambiguous: {heading}")
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _HEADING_RE.match(lines[index]):
            end = index
            break
    return start, end, "".join(lines[start:end])


def preview_operator_profile_replacement(
    replacement: str,
    *,
    profile: Any,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a full replacement and bind its preview to current profile truth."""
    candidate = _normalize_replacement(replacement)
    profile_db = _profile_db_path(profile, config)
    with profile_transaction_lock(profile_db):
        source = _read_operator_profile(_operator_path(profile_db))
    return {
        "replaced": False,
        "source_sha256": _sha256(source),
        "replacement_sha256": _sha256(candidate),
        "source_line_count": len(source.splitlines()),
        "replacement_line_count": len(candidate.splitlines()),
    }


def replace_operator_profile(
    replacement: str,
    *,
    expected_source_sha256: str,
    profile: Any,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archive the complete current profile, then install a preview-bound replacement.

    ``expected_source_sha256`` must come from a prior preview. The check happens
    under the profile transaction lock, before the immutable archive is created,
    so a stale preview cannot overwrite newer operator profile truth. The archive
    is created before replacement; a write failure can duplicate data but cannot
    lose the former document.
    """
    candidate = _normalize_replacement(replacement)
    expected = str(expected_source_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError("a valid source SHA-256 from preview is required")

    profile_db = _profile_db_path(profile, config)
    operator_path = _operator_path(profile_db)
    with profile_transaction_lock(profile_db):
        source = _read_operator_profile(operator_path)
        source_digest = _sha256(source)
        if source_digest != expected:
            raise ValueError("operator profile changed after preview; preview again before replacing it")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_dir = profile_db.parent / "archive" / "profile-snapshots"
        archive_path = archive_dir / f"{stamp}-operator-{source_digest[:12]}.md"
        payload = (
            "# Archived Operator Profile Snapshot\n\n"
            "- Source: `profile/operator.md`\n"
            f"- Content SHA-256: `{source_digest}`\n\n"
            "The exact former document follows. This archive is inert and is not current profile truth.\n\n"
            + _SNAPSHOT_MARKER
            + source
        ).encode("utf-8")
        try:
            atomic_create_bytes(archive_path, payload)
        except FileExistsError:
            if archive_path.read_bytes() != payload:
                raise ValueError("archive destination collision; operator profile was not changed")

        atomic_write_text(operator_path, candidate, encoding="utf-8")

    _invalidate(profile)
    return {
        "replaced": True,
        "source_sha256": source_digest,
        "replacement_sha256": _sha256(candidate),
        "source_line_count": len(source.splitlines()),
        "replacement_line_count": len(candidate.splitlines()),
        "archive_path": archive_path.relative_to(profile_db.parent).as_posix(),
    }


def _profile_document_path(profile_db: Path, document: str) -> tuple[str, Path]:
    name = str(document or "").strip()
    if name not in TEMPLATE_FILES:
        raise ValueError("profile document must be one of: " + " | ".join(sorted(TEMPLATE_FILES)))
    return name, profile_db.parent / "profile" / name


def _normalize_document_replacement(document: str, replacement: str) -> str:
    if document == "operator.md":
        return _normalize_replacement(replacement)
    text = str(replacement or "")
    if "\x00" in text:
        raise ValueError("profile document replacement contains a NUL byte")
    if len(text.encode("utf-8")) > _MAX_OPERATOR_PROFILE_BYTES:
        raise ValueError("profile document replacement is too large")
    if not text.endswith("\n"):
        text += "\n"
    if not text.startswith("# "):
        raise ValueError("profile document replacement must start with a level-one heading")
    return text


def preview_profile_document_replacement(
    document: str,
    replacement: str,
    *,
    profile: Any,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a canonical prose-document replacement against current truth."""
    profile_db = _profile_db_path(profile, config)
    name, path = _profile_document_path(profile_db, document)
    candidate = _normalize_document_replacement(name, replacement)
    with profile_transaction_lock(profile_db):
        try:
            source = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(f"profile document is missing: {name}") from exc
    return {
        "replaced": False,
        "document": name,
        "source_sha256": _sha256(source),
        "replacement_sha256": _sha256(candidate),
        "source_line_count": len(source.splitlines()),
        "replacement_line_count": len(candidate.splitlines()),
    }


def replace_profile_document(
    document: str,
    replacement: str,
    *,
    expected_source_sha256: str,
    profile: Any,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Losslessly replace one of the six canonical prose profile documents."""
    if str(document or "").strip() == "operator.md":
        return replace_operator_profile(
            replacement,
            expected_source_sha256=expected_source_sha256,
            profile=profile,
            config=config,
        )
    expected = str(expected_source_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError("a valid source SHA-256 from preview is required")
    profile_db = _profile_db_path(profile, config)
    name, path = _profile_document_path(profile_db, document)
    candidate = _normalize_document_replacement(name, replacement)
    with profile_transaction_lock(profile_db):
        try:
            source = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(f"profile document is missing: {name}") from exc
        source_digest = _sha256(source)
        if source_digest != expected:
            raise ValueError("profile document changed after preview; preview again before replacing it")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_dir = profile_db.parent / "archive" / "profile-snapshots"
        archive_path = archive_dir / f"{stamp}-{Path(name).stem}-{source_digest[:12]}.md"
        marker = "<!-- exact-former-profile-document-follows -->\n"
        payload = (
            "# Archived Profile Document Snapshot\n\n"
            f"- Source: `profile/{name}`\n"
            f"- Content SHA-256: `{source_digest}`\n\n"
            "The exact former document follows. This archive is inert and is not current profile truth.\n\n"
            + marker
            + source
        ).encode("utf-8")
        try:
            atomic_create_bytes(archive_path, payload)
        except FileExistsError:
            if archive_path.read_bytes() != payload:
                raise ValueError("archive destination collision; profile document was not changed")
        atomic_write_text(path, candidate, encoding="utf-8")

    _invalidate(profile)
    return {
        "replaced": True,
        "document": name,
        "source_sha256": source_digest,
        "replacement_sha256": _sha256(candidate),
        "source_line_count": len(source.splitlines()),
        "replacement_line_count": len(candidate.splitlines()),
        "archive_path": archive_path.relative_to(profile_db.parent).as_posix(),
    }


def archive_operator_profile_section(
    heading: str,
    *,
    profile: Any,
    config: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or losslessly archive one exact ``##`` operator profile section.

    The immutable archive is created before current profile truth is changed. If
    replacement fails, the result is a harmless duplicate rather than data loss.
    """
    clean_heading = " ".join(str(heading or "").split()).strip()
    if not clean_heading or len(clean_heading) > 160 or any(ch in clean_heading for ch in "\r\n"):
        raise ValueError("provide one exact operator profile section heading")

    profile_db = _profile_db_path(profile, config)
    operator_path = _operator_path(profile_db)
    with profile_transaction_lock(profile_db):
        text = _read_operator_profile(operator_path)
        start, end, section = _section_bounds(text, clean_heading)
        digest = _sha256(section)
        result: dict[str, Any] = {
            "archived": False,
            "heading": clean_heading,
            "line_count": len(section.splitlines()),
            "sha256": digest,
        }
        if not confirm:
            return result

        slug = re.sub(r"[^a-z0-9]+", "-", clean_heading.casefold()).strip("-")[:64] or "section"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_dir = profile_db.parent / "archive" / "profile-sections"
        archive_path = archive_dir / f"{stamp}-{slug}-{digest[:12]}.md"
        payload = (
            "# Archived Operator Profile Section\n\n"
            f"- Source: `profile/operator.md`\n"
            f"- Heading: `{clean_heading}`\n"
            f"- Content SHA-256: `{digest}`\n\n"
            "The exact former section follows. This archive is inert and is not current profile truth.\n\n"
            + section
        ).encode("utf-8")
        try:
            atomic_create_bytes(archive_path, payload)
        except FileExistsError:
            if archive_path.read_bytes() != payload:
                raise ValueError("archive destination collision; operator profile was not changed")

        lines = text.splitlines(keepends=True)
        remaining = "".join(lines[:start] + lines[end:])
        atomic_write_text(operator_path, remaining, encoding="utf-8")

    _invalidate(profile)
    result.update({
        "archived": True,
        "archive_path": archive_path.relative_to(profile_db.parent).as_posix(),
    })
    return result
