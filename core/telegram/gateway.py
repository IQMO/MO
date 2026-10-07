"""Telegram gateway for MO.

The gateway is intentionally thin: Telegram owns transport/auth/session mapping;
MO's ``Gateway.run_turn`` owns work execution and taskboard truth.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import hashlib
import io
import os
import queue
import re
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from core.agent.agent_utils import load_session_from_manager
from core.agent.slash_result import command_result
from core.telegram.auto_reply import maybe_auto_reply
from core.runtime.backend_monitor import get_monitor, redact_monitor_text
from core.runtime.heartbeat import record_heartbeat
from core.state.paths import resolve_state_path
from core.runtime.lock import acquire_runtime_lock, release_runtime_lock
from core.state.secrets import resolve_secret, secret_status
from core.session.session import Session
from core.tasking.task_board import attach_taskboard_to_text

from .auth import TelegramAuthStore
from .formatting import compact_for_telegram
from .sessions import TelegramSessionStore


class _TelegramReplyReady(Exception):
    """Internal worker short-circuit after a transport-level reply is prepared."""


def _telegram_command_reply(raw_result: Any) -> str:
    if raw_result is None:
        return ""
    result = command_result(raw_result)
    if result.action in {"exit", "terminal", "goal_start", "goal_continue", "retry", "run_turn"}:
        return "This command requires an interactive MO terminal."
    return result.plain_text


@dataclass
class TelegramJob:
    sender_id: str
    chat_id: str
    text: str
    chat_type: str
    client: Any
    base: str
    message_id: int | None
    files: tuple[dict[str, Any], ...] = ()
    source_message_id: int = 0


@dataclass
class TelegramGateway:
    agent: Any
    enabled: bool
    token_env: str
    dm_policy: str
    auth: TelegramAuthStore
    sessions: TelegramSessionStore
    gateway: Any = None
    allow_from: tuple[str, ...] = ()
    groups_require_mention: bool = True
    groups_allow_from: tuple[str, ...] = ()
    bot_username: str = ""
    worker_count: int = 2
    config: dict[str, Any] = field(default_factory=dict, repr=False)
    cancel_events: dict[str, threading.Event] = field(default_factory=dict)
    job_queues: dict[str, queue.Queue] = field(default_factory=dict)
    job_threads: dict[str, threading.Thread] = field(default_factory=dict)
    active_chats: set[str] = field(default_factory=set)
    steer_buffers: dict[str, list[str]] = field(default_factory=dict)
    continuity_choice_sets: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    completed_jobs: int = 0
    failed_jobs: int = 0
    queue_lock: threading.Lock = field(default_factory=threading.Lock)
    agent_lock: threading.RLock = field(default_factory=threading.RLock)
    _worker_semaphore: threading.BoundedSemaphore = field(init=False, repr=False)
    _poll_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop_event: threading.Event | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._worker_semaphore = threading.BoundedSemaphore(max(1, int(self.worker_count or 1)))
        if self.gateway is None:
            self.gateway = getattr(self.agent, "gateway", None)

    @classmethod
    def from_agent(cls, agent: Any, gateway: Any = None) -> "TelegramGateway":
        cfg = (getattr(agent, "config", {}) or {}).get("telegram", {}) or {}
        path = resolve_state_path(cfg.get("db_path") or "memory/surfaces/telegram.sqlite", getattr(agent, "config", {}) or {})
        groups = cfg.get("groups", {}) or {}
        return cls(
            agent=agent,
            gateway=gateway or getattr(agent, "gateway", None),
            enabled=bool(cfg.get("enabled", DEFAULT_PREFERENCES["telegram.enabled"])),
            token_env=str(cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")),
            dm_policy=str(cfg.get("dm_policy", DEFAULT_PREFERENCES["telegram.dm_policy"])),
            auth=TelegramAuthStore(path),
            sessions=TelegramSessionStore(path),
            allow_from=tuple(str(x) for x in (cfg.get("allow_from") or [])),
            groups_require_mention=bool(groups.get("require_mention", True)),
            groups_allow_from=tuple(str(x) for x in (groups.get("allow_from") or [])),
            bot_username=str(cfg.get("bot_username") or os.getenv("TELEGRAM_BOT_USERNAME", "")).lstrip("@"),
            worker_count=max(1, int(cfg.get("worker_count", 2) or 2)),
            config=getattr(agent, "config", {}) or {},
        )

    def status(self) -> dict[str, Any]:
        paired, pending = self.auth.counts()
        token_status = secret_status(self.token_env, config=self.config, service="telegram")
        queue_depths = {chat: q.qsize() for chat, q in self.job_queues.items()}
        unfinished = {chat: getattr(q, "unfinished_tasks", q.qsize()) for chat, q in self.job_queues.items()}
        return {
            "enabled": self.enabled,
            "running": bool(self._poll_thread and self._poll_thread.is_alive()),
            "token_env": self.token_env,
            "token_present": bool(token_status.present),
            "token_source": token_status.source,
            "dm_policy": self.dm_policy,
            "paired": paired,
            "pending": pending,
            "sessions": self.sessions.count(),
            "groups_require_mention": self.groups_require_mention,
            "allowlist_static": len(self.allow_from),
            "groups_allowlist_static": len(self.groups_allow_from),
            "worker_count": self.worker_count,
            "active_chat_workers": len([t for t in self.job_threads.values() if t.is_alive()]),
            "pending_jobs": sum(q.qsize() for q in self.job_queues.values()),
            "unfinished_jobs": sum(unfinished.values()),
            "queue_depths": queue_depths,
            "active_chats": sorted(self.active_chats),
            "queued_steer": sum(len(v) for v in self.steer_buffers.values()),
            "completed_jobs": self.completed_jobs,
            "failed_jobs": self.failed_jobs,
        }

    def queue_report(self) -> str:
        st = self.status()
        lines = [
            "telegram queue:",
            f"  workers: active_chats={st['active_chat_workers']} configured={st['worker_count']}",
            f"  jobs:    pending={st['pending_jobs']} unfinished={st['unfinished_jobs']} steer={st['queued_steer']} completed={st['completed_jobs']} failed={st['failed_jobs']}",
        ]
        depths = st.get("queue_depths") or {}
        if depths:
            lines.append("  chats:")
            for chat, depth in sorted(depths.items()):
                lines.append(f"    {chat}: pending={depth}")
        return "\n".join(lines)

    def session_report(self, *, limit: int = 10) -> str:
        rows = self.sessions.list_mappings(limit=limit)
        lines = ["telegram chats:"]
        if not rows:
            lines.append("  none")
            return "\n".join(lines)
        for row in rows:
            active = " active" if row["chat_id"] in self.active_chats else ""
            pending = self.job_queues.get(row["chat_id"]).qsize() if row["chat_id"] in self.job_queues else 0
            lines.append(f"  chat={row['chat_id']} -> {row['session_name']} pending={pending}{active}")
        return "\n".join(lines)

    def _ignores_group_message(self, text: str, chat_type: str) -> bool:
        """Return True when a group message should be ignored silently."""
        if str(chat_type or "") not in {"group", "supergroup"} or not self.groups_require_mention:
            return False
        mention = f"@{self.bot_username}" if self.bot_username else ""
        return not mention or mention.lower() not in str(text or "").lower()

    def approve(self, code: str) -> bool:
        return self.auth.approve(code)

    def authorize_or_pair(self, sender_id: str, *, chat_type: str = "private") -> tuple[bool, str]:
        sender_id = str(sender_id)
        chat_type = str(chat_type or "private")
        if chat_type in {"group", "supergroup"} and self.groups_allow_from and sender_id not in self.groups_allow_from:
            return False, "Telegram group sender not allowlisted."
        if sender_id in self.allow_from or sender_id in self.groups_allow_from:
            return True, "authorized"
        if self.dm_policy == "disabled":
            return False, "Telegram DM access disabled."
        if self.auth.is_allowed(sender_id):
            return True, "authorized"
        if self.dm_policy == "pairing":
            code = self.auth.create_pairing(sender_id)
            return False, f"Pairing required. Approve locally with /telegram approve {code.code}"
        return False, "Telegram sender not allowlisted."

    def _sender_is_authorized(
        self, sender_id: str, chat_type: str = "private"
    ) -> bool:
        """Read current Telegram authority without creating a pairing request."""
        sender = str(sender_id)
        kind = str(chat_type or "private")
        if (
            kind in {"group", "supergroup"}
            and self.groups_allow_from
            and sender not in self.groups_allow_from
        ):
            return False
        return (
            sender in self.allow_from
            or sender in self.groups_allow_from
            or self.auth.is_allowed(sender)
        )

    def stop_chat(self, chat_id: str) -> None:
        self.cancel_events.setdefault(str(chat_id), threading.Event()).set()

    def stop(self, timeout: float = 3.0) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        thread = self._poll_thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        release_runtime_lock(getattr(self, "_runtime_lock", None))
        self._runtime_lock = None

    def send_system_message(self, chat_id: str, text: str) -> bool:
        """Deliver one bounded product notice to an already-authorized DM."""
        target = str(chat_id or "")
        message = str(text or "").strip()
        if (
            not target.isdigit()
            or int(target) <= 0
            or not self._sender_is_authorized(target, "private")
            or not message
            or len(message) > 3500
        ):
            return False
        token = _resolve_secret(self.token_env, config=self.config)
        if not token:
            return False
        try:
            import httpx

            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": target, "text": message},
                )
                if int(getattr(response, "status_code", 0) or 0) != 200:
                    return False
                payload = response.json()
                return isinstance(payload, dict) and payload.get("ok") is True
        except Exception:
            return False

    def _handle_stop(self, *, sender_id: str, chat_id: str, chat_type: str = "private") -> str:
        ok, msg = self.authorize_or_pair(str(sender_id), chat_type=chat_type)
        if not ok:
            return msg
        self.stop_chat(str(chat_id))
        try:
            if hasattr(self.agent, "process_slash_command"):
                reply = _telegram_command_reply(self.agent.process_slash_command("/stop"))
                return compact_for_telegram(reply or "stop requested")
        except Exception as exc:
            return f"stop requested; cleanup unavailable: {type(exc).__name__}: {exc}"
        return "stop requested"

    def _chat_worker(self, chat_id: str) -> None:
        q = self.job_queues[str(chat_id)]
        while True:
            try:
                job = q.get(timeout=0.25)
            except queue.Empty:
                with self.queue_lock:
                    if q.empty():
                        self.job_threads.pop(str(chat_id), None)
                        self.job_queues.pop(str(chat_id), None)
                        return
                continue
            slot_acquired = False
            try:
                self._worker_semaphore.acquire()
                slot_acquired = True
                with self.queue_lock:
                    self.active_chats.add(str(chat_id))
                try:
                    text = job.text
                    if job.files:
                        ok, message = self.authorize_or_pair(
                            job.sender_id, chat_type=job.chat_type
                        )
                        if not ok:
                            reply = message
                            raise _TelegramReplyReady
                        attached = self._receive_telegram_files(job)
                        paths = ", ".join(str(item) for item in attached)
                        caption = str(text or "").strip()
                        text = (
                            (caption + "\n\n" if caption else "")
                            + f"[Operator attached {len(attached)} Telegram "
                            f"file(s). They were delivered through MO file transfer "
                            f"and saved in the shared catalog: {paths}. Use the file "
                            "tools to inspect them when relevant.]"
                        )
                    reply = self.handle_text(
                        sender_id=job.sender_id,
                        chat_id=job.chat_id,
                        text=text,
                        chat_type=job.chat_type,
                        clear_cancel=False,
                    )
                except _TelegramReplyReady:
                    pass
                except Exception as exc:
                    detail = redact_monitor_text(exc, 240)
                    reply = "\n".join([
                        "MO telegram error: turn failed",
                        "where: Telegram gateway",
                        "next: try again; use /status in MO if this repeats.",
                        f"detail: {detail}",
                    ])
                if reply:
                    self._deliver_reply(job.client, job.base, job.chat_id, reply, message_id=job.message_id)
                if self._sender_is_authorized(job.sender_id, job.chat_type):
                    self._deliver_transfer_notices(
                        job.client,
                        job.base,
                        include_unassigned=True,
                        fallback_chat_id=job.chat_id,
                    )
                self.completed_jobs += 1
            except Exception:
                self.failed_jobs += 1
            finally:
                with self.queue_lock:
                    self.active_chats.discard(str(chat_id))
                if slot_acquired:
                    self._worker_semaphore.release()
                q.task_done()

    def enqueue_text(
        self,
        *,
        sender_id: str,
        chat_id: str,
        text: str,
        chat_type: str,
        client: Any,
        base: str,
        message_id: int | None,
        files: tuple[dict[str, Any], ...] = (),
        source_message_id: int = 0,
    ) -> None:
        chat_key = str(chat_id)
        with self.queue_lock:
            clean_text = str(text)
            if chat_key in self.active_chats and _is_stop_text(clean_text):
                reply = self._handle_stop(sender_id=str(sender_id), chat_id=chat_key, chat_type=str(chat_type or "private"))
                self._deliver_reply(client, base, chat_key, reply or "stop requested", message_id=message_id)
                return
            if (
                chat_key in self.active_chats
                and not files
                and clean_text.strip()
                and not clean_text.strip().startswith("/")
            ):
                # Enforce the same allowlist as the job path before steering text
                # into a live turn — otherwise a non-authorized group member could
                # inject into an active turn (auth was only checked in handle_text).
                ok, msg = self.authorize_or_pair(str(sender_id), chat_type=str(chat_type or "private"))
                if not ok:
                    self._deliver_reply(client, base, chat_key, msg, message_id=message_id)
                    return
                injected = False
                injector = getattr(self.agent, "add_live_steer", None)
                if callable(injector):
                    try:
                        injected = bool(injector(clean_text, source="telegram", worker_id=f"telegram-{chat_key}"))
                    except Exception:
                        injected = False
                if not injected:
                    self.steer_buffers.setdefault(chat_key, []).append(clean_text)
                self._deliver_reply(client, base, chat_key, "queued as steer for active turn", message_id=message_id)
                return
            self.cancel_events.setdefault(chat_key, threading.Event()).clear()
            q = self.job_queues.setdefault(chat_key, queue.Queue())
            q.put(
                TelegramJob(
                    sender_id=str(sender_id),
                    chat_id=chat_key,
                    text=clean_text,
                    chat_type=str(chat_type or "private"),
                    client=client,
                    base=base,
                    message_id=message_id,
                    files=tuple(files),
                    source_message_id=max(0, int(source_message_id or 0)),
                )
            )
            thread = self.job_threads.get(chat_key)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(target=self._chat_worker, args=(chat_key,), daemon=True, name=f"mo-tg-{chat_key}")
                self.job_threads[chat_key] = thread
                thread.start()

    def pop_steer(self, chat_id: str) -> str | None:
        with self.queue_lock:
            items = self.steer_buffers.get(str(chat_id)) or []
            if not items:
                return None
            value = items.pop(0)
            if not items:
                self.steer_buffers.pop(str(chat_id), None)
            return value

    def wait_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            with self.queue_lock:
                queues = list(self.job_queues.values())
                threads = list(self.job_threads.values())
            if not queues and not any(t.is_alive() for t in threads):
                return True
            if all(q.unfinished_tasks == 0 for q in queues):
                for t in threads:
                    t.join(timeout=0.05)
                with self.queue_lock:
                    if not self.job_queues:
                        return True
            time.sleep(0.02)
        return False

    def handle_text(self, *, sender_id: str, chat_id: str, text: str, chat_type: str = "private", clear_cancel: bool = True) -> str:
        chat_type = str(chat_type or "private")
        text = str(text or "")
        if chat_type in {"group", "supergroup"} and self.groups_require_mention:
            mention = f"@{self.bot_username}" if self.bot_username else ""
            if not mention or mention.lower() not in text.lower():
                return ""
            text = re.sub(re.escape(mention), "", text, flags=re.I).strip()
        ok, msg = self.authorize_or_pair(str(sender_id), chat_type=chat_type)
        if not ok:
            return msg
        handoff_adapter = getattr(self, "_conversation_handoff_adapter", None)
        handoff_handler = getattr(handoff_adapter, "handle_command", None)
        if callable(handoff_handler):
            handoff_reply = handoff_handler(
                sender_id=str(sender_id),
                chat_id=str(chat_id),
                chat_type=chat_type,
                text=text,
            )
            if handoff_reply is not None:
                return compact_for_telegram(handoff_reply)
        session_name = self.sessions.get_or_create(str(chat_id))
        clean_text = str(text or "").strip()
        if _is_stop_text(clean_text):
            return self._handle_stop(sender_id=str(sender_id), chat_id=str(chat_id), chat_type=chat_type)
        if chat_type not in {"group", "supergroup"}:
            continuity_reply = self._handle_continuity_choice(
                clean_text,
                chat_id=str(chat_id),
                session_name=session_name,
            )
            if continuity_reply is not None:
                return compact_for_telegram(continuity_reply)
        auto = maybe_auto_reply(clean_text, agent=self.agent, gateway=self.gateway, surface="telegram")
        if auto:
            record_heartbeat(
                self.agent,
                gateway=self.gateway,
                surface="telegram",
                event=f"auto_reply:{auto.reason}",
                extra={"chat_id": str(chat_id), "chat_type": chat_type},
            )
            return compact_for_telegram(auto.text)
        cancel_event = self.cancel_events.setdefault(str(chat_id), threading.Event())
        if clear_cancel:
            cancel_event.clear()
        with self.agent_lock:
            with self._session_context(session_name):
                if clean_text.startswith("/") and hasattr(self.agent, "process_slash_command"):
                    result = self.agent.process_slash_command(clean_text, surface="telegram")
                    return compact_for_telegram(_telegram_command_reply(result))
                reply = self._run_mo_turn(
                    clean_text,
                    cancel_event=cancel_event,
                    chat_id=str(chat_id),
                    chat_type=chat_type,
                    session_name=session_name,
                )
                mark_running = getattr(handoff_adapter, "mark_running", None)
                if callable(mark_running) and chat_type == "private":
                    mark_running(chat_id=str(chat_id), session_name=session_name)
                return compact_for_telegram(reply)

    def _handle_continuity_choice(self, text: str, *, chat_id: str, session_name: str) -> str | None:
        """Turn ambiguous cross-surface resume into a bounded Telegram choice."""
        raw = str(text or "").strip()
        command, _, argument = raw.partition(" ")
        explicit = command.lower() == "/continue"
        if not explicit:
            try:
                from core.runtime.turn_intent import looks_like_continuity_request

                if not looks_like_continuity_request(raw):
                    return None
            except Exception:
                return None
        try:
            from core.state.surface_handoff import (
                bind_continuity_thread,
                continuity_thread_choices,
                has_cross_surface_continuity_binding,
                pending_handoff,
            )

            choices = continuity_thread_choices(
                self.agent,
                target_surface="telegram",
                target_key=session_name,
            )
        except Exception:
            return None
        if not explicit:
            try:
                if pending_handoff(
                    self.agent,
                    target_surface="telegram",
                    target_key=session_name,
                ) is not None or has_cross_surface_continuity_binding(
                    self.agent,
                    target_surface="telegram",
                    target_key=session_name,
                ):
                    return None
            except Exception:
                pass
        if not explicit and len(choices) <= 1:
            if len(choices) == 1:
                try:
                    selected = pending_handoff(
                        self.agent,
                        target_surface="telegram",
                        target_key=session_name,
                    )
                    if selected is None:
                        bind_continuity_thread(
                            self.agent,
                            target_surface="telegram",
                            target_key=session_name,
                            thread_id=str(choices[0].get("thread_id") or ""),
                        )
                except Exception:
                    pass
            return None
        if explicit and argument.strip():
            selected = self._select_continuity_choice(chat_id, argument, choices)
            if selected is None:
                return self._render_continuity_choices(chat_id, choices, invalid=True)
            bind_continuity_thread(
                self.agent,
                target_surface="telegram",
                target_key=session_name,
                thread_id=str(selected.get("thread_id") or ""),
            )
            self.continuity_choice_sets.pop(chat_id, None)
            return _continuity_following_reply(selected, choices)
        if explicit and len(choices) == 1:
            selected = choices[0]
            bind_continuity_thread(
                self.agent,
                target_surface="telegram",
                target_key=session_name,
                thread_id=str(selected.get("thread_id") or ""),
            )
            return _continuity_following_reply(selected, choices)
        return self._render_continuity_choices(chat_id, choices)

    def _select_continuity_choice(
        self,
        chat_id: str,
        argument: str,
        choices: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        token = str(argument or "").strip().lower()
        remembered = self.continuity_choice_sets.get(chat_id, ())
        if token.isdigit() and remembered:
            index = int(token) - 1
            if 0 <= index < len(remembered):
                wanted, reference = remembered[index]
                selected = next((item for item in choices if str(item.get("thread_id") or "") == wanted), None)
                if selected is not None:
                    return {**selected, "_selection_reference": reference}
        references = _continuity_choice_references(choices)
        referenced = next(
            (
                item for item in choices
                if references.get(str(item.get("thread_id") or ""), "").lower() == token
            ),
            None,
        )
        if referenced is not None:
            return referenced
        return next(
            (
                item for item in choices
                if str(item.get("thread_id") or "").lower().startswith(token) and len(token) >= 6
            ),
            None,
        )

    def _render_continuity_choices(
        self,
        chat_id: str,
        choices: list[dict[str, Any]],
        *,
        invalid: bool = False,
    ) -> str:
        if not choices:
            self.continuity_choice_sets.pop(chat_id, None)
            return "No cross-surface MO thread is waiting to continue."
        bounded = choices[:8]
        references = _continuity_choice_references(bounded)
        self.continuity_choice_sets[chat_id] = tuple(
            (thread_id, references.get(thread_id, ""))
            for item in bounded
            if (thread_id := str(item.get("thread_id") or ""))
        )
        lines = [
            "That selection is no longer available. Choose again:" if invalid else "More than one MO thread can continue here. Choose one:",
        ]
        for index, item in enumerate(bounded, 1):
            source = _continuity_surface_name(item.get("source_surface"))
            thread_id = str(item.get("thread_id") or "")
            detail = _continuity_choice_detail(item, reference=references.get(thread_id, ""))
            lines.append(f"{index}. {source} · {detail}\n   {_continuity_summary(item.get('intent'))}")
        lines.append("Reply with /continue 1 (or another number).")
        return "\n".join(lines)

    @contextmanager
    def _session_context(self, session_name: str):
        session = self._load_session(session_name)
        with self.agent.isolated_session(session), self.agent.surface_session_scope(session_name):
            try:
                yield
            finally:
                self._save_session(session_name, session)

    def _load_session(self, session_name: str) -> Session:
        return load_session_from_manager(
            self.agent, session_name,
            session_id_prefix="mo-telegram",
            sanitize=True,
        )

    def _save_session(self, session_name: str, session: Session) -> None:
        manager = getattr(self.agent, "_sessions", None)
        if not manager or not hasattr(manager, "save_snapshot"):
            return
        try:
            manager.save_snapshot(session_name, session, extra_meta={"surface": "telegram"})
        except Exception:
            traceback.print_exc()

    def _run_mo_turn(
        self,
        text: str,
        *,
        cancel_event: threading.Event,
        chat_id: str,
        chat_type: str,
        session_name: str = "",
    ) -> str:
        router = self.gateway or getattr(self.agent, "gateway", None)
        gateway_manages_heartbeat = router is not None and hasattr(router, "run_turn")
        gateway_manages_handoff = bool(getattr(router, "manages_surface_handoff", False))
        if not gateway_manages_heartbeat:
            record_heartbeat(
                self.agent,
                gateway=self.gateway,
                surface="telegram",
                event="turn_start",
                extra={"chat_id": chat_id, "chat_type": chat_type},
            )
        handoff_record = None
        handoff_scope = nullcontext()
        publish_scope = nullcontext()
        learning_scope = nullcontext()
        if chat_type in {"group", "supergroup"}:
            suppress = getattr(self.agent, "suppress_surface_handoff_scope", None)
            if callable(suppress):
                publish_scope = suppress()
        elif chat_type == "private":
            shared_learning = getattr(self.agent, "shared_learning_scope", None)
            if callable(shared_learning):
                learning_scope = shared_learning()
        if chat_type not in {"group", "supergroup"} and not gateway_manages_handoff:
            try:
                from core.state.surface_handoff import pending_handoff, render_handoff_context
                handoff_record = pending_handoff(
                    self.agent,
                    target_surface="telegram",
                    target_key=session_name or f"telegram-{chat_id}",
                )
                scoped = getattr(self.agent, "continuity_handoff_scope", None)
                if handoff_record is not None and callable(scoped):
                    context = render_handoff_context(handoff_record)
                    try:
                        handoff_scope = scoped(context, record=handoff_record)
                    except TypeError:
                        handoff_scope = scoped(context)
            except Exception:
                handoff_record = None
        # Show images the turn produces IN Telegram (photo upload), not just as
        # text — the Telegram equivalent of the terminal/desktop image render.
        _photo_chat = str(chat_id)

        def _on_operator_image(image_path: str, _cid: str = _photo_chat) -> None:
            self._send_photo(_cid, str(image_path))

        try:
            with handoff_scope, publish_scope, learning_scope:
                if router is not None and hasattr(router, "run_turn"):
                    reply = router.run_turn(text, cancel_event=cancel_event, route_source="telegram", on_operator_image=_on_operator_image)
                    result = self._append_task_board(reply, router)
                elif hasattr(self.agent, "run_turn"):
                    try:
                        reply = self.agent.run_turn(text, cancel_event=cancel_event, on_operator_image=_on_operator_image)
                    except TypeError:
                        reply = self.agent.run_turn(text, cancel_event=cancel_event)
                    result = self._append_task_board(reply, getattr(self.agent, "gateway", None))
                else:
                    raise RuntimeError("No MO turn runner available for Telegram gateway")
            if handoff_record is not None:
                try:
                    from core.state.surface_handoff import mark_handoff_consumed
                    mark_handoff_consumed(
                        self.agent,
                        handoff_record,
                        target_surface="telegram",
                        target_key=session_name or f"telegram-{chat_id}",
                    )
                except Exception:
                    traceback.print_exc()
            return str(result or "")
        finally:
            # Gateway owns the general turn heartbeat, while Telegram owns the
            # chat target needed for one-surface delivery notifications.
            record_heartbeat(
                self.agent,
                gateway=self.gateway,
                surface="telegram",
                event="turn_end",
                extra={"chat_id": chat_id, "chat_type": chat_type},
            )

    @staticmethod
    def _append_task_board(reply: str, router: Any) -> str:
        """Append the compact taskboard to remote work replies."""
        return attach_taskboard_to_text(router, reply)

    def _post_json(self, client: Any, base: str, method: str, payload: dict[str, Any], *, timeout: float = 20.0) -> Any:
        return client.post(f"{base}/{method}", json=payload, timeout=timeout)

    def _send_working(self, client: Any, base: str, chat_id: str) -> int | None:
        try:
            data = self._post_json(client, base, "sendMessage", {"chat_id": chat_id, "text": "MO working…"}).json()
            return (((data or {}).get("result") or {}).get("message_id"))
        except Exception:
            return None

    def _deliver_reply(self, client: Any, base: str, chat_id: str, text: str, *, message_id: int | None = None) -> None:
        text = str(text or "")
        if len(text) <= 3500:
            payload = {"chat_id": chat_id, "text": compact_for_telegram(text)}
            if message_id:
                payload["message_id"] = message_id
                self._post_json(client, base, "editMessageText", payload)
            else:
                self._post_json(client, base, "sendMessage", payload)
            return
        summary = compact_for_telegram(text)
        if message_id:
            self._post_json(client, base, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": summary})
        else:
            self._post_json(client, base, "sendMessage", {"chat_id": chat_id, "text": summary})
        data = io.BytesIO(text.encode("utf-8", errors="replace"))
        data.name = "mo-output.txt"
        try:
            client.post(f"{base}/sendDocument", data={"chat_id": chat_id}, files={"document": (data.name, data, "text/plain")}, timeout=30.0)
        except TypeError:
            self._post_json(client, base, "sendDocument", {"chat_id": chat_id, "document": "mo-output.txt"})

    def _deliver_transfer_notices(
        self,
        client: Any,
        base: str,
        *,
        include_unassigned: bool,
        fallback_chat_id: str = "",
    ) -> None:
        """Consume only this live Telegram process's transfer notifications."""
        try:
            from core.runtime.instance import get_instance_id
            from core.transfer.presence import claim_transfer_notices

            notices = claim_transfer_notices(
                self.config,
                surface="telegram",
                instance_id=get_instance_id(),
                limit=3,
                include_unassigned=include_unassigned,
                require_target_key=not bool(fallback_chat_id),
            )
        except Exception:
            return
        for item in notices:
            chat_id = str(item.get("target_key") or fallback_chat_id).strip()
            if not chat_id or len(chat_id) > 120:
                continue
            try:
                self._post_json(
                    client,
                    base,
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": (
                            f"File received: {item.get('name') or 'file'} "
                            f"· {int(item.get('size_bytes') or 0):,} bytes"
                        ),
                    },
                )
            except Exception:
                # Claiming before delivery preserves at-most-one notification;
                # Telegram transport failures must not duplicate later.
                continue

    def _send_photo(self, chat_id: str, path: str) -> None:
        """Best-effort: upload an image FILE to the Telegram chat so the operator
        SEES it — the Telegram equivalent of the terminal/desktop on_operator_image
        render. Any produced image (generate_image, show_image, edit_image) flows
        here. Falls back to sendDocument for oversized/unsupported images; every
        failure is swallowed (display is best-effort, owned by the surface)."""
        try:
            if not path or not os.path.isfile(path):
                return
            token = _resolve_secret(self.token_env, config=self.config)
            if not token:
                return
            base = f"https://api.telegram.org/bot{token}"
            with open(path, "rb") as handle:
                data = handle.read()
            name = os.path.basename(path)
            mime = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
                ".tiff": "image/tiff", ".tif": "image/tiff", ".ico": "image/x-icon",
            }.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
            import httpx
            with httpx.Client(timeout=60.0) as client:
                try:
                    resp = client.post(
                        f"{base}/sendPhoto",
                        data={"chat_id": str(chat_id)},
                        files={"photo": (name, io.BytesIO(data), mime)},
                    )
                    if resp.status_code == 200 and resp.json().get("ok"):
                        return
                except Exception:
                    pass
                # Fallback: any format/size Telegram accepts as a file.
                try:
                    client.post(
                        f"{base}/sendDocument",
                        data={"chat_id": str(chat_id)},
                        files={"document": (name, io.BytesIO(data), mime)},
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _receive_telegram_files(self, job: TelegramJob) -> list[Path]:
        """Download authorized Telegram files and hand them to the one transfer pipe."""
        from core.state.attachments import safe_attachment_name
        from core.transfer import (
            HUB_TARGET_ID,
            TURN_CONTEXT_MAX_BYTES,
            TransferService,
        )

        service = TransferService(self.config)
        if not service.settings.enabled:
            raise RuntimeError("file transfer is disabled in MO configuration")
        stage_root = Path(
            resolve_state_path("memory/transfers/telegram-incoming", self.config)
        ).resolve(strict=False)
        stage_root.mkdir(parents=True, exist_ok=True)
        delivered: list[Path] = []
        for position, descriptor in enumerate(job.files):
            declared = int(descriptor.get("bytes") or 0)
            if declared > TURN_CONTEXT_MAX_BYTES:
                raise RuntimeError("Telegram file exceeds the configured transfer limit")
            file_id = str(descriptor.get("file_id") or "")
            if not file_id:
                continue
            metadata_response = job.client.get(
                f"{job.base}/getFile",
                params={"file_id": file_id},
                timeout=30.0,
            )
            raise_for_status = getattr(metadata_response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            metadata = metadata_response.json()
            if not isinstance(metadata, dict):
                raise RuntimeError("Telegram returned invalid file metadata")
            file_path = str(((metadata or {}).get("result") or {}).get("file_path") or "")
            if not metadata.get("ok") or not file_path:
                raise RuntimeError("Telegram did not return downloadable file metadata")
            name = safe_attachment_name(
                descriptor.get("name") or Path(file_path).name or "telegram-file"
            )
            staged = stage_root / f"{uuid.uuid4().hex}-{name}"
            download_url = job.base.replace(
                "https://api.telegram.org/bot",
                "https://api.telegram.org/file/bot",
                1,
            ) + "/" + file_path.lstrip("/")
            total = 0
            try:
                stream = getattr(job.client, "stream", None)
                if callable(stream):
                    with stream("GET", download_url, timeout=120.0) as response:
                        response.raise_for_status()
                        content_length = str(
                            getattr(response, "headers", {}).get("content-length", "")
                        ).strip()
                        if content_length and (
                            not content_length.isdigit()
                            or int(content_length) > TURN_CONTEXT_MAX_BYTES
                        ):
                            raise RuntimeError(
                                "Telegram file exceeds the configured transfer limit"
                            )
                        with staged.open("xb") as handle:
                            for block in response.iter_bytes(1024 * 1024):
                                if not block:
                                    continue
                                total += len(block)
                                if total > TURN_CONTEXT_MAX_BYTES:
                                    raise RuntimeError(
                                        "Telegram file exceeds the configured transfer limit"
                                    )
                                handle.write(block)
                else:
                    try:
                        response = job.client.get(
                            download_url,
                            timeout=120.0,
                            stream=True,
                        )
                    except TypeError:
                        response = job.client.get(download_url, timeout=120.0)
                    try:
                        raise_for_status = getattr(response, "raise_for_status", None)
                        if callable(raise_for_status):
                            raise_for_status()
                        content_length = str(
                            getattr(response, "headers", {}).get("content-length", "")
                        ).strip()
                        if content_length and (
                            not content_length.isdigit()
                            or int(content_length) > TURN_CONTEXT_MAX_BYTES
                        ):
                            raise RuntimeError(
                                "Telegram file exceeds the configured transfer limit"
                            )
                        iter_content = getattr(response, "iter_content", None)
                        iter_bytes = getattr(response, "iter_bytes", None)
                        if callable(iter_content):
                            blocks = iter_content(chunk_size=1024 * 1024)
                        elif callable(iter_bytes):
                            blocks = iter_bytes(1024 * 1024)
                        else:
                            raise RuntimeError(
                                "Telegram client does not support bounded streaming"
                            )
                        with staged.open("xb") as handle:
                            for block in blocks:
                                if not block:
                                    continue
                                total += len(block)
                                if total > TURN_CONTEXT_MAX_BYTES:
                                    raise RuntimeError(
                                        "Telegram file exceeds the configured transfer limit"
                                    )
                                handle.write(block)
                    finally:
                        close = getattr(response, "close", None)
                        if callable(close):
                            close()
                if total < 1:
                    raise RuntimeError("Telegram returned an empty file")
                if declared and total != declared:
                    raise RuntimeError("Telegram file byte count changed during download")
                record = service.send_local_file(
                    staged,
                    sender_device_id=f"telegram-{job.chat_id}",
                    target_device_id=HUB_TARGET_ID,
                    source_surface="telegram",
                    purpose="turn_context",
                    name=name,
                    client_request_id=(
                        f"telegram-{job.chat_id}-"
                        f"{job.source_message_id}-{position}-"
                        + hashlib.sha256(
                            str(
                                descriptor.get("file_unique_id")
                                or descriptor.get("file_id")
                                or name
                            ).encode("utf-8", errors="replace")
                        ).hexdigest()[:16]
                    ),
                )
                if record.saved_path is None:
                    raise RuntimeError("Telegram file was not saved in the catalog")
                delivered.append(record.saved_path)
            finally:
                staged.unlink(missing_ok=True)
        if not delivered:
            raise RuntimeError("Telegram message did not contain a downloadable file")
        return delivered

    def run_polling(self, *, poll_interval: float = 1.0, stop_event: Any = None, once: bool = False, client: Any = None) -> None:
        if not self.enabled:
            raise RuntimeError("telegram.enabled is false")
        token = _resolve_secret(self.token_env, config=self.config)
        if not token:
            raise RuntimeError(f"Telegram canonical token missing: {self.token_env}")
        close_client = False
        if client is None:
            import httpx
            client = httpx.Client(timeout=35.0)
            close_client = True
        base = f"https://api.telegram.org/bot{token}"
        offset = 0
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    response = client.get(f"{base}/getUpdates", params={"timeout": 25, "offset": offset}, timeout=35.0)
                except Exception as exc:
                    raise RuntimeError(f"Telegram getUpdates error: {redact_monitor_text(exc, 240)}") from None
                data = response.json()
                if not data.get("ok", False):
                    raise RuntimeError(f"Telegram getUpdates failed: {data}")
                for update in data.get("result", []) or []:
                    if "update_id" in update:
                        offset = max(offset, int(update["update_id"]) + 1)
                    msg = update.get("message") or update.get("edited_message") or {}
                    text = msg.get("text")
                    if text is None:
                        text = msg.get("caption") or ""
                    files = _telegram_file_descriptors(msg)
                    chat = msg.get("chat") or {}
                    sender = msg.get("from") or {}
                    chat_id = chat.get("id")
                    chat_type = chat.get("type") or "private"
                    sender_id = sender.get("id")
                    if (not text and not files) or chat_id is None or sender_id is None:
                        continue
                    if self._ignores_group_message(str(text), str(chat_type)):
                        continue
                    working_id = self._send_working(client, base, str(chat_id))
                    if _is_stop_text(str(text)):
                        reply = self._handle_stop(sender_id=str(sender_id), chat_id=str(chat_id), chat_type=str(chat_type))
                        if reply:
                            self._deliver_reply(client, base, str(chat_id), reply, message_id=working_id)
                        continue
                    self.enqueue_text(
                        sender_id=str(sender_id),
                        chat_id=str(chat_id),
                        text=str(text),
                        chat_type=str(chat_type),
                        client=client,
                        base=base,
                        message_id=working_id,
                        files=files,
                        source_message_id=int(msg.get("message_id") or 0),
                    )
                self._deliver_transfer_notices(
                    client,
                    base,
                    include_unassigned=False,
                )
                if once:
                    self.wait_idle(timeout=10.0)
                    break
                time.sleep(max(0.0, poll_interval))
        finally:
            if close_client:
                client.close()


def _telegram_file_descriptors(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return bounded document/photo/video/audio metadata before downloading bytes."""
    document = message.get("document")
    if isinstance(document, dict) and document.get("file_id"):
        return (
            {
                "file_id": str(document["file_id"]),
                "file_unique_id": str(document.get("file_unique_id") or ""),
                "name": str(document.get("file_name") or "telegram-document"),
                "bytes": _telegram_file_size(document.get("file_size")),
                "mime": str(document.get("mime_type") or ""),
            },
        )
    photos = message.get("photo")
    if isinstance(photos, list):
        usable = [item for item in photos if isinstance(item, dict) and item.get("file_id")]
        if usable:
            selected = max(
                usable,
                key=lambda item: _telegram_file_size(item.get("file_size")),
            )
            unique = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "-",
                str(selected.get("file_unique_id") or selected.get("file_id") or "photo"),
            )[:48]
            return (
                {
                    "file_id": str(selected["file_id"]),
                    "file_unique_id": str(selected.get("file_unique_id") or ""),
                    "name": f"telegram-photo-{unique}.jpg",
                    "bytes": _telegram_file_size(selected.get("file_size")),
                    "mime": "image/jpeg",
                },
            )
    for kind, fallback_name in (
        ("video", "telegram-video.mp4"),
        ("audio", "telegram-audio"),
    ):
        media = message.get(kind)
        if isinstance(media, dict) and media.get("file_id"):
            return (
                {
                    "file_id": str(media["file_id"]),
                    "file_unique_id": str(media.get("file_unique_id") or ""),
                    "name": str(media.get("file_name") or fallback_name),
                    "bytes": _telegram_file_size(media.get("file_size")),
                    "mime": str(media.get("mime_type") or ""),
                },
            )
    return ()


def _telegram_file_size(value: Any) -> int:
    try:
        size = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, size)


def start_telegram_gateway_if_enabled(agent: Any, gateway: Any = None) -> TelegramGateway | None:
    telegram = TelegramGateway.from_agent(agent, gateway=gateway)
    try:
        setattr(agent, "telegram_gateway", telegram)
        setattr(agent, "_telegram_gateway", telegram)
    except Exception:
        traceback.print_exc()
    if not telegram.enabled:
        return None
    monitor = get_monitor()
    if not _resolve_secret(
        telegram.token_env,
        config=telegram.config,
    ):
        if monitor:
            monitor.emit(
                "session_event",
                {
                    "kind": "telegram_not_started",
                    "reason": f"missing canonical credential {telegram.token_env}",
                },
            )
        return telegram
    resource_lock = acquire_runtime_lock(lock_name="mo-telegram-poller.lock", label="MO Telegram poller", quiet=True)
    if resource_lock is None:
        if monitor:
            monitor.emit("session_event", {"kind": "telegram_not_started", "reason": "resource lock held"})
        return telegram
    telegram._runtime_lock = resource_lock
    stop_event = threading.Event()
    telegram._stop_event = stop_event

    def _runner() -> None:
        failures = 0
        while not stop_event.is_set():
            try:
                telegram.run_polling(stop_event=stop_event)
                break
            except Exception as exc:
                failures += 1
                mon = get_monitor()
                if mon:
                    mon.emit("session_event", {
                        "kind": "telegram_poll_error",
                        "error_type": type(exc).__name__,
                        "error": redact_monitor_text(exc, 240),
                        "failures": failures,
                    })
                if stop_event.wait(min(60.0, 5.0 * failures)):
                    break

    thread = threading.Thread(target=_runner, name="mo-telegram", daemon=True)
    telegram._poll_thread = thread
    thread.start()
    if monitor:
        monitor.emit("session_event", {"kind": "telegram_started", "token_env": telegram.token_env})
    return telegram


def _resolve_secret(env_name: str, *, config: dict | None = None) -> str:
    return resolve_secret(str(env_name or ""), config=config, service="telegram").strip()


def _is_stop_text(text: str) -> bool:
    return " ".join(str(text or "").strip().lower().split()) in {"/stop", "stop", "cancel", "/cancel"}


def _continuity_surface_name(value: Any) -> str:
    surface = str(value or "terminal").strip().lower().replace("-", "_")
    return {
        "api": "phone/web",
        "companion": "MO Desktop",
        "mo_desktop": "MO Desktop",
        "telegram": "Telegram",
        "terminal": "terminal",
    }.get(surface, "another MO surface")


def _continuity_summary(value: Any, limit: int = 120) -> str:
    summary = " ".join(str(value or "work in progress").split())
    bounded = max(24, min(240, int(limit)))
    return summary if len(summary) <= bounded else summary[: bounded - 1].rstrip() + "…"


def _continuity_following_reply(selected: dict[str, Any], choices: list[dict[str, Any]]) -> str:
    """Confirm the selected thread even when its latest turn changed after the menu."""
    source = _continuity_surface_name(selected.get("source_surface"))
    thread_id = str(selected.get("thread_id") or "")
    reference = str(selected.get("_selection_reference") or "").strip()
    if not reference:
        reference = _continuity_choice_references(choices).get(thread_id, "")
    label = f"Following {source}"
    if reference:
        label += f" · ref {reference}"
    return f"{label}: {_continuity_summary(selected.get('intent'))}\nSend your next message and MO will continue with that bounded context."


def _continuity_choice_references(choices: list[dict[str, Any]]) -> dict[str, str]:
    """Return bounded shortest distinguishing references for choice labels."""
    thread_ids = [str(item.get("thread_id") or "") for item in choices]
    references: dict[str, str] = {}
    for thread_id in thread_ids:
        if not thread_id:
            continue
        upper = min(12, len(thread_id))
        reference = thread_id[:upper]
        for length in range(min(6, upper), upper + 1):
            candidate = thread_id[:length]
            if sum(other.startswith(candidate) for other in thread_ids) == 1:
                reference = candidate
                break
        references[thread_id] = reference
    return references


def _continuity_choice_detail(
    item: dict[str, Any],
    *,
    reference: str = "",
    now: float | None = None,
) -> str:
    status = {
        "completed": "done",
        "paused": "paused",
        "cancelled": "cancelled",
        "failed": "failed",
    }.get(str(item.get("status") or "").strip().lower(), "ready")
    try:
        updated_at = float(item.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        updated_at = 0.0
    current = time.time() if now is None else float(now)
    if updated_at <= 0:
        age = "time unknown"
    else:
        elapsed = max(0, int(current - updated_at))
        if elapsed < 60:
            age = "now"
        elif elapsed < 3600:
            age = f"{elapsed // 60}m ago"
        elif elapsed < 86_400:
            age = f"{elapsed // 3600}h ago"
        else:
            age = f"{elapsed // 86_400}d ago"
    parts = [status, age]
    if reference:
        parts.append(f"ref {reference}")
    return " · ".join(parts)
