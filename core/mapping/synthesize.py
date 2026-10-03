"""S4 — synthesize skeleton-based slice maps into one project orientation map.

Output location is resolved from the project working directory MO was called on
(``project_cwd``), reusing an existing ``docs/`` / ``documentation/`` folder if the
project has one, else creating ``docs/``. If that output is gitignored, it is
runtime-private state and is written to MO's per-project cache instead.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from .partition import subsystem_key

MAP_FILENAME = "PROJECT-MAP.md"
_DOCS_NAMES = ("docs", "documentation", "doc")


def resolve_docs_dir(base: str | Path) -> Path:
    """Return the project docs directory to write into: an existing docs/documentation
    folder if present, otherwise a freshly created ``docs/``. Never the profile home."""
    base = Path(base)
    for name in _DOCS_NAMES:
        candidate = base / name
        if candidate.is_dir():
            return candidate
    docs = base / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    return docs


def _map_output_path(base: str | Path, filename: str) -> Path:
    base = Path(base).resolve(strict=False)
    docs = next((base / name for name in _DOCS_NAMES if (base / name).is_dir()), base / "docs")
    candidate = docs / filename
    try:
        run_kwargs = {
            "cwd": str(base), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
            "timeout": 2, "check": False,
        }
        apply_windows_hidden_process_flags(run_kwargs)
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(candidate.relative_to(base))],
            **run_kwargs,
        ).returncode == 0
    except Exception:
        ignored = False
    if ignored:
        from ..state.paths import project_cache_dir

        return project_cache_dir("maps", base) / filename
    return candidate


_OVERVIEW_PROMPT = (
    "You are given {n} completed subsystem {map_word} of ONE software project. Each completed map "
    "was written by a worker that received only its own bounded skeleton digest, so no worker "
    "read full files or saw the whole project. Blocked slices are listed separately as coverage "
    "gaps; they are not subsystem maps and their error text is not architecture evidence. "
    "Synthesize a coherent "
    "whole-project overview FROM THESE MAPS ONLY — do not invent; where the maps don't say, "
    "say so. Write exactly these sections in Markdown:\n"
    "**What this project is** — 2-4 sentences.\n"
    "**Architecture at a glance** — the main subsystems and how they relate.\n"
    "**Cross-cutting relationships** — how the subsystems connect and how data/control flows "
    "across slices (the wiring no single worker saw).\n"
    "**Top risks / notable** — the most important caveats across the whole project.\n"
    "**Verdict** — one line: what this project is at its core.\n\n"
    "Mapping results:\n\n{digest}"
)


def build_overview(
    agent: object,
    slice_results: list[tuple[list[str], str, str]],
    *,
    synthesizer: object = None,
    max_chars: int = 16000,
    cancel_event: object = None,
) -> str:
    """MO's cross-slice synthesis: read the 4 slice-MAPS (summaries, not the files) and
    produce the coherent whole-project view no single worker had. This is MO's light
    confirmation — it reads the maps, not every file. ``synthesizer`` is injectable
    (``synthesizer(agent, prompt) -> text``) so it is testable without a live model;
    live no-tools synthesis reuses the mapping run's cancellation event."""
    completed_rows = []
    blocked_parts = []
    for i, (files, text, state) in enumerate(slice_results, 1):
        if state == "completed":
            completed_rows.append((i, str(text).strip()))
        else:
            file_word = "skeleton" if len(files) == 1 else "skeletons"
            blocked_parts.append(
                f"### Blocked slice {i} ({len(files)} file {file_word}; coverage unavailable)"
            )
    blocked_digest = "\n\n".join(blocked_parts) or "_(none)_"
    completed_intro = "## Completed subsystem maps\n\n"
    blocked_section = (
        "\n\n## Blocked slices (coverage gaps, not subsystem maps)\n\n"
        f"{blocked_digest}"
    )
    headers = [f"### Subsystem map {index}\n" for index, _text in completed_rows]
    fixed_chars = len(completed_intro) + len(blocked_section) + sum(map(len, headers))
    fixed_chars += max(0, len(headers) - 1) * 2
    text_budget = max(0, int(max_chars) - fixed_chars)
    per_map_budget = text_budget // max(1, len(completed_rows))
    completed_parts = [
        f"{header}{text[:min(6000, per_map_budget)]}"
        for header, (_index, text) in zip(headers, completed_rows)
    ]
    maps_digest = "\n\n".join(completed_parts) or "_(no completed subsystem maps)_"
    digest = f"{completed_intro}{maps_digest}{blocked_section}"[: max(0, int(max_chars))]
    prompt = _OVERVIEW_PROMPT.format(
        n=len(completed_rows),
        map_word="map" if len(completed_rows) == 1 else "maps",
        digest=digest,
    )
    if synthesizer is not None:
        try:
            return str(synthesizer(agent, prompt) or "").strip()
        except Exception:
            pass
    # No-tools call: the synthesis must read the MAPS, not the files. run_turn would
    # give it tools and it could read files (the same soft-nudge flaw the mappers had).
    try:
        if agent is not None and hasattr(agent, "complete_no_tools"):
            response, _prov = agent.complete_no_tools(
                surface="main",
                request="mapthis-synthesis",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
                cancel_event=cancel_event,
            )
            text = str(getattr(response, "content", "") or "").strip()
            if text:
                return text
    except Exception:
        pass
    completed = [item for item in slice_results if item[2] == "completed"]
    blocked = len(slice_results) - len(completed)
    subs = sorted({subsystem_key(f) for (files, _t, _s) in completed for f in files})
    blocked_word = "slice" if blocked == 1 else "slices"
    blocked_note = f"; {blocked} blocked {blocked_word} not mapped" if blocked else ""
    return (
        "_(overview synthesis unavailable — mapped subsystems present: "
        + ", ".join(subs[:20])
        + blocked_note
        + ")_"
    )


