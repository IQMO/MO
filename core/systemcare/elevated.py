"""One requested Windows permission boundary, reusing the SystemCare service."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from typing import Any


def run_elevated(config: dict[str, Any], operation: str, arguments: dict[str, Any], *, cancel_event: Any = None,
                 progress: Any = None) -> Any:
    if os.name != "nt" or operation not in {"apply", "inspect", "restore"}:
        raise ValueError("Unknown Windows permission request")
    from core.state.paths import mo_home, repo_root, runtime_config_path, resolve_state_path
    from core.utils.atomic_write import atomic_write_json
    request_id = uuid.uuid4().hex
    root = Path(resolve_state_path("run/systemcare", config))
    root.mkdir(parents=True, exist_ok=True)
    request, response = root / (request_id + ".request.json"), root / (request_id + ".response.json")
    progress_path = root / (request_id + ".progress.json")
    atomic_write_json(request, {"operation": operation, "arguments": arguments,
                              "config_path": str(runtime_config_path(config, fallback_to_default=True) or "")})
    class ExecuteInfo(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("mask", ctypes.c_ulong), ("window", ctypes.c_void_p),
                    ("verb", ctypes.c_wchar_p), ("file", ctypes.c_wchar_p), ("parameters", ctypes.c_wchar_p),
                    ("directory", ctypes.c_wchar_p), ("show", ctypes.c_int), ("instance", ctypes.c_void_p),
                    ("id_list", ctypes.c_void_p), ("class_name", ctypes.c_wchar_p), ("class_key", ctypes.c_void_p),
                    ("hot_key", ctypes.c_ulong), ("icon", ctypes.c_void_p), ("process", ctypes.c_void_p)]
    info = ExecuteInfo()
    info.size, info.mask, info.show = ctypes.sizeof(info), 0x40 | 0x400, 0
    info.verb, info.file = "runas", sys.executable
    info.parameters = subprocess.list2cmdline(["-m", "core.systemcare.elevated", "--home", str(mo_home(config)), "--request", request_id])
    info.directory = repo_root()
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = (ctypes.POINTER(ExecuteInfo),)
    shell.ShellExecuteExW.restype = ctypes.c_bool
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.GetProcessId.argtypes = (ctypes.c_void_p,)
    kernel.GetProcessId.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    finished = False
    last_sequence = 0
    cancel_state = None
    cancel_sent = False
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)) or not info.process:
            finished = True
            raise RuntimeError("Windows permission was declined or the protected operation could not start")
        worker_pid = kernel.GetProcessId(info.process)
        deadline = time.monotonic() + 3600
        while True:
            wait = kernel.WaitForSingleObject(info.process, 250)
            if progress is not None and progress_path.is_file():
                try:
                    payload = json.loads(progress_path.read_text(encoding="utf-8"))
                    if int(payload.get("sequence") or 0) > last_sequence:
                        last_sequence = int(payload["sequence"])
                        progress(payload)
                except Exception:
                    # Presentation failures cannot abandon the protected worker.
                    # Its response and persisted receipt remain authoritative.
                    pass
            if wait == 0:
                finished = True
                break
            if wait != 258:
                raise RuntimeError("Protected operation status is unavailable; inspect its active state")
            if cancel_event is not None and cancel_event.is_set() and not cancel_sent:
                from .state import SystemCareState
                if cancel_state is None:
                    cancel_state = SystemCareState(config)
                active = cancel_state.active_operation()
                if worker_pid and active.get("pid") == worker_pid:
                    cancel_sent = cancel_state.request_cancel(str(active.get("operation_id") or ""))
            if time.monotonic() >= deadline:
                raise RuntimeError("Protected operation is still running; its journal and active state are retained")
        if not response.is_file():
            raise RuntimeError("Protected operation ended without a receipt; inspect its journal")
        payload = json.loads(response.read_text(encoding="utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Protected operation failed"))
        return payload["result"]
    finally:
        if info.process:
            kernel.CloseHandle(info.process)
        if finished:
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
            progress_path.unlink(missing_ok=True)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--request", required=True)
    options = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", options.request):
        raise ValueError("Invalid SystemCare permission request")
    os.environ["MO_STATE_HOME"] = str(Path(options.home).resolve(strict=True))
    from core.runtime.backend_monitor import BackendMonitor, set_monitor
    set_monitor(BackendMonitor())
    from core.state.paths import resolve_state_path
    from core.utils.atomic_write import atomic_write_json
    from .service import SystemCareService
    from .windows import ScanCancelled
    root = Path(resolve_state_path("run/systemcare"))
    response = root / (options.request + ".response.json")
    try:
        payload = json.loads((root / (options.request + ".request.json")).read_text(encoding="utf-8"))
        config_path = payload.get("config_path")
        config = {}
        if config_path:
            from core.provider.provider import load_config
            config = load_config(config_path)
        service = SystemCareService(config)
        if not service.adapter._is_admin():
            raise RuntimeError("Windows elevation was not granted")
        arguments = payload["arguments"]
        operation = payload["operation"]
        if operation == "apply":
            result = service.apply(**arguments, progress=lambda event: atomic_write_json(
                root / (options.request + ".progress.json"), event.to_dict())).to_dict()
        elif operation == "inspect" and arguments.get("context") == "machine" and arguments.get("section") in {"integrity", "component_store", "driver_packages", "updates", "filesystem"}:
            result = service.inspect(**arguments)
        elif operation == "restore":
            result = service.restore(**arguments)
        else:
            raise ValueError("Unknown protected operation")
        atomic_write_json(response, {"ok": True, "result": result})
        return 0
    except ScanCancelled:
        atomic_write_json(response, {"ok": True, "result": {
            "state": "cancelled", "detail": "Inspection stopped; no completed result was published."}})
        return 0
    except Exception as exc:
        from core.runtime.backend_monitor import redact_monitor_text
        atomic_write_json(response, {"ok": False, "error": redact_monitor_text(str(exc), 700)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
