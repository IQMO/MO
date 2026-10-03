"""MO care composes canonical diagnostics and retention owners."""
from __future__ import annotations

import time
from typing import Any, Callable


def inspect_mo(config: dict[str, Any], section: str, *, cancelled: Callable[[], bool]) -> dict[str, Any]:
    from core.state.paths import mo_home
    home = mo_home(config)
    if cancelled():
        from .windows import ScanCancelled
        raise ScanCancelled("MO inspection cancelled")
    if section == "health":
        from core.diagnostics.doctor import build_doctor_report
        report = build_doctor_report(home=home, project_path=None, config=config)
        rows = [{"name": row.name, "state": row.status, "detail": row.detail} for row in report.checks]
        detail = "Offline owner checks; no live provider/tool request performed"
    elif section == "caches":
        from core.diagnostics.system_health import check_file_health
        report = check_file_health(str(home))
        rows = []
        for name, value in report.items():
            limits = {k: v for k, v in value.items() if k.startswith("max_") and v is not None}
            counts = [str(value[k]) + " " + k for k in ("files", "entries", "lines") if k in value]
            rows.append({"name": name, "state": value["status"], "bytes": value["bytes"],
                         "detail": "; ".join([*counts, "Retention limits: " + str(limits) if limits else "Known retained state",
                                               "Existing writer/retention owner decides cleanup; curated content is preserved"])})
        return {"state": "partial", "at": time.time(), "rows": rows, "report": report,
                "detail": "Known MO audit/retained-state sizes and caps from the existing diagnostic owner; missing files are not a whole-runtime failure"}
    elif section == "indexes":
        from core.diagnostics.system_health import check_graph_health, check_learning_health
        from core.state.paths import repo_root
        graph = check_graph_health(repo_root())
        memory = check_learning_health(str(home)).get("memory", {})
        graph_metrics = {k: v for k, v in graph.items() if isinstance(v, (str, int, float, bool)) or v is None}
        graph_detail = " · ".join(str(graph[k]) + " " + label for k, label in
                                  (("nodes", "nodes"), ("edges", "links"), ("communities", "groups")) if k in graph)
        if graph.get("built_at"):
            graph_detail += " · Built " + str(graph["built_at"])
        memory_detail = (str(memory.get("turns", 0)) + " stored turns · Keyword search: "
                         + str(memory.get("keyword_mode") or "not checked").replace("_", " "))
        if memory.get("keyword_reason"):
            memory_detail += " · " + str(memory["keyword_reason"])
        rows = [{"name": "Structural graph", "state": "stale" if graph.get("stale") else "observed" if graph.get("exists") else "not found",
                 "detail": graph_detail or "No readable source graph evidence"},
                {"name": "Episodic memory index", "state": "not found" if not memory.get("exists") else "Review" if memory.get("keyword_mode") != "bm25" else "observed",
                 "detail": memory_detail if memory.get("exists") else "No readable memory index evidence"}]
        return {"state": "partial", "at": time.time(), "rows": rows, "report": {"graph": graph_metrics, "memory": memory},
                "detail": "Canonical source graph and private episodic-memory index evidence; no rebuild, deletion or semantic-health inference"}
    elif section == "coverage":
        from pathlib import Path
        from core.runtime.backend_monitor import backend_event_catalog
        report = backend_event_catalog(Path(__file__).resolve().parents[2], verify_content=True)
        errors = int(report.get("source_error_count") or 0)
        unregistered = len(report.get("unregistered_events") or [])
        rows = [{"name": "Trace declarations", "state": "Review" if errors or unregistered else "observed",
                 "detail": str(report.get("source_files", 0)) + " source files · "
                           + str(len(report.get("declared_events") or {})) + " event types · "
                           + str(len(report.get("declared_phases") or {})) + " runtime phases · "
                           + str(errors) + " source errors · " + str(unregistered) + " unregistered event types"},
                {"name": "Loaded process", "state": "not checked", "detail": "Disk declarations do not prove the running process loaded source edits"}]
        return {"state": "partial", "at": time.time(), "rows": rows, "report": report,
                "detail": "Canonical trace declaration owner; task truth and loaded-source evidence require runtime observations"}
    else:
        raise ValueError("Unknown MO diagnostic owner")
    return {"state": "partial", "at": time.time(), "rows": rows, "detail": detail}