def structural_backbone_lines(base: str | Path | None = None, *, max_chars: int = 700) -> list[str]:
    """The structural facts (god-nodes / surprising deps / import cycles) from MO's own
    ``build_structural_summary`` for the project at ``base`` — fact bullets only,
    orientation preamble dropped. Empty when the structural graph isn't available. ``base``
    MUST be the mapped project (not the process cwd) or a different project's graph would
    leak in. Reused by both the transcript viz and the written map so mapthis consumes MO's
    graph, not a bespoke re-derivation."""
    try:
        from ..graph.structural_graph import build_structural_summary, graph_exists

        if not graph_exists(base):  # use the graph if it's built; never build one just for this
            return []
        summary = build_structural_summary(cwd=base, max_chars=max_chars) or ""
        return [ln for ln in summary.splitlines() if ln.strip().startswith("-")]
    except Exception:
        return []


def external_architecture_lines(base: str | Path | None = None, *, max_chars: int = 1800) -> list[str]:
    """Architecture facts from an optional codebase-memory MCP backend.

    This never starts or installs an MCP server; it only uses the safe graph
    backend when the current MO process already has one configured.
    """
    try:
        from ..graph.mcp_backend import architecture_summary

        summary = architecture_summary(cwd=base, max_chars=max_chars)
        if not summary:
            return []
        return [ln.rstrip() for ln in summary.splitlines() if ln.strip()]
    except Exception:
        return []


