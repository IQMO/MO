"""Optional embeddings backend for semantic memory recall.

Off by default. When enabled, MO recalls past turns by meaning (vector cosine) instead
of keyword overlap. Two backends; if unconfigured or the backend fails, callers fall
back to the bm25 keyword recall.

- ``backend: api`` (default) — an OpenAI-compatible ``/embeddings`` endpoint over stdlib
  HTTP. NO Python dependency. Best quality, but sends recall text to that endpoint.
- ``backend: local`` — a slim on-device ONNX model via the OPTIONAL ``fastembed``
  package (no torch). Fully offline/private; nothing leaves the machine. Lazy-imported,
  so the base install stays lean — if ``fastembed`` isn't installed, MO logs once and
  falls back to keyword recall rather than failing.

Config (``embeddings`` section):
    enabled: true
    backend: api                            # api | local
    base_url: https://api.openai.com/v1     # api: any OpenAI-compatible /embeddings host
    api_key_env: OPENAI_API_KEY             # api: env var holding the key
    model: text-embedding-3-small           # api model, OR local model (default BAAI/bge-small-en-v1.5)
"""
from __future__ import annotations

import json
import math
import os
import sys
import traceback
import urllib.request
from typing import Any, Callable

_DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
_local_warned = False


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


def _local_embedder(model: str) -> Callable[[str], list[float]]:
    """On-device ONNX embedder via the optional ``fastembed`` package (no torch).

    Imported lazily so the dependency is only needed when local embeddings are enabled.
    The model is downloaded once on first use, then runs fully offline.
    """
    from fastembed import TextEmbedding  # optional dependency: pip install fastembed

    embedder = TextEmbedding(model_name=model or _DEFAULT_LOCAL_MODEL)

    def embed(text: str) -> list[float]:
        vecs = list(embedder.embed([str(text or "")[:8000]]))
        return [float(x) for x in vecs[0]] if vecs else []

    return embed


def build_embedder(config: dict[str, Any] | None) -> Callable[[str], list[float]] | None:
    """Return an embedder callable from config, or None when semantic recall is off.

    Returns None (→ bm25 fallback) unless ``embeddings.enabled`` is true and the chosen
    backend is usable. Never raises on config/availability problems — degrades to None.
    """
    global _local_warned
    try:
        cfg = config if isinstance(config, dict) else {}
        emb = cfg.get("embeddings", {}) if isinstance(cfg.get("embeddings", {}), dict) else {}
        if not emb.get("enabled"):
            return None
        backend = str(emb.get("backend") or "api").strip().lower()
        model = str(emb.get("model") or "").strip()

        if backend == "local":
            # Cheap availability probe (~0.4ms, no import) keeps the bm25 fallback
            # when fastembed isn't installed...
            import importlib.util
            if importlib.util.find_spec("fastembed") is None:
                if not _local_warned:
                    _local_warned = True
                    sys.stderr.write(
                        "[embeddings] local backend needs `pip install fastembed`; "
                        "falling back to keyword (bm25) recall.\n"
                    )
                return None
            # ...but DEFER the heavy fastembed import + ONNX model init (~1.2s for
            # bge-small) to the FIRST embed call. A startup that never touches
            # semantic recall no longer pays it. _embed_safe tolerates a [] return,
            # so a deferred build failure degrades to keyword recall.
            _state: dict[str, Any] = {}

            def _lazy_local(text: str) -> list[float]:
                fn = _state.get("fn")
                if fn is None:
                    try:
                        fn = _local_embedder(model)
                    except Exception:
                        traceback.print_exc()
                        fn = lambda _t: []
                    _state["fn"] = fn
                return fn(text)

            return _lazy_local

        # Default: OpenAI-compatible HTTP endpoint.
        base_url = str(emb.get("base_url") or "").strip()
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
