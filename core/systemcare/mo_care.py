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
        from core.diagnostics.personalization import build_personalization_report
        from core.state.paths import repo_root
        report = build_doctor_report(home=home, project_path=None, config=config)
        rows = [{"name": row.name, "state": row.status, "detail": row.detail} for row in report.checks]
        if cancelled():
            from .windows import ScanCancelled
            raise ScanCancelled("MO inspection cancelled")
        personalization = build_personalization_report(state_root=home, project_root=repo_root())
        profile = personalization["profile"]
        learning = personalization["learning"]
        sessions = personalization["sessions"]
        recurrence = personalization["recurrence"]
        rows.extend([
            {"name": "Personalization maintenance", "state": personalization["verdict"],
             "detail": personalization["summary"]},
            {"name": "Profile structure", "state": profile["status"],
             "detail": f"{len(profile['missing'])} missing documents · {len(profile['unreadable'])} unreadable · "
                       f"{profile['learning_duplicate_entries'] + profile['behavior_duplicate_rules']} duplicate accepted entries; "
                       "semantic freshness is not inferred"},
            {"name": "Learning authorities", "state": learning["status"],
             "detail": f"{learning['pending_review']} pending learning reviews · "
                       f"{learning['pending_product_intents']} staged product intents · "
                       f"authorities {'aligned' if learning['authority_alignment'] else 'misaligned'}"},
            {"name": "Episodic recall", "state": "observed" if learning["memory_inspection"] == "ready" else "unavailable",
             "detail": f"{learning['memory_turns']} stored turns · {learning['recall_mode']} · {learning['memory_inspection']}"},
            {"name": "Session retention", "state": sessions["status"],
             "detail": f"{sessions['cleanup_candidates']} cleanup candidates · {sessions['unreadable_json']} unreadable metadata · "
                       f"latest closeout {sessions['latest_closeout']['status']}; named sessions stay with their owner"},
            {"name": "Project recurrence", "state": "Review" if recurrence["status"] == "detected" else recurrence["status"],
             "detail": f"{len(recurrence['repeated'])} repeated paths / {recurrence['window_commits']} commits; "
                       "signal only, not evidence of a defect or cause"},
        ])
        for name, key in (("Mechanical maintenance", "deterministic_maintenance"),
                          ("Operator decisions", "operator_decisions"), ("Evidence review", "evidence_review")):
            actions = personalization["next_actions"][key]
            if actions:
                rows.append({"name": name, "state": "Review", "detail": "; ".join(actions)})
        return {"state": "partial", "at": time.time(), "rows": rows, "report": personalization,
                "detail": "Offline doctor and canonical personalization evidence; no live provider/tool request, "
                          "semantic-health verdict or maintenance performed. Review uses the existing MO conversation."}
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


# ---------------------------------------------------------------- MO Care: background problems, reported once
WATCH_STATE = "run/mo-care.json"
WATCH_INTERVAL_SECONDS = 15 * 60
_ERROR_EVENTS = {
    "provider_error": "Provider error", "turn_error": "Turn error", "learning_write_error": "Learning write failed",
    "memory_index_error": "Memory index failed", "memory_recall_error": "Memory recall failed",
    "memory_embed_error": "Memory embedding failed", "worker_on_finish_error": "Worker finish failed",
}
MAX_FINDINGS = 5


def watch(config: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """MO Care: MO's own background problems since the last look, each reported once. Deterministic (no model):
    the backend monitor (provider and turn errors, workers that ended blocked, apps that failed to open, memory
    and learning write failures), scheduled jobs that failed, and failing offline doctor checks. The first look
    sets a baseline, so history is never resurfaced as news."""
    import json
    from pathlib import Path

    from core.state.paths import resolve_state_path
    from core.utils.atomic_write import atomic_write_json

    current = time.time() if now is None else float(now)
    state_path = Path(resolve_state_path(WATCH_STATE, config))
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        since = float(state.get("since") or current)
        seen = set(state.get("seen") or [])
    except (OSError, ValueError, TypeError, AttributeError):
        since, seen = current, set()
    findings: list[dict[str, Any]] = []

    def add(fingerprint: str, kind: str, detail: str, at: float, source: str) -> None:
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        findings.append({"kind": kind, "detail": " ".join(str(detail or "").split())[:300], "at": at, "source": source})

    monitor_dir = Path(resolve_state_path("logs/monitor", config))
    for log in sorted(monitor_dir.glob("backend_monitor-*.jsonl")) if monitor_dir.is_dir() else []:
        try:
            if log.stat().st_mtime < since:
                continue
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
                at = float(row.get("ts") or 0.0)
            except (ValueError, TypeError, AttributeError):
                continue
            if at <= since:
                continue
            kind, payload = str(row.get("type") or ""), row.get("payload") or {}
            if kind in _ERROR_EVENTS:
                error = str(payload.get("error") or payload.get("reason") or "")
                provider = str(payload.get("provider") or "")
                add(f"{kind}:{provider}:{error[:80]}", _ERROR_EVENTS[kind], (f"{provider}: " if provider else "") + error,
                    at, f"monitor {log.name} {kind}")
            elif kind == "worker_event" and payload.get("state") == "blocked":
                who = payload.get("role") or payload.get("kind") or "worker"
                add(f"worker:{payload.get('worker_id')}", "Worker blocked", f"{who}: {payload.get('note') or payload.get('objective') or ''}",
                    at, f"monitor {log.name} worker_event {payload.get('worker_id')}")
            elif kind == "session_event" and payload.get("kind") == "scheduler_job_run" and payload.get("status") == "error":
                add(f"scheduler:{payload.get('job_id')}:{str(payload.get('error'))[:80]}", "Scheduled job failed",
                    f"{payload.get('job_kind') or 'job'} {payload.get('job_id') or ''}: {payload.get('error') or ''}", at,
                    f"monitor {log.name} scheduler_job_run")
            elif kind == "desktop_launch" and payload.get("stage") == "renderer_ready" and payload.get("confirmed") is False:
                add(f"launch:{payload.get('app')}:{int(at)}", "App failed to open", f"{payload.get('app') or 'app'} did not confirm it opened",
                    at, f"monitor {log.name} desktop_launch")
    try:
        from core.diagnostics.doctor import FAIL, build_doctor_report
        from core.state.paths import mo_home

        for check in build_doctor_report(home=mo_home(config), project_path=None, config=config).checks:
            if check.status == FAIL:
                add(f"doctor:{check.name}:{str(check.detail)[:80]}", "Health check failing", f"{check.name}: {check.detail}",
                    current, "offline doctor (/doctor)")
    except Exception:
        pass
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_path, {"since": current, "seen": sorted(seen)[-300:]})
    return {"checked_at": current, "findings": findings[:MAX_FINDINGS], "more": max(0, len(findings) - MAX_FINDINGS)}