def synthesize_map(
    slice_results: list[tuple[list[str], str, str]],
    base: str | Path,
    all_files: list[str],
    verifications: list[dict[str, object]],
    *,
    overview: str = "",
    graph_provenance: dict | None = None,
) -> str:
    """Assemble the synthesized overview + re-audited slice-maps into one PROJECT-MAP doc."""
    base = Path(base)
    total_files = len(all_files)
    completed_slices = [item for item in slice_results if item[2] == "completed"]
    mapped_files = sum(len(files) for files, _text, _state in completed_slices)
    total_c = sum(int(v.get("citations", 0)) for v in verifications)
    total_r = sum(int(v.get("resolved", 0)) for v in verifications)

    lines: list[str] = [
        f"# Project Map — {base.name}",
        "",
        "> Generated by MO `mapthis` as skeleton-based project orientation, not a source audit.",
        f"> {mapped_files} of {total_files} file skeletons mapped by "
        f"{len(completed_slices)} of {len(slice_results)} coordinated workers, then "
        "synthesized into one view; workers did not read full file bodies.",
        "",
        "## Overview",
        (overview.strip() or "_(no overview synthesized)_"),
        "",
    ]
    backbone = structural_backbone_lines(base)
    if backbone:
        lines += [
            "## Structural backbone",
            "_From MO's structural graph — the architecture the file list can't show "
            "(god-nodes, surprising deps, import cycles):_",
            *backbone,
            "",
        ]
    external_arch = external_architecture_lines(base)
    if external_arch:
        lines += [
            "## External graph architecture",
            "_From the configured codebase-memory MCP backend:_",
            *external_arch,
            "",
        ]
    graph_truth = graph_provenance if isinstance(graph_provenance, dict) else {}
    if graph_truth.get("available"):
        lines += [
            "## Graph provenance",
            f"- Source: {graph_truth.get('source_kind') or 'native'} structural graph (orientation only).",
            f"- Edge confidence: {graph_truth.get('confidence_breakdown') or {}}.",
            f"- Edge provenance: {graph_truth.get('provenance_breakdown') or {}}.",
            "- These are extractor-owned edge facts; verify selected source and tests before findings.",
            "",
        ]
    lines += [
        "## Verification",
        f"- **{total_r}/{total_c}** evidence citations resolved to real files "
        "(existence-checked: the workers' `path:line` evidence points at real code — this "
        "confirms the citations are not hallucinated; it does not by itself prove every "
        "description is correct).",
    ]
    for i, (files, _text, state) in enumerate(slice_results, 1):
        v = verifications[i - 1]
        state_label = "completed" if state == "completed" else "blocked"
        resolution = v.get("citation_resolution", "unverified")
        unresolved = v.get("unresolved") or []
        flag = "" if resolution in {"high", "medium"} else "  ⚠ low citation resolution"
        lines.append(
            f"- Slice {i}: {v.get('resolved', 0)}/{v.get('citations', 0)} cited, "
            f"citation resolution **{resolution}**, worker {state_label}{flag}"
        )
        if int(v.get("digest_omitted", 0) or 0):
            lines.append(
                f"    - digest coverage: {v.get('digest_shown', 0)}/{v.get('digest_total', len(files))} "
                f"file skeletons shown; {v.get('digest_omitted', 0)} omitted by the cap"
            )
        if state == "completed" and unresolved:
            lines.append(f"    - unresolved citations: {', '.join(map(str, unresolved[:8]))}")
    lines.append("")

    for i, (files, text, state) in enumerate(slice_results, 1):
        subs = sorted({subsystem_key(f) for f in files})
        shown = ", ".join(subs[:8]) + ("…" if len(subs) > 8 else "")
        state_label = "completed" if state == "completed" else "blocked"
        lines.append(f"## Slice {i} — {shown}  ({len(files)} file skeletons, worker {state_label})")
        lines.append("")
        if state == "completed":
            lines.append(str(text).strip() or "_(no output)_")
        else:
            # A blocked worker's body is an operational diagnostic, not map
            # content. It may include an exception, private path, or credential
            # fragment, so the generated artifact records only the coverage gap.
            lines.append("_(No subsystem map was generated; coverage is unavailable.)_")
        lines.append("")

    return "\n".join(lines)


def write_map_doc(doc: str, base: str | Path, *, filename: str = MAP_FILENAME) -> Path:
    """Write the map to project docs, or private state when that path is ignored."""
    path = _map_output_path(base, filename)
    atomic_write_text(path, str(doc), encoding="utf-8")
    return path
