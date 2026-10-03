"""Worker registry, runtime, and routing policy for MO."""
from __future__ import annotations
from importlib import import_module

from .registry import (
    WorkerRecord,
    WorkerRegistry,
    ensure_worker_registry,
    extract_worker_paths,
    normalize_worker_paths,
    paths_conflict,
)

_RUNTIME_EXPORTS = {
    "BackgroundWorkerRuntime",
    "build_background_worker_prompt",
    "ensure_worker_runtime",
    "format_worker_completion_notice",
    "notify_native_async",
    "summarize_background_test_output",
    "summarize_worker_result",
}
__all__ = [
    "BackgroundWorkerRuntime",
    "WorkerRecord",
    "WorkerRegistry",
    "build_background_worker_prompt",
    "ensure_worker_registry",
    "ensure_worker_runtime",
    "extract_worker_paths",
    "format_worker_completion_notice",
    "normalize_worker_paths",
    "notify_native_async",
    "paths_conflict",
    "summarize_background_test_output",
    "summarize_worker_result",
]


def __getattr__(name: str):
    if name in _RUNTIME_EXPORTS:
        module = import_module(".runtime", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
