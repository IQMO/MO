"""Private, session-bound media jobs: submit once, resume, download, review cleanup.

Provider task IDs and private request settings survive restart. Capability URLs
do not. A failed/uncertain submission is never silently repeated. Polling and
local preparation run in the caller's worker, never on the Desktop GUI thread.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from core.runtime.lock import file_byte_lock
from core.state.paths import resolve_state_path
from core.utils.atomic_write import atomic_write_json
from core.utils.file_hash import file_sha256
from .catalog import PRIVACY_NOTICE, build_payload, default_model, settings
from . import kie

_LOCK = threading.RLock()
_POLL_LOCKS: dict[str, threading.Lock] = {}
_ID = re.compile(r"^[a-f0-9]{32}$")
_FINISHED = {"ready", "failed", "rejected", "preparation_failed", "submission_unknown"}


def _root(config: dict) -> Path:
    return Path(resolve_state_path("memory/media/jobs", config))


@contextmanager
def _locked(config: dict):
    with file_byte_lock(_root(config) / ".lock", _LOCK):
        yield


def _path(config: dict, job_id: str) -> Path:
    if not _ID.fullmatch(str(job_id)):
        raise ValueError("An exact MO media job ID is required.")
    return _root(config) / (job_id + ".json")


def _load(config: dict, job_id: str, session_id: str) -> dict:
    row = json.loads(_path(config, job_id).read_text(encoding="utf-8"))
    if row.get("session_id") != session_id:
        raise ValueError("This media job belongs to another conversation.")
    return row


def _save(config: dict, row: dict):
    row["updated_at"] = time.time()
    atomic_write_json(_path(config, row["id"]), row, indent=2, ensure_ascii=False)


def _public(row: dict) -> dict:
    from .references import lease_status

    return {key: row.get(key) for key in (
        "id", "operation", "model", "state", "created_at", "updated_at", "task_id", "outputs",
        "parent_id", "error", "credits_consumed", "reference_count", "references_revoked_at",
    )} | {"references": lease_status(row["id"]), "provider": "Kie", "remaining_time": "unavailable"}


def recent(config: dict, session_id: str) -> list[dict]:
    root = _root(config)
    if not root.is_dir():
        return []
    rows = []
    with _locked(config):
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if row.get("session_id") == session_id:
                    rows.append(_public(row))
            except (OSError, ValueError):
                continue
            if len(rows) == 20:
                break
    return rows


def status(config: dict, job_id: str, session_id: str) -> dict:
    with _locked(config):
        return _public(_load(config, job_id, session_id))


def _activity(callback, stage: str, started: float):
    if callable(callback):
        callback(f"media: {max(0, int(time.time() - started))}s elapsed · {stage} · ETA unavailable")


def create(config: dict, session_id: str, turn_id: str, *, operation: str, model: str = "",
           options: dict | None = None, references: list[dict] | None = None,
           parent_id: str = "", output_index: int = 0, on_activity=None, cancel=None) -> dict:
    from .preparation import final_frame, kind_for, require_tools
    from .references import ReferenceLease, revoke

    if settings(config).get("enabled") is not True:
        raise ValueError("Media creation is off. Enable Kie generation in Settings → Models & providers → Generate provider.")
    if not session_id or not turn_id:
        raise ValueError("Media generation requires a live conversation and request identity.")
    kie.check_network(config, kie.API_ORIGIN)
    kie.require_credential(config)  # No reference sharing before account setup.
    model = model or default_model(operation, config)
    if options is not None and not isinstance(options, dict):
        raise ValueError("Media options must be an object.")
    options = dict(options or {})
    refs = [dict(ref) for ref in (references or [])]
    if len(refs) > 50:
        raise ValueError("Too many media references.")
    for ref in refs:
        path = Path(str(ref.get("path") or "")).expanduser().resolve(strict=True)
        ref["path"] = str(path)
        ref["kind"] = kind_for(path)
        ref.pop("url", None)
    require_tools(operation, refs)
    # Parent selection and cleanup use the same lock; originals are never purge targets.
    parent_path = None
    with _locked(config):
        if operation in {"extend_music", "extend_video"}:
            if not parent_id:
                raise ValueError("Choose an exact parent media job and output.")
            parent = _load(config, parent_id, session_id)
            outputs = parent.get("outputs", [])
            if isinstance(output_index, bool) or not isinstance(output_index, int) or output_index < 0:
                raise ValueError("Choose the exact parent variation to extend.")
            source = next((item for item in outputs if item.get("index") == output_index), None)
            expected_kind = "audio" if operation == "extend_music" else "video"
            if source is None or source.get("kind") != expected_kind:
                raise ValueError("Choose a saved result of the correct media type to extend.")
            parent_path = Path(source["path"])
            if not parent_path.is_file() or file_sha256(parent_path) != source["sha256"]:
                raise ValueError("The parent output changed or was removed; no continuation was submitted.")
            if operation == "extend_music":
                if parent["model"] != model or not source.get("track_id"):
                    raise ValueError("Music continuation needs a returned track ID and the parent's model.")
                if "continue_at" in options:
                    from .catalog import _number
                    _number(options["continue_at"], 0, source.get("duration", 0), "continue_at")
                    if not 0 < options["continue_at"] < source.get("duration", 0):
                        raise ValueError("continue_at must be greater than zero and before the track ends.")
                options["audio_id"] = source["track_id"]
        fingerprint = hashlib.sha256(json.dumps(
            [session_id, turn_id, operation, model, options, refs, parent_id, output_index], sort_keys=True).encode()).hexdigest()
        for path in _root(config).glob("*.json"):
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if previous.get("fingerprint") == fingerprint:
                return _public(previous)
        # Validate options before copying or exposing references. A continuation
        # frame's placeholder carries the same role as the real decoded frame.
        validation_refs = [{**r, "url": "https://reference.invalid/input"} for r in refs]
        if operation == "extend_video":
            validation_refs.insert(0, {"kind": "image", "role": "reference" if refs else "first_frame",
                                       "url": "https://reference.invalid/frame"})
        build_payload(operation, model, options, validation_refs)
        row = {"id": uuid.uuid4().hex, "fingerprint": fingerprint, "session_id": session_id,
               "turn_id": turn_id, "operation": operation, "model": model, "options": options,
               "references": [{"path": r["path"], "role": r.get("role", "reference")} for r in refs],
               "reference_count": len(validation_refs), "parent_id": parent_id, "parent_output": output_index,
               "state": "preparing", "created_at": time.time(), "outputs": [], "task_id": ""}
        _save(config, row)
    temporary = None
    try:
        _activity(on_activity, "preparing selected references" if validation_refs else "preparing request", row["created_at"])
        if operation == "extend_video":
            stage_root = Path(resolve_state_path("run/media", config))
            stage_root.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.TemporaryDirectory(prefix="continuation-", dir=stage_root)
            frame = Path(temporary.name) / "frame.png"
            final_frame(parent_path, frame)
            refs.insert(0, {"path": str(frame), "kind": "image", "role": "reference" if refs else "first_frame"})
        urls = []
        if refs:
            _activity(on_activity, "sharing only prepared references temporarily", row["created_at"])
            lease = ReferenceLease(config, row["id"], refs, operation, model)
            urls = lease.urls(cancel)
        payload = build_payload(operation, model, options, urls)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Stopped before submission; no generation was requested.")
        with _locked(config):
            row["state"] = "submitting"
            row["error"] = "Submission is pending or was interrupted. Without a verified task ID, check Kie history before another paid request."
            _save(config, row)  # intent precedes the single billable HTTP attempt
        _activity(on_activity, "submitting to Kie", row["created_at"])
        response = kie.request(config, "/api/v1/jobs/createTask", payload)
        task_id = response.get("taskId") if isinstance(response, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", task_id):
            raise kie.ProviderError("Submission outcome is unknown; check Kie history before starting another job.", uncertain=True)
        with _locked(config):
            row.update(task_id=task_id, state="waiting")
            row.pop("error", None)
            _save(config, row)
        return _public(row)
    except (OSError, ValueError, kie.ProviderError) as exc:
        uncertain = row["state"] == "submitting" and not (isinstance(exc, kie.ProviderError) and not exc.uncertain)
        row["state"] = "submission_unknown" if uncertain else "rejected" if row["state"] == "submitting" else "preparation_failed"
        # Never serialize raw network/decoder exceptions or access-bearing URLs.
        row["error"] = str(exc) if isinstance(exc, (ValueError, kie.ProviderError, InterruptedError)) else "Local media preparation or persistence failed."
        with _locked(config):
            _save(config, row)
        if not uncertain:
            revoke(row["id"])
        return _public(row)
    finally:
        if temporary:
            temporary.cleanup()


def _result_items(data: dict, operation: str) -> list[dict]:
    """Accept documented URL results and music track records, never recursive URL scraping."""
    value = data.get("resultJson")
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise ValueError("Provider result has no recognized media payload.")
    music = operation in {"music", "cover", "extend_music"}
    # Live unified Suno polling uses {code: 200, data: [audio records]}.
    # Generic marketplace operations may instead return resultObject/URLs.
    tracks = value.get("data") if value.get("code") == 200 else value.get("resultObject")
    if isinstance(tracks, dict):
        tracks = tracks.get("data")
    if music and isinstance(tracks, list):
        items = [{"url": t["audio_url"], "track_id": t.get("id", "")}
                 for t in tracks if isinstance(t, dict) and isinstance(t.get("audio_url"), str)]
    else:
        urls = value.get("resultUrls")
        items = [{"url": url, "track_id": ""} for url in urls if isinstance(url, str)] if isinstance(urls, list) else []
    if not 1 <= len(items) <= 10:
        raise ValueError("Provider returned no supported media results; the task will not be resubmitted.")
    for item in items:
        track_id = item["track_id"]
        if not isinstance(track_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", track_id):
            item["track_id"] = ""  # Never persist an access-bearing URL/payload as an ID.
    return items


def wait(config: dict, job_id: str, session_id: str, *, seconds: int = 60, on_activity=None, cancel=None) -> dict:
    """Bounded status/download work; repeated waits never create another job."""
    from .preparation import probe
    from .references import revoke

    deadline = time.monotonic() + max(0, min(int(seconds), 60))
    # The per-job lease serializes download/resume, without blocking other jobs
    # or holding the metadata/cleanup lock across provider IO.
    lock_path = _path(config, job_id).with_suffix(".poll.lock")
    with _LOCK:
        poll_lock = _POLL_LOCKS.setdefault(str(lock_path), threading.Lock())
    with file_byte_lock(lock_path, poll_lock):
        with _locked(config):
            row = _load(config, job_id, session_id)
        if row["state"] in _FINISHED or not row.get("task_id"):
            return _public(row)
        while True:
            if cancel is not None and cancel.is_set():
                return _public(row) | {"waiting_stopped": True, "provider_cancelled": False}
            try:
                data = kie.task(config, row["task_id"])
                provider_state = data.get("state")
                if provider_state not in {"waiting", "queuing", "generating", "success", "fail"}:
                    raise ValueError("Unrecognized provider task state; no resubmission.")
                row["state"] = "failed" if provider_state == "fail" else "downloading" if provider_state == "success" else provider_state
                row.pop("error", None)
                consumed = data.get("creditsConsumed")
                if isinstance(consumed, (int, float)) and not isinstance(consumed, bool) and math.isfinite(consumed) and consumed >= 0:
                    row["credits_consumed"] = consumed
                if provider_state == "fail":
                    row["error"] = "Kie reported generation failure. No automatic paid retry."
                if provider_state in {"success", "fail"}:
                    revoke(job_id)
                    row["references_revoked_at"] = time.time()
                if provider_state == "success":
                    outputs = _result_items(data, row["operation"])
                    kind = "audio" if row["operation"] in {"music", "cover", "extend_music"} else "video" if "video" in row["operation"] else "image"
                    root = Path(resolve_state_path(f"media/generated/{job_id}", config))
                    root.mkdir(parents=True, exist_ok=True)
                    for index, output in enumerate(outputs):
                        # Saved outputs are immutable. Missing/changed results are
                        # re-downloaded into a new file, never overwrite user edits.
                        prior = next((o for o in row["outputs"] if o["index"] == index), None)
                        if prior and Path(prior["path"]).is_file() and file_sha256(prior["path"]) == prior["sha256"]:
                            continue
                        _activity(on_activity, f"downloading {kind} {index + 1}/{len(outputs)}", row["created_at"])
                        from urllib.parse import urlsplit

                        suffix = Path(urlsplit(output["url"]).path).suffix.lower()
                        allowed = {"image": {".jpg", ".jpeg", ".png", ".webp"}, "video": {".mp4", ".mov"}, "audio": {".mp3", ".wav", ".m4a"}}
                        if suffix not in allowed[kind]:
                            suffix = {"image": ".png", "video": ".mp4", "audio": ".mp3"}[kind]
                        target = root / f"result-{index + 1}-{uuid.uuid4().hex[:10]}{suffix}"
                        try:
                            kie.download(output["url"], target, config=config, max_bytes=500 * 1024 * 1024, cancel=cancel)
                            info = probe(target)
                            if info["kind"] != kind:
                                raise ValueError("Downloaded media does not match the requested output type.")
                        except BaseException:
                            target.unlink(missing_ok=True)
                            raise
                        record = {"index": index, "path": str(target), "sha256": file_sha256(target),
                                  "track_id": output["track_id"], **info}
                        row["outputs"] = [o for o in row["outputs"] if o["index"] != index] + [record]
                        with _locked(config):
                            _save(config, row)
                    row["state"] = "ready"
                with _locked(config):
                    _save(config, row)
            except (OSError, ValueError, kie.ProviderError) as exc:
                row["error"] = str(exc) if isinstance(exc, (ValueError, kie.ProviderError, InterruptedError)) else "Status or download unavailable; resume this job, do not submit again."
                with _locked(config):
                    _save(config, row)
                return _public(row)
            _activity(on_activity, row["state"], row["created_at"])
            if row["state"] in _FINISHED or time.monotonic() >= deadline:
                return _public(row)
            delay = min(5, max(0, deadline - time.monotonic()))
            if cancel is not None:
                cancel.wait(delay)
            else:
                time.sleep(delay)


def review_cleanup(config: dict, job_id: str, session_id: str) -> dict:
    """Review local reference revocation only. Saved results/originals are not targets."""
    with _locked(config):
        row = _load(config, job_id, session_id)
        return _cleanup_review(row)


def _cleanup_review(row: dict) -> dict:
    receipt = hashlib.sha256(json.dumps([row["id"], row["updated_at"], row["state"]]).encode()).hexdigest()
    return {"job_id": row["id"], "created_at": row["created_at"], "receipt": receipt,
            "outputs_kept": [Path(o["path"]).name for o in row["outputs"]],
            "can_revoke": row["state"] in _FINISHED,
            "effect": "Revoke local reference links and remove prepared copies only. Saved outputs and originals remain available for continuation. Provider deletion is not verified."}


def confirm_cleanup(config: dict, job_id: str, session_id: str, receipt: str) -> dict:
    from .references import revoke

    with _locked(config):
        row = _load(config, job_id, session_id)
        review = _cleanup_review(row)
        if review["receipt"] != receipt or not review["can_revoke"]:
            raise ValueError("Cleanup review changed or the job still needs its references; review again.")
        revoke(job_id)
        row["references_revoked_at"] = time.time()
        _save(config, row)
        return _public(row)
