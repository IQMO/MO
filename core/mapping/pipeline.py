"""mapthis project-map pipeline — bounded implementation behind MO's native tool.

A bounded implementation used by the provider-facing ``map_project`` tool. The
inline ``mapthis`` keyword only activates that tool inside a normal MO turn; it no
longer bypasses Gateway/taskboard/final gates. The tool itself still uses bounded
``complete_no_tools`` mapper calls for the expensive fan-out step:

  build inventory -> partition -> dispatch no-tools mappers ->
  barrier -> re-audit each slice's citations -> synthesize -> write to the
  project docs/private cache -> MO reports skeleton coverage + citation checks.

MO owns the outer turn: whether to call the tool, what to do with its result, and
the final expert judgment. The tool preserves bounded cost and coverage receipts.
It is an orientation aid, not a source audit; MO must inspect relevant files before
making source-level findings.
"""
from __future__ import annotations

from typing import Any, Callable

from .partition import partition_files, project_file_inventory, slice_scope_label
from .workers import dispatch_mappers

Dispatcher = Callable[[Any, list], list]


def _complexity_threshold(agent: Any) -> int:
    """Score at/above which mapping uses deep request treatment."""
    from ..provider.model_tier import DEFAULT_TIER_THRESHOLD

    try:
        cfg = getattr(agent, "config", {}) or {}
        section = cfg.get("mapthis") if isinstance(cfg, dict) else {}
        return int((section or {}).get("complexity_threshold", DEFAULT_TIER_THRESHOLD))
    except Exception:
        return DEFAULT_TIER_THRESHOLD


def _register_mappers(agent: Any, slices: list, tiers: list | None) -> list:
    """Register each mapper as a REAL MO worker in the WorkerRegistry (id, claimed_paths,
    lifecycle) so mappers are first-class tracked workers like PRT/goals — not anonymous
    threads. Returns the record ids ([] if the registry is unavailable). Bounded no-tools
    execution is unchanged; this is tracking only."""
    try:
        from ..worker.registry import ensure_worker_registry

        reg = ensure_worker_registry(agent)
        ids = []
        for i, files in enumerate(slices):
            surface = tiers[i][0] if tiers and i < len(tiers) else "mapper"
            depth = "deep" if str(surface).lower().endswith("-deep") else "standard"
            obj = f"map {slice_scope_label(files)} · {len(files)} files · {depth}"
            rec = reg.create(
                kind="mapper", source="map_project", route="map_project", objective=obj,
                claimed_paths=list(files), role="project-mapper", state="running",
            )
            ids.append(rec.id)
        return ids
    except Exception:
        return []


def _update_mapper_record(agent: Any, ids: list, index: int, state: str) -> None:
    """Advance one mapper's worker record on its real lifecycle event."""
    if not ids or index < 0 or index >= len(ids) or state == "inflight":
        return
    try:
        from ..worker.registry import ensure_worker_registry

        ensure_worker_registry(agent).update(ids[index], "completed" if state == "delivered" else "blocked")
    except Exception:
        pass


def _finalize_mappers(agent: Any, ids: list, slice_results: list) -> None:
    """Reconcile every still-open mapper record against the REAL slice outcome, so none
    leak as perpetually-active and a completed slice is never mislabelled blocked (the
    per-agent lifecycle may not have fired — e.g. an injected dispatcher or a timeout)."""
    if not ids:
        return
    try:
        from ..worker.registry import ensure_worker_registry

        reg = ensure_worker_registry(agent)
        for i, rec_id in enumerate(ids):
            rec = reg.get(rec_id)
            if rec and rec.active:
                state = slice_results[i][2] if i < len(slice_results) else "timeout"
                reg.update(rec_id, "completed" if state == "completed" else "blocked")
    except Exception:
        pass


