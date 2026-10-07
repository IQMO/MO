"""Inventory: one read-only basket of everything MO did with the user, newest first, today in front.

Every item comes from an existing record: saved conversations, taskboards, goals, the files MO changed (the
per-session file-operation summary), generated media and attachments, MO Design documents, lessons MO adopted
and the projects' recent commits. Nothing is diagnostic and nothing is invented; an item carries only what its
record says.
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import time
from pathlib import Path
from typing import Any

KINDS = ("conversation", "task", "goal", "file", "picture", "media", "design", "lesson", "commit")
_PICTURE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MEDIA = {".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".webm", ".mov"}


def _redact(text: Any, limit: int) -> str:
    from core.runtime.backend_monitor import redact_monitor_text

    return redact_monitor_text(" ".join(str(text or "").split()), limit)


class InventoryReader:
    """Builds the basket; thumbnails and commits are cached so a refresh stays cheap."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config if isinstance(config, dict) else {}
        self._thumbs: dict[str, str] = {}
        self._commits: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._design_rows: tuple[float, list[dict[str, Any]]] = (-1e9, [])

    def _path(self, relative: str) -> Path:
        from core.state.paths import resolve_state_path

        return Path(resolve_state_path(relative, self.config))

    def items(self, *, limit: int = 160) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for reader in (self._conversations, self._tasks, self._goals, self._files, self._media, self._designs,
                       self._lessons, self._commits_items):
            try:
                rows.extend(reader())
            except Exception:
                continue
        rows.sort(key=lambda row: float(row.get("at") or 0.0), reverse=True)
        return rows[:limit]

    # ------------------------------------------------------------------ readers
    def _conversations(self) -> list[dict[str, Any]]:
        out = []
        folder = self._path("memory/sessions/conversations")
        for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            first = next((m.get("content") for m in data.get("messages") or []
                          if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)
                          and not m.get("content", "").startswith("[")), "")
            if not first:
                continue
            surface = str((data.get("meta") or {}).get("surface") or "")
            out.append({"id": f"conversation:{path.stem}", "kind": "conversation", "title": _redact(first, 90),
                        "sub": ("Desktop" if "desktop" in surface else "Terminal") + f" · {int(data.get('turn_count') or 0)} turns",
                        "at": float(data.get("saved_at") or path.stat().st_mtime), "ref": f"saved conversation {path.stem}"})
        return out

    def _tasks(self) -> list[dict[str, Any]]:
        path = self._path("memory/work/taskboards/taskboards.jsonl")
        latest: dict[str, dict[str, Any]] = {}
        try:
            with path.open(encoding="utf-8") as handle:
                lines = handle.readlines()[-600:]
        except OSError:
            return []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("board_id"):
                latest[row["board_id"]] = row
        out = []
        for board in latest.values():
            title = board.get("objective") or board.get("title")     # the user's request, not the plan's generic title
            if not title:
                continue
            tasks = [t for t in board.get("tasks") or [] if isinstance(t, dict)]
            done = sum(1 for t in tasks if t.get("status") == "completed")
            out.append({"id": f"task:{board['board_id']}", "kind": "task", "title": _redact(title, 90),
                        "sub": f"{board.get('state') or 'board'} · {done}/{len(tasks)} done" if tasks else str(board.get("state") or "board"),
                        "at": float(board.get("updated_at") or board.get("created_at") or 0.0),
                        "ref": f"taskboard '{_redact(title, 120)}' ({board.get('state') or 'board'})"})
        return out

    def _goals(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self._path("memory/work/goals").glob("*.json"))[-20:]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("objective"):
                out.append({"id": f"goal:{path.stem}", "kind": "goal", "title": _redact(data["objective"], 90),
                            "sub": "goal · finished" if data.get("finished_at") else "goal",
                            "at": float(data.get("finished_at") or data.get("started_at") or path.stat().st_mtime),
                            "ref": f"goal '{_redact(data['objective'], 160)}'"})
        return out

    def _files(self) -> list[dict[str, Any]]:
        path = self._path("logs/file_operations.jsonl")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-200:]
        except OSError:
            return []
        seen: dict[str, dict[str, Any]] = {}
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            for name in row.get("files_modified") or []:
                seen.pop(str(name), None)            # re-insert: newest change last
                seen[str(name)] = {"id": f"file:{name}", "kind": "file", "title": Path(str(name)).name,
                                   "sub": f"changed · {_redact(Path(str(name)).parent, 60)}", "at": float(row.get("closed_at") or 0.0),
                                   "ref": f"file {name}", "path": str(name)}
        return list(seen.values())[-30:]             # the newest 30: the basket is not a file browser

    def _media(self) -> list[dict[str, Any]]:
        out = []
        root = self._path("media/generated")
        files = [p for p in root.rglob("*") if p.suffix.lower() in _PICTURE | _MEDIA] if root.is_dir() else []
        for rank, path in enumerate(sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:40]):
            suffix = path.suffix.lower()
            kind = "picture" if suffix in _PICTURE else "media"
            out.append({"id": f"{kind}:{path}", "kind": kind, "title": path.name[:60],
                        "sub": "generated · " + ("song" if suffix in {".mp3", ".wav", ".m4a", ".ogg"} else "video" if kind == "media" else "image"),
                        "at": path.stat().st_mtime, "ref": f"{'image' if kind == 'picture' else 'media'} {path}",
                        "path": str(path), "thumb": self._thumb(path) if kind == "picture" and rank < 24 else ""})
        index = self._path("media/attachments/index.jsonl")
        try:
            lines = index.read_text(encoding="utf-8").splitlines()[-80:]
        except OSError:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            saved = Path(str(row.get("saved_path") or ""))
            suffix = saved.suffix.lower()
            kind = "picture" if suffix in _PICTURE else "media" if suffix in _MEDIA else "file"
            out.append({"id": f"attachment:{row.get('id')}", "kind": kind, "title": _redact(row.get("name") or saved.name, 60),
                        "sub": f"attached · {row.get('origin') or row.get('category') or 'MO'}", "at": float(row.get("attached_at") or 0.0),
                        "ref": f"attachment {saved}", "path": str(saved), "thumb": self._thumb(saved) if kind == "picture" else ""})
        return out

    def _designs(self) -> list[dict[str, Any]]:
        at, cached = self._design_rows
        if time.monotonic() - at < 120:          # list_designs reads every document: seconds, so twice a minute at most
            return cached
        if not getattr(self, "_designs_loading", False):
            # Off the refresh path: the basket appears without designs first; they join the next refresh.
            import threading

            self._designs_loading = True
            threading.Thread(target=self._load_designs, name="inventory-designs", daemon=True).start()
        return cached

    def _load_designs(self) -> None:
        try:
            self._design_rows = (time.monotonic(), self._read_designs())
        except Exception:
            self._design_rows = (time.monotonic(), [])
        finally:
            self._designs_loading = False

    def _read_designs(self) -> list[dict[str, Any]]:
        from datetime import datetime

        from core.design.service import list_designs

        def stamp(value: Any) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                try:
                    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    return 0.0

        out = []
        for row in list_designs(config=self.config, limit=24):
            name = row.get("title") or row.get("name") or Path(str(row.get("path") or "design")).stem
            out.append({"id": f"design:{row.get('id') or row.get('path')}", "kind": "design", "title": _redact(name, 80),
                        "sub": "MO Design", "at": stamp(row.get("updated_at") or row.get("modified_at")),
                        "ref": f"MO Design '{_redact(name, 120)}' ({row.get('path') or ''})"})
        return out

    def _lessons(self) -> list[dict[str, Any]]:
        path = self._path("memory/learning/suggestions.jsonl")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-200:]
        except OSError:
            return []
        latest: dict[str, dict[str, Any]] = {}
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("id"):
                latest[row["id"]] = row
        return [{"id": f"lesson:{row['id']}", "kind": "lesson", "title": _redact(row.get("recommendation"), 90),
                 "sub": "lesson MO adopted", "at": float(row.get("auto_promoted_at") or row.get("updated_at") or 0.0),
                 "ref": f"lesson '{_redact(row.get('recommendation'), 160)}'"}
                for row in latest.values()
                if (row.get("status") in {"confirmed", "promoted"} or row.get("auto_promoted"))
                and row.get("status") not in {"superseded", "expired", "dismissed", "retired"}]

    def _commits_items(self) -> list[dict[str, Any]]:
        at, cached = self._commits
        if time.monotonic() - at < 120:
            return cached
        projects = set()
        try:
            from core.runtime.instance import recent_instance_snapshots

            projects = {str(row.get("cwd") or "") for row in recent_instance_snapshots(self.config, current_pid=-1,
                                                                                        max_age_seconds=300, limit=16)}
        except Exception:
            pass
        out = []
        for project in sorted(p for p in projects if p and (Path(p) / ".git").exists()):
            try:
                log = subprocess.run(["git", "-C", project, "log", "-n", "12", "--since=7.days", "--format=%h%x09%ct%x09%s"],
                                     capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                continue
            for line in log.stdout.splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    out.append({"id": f"commit:{project}:{parts[0]}", "kind": "commit", "title": _redact(parts[2], 90),
                                "sub": f"{Path(project).name} · {parts[0]}", "at": float(parts[1]),
                                "ref": f"commit {parts[0]} in {project}: {_redact(parts[2], 120)}"})
        self._commits = (time.monotonic(), out)
        return out

    def _thumb(self, path: Path) -> str:
        key = f"{path}:{path.stat().st_mtime if path.exists() else 0}"
        if key in self._thumbs:
            return self._thumbs[key]
        data = ""
        try:
            from PIL import Image

            with Image.open(path) as image:
                image.thumbnail((240, 150))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=78)
            data = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            data = ""
        if len(self._thumbs) > 120:
            self._thumbs.clear()
        self._thumbs[key] = data
        return data


