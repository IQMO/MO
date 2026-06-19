"""Optional embeddings backend for semantic memory recall.

Off by default. When the operator configures an OpenAI-compatible ``/embeddings``
endpoint, MO recalls past turns by meaning (vector cosine) instead of keyword overlap.
Uses stdlib HTTP only — NO new Python dependency (no torch / sentence-transformers). If
unconfigured or the endpoint fails, callers fall back to the bm25 keyword recall.

Config (``embeddings`` section):
    enabled: true
    base_url: https://api.openai.com/v1     # any OpenAI-compatible /embeddings host
    api_key_env: OPENAI_API_KEY             # env var holding the key
    model: text-embedding-3-small
"""
from __future__ import annotations

import json
import math
import os
import traceback
import urllib.request
from typing import Any, Callable


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _http_embedder(base_url: str, api_key: str, model: str, timeout: float = 12.0) -> Callable[[str], list[float]]:
    url = base_url.rstrip("/") + "/embeddings"

    def embed(text: str) -> list[float]:
        body = json.dumps({"model": model, "input": str(text or "")[:8000]}).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "MO-Agent/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        vec = (((data.get("data") or [{}])[0]).get("embedding")) or []
        return [float(x) for x in vec]

    return embed


def build_embedder(config: dict[str, Any] | None) -> Callable[[str], list[float]] | None:
    """Return an embedder callable from config, or None when semantic recall is off.

    Returns None (→ bm25 fallback) unless ``embeddings.enabled`` is true AND a base_url
    and model are set. Never raises on config problems — degrades to None.
    """
    try:
        cfg = config if isinstance(config, dict) else {}
        emb = cfg.get("embeddings", {}) if isinstance(cfg.get("embeddings", {}), dict) else {}
        if not emb.get("enabled"):
            return None
        base_url = str(emb.get("base_url") or "").strip()
        model = str(emb.get("model") or "").strip()
        if not base_url or not model:
            return None
        api_key = ""
        key_env = str(emb.get("api_key_env") or "").strip()
        if key_env:
            api_key = os.environ.get(key_env, "").strip()
        return _http_embedder(base_url, api_key, model)
    except Exception:
        traceback.print_exc()
        return None
