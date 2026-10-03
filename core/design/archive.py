"""Portable image custody for Design downloads; private documents remain YAML."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from core.state.attachments import MAX_ATTACHMENT_BYTES, find_attachment, import_attachment
from core.utils.atomic_write import atomic_write_text
from .schema import MAX_DOCUMENT_BYTES, DesignDocument, parse_design, render_design

MAX_ARCHIVE_BYTES = 160 * 1024 * 1024
_DOCUMENT = "design.modesign"
_MANIFEST = "images.json"


def read_archive(path: Path) -> tuple[DesignDocument, dict[str, str]]:
    if path.stat().st_size > MAX_ARCHIVE_BYTES + 1_000_000:
        raise ValueError("MO Design bundle exceeds its limits")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > 4098 or len(set(names)) != len(names) or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise ValueError("MO Design bundle exceeds its limits")
        for info in infos:
            limit = MAX_DOCUMENT_BYTES if info.filename == _DOCUMENT else 600_000 if info.filename == _MANIFEST else MAX_ATTACHMENT_BYTES
            if info.file_size > limit or info.flag_bits & 1:
                raise ValueError("Invalid MO Design bundle member")
        try:
            document = parse_design(archive.read(_DOCUMENT))
            manifest = json.loads(archive.read(_MANIFEST))
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid MO Design bundle") from exc
        ids = {row["attachment_id"] for row in document.board.elements if row["kind"] == "image"}
        if not isinstance(manifest, dict) or set(manifest) != ids:
            raise ValueError("MO Design bundle image manifest does not match its Board")
        if any(not isinstance(name, str) or not re.fullmatch(r"images/[0-9]+\.(png|jpe?g|webp|gif|bmp|svg)", name) for name in manifest.values()):
            raise ValueError("Invalid MO Design bundle image name")
        if set(names) != {_DOCUMENT, _MANIFEST, *manifest.values()}:
            raise ValueError("Unexpected MO Design bundle members")
        return document, manifest


def export_design(document: DesignDocument, target: Path, *, config: dict[str, Any] | None = None) -> None:
    ids = sorted({row["attachment_id"] for row in document.board.elements if row["kind"] == "image"})
    if not ids:
        atomic_write_text(target, render_design(document), encoding="utf-8")
        return
    images = []
    total = 0
    for index, attachment_id in enumerate(ids):
        row = find_attachment(config, attachment_id)
        if not row or row.get("category") != "gallery":
            raise ValueError("A Board image is unavailable. Restore it before downloading.")
        path = Path(row["saved_path"])
        size = path.stat().st_size
        total += size
        if not 0 < size <= MAX_ATTACHMENT_BYTES or total > MAX_ARCHIVE_BYTES - MAX_DOCUMENT_BYTES:
            raise ValueError("MO Design images exceed the portable bundle limit")
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}:
            raise ValueError("Unsupported portable Board image")
        images.append((attachment_id, path, f"images/{index}{suffix}"))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".mo-design-", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(_DOCUMENT, render_design(document))
            archive.writestr(_MANIFEST, json.dumps({key: name for key, _path, name in images}))
            for _key, path, name in images:
                archive.write(path, name)
        read_archive(Path(temporary))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_archive_images(path: Path, manifest: dict[str, str], *, config: dict[str, Any] | None = None) -> dict[str, str]:
    """Import only validated members, without extracting archive paths."""
    result = {}
    with tempfile.TemporaryDirectory(prefix="mo-design-import-") as directory, zipfile.ZipFile(path) as archive:
        for index, (old_id, name) in enumerate(manifest.items()):
            target = Path(directory) / f"image-{index}{Path(name).suffix}"
            target.write_bytes(archive.read(name))
            imported = import_attachment(config, target, origin="mo_design", max_bytes=MAX_ATTACHMENT_BYTES, allow_empty=False)
            result[old_id] = imported["id"]
    return result