def running_terminals(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The running MO Terminals to send to, with what each is on (heartbeat ledger)."""
    from core.runtime.instance import recent_instance_snapshots
    from core.runtime.surface_identity import normalize_runtime_surface

    out = []
    for row in recent_instance_snapshots(config or {}, current_pid=-1, max_age_seconds=300, limit=16):
        if not row.get("pid_alive") or normalize_runtime_surface(row.get("surface")) != "terminal":
            continue
        turn = row.get("turn") if isinstance(row.get("turn"), dict) else {}
        board = row.get("taskboard") if isinstance(row.get("taskboard"), dict) else {}
        on = turn.get("request") or board.get("active_task_title") or board.get("next_task_title") or "idle"
        cwd = str(row.get("cwd") or "")
        out.append({"id": str(row.get("instance_id") or ""), "pid": int(row.get("pid") or 0), "slot": str(row.get("slot") or ""),
                    "cwd": cwd, "project": Path(cwd).name or cwd, "on": _redact(on, 60), "busy": bool(turn.get("busy"))})
    return out


def compose_request(items: list[dict[str, Any]]) -> str:
    """The prepared message a terminal receives: what the user picked, each with its record reference."""
    lines = ["From my Inventory, I picked these:"]
    lines += [f"- {item.get('kind')}: {item.get('ref') or item.get('title')}" for item in items]
    return "\n".join(lines)
