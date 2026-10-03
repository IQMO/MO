"""Map each slice with ONE no-tools provider call — bounded by construction.

The earlier design ran agentic workers that were *asked* not to read files; they
ignored that and read every file, re-exploding the context. This does not give the
model that choice: each slice is a single ``complete_no_tools`` call — the slice's
skeleton digest goes in, a structured slice-map comes out, no tools, no file reads,
no loop. Token cost is bounded by the digest cap. The ``mapper``/``mapper-deep``
surfaces change only request depth and token budget; both resolve to the model
selected in the current interface. Generic background workers are untouched, and
the calls run in parallel behind a cancellable barrier.
"""
from __future__ import annotations

import contextvars
from contextlib import nullcontext
import threading
import time
from typing import Any

from .complexity import DEEP_SURFACE, STANDARD_MAX_TOKENS, STANDARD_SURFACE
from .digest import build_slice_digest_result
from .graph_context import slice_graph_context
from .partition import slice_scope_label, subsystem_key

MAPPER_SURFACE = STANDARD_SURFACE

MAPPER_SYSTEM = (
    "You are a project-mapper. You have NO tools — you cannot read, search, or run "
    "anything. Map ONLY from the file skeletons given in the message (signatures, classes, "
    "functions, docstrings, imports). For your assigned slice produce a structured Markdown "
    "slice-map: per subsystem cover Purpose, Architecture/design, Features, Entry points/"
    "deps, and anything Notable. Cite the file `path` (in backticks) for every non-trivial "
    "claim — do NOT append line numbers, the skeletons have no reliable lines. If the "
    "skeletons don't support a claim, mark it (inferred) or omit it. When a `Structural "
    "context` block is present (facts from MO's graph), USE it to ground your Entry "
    "points/deps and Notable — it shows the REAL cross-file callers and dependencies the "
    "local skeletons cannot. Be complete over your slice, honest, and do not invent."
)


def _slice_label(files: list[str]) -> str:
    return slice_scope_label(files)


def slice_objective(index: int, total: int, files: list[str], digest: str, graph_context: str = "") -> str:
    """The user message for a mapper call: MO's structural context (its graph brain) +
    the slice's skeletons."""
    subs = sorted({subsystem_key(f) for f in files})
    return (
        f"Map slice {index + 1} of {total}: {len(files)} files across {', '.join(subs)}.\n\n"
        f"{graph_context}"
        f"Map from the skeletons below (grounded by the structural context above, if present).\n\n"
        f"Slice skeletons:\n{digest}"
    )


def _response_text(response: Any) -> str:
    return str(getattr(response, "content", "") or "").strip()


def _capacity_blocked_surfaces(agent: Any, surfaces: list[str]) -> set[str]:
    """Return mapper surfaces whose configured exact routes are all exhausted.

    This is only a fan-out preflight.  A surface with at least one viable route is
    left to ``complete_no_tools`` so its normal ordered fallback semantics remain
    authoritative.  Resolution failures and empty route lists likewise fall
    through to the runtime instead of being mislabelled as capacity exhaustion.
    """
    resolver = getattr(agent, "providers_for_surface", None)
    if not callable(resolver):
        return set()

    from ..provider.provider_capacity import get_capacity

    capacity = get_capacity()
    blocked: set[str] = set()
    for surface in dict.fromkeys(surfaces):
        try:
            providers = list(resolver(surface) or [])
            fallback_provider = getattr(agent, "provider_name", "")
            fallback_model = getattr(agent, "model", "")
            routes = [
                (
                    str(getattr(provider, "name", fallback_provider) or fallback_provider),
                    str(getattr(provider, "model", fallback_model) or fallback_model),
                )
                for provider in providers
            ]
            if routes and all(not capacity.can_accept(provider, model) for provider, model in routes):
                blocked.add(surface)
        except Exception:
            # Capacity preflight is advisory.  The provider runtime owns route
            # resolution errors and may still recover through its normal path.
            continue
    return blocked


