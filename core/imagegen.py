"""MO image generation — a provider-agnostic, backend-selected asset producer.

This is a GENERATOR (a file-producing tool, the sibling of ``write``), NOT a
provider capability. MO's vision — seeing images — lives in ``core/perception``
and the provider layer; generation deliberately touches neither, so the model MO
reasons with and the backend that paints pixels stay independently swappable.

Backends resolve cheapest-credential-first. Everything imported at module top is
stdlib, so gating the tool at Agent init stays light; each backend imports its
transport lazily on first use:

  - ``codex``             OpenAI ``gpt-image-2`` via the local Codex CLI OAuth
                          bridge (spends the ChatGPT subscription quota, no API
                          key). No new dependency.
  - ``openai_compatible`` ``POST {base_url}/images/generations`` with an API key
                          — OpenAI, Azure, or any compatible endpoint. Uses
                          stdlib ``urllib`` (no new dependency).

The tool is ABSENT when no backend resolves (see ``Agent._init_tools``): a
checkout with neither a Codex login nor an image API key never exposes
``generate_image`` — no dead tool in the spec, no runtime error to discover.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import shutil
from pathlib import Path

DEFAULT_MODEL = "gpt-image-2"


def _is_cancelled(cancel_event) -> bool:
    try:
        return bool(cancel_event is not None and cancel_event.is_set())
    except Exception:
        return False


def _cancelled_result(backend: str) -> dict:
    return {"ok": False, "error": "image generation cancelled", "backend": backend}


def _image_cfg(config: dict | None) -> dict:
    cfg = (config or {}).get("image")
    return cfg if isinstance(cfg, dict) else {}


def _codex_available() -> bool:
    """True only when the Codex CLI binary AND its OAuth credentials are present."""
    if not shutil.which("codex"):
        return False
    try:
        from core.state.paths import codex_auth_path

        return Path(codex_auth_path(None)).expanduser().is_file()
    except Exception:
        return False


def _openai_key(image_cfg: dict, config: dict | None = None) -> str:
    env = image_cfg.get("api_key_env")
    from core.state.secrets import resolve_secret

    return (
        resolve_secret(str(env), config=config, service="providers").strip()
        if env else ""
    )


def resolve_backend(config: dict | None = None) -> dict | None:
    """Pick the active image backend, cheapest-credential-first, or ``None``.

    ``image.backend`` chooses explicitly; ``auto`` (default) prefers the no-key
    Codex bridge, then an OpenAI-compatible key; ``off`` disables generation.
    Returns a descriptor the executor dispatches on, or ``None`` when nothing is
    configured/available — in which case the tool is never exposed.
    """
    cfg = _image_cfg(config)
    choice = str(cfg.get("backend") or DEFAULT_PREFERENCES["image.backend"]).strip().lower()
    model = str(cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    if choice == "off":
        return None

    def _codex() -> dict | None:
        return {"kind": "codex", "model": model} if _codex_available() else None

    def _openai() -> dict | None:
        key = _openai_key(cfg, config)
        if not key:
            return None
        base_url = str(cfg.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        return {"kind": "openai_compatible", "model": model, "base_url": base_url, "api_key": key}

    if choice == "codex":
        return _codex()
    if choice in ("openai_compatible", "openai", "api"):
        return _openai()
    return _codex() or _openai()  # auto


def available(config: dict | None = None) -> bool:
    """Whether any image backend resolves — the gate for exposing the tool."""
    return resolve_backend(config) is not None


def backend_label(config: dict | None = None) -> str:
    b = resolve_backend(config)
    return b["kind"] if b else "none"


def generate(
    prompt: str,
    out_path: str,
    *,
    size: str = "1024x1024",
    config: dict | None = None,
    cancel_event=None,
) -> dict:
    """Generate one image to ``out_path`` with cooperative cancellation.

    Returns ``{ok, path, backend, error}``. Never raises for a backend/transport
    failure — it degrades to ``ok=False`` with an actionable ``error`` so the
    tool reports honestly instead of crashing the turn.
    """
    backend = resolve_backend(config)
    if backend is None:
        return {"ok": False, "error": "no image backend configured", "backend": None}
    if _is_cancelled(cancel_event):
        return _cancelled_result(backend["kind"])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if backend["kind"] == "openai_compatible":
        return _generate_openai(backend, prompt, out, size, cancel_event=cancel_event)
    if backend["kind"] == "codex":
        return _generate_codex(backend, prompt, out, size, cancel_event=cancel_event)
    return {"ok": False, "error": f"unknown backend {backend['kind']}", "backend": backend["kind"]}


def _generate_openai(
    backend: dict, prompt: str, out: Path, size: str, *, cancel_event=None,
) -> dict:
    """Deterministic ``/images/generations`` call via stdlib urllib (no new dep)."""
    import base64
    import json
    import urllib.request

    if _is_cancelled(cancel_event):
        return _cancelled_result("openai_compatible")
    url = f"{backend['base_url']}/images/generations"
    body = json.dumps(
        {"model": backend["model"], "prompt": prompt, "size": size, "n": 1}
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {backend['api_key']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:  # network/HTTP/JSON — report, don't crash
        return {"ok": False, "error": f"image API request failed: {exc}", "backend": "openai_compatible"}
    if _is_cancelled(cancel_event):
        return _cancelled_result("openai_compatible")

    data = (payload.get("data") or [{}])[0]
    b64 = data.get("b64_json")
    if b64:
        try:
            decoded = base64.b64decode(b64)
            if _is_cancelled(cancel_event):
                return _cancelled_result("openai_compatible")
            out.write_bytes(decoded)
        except Exception as exc:
            return {"ok": False, "error": f"image decode/write failed: {exc}", "backend": "openai_compatible"}
        if _is_cancelled(cancel_event):
            out.unlink(missing_ok=True)
            return _cancelled_result("openai_compatible")
        return {"ok": True, "path": str(out), "backend": "openai_compatible"}

    img_url = data.get("url")
    if img_url:
        try:
            with urllib.request.urlopen(img_url, timeout=180) as resp:
                decoded = resp.read()
            if _is_cancelled(cancel_event):
                return _cancelled_result("openai_compatible")
            out.write_bytes(decoded)
        except Exception as exc:
            return {"ok": False, "error": f"image download failed: {exc}", "backend": "openai_compatible"}
        if _is_cancelled(cancel_event):
            out.unlink(missing_ok=True)
            return _cancelled_result("openai_compatible")
        return {"ok": True, "path": str(out), "backend": "openai_compatible"}

    return {"ok": False, "error": "image API returned no image data", "backend": "openai_compatible"}


def _generate_codex(
    backend: dict, prompt: str, out: Path, size: str, *, cancel_event=None,
) -> dict:
    """Best-effort cancellable Codex CLI bridge to gpt-image-2."""
    import subprocess
    import time

    from .runtime.subprocess_flags import apply_windows_hidden_process_flags

    exe = shutil.which("codex")
    if not exe:
        return {"ok": False, "error": "codex CLI not found on PATH", "backend": "codex"}
    if _is_cancelled(cancel_event):
        return _cancelled_result("codex")
    instruction = (
        f"Generate an image and save it as '{out.name}' in the current working "
        f"directory using the image generation tool ($imagegen). Target size "
        f"{size}. Do not ask any questions. Image description: {prompt}"
    )
    pre_mtime = out.stat().st_mtime_ns if out.is_file() else -1
    proc = None
    stdout = ""
    stderr = ""
    try:
        run_kwargs = {
            "cwd": str(out.parent),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            # Codex emits UTF-8; decoding with the platform default (cp1252 on
            # Windows) crashes the subprocess reader threads on non-latin bytes.
            "encoding": "utf-8",
            "errors": "replace",
        }
        apply_windows_hidden_process_flags(run_kwargs)
        proc = subprocess.Popen(
            [
                exe, "exec", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "workspace-write", instruction,
            ],
            **run_kwargs,
        )
        deadline = time.monotonic() + 600
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if _is_cancelled(cancel_event):
                    proc.terminate()
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate()
                    if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
                        out.unlink(missing_ok=True)
                    return _cancelled_result("codex")
                if time.monotonic() >= deadline:
                    proc.terminate()
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate()
                    return {"ok": False, "error": "codex image generation timed out (>10m)", "backend": "codex"}
    except Exception as exc:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        return {"ok": False, "error": f"codex invocation failed: {exc}", "backend": "codex"}

    if _is_cancelled(cancel_event):
        if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
            out.unlink(missing_ok=True)
        return _cancelled_result("codex")
    if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
        return {"ok": True, "path": str(out), "backend": "codex"}

    tail = ((stderr or "") + (stdout or "")).strip()[-300:]
    return {"ok": False, "error": f"codex produced no image file. {tail}".strip(), "backend": "codex"}
