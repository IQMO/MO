"""Atomic local file replacement helpers for small state files."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


def _temporary_sibling(target: Path) -> Path:
    """Return a short same-directory temp path that stays below Windows limits."""
    return target.with_name(f".mo-{os.getpid():x}-{uuid.uuid4().hex[:12]}.tmp")


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text via same-directory temp file and atomic replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temporary_sibling(target)
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def atomic_create_bytes(path: str | Path, data: bytes) -> None:
    """Atomically create a new file without ever replacing an existing path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _temporary_sibling(target)
    try:
        tmp.write_bytes(data)
        os.link(tmp, target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def atomic_write_json(
    path: str | Path,
    data: Any,
    *,
    encoding: str = "utf-8",
    **json_kwargs: Any,
) -> None:
    atomic_write_text(path, json.dumps(data, **json_kwargs), encoding=encoding)