def _maybe_remap(
    agent,
    slice_results,
    verifications,
    base,
    tiers,
    dispatcher,
    say,
    *,
    cancel_event=None,
    worker_ids: list[str] | None = None,
):
    """One bounded re-map pass for completed slices whose citations resolved poorly —
    correcting like extrathink's re-audit, not just labelling. A re-map is kept only when
    it resolves BETTER, so the retry can never make the map worse."""
    from .verify import verify_slice_map

    def poor(i: int) -> bool:
        v = verifications[i]
        c = int(v.get("citations", 0) or 0)
        r = int(v.get("resolved", 0) or 0)
        return slice_results[i][2] == "completed" and c >= 3 and (r / max(1, c)) < 0.6

    low = [i for i in range(len(slice_results)) if poor(i)]
    if not low or getattr(cancel_event, "is_set", lambda: False)():
        return slice_results, verifications
    say(f"map_project: re-mapping {len(low)} low-resolution slice(s) for citation precision…")
    sub_slices = [slice_results[i][0] for i in low]
    sub_tiers = [tiers[i] for i in low] if tiers else None
    nudge = (
        "\n\nPRECISION RE-MAP: a prior pass cited files that do not exist. Cite ONLY file "
        "paths that appear as `### path` headers in the skeletons below, exactly as written; "
        "omit any claim you cannot tie to a shown file."
    )
    try:
        if dispatcher is not None:
            redone = dispatcher(agent, sub_slices)
        else:
            redone = dispatch_mappers(
                agent,
                sub_slices,
                base=str(base),
                tiers=sub_tiers,
                system_suffix=nudge,
                cancel_event=cancel_event,
                worker_ids=[worker_ids[i] for i in low if i < len(worker_ids)] if worker_ids else None,
            )
    except Exception:
        return slice_results, verifications
    for j, i in enumerate(low):
        if j >= len(redone):
            break
        _rf, rt, rs = redone[j]
        if rs == "completed":
            rv = verify_slice_map(rt, base)
            if int(rv.get("resolved", 0) or 0) > int(verifications[i].get("resolved", 0) or 0):
                # Re-mapping changes only the model-authored map and its citation
                # resolution. The digest coverage receipt belongs to the original
                # bounded input and must survive a successful precision retry.
                for key in ("digest_shown", "digest_total", "digest_omitted"):
                    if key in verifications[i]:
                        rv[key] = verifications[i][key]
                slice_results[i] = (slice_results[i][0], rt, rs)   # keep the better map
                verifications[i] = rv
    return slice_results, verifications


