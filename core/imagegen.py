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


def _requested_dimensions(size: str) -> tuple[int, int] | None:
    parts = str(size or "").lower().split("x", 1)
    try:
        dimensions = (int(parts[0]), int(parts[1])) if len(parts) == 2 else None
    except ValueError:
        return None
    if dimensions and dimensions[0] > 0 and dimensions[1] > 0:
        return dimensions
    return None


def _png_artifact_issue(path: Path, expected_size: str) -> str | None:
    """Return why ``path`` is not the requested complete PNG, or ``None``.

    Image generation remains available without Pillow, so this boundary check
    uses the fixed PNG signature/IHDR/IEND structure instead of importing an
    optional renderer.  It catches the important custody failures (including a
    zero-byte placeholder) before a backend can report success.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
            if (
                len(header) != 24
                or header[:8] != b"\x89PNG\r\n\x1a\n"
                or header[12:16] != b"IHDR"
            ):
                return "an unreadable or incomplete PNG"
            width = int.from_bytes(header[16:20], "big")
            height = int.from_bytes(header[20:24], "big")
            if width <= 0 or height <= 0:
                return "a PNG with invalid dimensions"
            handle.seek(-12, 2)
            if handle.read(12) != b"\x00\x00\x00\x00IEND\xaeB`\x82":
                return "an incomplete PNG"
    except (OSError, ValueError):
        return "an unreadable or incomplete PNG"

    requested = _requested_dimensions(expected_size)
    if requested and requested != (width, height):
        return f"a {width}x{height} PNG instead of the requested {requested[0]}x{requested[1]}"
    return None


def _normalize_png_artifact(path: Path, expected_size: str) -> tuple[str | None, str]:
    """Validate a generated PNG and normalize a backend size mismatch.

    Backends occasionally return a nearby supported resolution despite an exact
    request.  Keep that transport result only after the existing image-edit
    owner has resized it through a staged file and the staged PNG passes the
    same custody check.  A different aspect ratio is centre-cropped to the
    requested one first, so the resize scales uniformly instead of stretching
    the picture, and the returned note says so.  Pillow remains optional:
    without it, the mismatch is reported honestly instead of weakening the
    requested-size contract.  Returns ``(issue, note)``.
    """
    structural_issue = _png_artifact_issue(path, "")
    if structural_issue:
        return structural_issue, ""

    requested = _requested_dimensions(expected_size)
    mismatch = _png_artifact_issue(path, expected_size)
    if not mismatch or requested is None:
        return mismatch, ""

    note = ""
    try:
        from . import imageedit

        if not imageedit.available():
            return f"{mismatch}; image resizing is unavailable", ""
        import os
        import tempfile

        from PIL import Image

        requested_width, requested_height = requested
        with Image.open(path) as generated:
            width, height = generated.size
        staged_files: list[Path] = []

        def stage(label: str) -> Path:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{path.stem}-{label}-", suffix=".png", dir=str(path.parent),
            )
            os.close(descriptor)
            staged_files.append(Path(name))
            return staged_files[-1]

        try:
            source = path
            if abs(width * requested_height - height * requested_width) > 0.01 * width * requested_height:
                crop_width = min(width, round(height * requested_width / requested_height))
                crop_height = min(height, round(width * requested_height / requested_width))
                source = stage("cropped")
                imageedit.crop(
                    str(path),
                    x=(width - crop_width) // 2,
                    y=(height - crop_height) // 2,
                    width=crop_width,
                    height=crop_height,
                    out=str(source),
                )
                note = (
                    f"centre-cropped the backend's {width}x{height} PNG to "
                    f"{crop_width}x{crop_height} to keep the requested aspect"
                )
            staged = stage("normalized")
            imageedit.resize(
                str(source),
                width=requested_width,
                height=requested_height,
                out=str(staged),
            )
            normalized_issue = _png_artifact_issue(staged, expected_size)
            if normalized_issue:
                return f"{mismatch}; resized output was {normalized_issue}", ""
            staged.replace(path)
        finally:
            for staged_file in staged_files:
                staged_file.unlink(missing_ok=True)
    except Exception as exc:
        return f"{mismatch}; resize failed: {exc}", ""
    return None, note


def _generated(path: Path, backend: str, note: str) -> dict:
    result = {"ok": True, "path": str(path), "backend": backend}
    if note:
        result["normalized"] = note
    return result


def _is_cancelled(cancel_event) -> bool:
    try:
        return bool(cancel_event is not None and cancel_event.is_set())
    except Exception:
        return False


def _cancelled_result(backend: str) -> dict:
    return {"ok": False, "error": "image generation cancelled", "backend": backend}


def _codex_generated_images(stdout: str) -> list[Path]:
    """Return PNGs owned by the JSONL Codex thread in ``stdout``.

    Codex stores image-tool results under its own thread-specific cache.  The
    thread ID makes retrieval concurrency-safe: images from another terminal or
    Codex session are never candidates.
    """
    import json
    import os
    import re

    thread_id = ""
    for line in str(stdout or "").splitlines():
        if "thread.started" not in line:
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if event.get("type") != "thread.started":
            continue
        thread = event.get("thread") if isinstance(event.get("thread"), dict) else {}
        thread_id = str(event.get("thread_id") or thread.get("id") or "").strip()
        break
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
        return []

    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    codex_home = Path(configured).expanduser() if configured else Path.home() / ".codex"
    owned = codex_home / "generated_images" / thread_id
    try:
        return sorted(path for path in owned.glob("*.png") if path.is_file())
    except OSError:
        return []


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
        issue, note = _normalize_png_artifact(out, size)
        if issue:
            out.unlink(missing_ok=True)
            return {"ok": False, "error": f"image API produced {issue}", "backend": "openai_compatible"}
        return _generated(out, "openai_compatible", note)

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
        issue, note = _normalize_png_artifact(out, size)
        if issue:
            out.unlink(missing_ok=True)
            return {"ok": False, "error": f"image API produced {issue}", "backend": "openai_compatible"}
        return _generated(out, "openai_compatible", note)

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
        f"Call the image generation tool ($imagegen) exactly once to generate one "
        f"brand-new PNG. Do not retry, create variants, ask questions, reuse an "
        f"earlier image, reinterpret the image description, or use shell/Python "
        f"commands to copy or re-encode the result; the caller retrieves the image "
        f"artifact directly from this Codex thread. Pass the image description below "
        f"verbatim to $imagegen. Target size {size}. Image description (pass "
        f"verbatim): {prompt}"
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
                exe, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
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
                    if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
                        out.unlink(missing_ok=True)
                    return {"ok": False, "error": "codex image generation timed out (>10m)", "backend": "codex"}
    except Exception as exc:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
            out.unlink(missing_ok=True)
        return {"ok": False, "error": f"codex invocation failed: {exc}", "backend": "codex"}
    if _is_cancelled(cancel_event):
        if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
            out.unlink(missing_ok=True)
        return _cancelled_result("codex")

    generated = _codex_generated_images(stdout)
    if len(generated) > 1:
        if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
            out.unlink(missing_ok=True)
        return {
            "ok": False,
            "error": f"codex generated {len(generated)} images in one thread; expected exactly one",
            "backend": "codex",
        }
    if len(generated) == 1:
        try:
            shutil.copyfile(generated[0], out)
        except OSError as exc:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "error": f"codex image custody failed: {exc}", "backend": "codex"}

    if out.is_file() and out.stat().st_mtime_ns > pre_mtime:
        issue, note = _normalize_png_artifact(out, size)
        if issue:
            out.unlink(missing_ok=True)
            return {"ok": False, "error": f"codex produced {issue}", "backend": "codex"}
        return _generated(out, "codex", note)

    tail = ((stderr or "") + (stdout or "")).strip()[-300:]
    return {"ok": False, "error": f"codex produced no image file. {tail}".strip(), "backend": "codex"}