def dispatch_mappers(
    agent: Any,
    slices: list[list[str]],
    *,
    base: str | None = None,
    tiers: list[tuple[str, int, int]] | None = None,
    timeout: float | None = None,
    on_progress: Any | None = None,
    on_agent: Any | None = None,
    system_suffix: str = "",
    cancel_event: Any | None = None,
    on_digest: Any | None = None,
    worker_ids: list[str] | None = None,
    _caller: Any | None = None,
) -> list[tuple[list[str], str, str]]:
    """Map every slice with one no-tools provider call each, in parallel. Each slice
    uses the surface + token budget from its ``tiers`` entry (``(surface, max_tokens,
    score)``); without tiers, all use standard depth. Returns
    ``[(slice_files, slice_map_text, state), …]`` in slice order. ``_caller`` is a test
    hook: ``_caller(surface, messages) -> text`` in place of the live provider.
    A surface whose configured exact routes are all known exhausted is returned as
    blocked without creating doomed threads; other surfaces retain normal fallback.
    Live dispatch has no independent wall-clock deadline by default: the selected
    provider owns its request timeout, so a healthy slow response cannot be discarded
    while its provider call keeps running. Callers may still pass an explicit ``timeout``
    for a stricter embedding boundary, and ``cancel_event`` always breaks the barrier.

    ``on_agent(index, state)`` — if given — reports each mapper's REAL lifecycle for a
    live view: ``"inflight"`` the moment its call starts, then ``"delivered"`` (mapped)
    or ``"blocked"`` (empty/error) when it returns. No fabricated sub-phases: a mapper
    is one atomic call, so these are the only honest per-agent transitions."""
    total = len(slices)
    if total == 0:
        return []
    results: dict[int, tuple[list[str], str, str]] = {}
    lock = threading.Lock()
    done = threading.Event()
    accepting = True

    def tier_for(idx: int) -> tuple[str, int]:
        if tiers and idx < len(tiers):
            surface, max_tokens, _score = tiers[idx]
            return surface, max_tokens
        return STANDARD_SURFACE, STANDARD_MAX_TOKENS

    surfaces = [tier_for(idx)[0] for idx in range(total)]
    capacity_blocked = _capacity_blocked_surfaces(agent, surfaces)

    def _emit_agent(idx: int, state: str) -> None:
        if on_agent:
            try:
                on_agent(idx, state)
            except Exception:
                pass

    def run_one(idx: int, files: list[str]) -> None:
        nonlocal accepting
        _emit_agent(idx, "inflight")
        surface, max_tokens = tier_for(idx)
        if base is not None:
            digest_result = build_slice_digest_result(files, base)
            digest = digest_result.text
            if on_digest:
                try:
                    on_digest(idx, digest_result.coverage())
                except Exception:
                    pass
        else:
            digest = "\n".join(files)
        graph_ctx = slice_graph_context(files, base) if base is not None else ""
        messages = [
            {"role": "system", "content": MAPPER_SYSTEM + (system_suffix or "")},
            {"role": "user", "content": slice_objective(idx, total, files, digest, graph_ctx)},
        ]
        text, state = "", "completed"
        try:
            if _caller is not None:
                text = str(_caller(surface, messages) or "")
            else:
                worker_id = str(worker_ids[idx]) if worker_ids and idx < len(worker_ids) else ""
                scoped_attr = getattr(agent, "_scoped_thread_attr", None)
                scope = (
                    scoped_attr(provider_worker_id=worker_id)
                    if worker_id and callable(scoped_attr)
                    else nullcontext()
                )
                with scope:
                    response, _prov = agent.complete_no_tools(
                        surface=surface,
                        request=f"mapthis-{idx + 1}",
                        messages=messages,
                        max_tokens=max_tokens,
                        cancel_event=cancel_event,
                    )
                text = _response_text(response)
            if not text:
                text, state = "[mapper returned nothing]", "blocked"
        except Exception as exc:
            text, state = f"[mapper error: {exc}]", "blocked"
        with lock:
            if accepting:
                results[idx] = (files, text, state)
                count = len(results)
                if count >= total:
                    done.set()
                terminal_state = "delivered" if state == "completed" else "blocked"
            else:
                count = len(results)
                terminal_state = "late_discarded"
        _emit_agent(idx, terminal_state)
        if on_progress:
            try:
                depth = "deep" if surface == DEEP_SURFACE else "standard"
                on_progress(count, total, f"{_slice_label(files)} [{depth}]")
            except Exception:
                pass

    # Pre-block only surfaces whose *every exact provider/model route* is known
    # exhausted.  These slices still receive honest lifecycle/results, but no
    # doomed mapper thread is created.  Healthy sibling models and surfaces run.
    for idx, files in enumerate(slices):
        surface = surfaces[idx]
        if surface not in capacity_blocked:
            continue
        with lock:
            results[idx] = (
                files,
                f"[mapper blocked: all configured routes for {surface} are capacity-exhausted]",
                "blocked",
            )
            count = len(results)
        _emit_agent(idx, "blocked")
        if on_progress:
            try:
                depth = "deep" if surface == DEEP_SURFACE else "standard"
                on_progress(count, total, f"{_slice_label(files)} [{depth}, capacity blocked]")
            except Exception:
                pass

    if len(results) >= total:
        done.set()

    for idx, files in enumerate(slices):
        if surfaces[idx] in capacity_blocked:
            continue
        # ContextVars do not cross raw thread boundaries. Give each mapper its
        # own copy so monitor/provider-audit events retain the parent turn and
        # session correlation without sharing mutable context between workers.
        context = contextvars.copy_context()
        threading.Thread(
            target=context.run,
            args=(run_one, idx, files),
            daemon=True,
            name=f"mo-mapper-{idx + 1}",
        ).start()

    # Do not race the provider with a shorter hidden wall-clock deadline. Provider
    # transports own their request timeout; an optional caller deadline remains useful
    # for tests/embedders, while interactive cancellation stays immediate.
    deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
    while not done.is_set():
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        done.wait(0.3)
    with lock:
        accepting = False
        got = dict(results)
    return [
        got.get(i, (slices[i], "[mapper did not return in time]", "timeout"))
        for i in range(total)
    ]