def run_project_map(
    gateway: Any,
    *,
    root: str | None = None,
    workers: int | None = None,
    dispatcher: Dispatcher | None = None,
    synthesizer: Any | None = None,
    on_activity: Any | None = None,
    cancel_event: Any | None = None,
) -> str:
    """Run the full exposure pipeline and return MO's light-confirm delivery text.

    Emits phases through the existing activity callback and registers mapper workers
    in MO's canonical worker registry. ``root`` and ``dispatcher`` are injectable so
    the pipeline is testable without live models."""
    from .verify import verify_slice_map
    from .synthesize import build_overview, synthesize_map, write_map_doc

    def say(text: str) -> None:
        if on_activity:
            try:
                on_activity(text)
            except Exception:
                pass

    def cancelled() -> bool:
        return bool(getattr(cancel_event, "is_set", lambda: False)())

    def cancelled_result(stage: str) -> str:
        return (
            f"[project map] Cancelled during {stage}; late mapper results were discarded "
            "and no synthesis or map write was started after cancellation."
        )

    if cancelled():
        return cancelled_result("inventory")

    say("map_project: indexing project skeletons…")
    files, base = project_file_inventory(root)
    if not files:
        return "[project map] No indexable files found under this project — nothing to map."

    # MO sizes the number of agents by the project (not a hardcoded 4), unless caller pins it.
    from .partition import decide_worker_count, subsystem_key

    if workers is None:
        workers = decide_worker_count(len(files), subsystem_count=len({subsystem_key(f) for f in files}))
    say(f"map_project: partitioning {len(files)} file skeletons into {workers} worker slices…")
    slices = partition_files(files, n=workers, base=base)
    agent = getattr(gateway, "agent", gateway)

    # Complexity changes request depth and token budget only. The selected model
    # remains authoritative for every mapper surface.
    from .complexity import slice_tiers

    threshold = _complexity_threshold(agent)
    tiers = slice_tiers(slices, str(base), threshold=threshold)

    def progress(done: int, total: int, label: str) -> None:
        say(f"map_project: mapped {done}/{total} slices (latest: {label})…")

    deep_n = sum(1 for (surface, _mt, _s) in tiers if surface.endswith("-deep"))
    say(f"map_project: dispatching {len(slices)} agents ({deep_n} deep, {len(slices) - deep_n} standard)…")
    mapper_ids = _register_mappers(agent, slices, tiers)
    digest_receipts: dict[int, dict[str, int]] = {}

    def on_agent(index: int, state: str) -> None:
        _update_mapper_record(agent, mapper_ids, index, state)

    run_dispatch = dispatcher or (
        lambda a, s: dispatch_mappers(
            a, s, base=str(base), tiers=tiers, on_progress=progress, on_agent=on_agent,
            on_digest=lambda index, receipt: digest_receipts.__setitem__(int(index), dict(receipt)),
            cancel_event=cancel_event, worker_ids=mapper_ids,
        )
    )
    slice_results = run_dispatch(agent, slices)
    _finalize_mappers(agent, mapper_ids, slice_results)
    if cancelled():
        return cancelled_result("mapping")

    completed = sum(1 for (_files, _text, state) in slice_results if state == "completed")
    if completed == 0:
        say("map_project: blocked — no mapper slice completed; no map was synthesized or written.")
        return (
            f"Error: map_project blocked — 0/{len(slices)} mapper slices completed, so no "
            "project map was synthesized or written. All discovered file skeletons remain "
            "unmapped; inspect the blocked mapper results and retry after fixing their cause."
        )

    say("map_project: re-auditing worker citations against live files…")
    verifications = [verify_slice_map(text, base) for (_files, text, _state) in slice_results]
    for index, verification in enumerate(verifications):
        receipt = digest_receipts.get(index) or {}
        if receipt:
            verification.update({
                "digest_shown": int(receipt.get("shown") or 0),
                "digest_total": int(receipt.get("total") or 0),
                "digest_omitted": int(receipt.get("omitted") or 0),
            })
    slice_results, verifications = _maybe_remap(
        agent,
        slice_results,
        verifications,
        base,
        tiers,
        dispatcher,
        say,
        cancel_event=cancel_event,
        worker_ids=mapper_ids,
    )
    if cancelled():
        return cancelled_result("re-audit")
    say("map_project: synthesizing the cross-slice overview…")
    overview = build_overview(
        agent,
        slice_results,
        synthesizer=synthesizer,
        cancel_event=cancel_event,
    )
    if cancelled():
        return cancelled_result("synthesis")
    from .graph_context import graph_orientation_status

    doc = synthesize_map(
        slice_results,
        base,
        files,
        verifications,
        overview=overview,
        graph_provenance=graph_orientation_status(base),
    )
    path = write_map_doc(doc, base)

    # Honest: only count files whose slice actually produced a map — a blocked slice's
    # files were NOT mapped, so they must not inflate coverage to a false 100%.
    mapped_files = sum(len(f) for (f, _t, state) in slice_results if state == "completed")
    total_c = sum(int(v.get("citations", 0)) for v in verifications)
    total_r = sum(int(v.get("resolved", 0)) for v in verifications)
    low = [i + 1 for i, v in enumerate(verifications) if v.get("citation_resolution") in {"low", "unverified"}]
    omitted = sum(int(v.get("digest_omitted", 0) or 0) for v in verifications)
    caveat = f" Low-citation-resolution slices to review: {low}." if low else ""
    if omitted:
        caveat += f" Digest caps omitted {omitted} file skeleton(s); no claims cover those omissions."

    say("map_project: done.")
    skipped = f" ({len(files) - mapped_files} in {len(slices) - completed} unfinished slice(s) not mapped)" if completed < len(slices) else ""
    return (
        f"[project map] Done — indexed {mapped_files} of {len(files)} file skeletons via "
        f"{completed}/{len(slices)} agents{skipped}.\n"
        f"Re-audit: {total_r}/{total_c} cited file paths resolved to real files "
        f"(existence-checked, not claim-checked).{caveat}\n"
        f"Map written to {path}.\n"
        f"I synthesized skeleton-derived slice maps into one cross-slice orientation. Neither "
        f"the workers nor I read every file body; cited paths were existence-checked only. Open the map for "
        f"skeleton-derived architecture, feature inventory, cross-cutting wiring, and per-subsystem detail."
    )
