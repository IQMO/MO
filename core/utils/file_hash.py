"""Streaming file digests shared by product surfaces."""
from __future__ import annotations

import hashlib
from pathlib import Path


DEFAULT_HASH_CHUNK_BYTES = 1024 * 1024


def file_sha256(path: str | Path, *, chunk_bytes: int = DEFAULT_HASH_CHUNK_BYTES) -> str:
    """Return the lowercase SHA-256 digest of one file without loading it whole."""
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(block)
    return digest.hexdigest()


def file_sha256_or_empty(path: str | Path) -> str:
    """Return an empty digest when an optional file cannot be read."""
    try:
        return file_sha256(path)
    except OSError:
        return ""
