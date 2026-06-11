from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from core.text_safety import configure_utf8_stdio

DEFAULT_LOG_PATH = Path("logs/monitor/backend_monitor.jsonl")


def resolve_log_path(argv: list[str] | None = None) -> Path:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        return Path(args[0])
    env_path = os.environ.get("MO_BACKEND_MONITOR_PATH")
    if env_path:
        return Path(env_path)
    latest = latest_monitor_log()
    return latest or DEFAULT_LOG_PATH


def latest_monitor_log() -> Path | None:
    parent = DEFAULT_LOG_PATH.parent
    if not parent.exists():
        return None
    files = sorted(parent.glob("backend_monitor-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def render(events: list[dict], path: Path | None = None) -> None:
    clear_screen()
    watched = path or DEFAULT_LOG_PATH
    now = time.time()
    print("MO backend monitor")
    print(f"Watching: {watched}")
    print("Press Ctrl+C to close this monitor.")
    print("-" * 72)
    if not events:
        print("waiting for backend events...")
        return

    last_taskboard = None
    provider_requests = 0
    tool_calls = 0
    errors = 0
    open_provider: dict | None = None
    open_tool: dict | None = None
    activity: list[str] = []
    
    # Show how fresh the data is
    newest_ts = max((float(e.get("ts") or 0) for e in events), default=0)
    age = max(0, int(now - newest_ts))
    age_text = f"{age}s ago" if age < 120 else f"{age//60}m {age%60}s ago"
    print(f"Last event: {age_text}")
    print("-" * 72)

    for event in events[-120:]:
        etype = event.get("type")
        payload = event.get("payload") or {}
        ts = float(event.get("ts") or now)
        if etype == "taskboard":
            last_taskboard = payload.get("rendered") or last_taskboard
        elif etype == "backend_status":
            activity.append(f"status: {payload.get('message', '')}")
        elif etype == "turn_start":
            activity.append(
                f"turn start: {payload.get('template') or '?'} source={payload.get('route_source') or '-'} "
                f"messages={payload.get('messages')} input={payload.get('input') or ''}"
            )
        elif etype == "turn_route":
            activity.append(f"turn route: template={payload.get('template') or '?'} board={payload.get('show_task_board')}")
        elif etype == "turn_context":
            flags = payload.get("flags") or {}
            active = ",".join(k for k, v in flags.items() if v) if isinstance(flags, dict) else ""
            activity.append(f"turn context: chars={payload.get('extra_context_chars')} active={active or '-'}")
        elif etype == "turn_intercept":
            kind = payload.get("kind") or "intercept"
            if kind in {"truncated_tool_call_stop"}:
                errors += 1
            activity.append(f"turn intercept: {kind} {payload.get('reason') or ''} {payload.get('pending_user_preview') or ''}".strip())
        elif etype == "turn_end":
            activity.append(
                f"turn end: {payload.get('status')} {payload.get('duration_ms')}ms result_chars={payload.get('result_chars')} "
                f"board={payload.get('has_task_board')}"
            )
        elif etype == "turn_error":
            errors += 1
            activity.append(f"turn error: {payload.get('error_type')} {payload.get('error') or ''}")
        elif etype == "session_event":
            activity.append(
                f"session: {payload.get('kind')} slot={payload.get('name') or payload.get('slot') or '-'} "
                f"turns={payload.get('turns', payload.get('turn_count', '-'))} messages={payload.get('messages', '-')}"
            )
        elif etype == "session_compact":
            activity.append(
                f"session compact: {payload.get('stage') or '-'} chains={payload.get('compacted_chains', 0)} "
                f"saved={payload.get('saved_chars', 0)} chars messages={payload.get('before_messages', '?')}->{payload.get('after_messages', '?')}"
            )
        elif etype == "context_handoff":
            activity.append(f"context handoff: {payload.get('reason') or ''} -> {payload.get('new_session_id') or ''}")
        elif etype == "provider_request":
            provider_requests += 1
            open_provider = {"ts": ts, "payload": payload}
            open_tool = None
            surface = payload.get("surface") or "main"
            prefix = f"{surface} " if surface and surface != "main" else ""
            activity.append(
                f"{prefix}provider request #{payload.get('request')}: {payload.get('provider')}/{payload.get('model')} "
                f"messages={payload.get('messages')} tools={payload.get('tools')}"
            )
            if payload.get("preview"):
                activity.append("sent preview: " + str(payload.get("preview")).replace("\n", " | "))
        elif etype == "provider_response":
            open_provider = None
            surface = payload.get("surface") or "main"
            prefix = f"{surface} " if surface and surface != "main" else ""
            activity.append(
                f"{prefix}provider response #{payload.get('request')}: finish={payload.get('finish_reason') or '?'} "
                f"tool_calls={payload.get('tool_calls')} content_chars={payload.get('content_chars')}"
            )
            if payload.get("preview"):
                activity.append("received preview: " + str(payload.get("preview")).replace("\n", " | "))
        elif etype == "provider_error":
            open_provider = None
            errors += 1
            surface = payload.get("surface") or "main"
            prefix = f"{surface} " if surface and surface != "main" else ""
            activity.append(f"{prefix}provider error #{payload.get('request')}: {payload.get('reason')} {payload.get('error')}")
        elif etype == "provider_fallback":
            activity.append(f"provider fallback #{payload.get('request')}: {payload.get('provider')}/{payload.get('model')} reason={payload.get('reason')}")
        elif etype == "tool_call":
            tool_calls += 1
            open_tool = {"ts": ts, "payload": payload}
            summary = payload.get("summary") or ""
            surface = payload.get("surface") or "main"
            prefix = f"{surface} " if surface and surface != "main" else ""
            activity.append(f"{prefix}tool call #{payload.get('request')}: {payload.get('tool')} {summary}".rstrip())
        elif etype == "tool_result":
            open_tool = None
            if payload.get("error") or payload.get("blocked"):
                errors += 1
            surface = payload.get("surface") or "main"
            prefix = f"{surface} " if surface and surface != "main" else ""
            activity.append(
                f"{prefix}tool result #{payload.get('request')}: {payload.get('tool')} "
                f"blocked={payload.get('blocked')} error={payload.get('error')} chars={payload.get('chars')}"
            )
        elif etype == "tool_compress":
            activity.append(
                f"tool compress: {payload.get('tool') or ''} saved={payload.get('saved_chars')} chars "
                f"({payload.get('saved_pct')}%)"
            )
        elif etype == "turn_limit":
            errors += 1
            activity.append(f"turn limit: {payload.get('kind')} limit={payload.get('limit')}")
        elif etype == "gateway_template":
            activity.append(
                f"gateway template: {payload.get('template')} target={payload.get('target') or ''}"
            )
        elif etype == "gateway_audit":
            errors += 1
            activity.append(f"gateway audit: {payload.get('warning') or payload.get('message') or ''}")
        elif etype == "memory_index":
            activity.append(
                f"memory index: {payload.get('turn_id') or ''} chars={payload.get('chars')} cleanup_removed={payload.get('cleanup_removed')}"
            )
        elif etype == "memory_recall":
            activity.append(f"memory recall: results={payload.get('results')} query={payload.get('query') or ''}")
        elif etype == "memory_cleanup":
            activity.append(f"memory cleanup: removed={payload.get('removed')} remaining={payload.get('remaining')}")
        elif etype == "memory_fts5_warning":
            errors += 1
            activity.append(f"memory warning: {payload.get('message') or ''}")
        elif etype == "sandbox_guard":
            activity.append(f"sandbox guard: {payload.get('tool')} lane={payload.get('lane') or '-'}")
        elif etype == "sandbox_blocked":
            errors += 1
            activity.append(f"sandbox blocked: {payload.get('tool')} {payload.get('reason') or ''}")
        elif etype == "lane_detect":
            activity.append(f"lane detect: {payload.get('lane')} input={payload.get('input') or ''}")
        elif etype == "goal_step":
            activity.append(
                f"goal step: {payload.get('iteration')} {payload.get('step_id')} {payload.get('status')} — {payload.get('title') or ''}"
            )
        elif etype == "goal_auditor":
            if not payload.get('approved'):
                errors += 1
            activity.append(f"goal auditor: approved={payload.get('approved')} findings={payload.get('findings') or []}")
        elif etype == "goal_finish":
            activity.append(
                f"goal finish: {payload.get('state')} {payload.get('completed')}/{payload.get('total')} — {payload.get('reason') or ''}"
            )
        elif etype == "live_steer":
            activity.append(f"live steer: count={payload.get('count')} {payload.get('preview') or ''}")
        elif etype == "ghost_event":
            activity.append(
                f"ghost {payload.get('kind') or 'event'}: route={payload.get('route') or '-'} "
                f"user={payload.get('user_preview') or ''} response={payload.get('response_preview') or ''}"
            )
        elif etype == "code_graph_context":
            activity.append(
                f"code graph: {payload.get('status')} files={payload.get('file_count')} selected={payload.get('selected_count')} "
                f"reason={payload.get('reason') or ''}"
            )
        elif etype == "structural_graph_update_started":
            activity.append(f"graph: structuring... ({payload.get('reason', '')})")
        elif etype == "structural_graph_update_ok":
            activity.append(f"graph: updated ({payload.get('reason', '')})")
        elif etype == "structural_graph_update_failed":
            activity.append(f"graph: update failed ({payload.get('reason', '')})")
        elif etype == "design_quality":
            findings = payload.get("findings") or []
            activity.append(f"design quality: checked={payload.get('checked')} findings={len(findings) if isinstance(findings, list) else '?'}")
        elif etype == "worker_event":
            activity.append(
                f"worker: {payload.get('worker_id')} {payload.get('state')} {payload.get('kind')}/{payload.get('route')} — "
                f"{payload.get('objective') or ''}"
            )

    print(f"summary: provider_requests={provider_requests} tool_calls={tool_calls} warnings={errors}")
    if open_provider:
        payload = open_provider["payload"]
        age = int(now - float(open_provider["ts"]))
        surface = payload.get("surface") or "main"
        print(f"LIVE: waiting on {surface} provider #{payload.get('request')} {payload.get('provider')}/{payload.get('model')} for {age}s")
        print(f"      messages={payload.get('messages')} tools={payload.get('tools')}")
    elif open_tool:
        payload = open_tool["payload"]
        age = int(now - float(open_tool["ts"]))
        surface = payload.get("surface") or "main"
        print(f"LIVE: running {surface} tool #{payload.get('request')} {payload.get('tool')} for {age}s {payload.get('summary') or ''}".rstrip())
    else:
        print("LIVE: idle / last backend step completed")
    print("-" * 72)
    if last_taskboard:
        print(last_taskboard)
        print("-" * 72)
    print("backend activity:")
    for status in activity[-18:]:
        print(f"- {status}")


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def has_live_backend_work(events: list[dict]) -> bool:
    """True when elapsed LIVE time should keep repainting."""
    live_provider = False
    live_tool = False
    for event in events[-200:]:
        etype = event.get("type")
        if etype == "provider_request":
            live_provider = True
            live_tool = False
        elif etype in {"provider_response", "provider_error", "provider_fallback", "turn_limit"}:
            live_provider = False
        elif etype == "tool_call":
            live_tool = True
        elif etype == "tool_result":
            live_tool = False
    return live_provider or live_tool


def main() -> None:
    configure_utf8_stdio()
    log_path = resolve_log_path()
    last_size = -1
    events: list[dict] = []
    try:
        while True:
            current_size = log_path.stat().st_size if log_path.exists() else 0
            if current_size != last_size:
                events = read_events(log_path)
                render(events, log_path)
                last_size = current_size
            elif has_live_backend_work(events):
                # Only repaint unchanged logs while something is actually running,
                # so LIVE elapsed time moves without burning cycles while idle.
                render(events, log_path)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nmonitor closed")
