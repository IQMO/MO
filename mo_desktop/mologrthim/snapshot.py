"""Mologrthim's one read-only picture of MO's real work.

Every running MO (the heartbeat ledger), the project specialists beside them with a track record and rank built
only from checked work (worker history in the backend monitor: the architect's verdict and the measured
difficulty), candidates waiting for the user's yes, messages between MOs, learning and archive lights, MO Care's
reports and the personalization behind the brain. It reads records, never starts work, and every number traces
to a record or says there is none yet.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

RANK_TIERS = ((35, 3), (15, 2), (5, 1))
DIFFICULTY_POINTS = {"simple": 1, "moderate": 2, "complex": 3}
_MONITOR_TYPES = ("worker_event", "memory_index", "learning_auto_promote")
_PERSONALIZATION_SECONDS = 600.0
_ROLES_SECONDS = 30.0


def rank_for(points: int) -> int:
    """Rank tiers at 5, 15 and 35 points (the approved formula)."""
    return next((tier for floor, tier in RANK_TIERS if points >= floor), 0)


def track_record(workers: list[dict[str, Any]]) -> dict[str, Any]:
    """points = each report the architect verified x how hard it was (simple 1, moderate 2, complex 3)
    - 2 x each correction. Reports nobody checked count as runs only."""
    verified = [w for w in workers if w.get("verdict") == "accepted"]
    corrected = [w for w in workers if w.get("verdict") == "rejected"]
    points = sum(DIFFICULTY_POINTS.get(str(w.get("difficulty") or ""), 1) for w in verified) - 2 * len(corrected)
    graded = Counter(str(w.get("difficulty")) for w in workers if w.get("difficulty"))
    return {"runs": len(workers), "verified": len(verified), "corrected": len(corrected),
            "points": points, "rank": rank_for(points),
            "usual": graded.most_common(1)[0][0] if graded else ""}


def _goal(lines: Any) -> dict[str, Any]:
    text = " | ".join(str(line) for line in lines or [])
    state = re.search(r"state:\s*([a-z_]+)", text)
    progress = re.search(r"progress:\s*(\d+)/(\d+)", text)
    objective = re.search(r"objective:\s*([^|]+)", text)
    if not state:
        return {}
    return {"state": state.group(1), "done": int(progress.group(1)) if progress else 0,
            "total": int(progress.group(2)) if progress else 0,
            "objective": objective.group(1).strip() if objective else ""}


class FloorObserver:
    """Builds the snapshot; caches what is expensive (monitor files read incrementally, the personalization
    audit every ten minutes, project roles every thirty seconds). One observer per Mologrthim process."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config if isinstance(config, dict) else {}
        self._files: dict[str, dict[str, Any]] = {}
        self._personalization: tuple[float, dict[str, Any] | None] = (0.0, None)
        self._roles: dict[str, tuple[float, list[Any]]] = {}

    # ------------------------------------------------------------------ sources
    def _monitor_rows(self) -> list[dict[str, Any]]:
        from core.state.paths import resolve_state_path

        configured = str(os.environ.get("MO_BACKEND_MONITOR_DIR") or "").strip()
        folder = Path(configured) if configured else Path(resolve_state_path("logs/monitor", self.config))
        rows: list[dict[str, Any]] = []
        for log in sorted(folder.glob("backend_monitor-*.jsonl")) if folder.is_dir() else []:
            key = str(log)
            try:
                size = log.stat().st_size
            except OSError:
                continue
            entry = self._files.get(key)
            if entry is None or size < entry["offset"]:
                entry = {"offset": 0, "rows": []}
            if size > entry["offset"]:
                try:
                    with log.open("rb") as handle:
                        handle.seek(entry["offset"])
                        chunk = handle.read(size - entry["offset"])
                except OSError:
                    chunk = b""
                end = chunk.rfind(b"\n") + 1          # keep a half-written last line for the next read
                for raw in chunk[:end].splitlines():
                    if not any(kind.encode() in raw for kind in _MONITOR_TYPES):
                        continue
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if row.get("type") in _MONITOR_TYPES:
                        entry["rows"].append({"type": row["type"], "ts": float(row.get("ts") or 0.0),
                                              "run": str(row.get("run_id") or ""), "payload": row.get("payload") or {}})
                entry["offset"] += end
            self._files[key] = entry
            rows.extend(entry["rows"])
        return rows

    def _project_roles(self, project: str) -> list[Any]:
        at, roles = self._roles.get(project, (0.0, []))
        if time.monotonic() - at < _ROLES_SECONDS:
            return roles
        try:
            from core.skills import default_skill_roots, list_roles

            roots = default_skill_roots(project, None, config=self.config)
            roles = [role for role in list_roles(roots, project_cwd=project)
                     if str(getattr(role, "project_root", "") or "").strip()]
        except Exception:
            roles = []
        self._roles[project] = (time.monotonic(), roles)
        return roles

    def _brain(self) -> dict[str, Any] | None:
        at, data = self._personalization
        if data is not None and time.monotonic() - at < _PERSONALIZATION_SECONDS:
            return data
        try:
            from core.diagnostics.personalization import build_personalization_report
            from core.state.paths import mo_home

            report = build_personalization_report(state_root=mo_home(self.config))
            profile = report.get("profile") or {}
            learning = report.get("learning") or {}
            documents = profile.get("documents") or {}
            present = len(documents) - len(profile.get("missing") or [])
            data = {
                "profile": f"{present}/{len(documents)}" if documents else "",
                "memory_turns": int(learning.get("memory_turns") or 0),
                "recall": str(learning.get("recall_mode") or ""),
                "lessons_adopted": int(learning.get("confirmed_authorities") or 0)
                                   + int(learning.get("auto_promoted_authorities") or 0),
                "lessons_pending": int(learning.get("pending_review") or 0),
                "product_intents": int(learning.get("pending_product_intents") or 0),
                "verdict": str(report.get("verdict") or ""),
            }
        except Exception:
            data = None
        self._personalization = (time.monotonic(), data)
        return data

    # ------------------------------------------------------------------ snapshot
    def observe(self) -> dict[str, Any]:
        from core.runtime.backend_monitor import redact_monitor_text
        from core.runtime.instance import recent_instance_snapshots
        from core.runtime.surface_identity import DESKTOP_SURFACES, normalize_runtime_surface

        now = time.time()
        mos: list[dict[str, Any]] = []
        live_runs: set[str] = set()
        for item in recent_instance_snapshots(self.config, current_pid=-1, max_age_seconds=300, limit=16):
            surface = normalize_runtime_surface(item.get("surface"))
            if item.get("pid_alive") and item.get("monitor_run"):
                live_runs.add(str(item["monitor_run"]))
            if not item.get("pid_alive") or (surface != "terminal" and surface not in DESKTOP_SURFACES):
                continue
            turn = item.get("turn") if isinstance(item.get("turn"), dict) else {}
            board = item.get("taskboard") if isinstance(item.get("taskboard"), dict) else {}
            cwd = str(item.get("cwd") or "")
            mos.append({
                "id": str(item.get("instance_id") or ""), "pid": int(item.get("pid") or 0),
                "slot": str(item.get("slot") or ""), "cwd": cwd, "project": Path(cwd).name or cwd,
                "desktop": surface in DESKTOP_SURFACES, "busy": bool(turn.get("busy")),
                "request": redact_monitor_text(str(turn.get("request") or ""), 100),
                "task": {"open": int(board.get("open") or 0), "state": str(board.get("state") or ""),
                         "active": str(board.get("active_task_title") or ""),
                         "next": str(board.get("next_task_title") or ""), "title": str(board.get("title") or "")},
                "goal": _goal(item.get("goal")),
                "files": [Path(str(row.get("path") or "")).name for row in (item.get("recent_files") or [])[:4]
                          if isinstance(row, dict)],
                "computer": bool((item.get("computer_activity") or {}).get("active")),
                "model": str(item.get("model") or ""),
            })
        mos.sort(key=lambda m: (m["desktop"], m["project"].casefold(), m["slot"]))

        rows = self._monitor_rows()
        workers: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = row["payload"]
            if row["type"] != "worker_event" or payload.get("source") != "project-architect":
                continue
            wid = str(payload.get("worker_id") or "")
            if wid and (wid not in workers or row["ts"] >= workers[wid]["ts"]):
                workers[wid] = {**payload, "ts": row["ts"], "run": row.get("run", "")}

        projects = sorted({m["cwd"] for m in mos if m["cwd"] and not m["desktop"]})
        roles_by_project = {project: self._project_roles(project) for project in projects}
        role_homes = Counter(str(getattr(role, "role", "")).casefold()
                             for roles in roles_by_project.values() for role in roles)
        specialists: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for project in projects:
            norm = os.path.normcase(os.path.abspath(project))
            local_roles = {str(getattr(role, "role", "")).casefold() for role in roles_by_project[project]}
            # Older worker events carry no project_root: they match by role id only when exactly one open
            # project has that specialist, never by guess.
            mine = [w for w in workers.values()
                    if (os.path.normcase(os.path.abspath(str(w["project_root"]))) == norm if w.get("project_root")
                        else str(w.get("role") or "").casefold() in local_roles
                        and role_homes[str(w.get("role") or "").casefold()] == 1)]
            for role in roles_by_project[project]:
                role_id = str(getattr(role, "role", "") or "")
                history = sorted((w for w in mine if str(w.get("role") or "").casefold() == role_id.casefold()),
                                 key=lambda w: w["ts"])
                latest = history[-1] if history else {}
                reported = [w for w in history if w.get("state") == "completed"]
                last = reported[-1] if reported else {}
                state = str(latest.get("state") or "")
                if state in {"offered", "accepted", "running"} and latest.get("run") not in live_runs:
                    state = "interrupted"   # its MO closed mid-run: the work never reported
                specialists.append({
                    "project": Path(project).name, "cwd": project, "role": role_id,
                    "name": str(getattr(role, "name", "") or role_id),
                    "focus": str(getattr(role, "description", "") or ""),
                    "state": ("working" if state in {"offered", "accepted", "running"} else
                              "interrupted" if state == "interrupted" else
                              "blocked" if state == "blocked" else
                              "verified" if latest.get("verdict") == "accepted" else
                              "corrected" if latest.get("verdict") == "rejected" else
                              "reported" if state == "completed" else "idle"),
                    "now": redact_monitor_text(str(latest.get("objective") or ""), 220) if state in {
                        "offered", "accepted", "running", "blocked", "interrupted"} else "",
                    "note": redact_monitor_text(str(latest.get("note") or ""), 160),
                    "last_report": redact_monitor_text(str(last.get("result_summary") or ""), 260),
                    "last_verdict": str(last.get("verdict") or ""),
                    "last_reason": redact_monitor_text(str(last.get("verdict_reason") or ""), 260),
                    **track_record(history),
                })
            try:
                from core.skills import role_candidates

                for row in role_candidates.pending(self.config, project):
                    candidates.append({"project": Path(project).name, "cwd": project, "name": str(row.get("name") or ""),
                                       "role": str(row.get("role") or ""),
                                       "description": redact_monitor_text(str(row.get("description") or ""), 220),
                                       "why": redact_monitor_text(str(row.get("body") or ""), 900),
                                       "at": float(row.get("at") or 0.0)})
            except Exception:
                pass

        messages = self._messages(now)
        learning = None
        try:
            from core.learning.status import build_learning_status

            learning = build_learning_status(None, config=self.config).as_dict()
        except Exception:
            pass
        archive = [r for r in rows if r["type"] == "memory_index"]
        recent_archive = [r for r in archive if now - r["ts"] < 600]
        care, care_unshown = [], 0
        try:
            from core.systemcare.mo_care import recent_findings

            findings = [f for f in recent_findings(self.config) if not f.get("dismissed")]
            care = [{k: f.get(k) for k in ("id", "kind", "detail", "at", "source", "shown_by")} for f in findings[:5]]
            care_unshown = sum(1 for f in findings if not f.get("shown_by"))
        except Exception:
            pass

        called: list[dict[str, Any]] = []
        for r in archive[-20:]:
            called.append({"kind": "archive", "at": r["ts"], "text": "archive indexed a turn into memory"})
        for r in rows[-400:]:
            if r["type"] == "learning_auto_promote":
                called.append({"kind": "learning", "at": r["ts"], "text": "learning adopted a lesson"})
        for w in workers.values():
            if w.get("verdict"):
                called.append({"kind": "verified" if w["verdict"] == "accepted" else "refused", "at": w["ts"],
                               "text": f"review gate {'verified' if w['verdict'] == 'accepted' else 'refused'} "
                                       f"{w.get('role') or 'a specialist'}'s report"})
        slots = {m["id"]: m["slot"] for m in mos if m["slot"]}
        for m in messages:
            to = "every MO in the project" if m["to"] == "project" else slots.get(m["to"], m["to"])
            called.append({"kind": "message", "at": m["at"], "text": f"{m['from']} → {to} message"})
        for f in care:
            called.append({"kind": "care", "at": float(f.get("at") or 0), "text": f"MO Care: {f.get('kind')}"})
        called = sorted((c for c in called if now - c["at"] < 3600), key=lambda c: c["at"], reverse=True)[:8]

        owner_desk: list[dict[str, str]] = []
        try:
            from core.local_extensions import command_specs

            owner_desk = [{"name": str(spec.get("name") or spec.get("command") or ""),
                           "description": str(spec.get("description") or "")[:120]}
                          for spec in command_specs() if spec.get("name") or spec.get("command")][:8]
        except Exception:
            pass

        review = Counter({"accepted": sum(s["verified"] for s in specialists),
                          "rejected": sum(s["corrected"] for s in specialists)})
        return {
            "at": now, "mos": mos, "specialists": specialists, "candidates": candidates,
            "messages": messages, "learning": learning,
            "archive": {"recent": len(recent_archive), "last_at": archive[-1]["ts"] if archive else 0.0},
            "care": {"findings": care, "unshown": care_unshown},
            "review": {"verified": review.get("accepted", 0), "refused": review.get("rejected", 0)},
            "brain": self._brain(), "just_called": called, "owner_desk": owner_desk,
        }

    def _messages(self, now: float) -> list[dict[str, Any]]:
        from core.state.paths import resolve_state_path

        path = Path(resolve_state_path("run/mo-messages.jsonl", self.config))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-40:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            at = float(row.get("at") or 0.0)
            if now - at > 24 * 3600:
                continue
            out.append({"from": str(row.get("from_slot") or row.get("from_instance") or "MO"),
                        "from_id": str(row.get("from_instance") or ""), "to": str(row.get("to") or ""),
                        "text": str(row.get("text") or "")[:300], "at": at})
        return out[-10:]


def conversation(config: dict[str, Any] | None, slot: str, *, limit: int = 8) -> list[dict[str, str]]:
    """The last visible user/MO messages of one MO's saved conversation (read-only, redacted): how the brain
    talks with the user, for the selected bay."""
    from core.runtime.backend_monitor import redact_monitor_text
    from core.session.session import INTERNAL_CONTINUATION_KEY, is_runtime_owned_session_summary
    from core.state.paths import resolve_state_path

    if not re.fullmatch(r"[\w.-]{1,80}", str(slot or "")):
        return []
    path = Path(resolve_state_path("memory/sessions/conversations", config)) / f"{slot}.json"
    try:
        messages = json.loads(path.read_text(encoding="utf-8")).get("messages") or []
    except (OSError, ValueError, AttributeError):
        return []
    rows = []
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") not in ("user", "assistant")
                or message.get(INTERNAL_CONTINUATION_KEY) or is_runtime_owned_session_summary(message)):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", "")) for part in content
                                if isinstance(part, dict) and part.get("type") == "text")
        if isinstance(content, str) and content.strip() and not content.startswith("[Turn interrupted"):
            rows.append({"role": message["role"], "text": redact_monitor_text(content, 600)})
    return rows[-limit:]
