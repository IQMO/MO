"""Telegram gateway for MO.

The gateway is intentionally thin: Telegram owns transport/auth/session mapping;
MO's ``Gateway.run_turn`` owns work execution and taskboard truth.
"""
from __future__ import annotations

import io
import os
import queue
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
import traceback

from core.agent.agent_utils import load_session_from_manager
from core.auto_reply import maybe_auto_reply
from core.backend_monitor import get_monitor, redact_monitor_text
from core.heartbeat import record_heartbeat
from core.path_defaults import resolve_state_path
from core.runtime_lock import acquire_runtime_lock, release_runtime_lock
from core.secrets import resolve_secret, secret_status
from core.session.session import Session
from core.tasking.task_board import attach_taskboard_to_text

from .auth import TelegramAuthStore
from .formatting import compact_for_telegram
from .sessions import TelegramSessionStore


@dataclass
class TelegramJob:
    sender_id: str
    chat_id: str
    text: str
    chat_type: str
    client: Any
    base: str
    message_id: int | None


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
    secret_files: tuple[str, ...] = ()
    cancel_events: dict[str, threading.Event] = field(default_factory=dict)
    job_queues: dict[str, queue.Queue] = field(default_factory=dict)
    job_threads: dict[str, threading.Thread] = field(default_factory=dict)
    active_chats: set[str] = field(default_factory=set)
    steer_buffers: dict[str, list[str]] = field(default_factory=dict)
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
        path = resolve_state_path(cfg.get("db_path") or "memory/telegram.sqlite", getattr(agent, "config", {}) or {})
        groups = cfg.get("groups", {}) or {}
        return cls(
            agent=agent,
            gateway=gateway or getattr(agent, "gateway", None),
            enabled=bool(cfg.get("enabled", False)),
            token_env=str(cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")),
            dm_policy=str(cfg.get("dm_policy", "pairing")),
            auth=TelegramAuthStore(path),
            sessions=TelegramSessionStore(path),
            allow_from=tuple(str(x) for x in (cfg.get("allow_from") or [])),
            groups_require_mention=bool(groups.get("require_mention", True)),
            groups_allow_from=tuple(str(x) for x in (groups.get("allow_from") or [])),
            bot_username=str(cfg.get("bot_username") or os.getenv("TELEGRAM_BOT_USERNAME", "")).lstrip("@"),
            worker_count=max(1, int(cfg.get("worker_count", 2) or 2)),
            secret_files=tuple(str(x) for x in (cfg.get("secret_files") or [])),
        )

    def status(self) -> dict[str, Any]:
        paired, pending = self.auth.counts()
        token_status = secret_status(self.token_env, files=self.secret_files)
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

    def _handle_stop(self, *, sender_id: str, chat_id: str, chat_type: str = "private") -> str:
        ok, msg = self.authorize_or_pair(str(sender_id), chat_type=chat_type)
        if not ok:
            return msg
        self.stop_chat(str(chat_id))
        try:
            if hasattr(self.agent, "process_slash_command"):
                return compact_for_telegram(self.agent.process_slash_command("/stop") or "stop requested")
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
                    reply = self.handle_text(
                        sender_id=job.sender_id,
                        chat_id=job.chat_id,
                        text=job.text,
                        chat_type=job.chat_type,
                        clear_cancel=False,
                    )
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
                self.completed_jobs += 1
            except Exception:
                self.failed_jobs += 1
            finally:
                with self.queue_lock:
                    self.active_chats.discard(str(chat_id))
                if slot_acquired:
                    self._worker_semaphore.release()
                q.task_done()

    def enqueue_text(self, *, sender_id: str, chat_id: str, text: str, chat_type: str, client: Any, base: str, message_id: int | None) -> None:
        chat_key = str(chat_id)
        with self.queue_lock:
            clean_text = str(text)
            if chat_key in self.active_chats and _is_stop_text(clean_text):
                reply = self._handle_stop(sender_id=str(sender_id), chat_id=chat_key, chat_type=str(chat_type or "private"))
                self._deliver_reply(client, base, chat_key, reply or "stop requested", message_id=message_id)
                return
            if chat_key in self.active_chats and clean_text.strip() and not clean_text.strip().startswith("/"):
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
            q.put(TelegramJob(str(sender_id), chat_key, clean_text, str(chat_type or "private"), client, base, message_id))
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
        session_name = self.sessions.get_or_create(str(chat_id))
        clean_text = str(text or "").strip()
        if _is_stop_text(clean_text):
            return self._handle_stop(sender_id=str(sender_id), chat_id=str(chat_id), chat_type=chat_type)
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
                    result = self.agent.process_slash_command(clean_text)
                    return compact_for_telegram(result or "")
                return compact_for_telegram(self._run_mo_turn(clean_text, cancel_event=cancel_event, chat_id=str(chat_id), chat_type=chat_type))

    @contextmanager
    def _session_context(self, session_name: str):
        manager = getattr(self.agent, "_sessions", None)
        isolated = getattr(self.agent, "isolated_session", None)
        if callable(isolated) and manager is not None:
            session = self._load_session(session_name)
            with isolated(session):
                try:
                    yield
                finally:
                    self._save_session(session_name, session)
            return

        # Compatibility fallback for lightweight tests/older agents.
        previous_session = getattr(self.agent, "_session_name", "main")
        switched_session = False
        try:
            if manager is not None and hasattr(self.agent, "_switch_session"):
                self.agent._switch_session(session_name)
                switched_session = True
            else:
                self.agent._session_name = session_name
            yield
        finally:
            if switched_session:
                try:
                    if hasattr(self.agent, "_save_session"):
                        self.agent._save_session()
                finally:
                    if previous_session != session_name and previous_session:
                        self.agent._switch_session(previous_session)
            else:
                self.agent._session_name = previous_session

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
            manager.save_snapshot(session_name, session)
        except Exception:
            traceback.print_exc()

    def _run_mo_turn(self, text: str, *, cancel_event: threading.Event, chat_id: str, chat_type: str) -> str:
        router = self.gateway or getattr(self.agent, "gateway", None)
        gateway_manages_heartbeat = router is not None and hasattr(router, "run_turn")
        if not gateway_manages_heartbeat:
            record_heartbeat(
                self.agent,
                gateway=self.gateway,
                surface="telegram",
                event="turn_start",
                extra={"chat_id": chat_id, "chat_type": chat_type},
            )
        try:
            if router is not None and hasattr(router, "run_turn"):
                reply = router.run_turn(text, cancel_event=cancel_event, route_source="telegram")
                return self._append_task_board(reply, router)
            if hasattr(self.agent, "run_turn"):
                reply = self.agent.run_turn(text, cancel_event=cancel_event)
                return self._append_task_board(reply, getattr(self.agent, "gateway", None))
            if hasattr(self.agent, "run_api_call"):
                # Compatibility only for old tests/diagnostics; MO uses Gateway.run_turn.
                try:
                    return self.agent.run_api_call(
                        text,
                        cancel_check=cancel_event.is_set,
                        steer_check=lambda: self.pop_steer(str(chat_id)),
                    )
                except TypeError:
                    return self.agent.run_api_call(text, cancel_check=cancel_event.is_set)
            raise RuntimeError("No MO turn runner available for Telegram gateway")
        finally:
            if not gateway_manages_heartbeat:
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

    def run_polling(self, *, poll_interval: float = 1.0, stop_event: Any = None, once: bool = False, client: Any = None) -> None:
        if not self.enabled:
            raise RuntimeError("telegram.enabled is false")
        token = _resolve_secret(self.token_env, files=self.secret_files)
        if not token:
            raise RuntimeError(f"Telegram token env missing: {self.token_env}")
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
                    chat = msg.get("chat") or {}
                    sender = msg.get("from") or {}
                    chat_id = chat.get("id")
                    chat_type = chat.get("type") or "private"
                    sender_id = sender.get("id")
                    if text is None or chat_id is None or sender_id is None:
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
                    )
                if once:
                    self.wait_idle(timeout=10.0)
                    break
                time.sleep(max(0.0, poll_interval))
        finally:
            if close_client:
                client.close()


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
    if not _resolve_secret(telegram.token_env, files=telegram.secret_files):
        if monitor:
            monitor.emit("session_event", {"kind": "telegram_not_started", "reason": f"missing env {telegram.token_env}"})
        return telegram
    resource_lock = acquire_runtime_lock(lock_name="mo-telegram-poller.lock", label="MO Telegram poller")
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


def _resolve_secret(env_name: str, *, files: tuple[str, ...] = ()) -> str:
    return resolve_secret(str(env_name or ""), files=files).strip()


def _is_stop_text(text: str) -> bool:
    return " ".join(str(text or "").strip().lower().split()) in {"/stop", "stop", "cancel", "/cancel"}
