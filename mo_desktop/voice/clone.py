"""Optional voice clone for MO Desktop speech.

MO ships no voice of its own. A user who trained their own RVC voice points
``voice.clone_model`` (and optionally ``voice.clone_index``) at it; the voice
worker then converts every spoken sentence through a resident audio.cpp server
(``rvc`` family). The worker speaks with the plain voice until the clone has
loaded, so a slow first load never silences MO.

Everything below ``clone_settings`` runs inside the isolated voice worker and
uses only the standard library.
"""
from __future__ import annotations

import array
import base64
import hashlib
import io
import json
import os
import pickletools
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave
import zipfile
from pathlib import Path
from typing import Any, Mapping

CLONE_ENV = "MO_VOICE_CLONE"
BACKENDS = ("vulkan", "cuda", "cpu")
_LOAD_TIMEOUT_SECONDS = 600.0
_CONVERT_TIMEOUT_SECONDS = 60.0


def clone_settings(config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Resolve the configured clone for the worker, or ``None`` when it is off or incomplete."""
    from mo_desktop.voice.storage import (
        clone_base_model_path,
        clone_server_path,
        resolve_voice_install_root,
        voice_layout,
    )

    cfg = dict(config or {})
    model = Path(str(cfg.get("clone_model") or "").strip()).expanduser()
    if not str(model) or str(model) == "." or not model.is_absolute() or not model.is_file():
        return None
    index_raw = str(cfg.get("clone_index") or "").strip()
    index = Path(index_raw).expanduser() if index_raw else None
    if index is not None and not (index.is_absolute() and index.is_file()):
        index = None
    backend = str(cfg.get("clone_backend") or "vulkan").strip().lower()
    if backend not in BACKENDS:
        backend = "vulkan"
    try:
        pitch = max(-24, min(24, int(cfg.get("clone_pitch") or 0)))
    except (TypeError, ValueError):
        pitch = 0
    root = resolve_voice_install_root(cfg)
    server = clone_server_path(root, backend)
    base = clone_base_model_path(root)
    if server is None or not base.is_file():
        return None
    return {
        "model": str(model),
        "index": str(index) if index is not None else "",
        "pitch": pitch,
        "backend": backend,
        "server": str(server),
        "base": str(base),
        "cache": str(voice_layout(root)["clone_cache"]),
    }


# --- worker side: standard library only ---------------------------------------------------------

_BOOL_TO_INT = {"NEWTRUE": b"K\x01", "NEWFALSE": b"K\x00"}
_MEMO_OPS = {"BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE"}


def _patched_metadata(pickled: bytes) -> bytes | None:
    """Return the checkpoint metadata with a boolean ``f0`` stored as 0/1, or ``None``.

    Some trainers save ``f0`` as ``True``; audio.cpp reads it as an integer.
    """
    ops = list(pickletools.genops(pickled))
    for index, (op, arg, _pos) in enumerate(ops):
        if op.name not in {"BINUNICODE", "SHORT_BINUNICODE", "UNICODE"} or arg != "f0":
            continue
        following = index + 1
        while following < len(ops) and ops[following][0].name in _MEMO_OPS:
            following += 1
        if following >= len(ops) or ops[following][0].name not in _BOOL_TO_INT:
            return None
        name, pos = ops[following][0].name, ops[following][2]
        patched = bytearray(pickled[:pos] + _BOOL_TO_INT[name] + pickled[pos + 1:])
        frames = [entry for entry in ops if entry[0].name == "FRAME" and entry[2] < pos]
        if frames:  # protocol 4+ frames carry their length; this one grew by one byte
            frame_pos, frame_len = frames[-1][2], int(frames[-1][1])
            patched[frame_pos + 1:frame_pos + 9] = struct.pack("<Q", frame_len + 1)
        return bytes(patched)
    return None


def prepared_checkpoint(model: str | Path, cache: str | Path) -> Path:
    """Return a checkpoint audio.cpp can load: the original, or a cached fixed copy."""
    source = Path(model)
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        pkl = next((entry for entry in entries if entry.filename.endswith("data.pkl")), None)
        patched = _patched_metadata(archive.read(pkl)) if pkl is not None else None
        if patched is None:
            return source
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        target = Path(cache) / f"{source.stem}.{digest}.pth"
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED) as out:
            for entry in entries:
                data = patched if entry.filename == pkl.filename else archive.read(entry)
                out.writestr(zipfile.ZipInfo(entry.filename, entry.date_time), data)
    os.replace(partial, target)
    return target


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap mono 16-bit PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm)
    return buffer.getvalue()


def pcm_from_wav(data: bytes, sample_rate: int) -> bytes:
    """Decode a PCM16 or float32 WAV to mono 16-bit PCM at ``sample_rate``."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV payload")
    pos, fmt, samples = 12, None, None
    while pos + 8 <= len(data):
        tag, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif tag == b"data":
            samples = body
        pos += 8 + size + (size & 1)
    if fmt is None or samples is None:
        raise ValueError("WAV payload has no fmt or data chunk")
    kind, channels, rate, _byte_rate, _align, bits = fmt
    if kind == 3 and bits == 32:
        values = array.array("f", samples[: len(samples) // 4 * 4])
        ints = [max(-32768, min(32767, int(value * 32767.0))) for value in values]
    elif kind == 1 and bits == 16:
        ints = list(array.array("h", samples[: len(samples) // 2 * 2]))
    else:
        raise ValueError(f"unsupported WAV format {kind}/{bits}")
    if sys.byteorder != "little" and kind == 1:
        swapped = array.array("h", ints)
        swapped.byteswap()
        ints = list(swapped)
    if channels > 1:
        ints = [sum(ints[i:i + channels]) // channels for i in range(0, len(ints) - channels + 1, channels)]
    if rate != sample_rate and ints:
        step = rate / float(sample_rate)
        count = int(len(ints) / step)
        last = len(ints) - 1
        ints = [
            int(ints[min(last, int(i * step))] + (ints[min(last, int(i * step) + 1)] - ints[min(last, int(i * step))])
                * (i * step - int(i * step)))
            for i in range(count)
        ]
    return array.array("h", ints).tobytes()


def _bind_to_worker(process: subprocess.Popen[bytes]) -> Any:
    """On Windows, end the server with this worker even if the worker crashes."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Limits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Limits), ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(limits), ctypes.sizeof(limits))
    handle = kernel32.OpenProcess(0x0101, False, process.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if handle:
        kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle))
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    return job  # the handle stays open for the worker's lifetime


class CloneServer:
    """One resident audio.cpp RVC server owned by the voice worker."""

    def __init__(self, settings: Mapping[str, Any], *, sample_rate: int) -> None:
        self._settings = dict(settings)
        self._sample_rate = int(sample_rate)
        self._process: subprocess.Popen[bytes] | None = None
        self._job: Any = None
        self._url = ""
        self.state = "off"  # off -> loading -> ready | failed
        self.error = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def start(self, on_state: Any = None) -> None:
        """Load in the background; the plain voice keeps speaking meanwhile."""
        self.state = "loading"

        def _load() -> None:
            try:
                self._launch()
                self._convert_or_raise(b"\x00\x00" * (self._sample_rate // 2))  # load the voice now
                self.state = "ready"
            except Exception as exc:
                self.error = type(exc).__name__ + (f": {exc}" if str(exc) else "")
                self.state = "failed"
                self.close()
            if on_state is not None:
                on_state(self.state, self.error)

        threading.Thread(target=_load, name="mo-voice-clone-load", daemon=True).start()

    def _launch(self) -> None:
        settings = self._settings
        cache = Path(settings["cache"])
        cache.mkdir(parents=True, exist_ok=True)
        model = prepared_checkpoint(settings["model"], cache)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        options = {
            "voice_model_path": str(model),
            "semitone_shift": str(int(settings.get("pitch") or 0)),
            "output_sample_rate": str(self._sample_rate),
        }
        if settings.get("index"):
            options.update({"retrieval_index_path": settings["index"], "retrieval_blend": "1.0"})
        config = {
            "host": "127.0.0.1", "port": port, "backend": settings["backend"], "device": 0, "threads": 4,
            "lazy_load": False, "log_request_body": False,
            "models": [{
                "id": "clone", "family": "rvc", "path": settings["base"], "task": "vc", "mode": "offline",
                "default_request_options": options,
            }],
        }
        config_path = cache / "server.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        flags = 0x08000000 | 0x00004000 if sys.platform == "win32" else 0  # no window, below-normal priority
        self._process = subprocess.Popen(
            [settings["server"], "--config", str(config_path), "--no-ui"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(Path(settings["server"]).parent), creationflags=flags,
        )
        self._job = _bind_to_worker(self._process)
        self._url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + _LOAD_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"clone server exited with {self._process.returncode}")
            try:
                with urllib.request.urlopen(self._url + "/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.5)
        raise TimeoutError("clone server did not start")

    def _convert_or_raise(self, pcm: bytes) -> bytes:
        # A server-local WAV path works on every audio.cpp release; inline audio
        # on this route is newer than the builds users have installed.
        source = Path(self._settings["cache"]) / f"sentence-{os.getpid()}-{threading.get_ident()}.wav"
        source.write_bytes(wav_bytes(pcm, self._sample_rate))
        try:
            body = json.dumps({"model": "clone", "request": {"audio": str(source)}}).encode("utf-8")
            request = urllib.request.Request(
                self._url + "/v1/tasks/run", data=body, headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=_CONVERT_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            source.unlink(missing_ok=True)
        return pcm_from_wav(base64.b64decode(payload["audio"]), self._sample_rate)

    def convert(self, pcm: bytes) -> bytes | None:
        """Speak ``pcm`` in the clone's voice; ``None`` keeps the plain voice for this sentence."""
        if not self.ready or not pcm:
            return None
        try:
            return self._convert_or_raise(pcm)
        except Exception as exc:
            self.error = type(exc).__name__
            if self._process is not None and self._process.poll() is not None:
                self.state = "failed"
            return None

    def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.state != "failed":
            self.state = "off"
