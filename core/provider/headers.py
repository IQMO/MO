"""Provider-neutral HTTP header normalization."""
from __future__ import annotations

from typing import Any


def normalize_headers(headers: Any) -> dict[str, str]:
    """Return lowercase string headers from a dict or mapping-like object."""
    if headers is None:
        return {}
    if isinstance(headers, dict):
        return {str(key).lower(): str(value) for key, value in headers.items()}
    result: dict[str, str] = {}
    try:
        for key, value in headers.items():
            result[str(key).lower()] = str(value)
    except (TypeError, AttributeError):
        pass
    return result
