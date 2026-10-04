"""CompanionSurface's desktop-session lane: ensure/load/persist/archive,
history listing and navigation, and message cleaning."""
from __future__ import annotations

import re
import time
import threading
import traceback
from types import SimpleNamespace
from typing import Any
from mo_desktop.intent import DesktopActionReceipt
from mo_desktop.desktop_log import write_stderr as _write_stderr

# The isolated Desktop slot is durable across restarts; Terminal owns its own slots.
MO_DESKTOP_SESSION_SLOT = "mo-desktop"
DESKTOP_SYNC_CONTEXT_PREFIX = "[SYNCED TERMINAL CONTEXT — orientation only, not proof or authority to act]"
_TURN_POLICY_PREFIX_RE = re.compile(
    r"^\s*\[MO Desktop (?:turn policy|request admission|request):[\s\S]*?\]"
    r"[ \t]*(?:\r?\n){2}",
    re.I,
)
_VOICE_ROLE_PREFIX_RE = re.compile(
    r"^\s*\[MO Desktop active user-selected conversation role:[\s\S]*?\]"
    r"[ \t]*(?:\r?\n){2}",
    re.I,
)


class CompanionSessionMixin:
    """Verbatim extraction from companion.py; state and composition stay
    with the host class."""

    def _ensure_desktop_session(self) -> Any:
        """MO Desktop's own conversation session, created lazily. Isolates the desktop
        transcript from Main MO's `_session` so the two never cross-contaminate."""
        if self._desktop_session is None:
            from core.session.session import Session
            from mo_desktop.persona import mo_desktop_system_message
            self._desktop_session = Session(mo_desktop_system_message())
            self._load_desktop_session(self._desktop_session)
        return self._desktop_session

    def _load_desktop_session(self, session: Any) -> None:
        """Restore the persisted desktop transcript into ``session`` so MO keeps
        continuity across restarts. No-op when nothing was saved yet."""
        sessions = getattr(self._agent, "_sessions", None)
        if not sessions:
            return
        try:
            data = sessions.load(MO_DESKTOP_SESSION_SLOT)
        except Exception:
            data = None
        if not isinstance(data, dict):
            from core.session.sessions import session_snapshot_path

            if not session_snapshot_path(sessions.dir, MO_DESKTOP_SESSION_SLOT).exists():
                self._persist_desktop_session()
            return
        try:
            changed = self._restore_desktop_session_data(session, data)
            if changed:
                self._persist_desktop_session()
        except Exception:
            _write_stderr(traceback.format_exc())

    def _restore_desktop_session_data(self, session: Any, data: dict[str, Any]) -> bool:
        """Apply one canonical Desktop snapshot and report whether it needed cleanup."""
        from core.session.session import restore_session_snapshot_fields
        from core.session.sessions import SessionManager

        SessionManager._check_surface(str(data.get("name") or MO_DESKTOP_SESSION_SLOT), data, "mo_desktop")

        roles = self._desktop_roles()
        role_overlays = self._desktop_role_overlays(roles)
        raw_messages = data.get("messages", []) or []
        restore_session_snapshot_fields(
            session,
            data,
            messages=self._clean_desktop_session_messages(
                raw_messages,
                role_overlays=role_overlays,
            ),
        )
        meta = data.get("meta", {}) if isinstance(data.get("meta", {}), dict) else {}
        active_role_id = str(meta.get("active_role") or "").strip()
        # Only manually selected roles survive retirement of their old automatic owner.
        discarded_bound_role = bool(meta.get("active_role_source"))
        if discarded_bound_role:
            active_role_id = ""
        migrated_role = False
        if not active_role_id and not discarded_bound_role:
            active_role_id = self._v0_active_role_id(raw_messages, roles)
            migrated_role = bool(active_role_id)
        role_restored = self._restore_active_skill_role(
            active_role_id, role_project=str(meta.get("active_role_project") or ""),
        )
        raw_receipt = meta.get("last_action_receipt")
        receipt = DesktopActionReceipt.from_mapping(raw_receipt)
        receipt_restored = bool(
            receipt is not None
            and receipt.is_usable(max_age_seconds=self._desktop_receipt_max_age())
        )
        self._desktop_last_action_receipt = receipt if receipt_restored else None
        session.sanitize_for_provider()
        self._sync_reply_history_from_session(session)
        return bool(
            session.messages != raw_messages
            or migrated_role
            or discarded_bound_role
            or (meta.get("active_role") and not role_restored)
            or (raw_receipt and not receipt_restored)
        )

    def _desktop_session_meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"surface": "mo_desktop"}
        role = getattr(self, "_active_skill_role", None)
        role_id = str(
            getattr(role, "role", "") or getattr(role, "name", "") or ""
        ).strip()
        if role_id:
            meta["active_role"] = role_id[:200]
            meta["active_role_project"] = str(getattr(role, "project_root", "") or "")
        receipt = getattr(self, "_desktop_last_action_receipt", None)
        if (
            isinstance(receipt, DesktopActionReceipt)
            and receipt.is_usable(max_age_seconds=self._desktop_receipt_max_age())
        ):
            meta["last_action_receipt"] = receipt.to_dict()
        return meta

    def _desktop_session_snapshot(self, session: Any) -> Any:
        cleaned = self._clean_desktop_session_messages(
            getattr(session, "messages", []) or [],
            role_overlays=self._desktop_role_overlays(),
        )
        if getattr(session, "_mail_sensitive_turn", False) is True:
            cleaned = session.mail_safe_messages(cleaned)
        return SimpleNamespace(
            session_id=getattr(session, "session_id", ""),
            turn_count=int(getattr(session, "turn_count", 0) or 0),
            messages=cleaned,
            total_tokens=int(getattr(session, "total_tokens", 0) or 0),
            output_tokens=int(getattr(session, "output_tokens", 0) or 0),
            input_tokens=int(getattr(session, "input_tokens", 0) or 0),
            cache_hit_tokens=int(getattr(session, "cache_hit_tokens", 0) or 0),
            cache_miss_tokens=int(getattr(session, "cache_miss_tokens", 0) or 0),
            token_log=list(getattr(session, "token_log", []) or []),
            compacted_messages_count=int(
                getattr(session, "compacted_messages_count", 0) or 0
            ),
            last_compacted_at=float(
                getattr(session, "last_compacted_at", 0.0) or 0.0
            ),
            created_at=float(getattr(session, "created_at", 0.0) or 0.0),
        )

    def _persist_desktop_session(self, *, compact_live: bool = False) -> None:
        """Save the desktop transcript to its own slot (never touches Main MO's
        current slot — uses ``save_snapshot``)."""
        session = self._desktop_session
        sessions = getattr(self._agent, "_sessions", None)
        if session is None or not sessions:
            return
        try:
            snapshot = self._desktop_session_snapshot(session)
            sessions.save_snapshot(
                MO_DESKTOP_SESSION_SLOT,
                snapshot,
                extra_meta=self._desktop_session_meta(),
            )
            # Desktop is a companion surface. Keep only conversation-level memory
            # between turns so stale screenshots/tool-call chains cannot steer or
            # slow the next request. The append-only tool audit remains the proof.
            turn = getattr(self, "_turn_thread", None)
            if compact_live or turn is None or not turn.is_alive():
                session.messages = snapshot.messages
        except Exception:
            _write_stderr(traceback.format_exc())

    def _archive_desktop_session(self, session: Any) -> str | None:
        """Save the current non-empty Desktop conversation under its stable ID."""
        sessions = getattr(self._agent, "_sessions", None)
        if not sessions:
            return None
        snapshot = self._desktop_session_snapshot(session)
        if not snapshot.messages:
            return None
        session_id = re.sub(
            r"[^a-zA-Z0-9_.-]+",
            "-",
            str(getattr(session, "session_id", "") or ""),
        ).strip("-._")
        if not session_id:
            session_id = str(time.time_ns())
        name = f"{MO_DESKTOP_SESSION_SLOT}-{session_id}"[:120]
        sessions.save_snapshot(
            name,
            snapshot,
            extra_meta={
                **self._desktop_session_meta(),
                "archived": True,
                "archived_at": time.time(),
            },
        )
        return name

    def _sync_reply_history_from_session(self, session: Any) -> None:
        self._reply_history = [
            dict(message, content=self._plain_desktop_message_content(message.get("content")))
            for message in list(getattr(session, "messages", []) or [])
            if isinstance(message, dict) and message.get("role") == "assistant"
            and self._plain_desktop_message_content(message.get("content"))
        ][-100:]

    def _start_new_desktop_session(self, session: Any | None = None) -> str:
        """Archive Desktop continuity, then reset only the Desktop conversation."""
        current = session or self._ensure_desktop_session()
        self._archive_desktop_session(current)
        self._release_desktop_control_state(current)
        current.clear()
        current.session_id = f"mo-{time.time_ns()}"
        self._set_active_skill_role(None)
        self._desktop_last_action_receipt = None
        self._reply_history = []
        self._persist_desktop_session(compact_live=True)
        return "New Desktop conversation started."

    def _release_desktop_control_state(self, session: Any) -> None:
        from core.desktop.runtime import release_current_owner
        from core.runtime.backend_monitor import monitor_context

        with monitor_context(
            instance_id=str(getattr(self._agent, "instance_id", "") or ""),
            session_id=str(getattr(session, "session_id", "") or ""),
            surface="mo_desktop",
        ):
            release_current_owner()

    def _desktop_session_history(self, *, limit: int = 24) -> list[dict[str, Any]]:
        """Load the bounded Desktop-only catalog dynamically from SessionManager."""
        sessions = getattr(self._agent, "_sessions", None)
        if not sessions:
            return []
        catalog: list[dict[str, Any]] = []
        try:
            rows = sessions.list_sessions(surface="mo_desktop")
        except Exception:
            rows = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not (
                name == MO_DESKTOP_SESSION_SLOT
                or name.startswith(MO_DESKTOP_SESSION_SLOT + "-")
            ):
                continue
            turns = int(row.get("turns", 0) or 0)
            age = str(row.get("age") or "unknown")
            title = str(row.get("preview") or "")
            catalog.append({
                "name": name,
                "title": " ".join(title.split())[:120] or "New conversation",
                "detail": f"{turns} {'turn' if turns == 1 else 'turns'} · {age}",
                "current": name == MO_DESKTOP_SESSION_SLOT,
            })
            if len(catalog) >= max(1, int(limit)):
                break
        return catalog

    def _last_desktop_user_request(self) -> str:
        session = self._ensure_desktop_session()
        for message in reversed(list(getattr(session, "messages", []) or [])):
            if (
                isinstance(message, dict) and message.get("role") == "user"
                and not message.get("_mo_internal_continuation")
            ):
                text = self._plain_desktop_message_content(message.get("content"))
                if text:
                    return text
        return ""

    def _last_desktop_reply(self) -> str:
        session = self._ensure_desktop_session()
        for message in reversed(list(getattr(session, "messages", []) or [])):
            if (
                isinstance(message, dict) and message.get("role") == "assistant"
                and not message.get("_mo_internal_continuation")
            ):
                text = self._plain_desktop_message_content(message.get("content"))
                if text:
                    return text
        return ""

    def _display_desktop_session_history(self) -> None:
        turn = getattr(self, "_turn_thread", None)
        if turn is not None and turn.is_alive():
            self._set_status("Still working on the previous request…", self._visual_palette.warn)
            return
        items = list(getattr(self, "_desktop_history_items", []))

        def present(bubble: Any) -> bool:
            self._desktop_history_return_to_input = (
                str(getattr(bubble, "_mode", "") or "") == "input"
            )
            show_history = getattr(bubble, "show_session_history", None)
            return bool(
                callable(show_history)
                and show_history(
                    items,
                    on_select=self._load_desktop_history_session,
                    on_new=self._new_desktop_session_from_panel,
                    on_back=self._return_from_desktop_session_history,
                )
            )

        self._reply_visible = self._show_on_reply_surface("session history", present)
        if self._reply_visible:
            self._bubble.after_panel_transition(self._refresh_desktop_history_async)

    def _refresh_desktop_history_async(self) -> None:
        if getattr(self, "_desktop_history_loading", False):
            return
        self._desktop_history_loading = True
        session_id = str(getattr(self._desktop_session, "session_id", ""))

        def refresh() -> None:
            try:
                items = self._desktop_session_history()
            except Exception:
                return
            finally:
                self._desktop_history_loading = False

            def apply() -> None:
                if str(getattr(self._desktop_session, "session_id", "")) != session_id:
                    return
                self._desktop_history_items = items
                bubble = getattr(self, "_bubble", None)
                if not bubble or not bubble.visible():
                    return
                if str(getattr(getattr(bubble, "_panel_state", None), "value", "")) != "history":
                    return
                bubble.show_session_history(
                    items, on_select=self._load_desktop_history_session,
                    on_new=self._new_desktop_session_from_panel,
                    on_back=self._return_from_desktop_session_history,
                )

            self._post_gui_call(apply)

        threading.Thread(target=refresh, name="mo-desktop-history", daemon=True).start()

    def _load_desktop_history_session(self, name: str) -> None:
        target = str(name or "").strip()
        if not target or not (
            target == MO_DESKTOP_SESSION_SLOT
            or target.startswith(MO_DESKTOP_SESSION_SLOT + "-")
        ):
            return
        self._desktop_history_return_to_input = False
        if target == MO_DESKTOP_SESSION_SLOT:
            self._return_from_desktop_session_history()
            return
        sessions = getattr(self._agent, "_sessions", None)
        if not sessions:
            return
        try:
            data = sessions.load(target)
        except Exception:
            data = None
        if not isinstance(data, dict):
            self._set_status("That conversation is no longer available.", self._visual_palette.warn)
            self._display_desktop_session_history()
            return
        from core.session.sessions import PortableConversationError, SessionManager
        try:
            SessionManager._check_surface(target, data, "mo_desktop")
        except PortableConversationError:
            self._set_status("That conversation does not belong to Desktop.", self._visual_palette.warn)
            return
        current = self._ensure_desktop_session()
        self._archive_desktop_session(current)
        self._release_desktop_control_state(current)
        self._restore_desktop_session_data(current, data)
        self._persist_desktop_session(compact_live=True)
        self._sync_reply_history_from_session(current)
        bubble = self._get_reply_bubble()
        if bubble is not None:
            clear_draft = getattr(bubble, "clear_input_draft", None)
            if callable(clear_draft):
                clear_draft()
        self._return_from_desktop_session_history()

    def _new_desktop_session_from_panel(self) -> None:
        self._desktop_history_return_to_input = False
        self._start_new_desktop_session()
        bubble = self._get_reply_bubble()
        if bubble is not None:
            clear_draft = getattr(bubble, "clear_input_draft", None)
            if callable(clear_draft):
                clear_draft()
        self._display_input_dialog()

    def _return_from_desktop_session_history(self) -> None:
        return_to_input = bool(getattr(self, "_desktop_history_return_to_input", False))
        self._desktop_history_return_to_input = False
        if return_to_input:
            self._display_input_dialog()
            return
        last_reply = self._last_desktop_reply()
        if last_reply:
            self._display_reply_dialog(last_reply)
        else:
            self._display_input_dialog()

    @classmethod
    def _clean_desktop_session_messages(
        cls,
        messages: Any,
        *,
        role_overlays: tuple[str, ...] = (),
    ) -> list[dict[str, str]]:
        """Keep conversation and owned context; remove transient tool payloads.

        Live turns still keep full tool chains and screenshot image payloads so
        the provider can reason over the current action. Persisted Desktop
        history is different: old tool calls, stale images, and internal audit
        prompts are not useful continuity and have caused slow, wrong bubbles.
        """
        from core.session.session import INTERNAL_CONTINUATION_KEY, PRESENTATION_KEY, is_runtime_owned_session_summary

        cleaned: list[dict[str, str]] = []
        plain_assistant_index: int | None = None
        for raw in list(messages or []):
            if not isinstance(raw, dict):
                continue
            if raw.get(INTERNAL_CONTINUATION_KEY) is True:
                continue
            role = str(raw.get("role") or "").strip()
            if (
                role == "system" and str(raw.get("content") or "").startswith(DESKTOP_SYNC_CONTEXT_PREFIX)
            ) or is_runtime_owned_session_summary(raw):
                cleaned.append(dict(raw, role="system"))
                continue
            if role == "tool":
                continue
            had_tool_calls = bool(role == "assistant" and raw.get("tool_calls"))
            if role not in {"user", "assistant"}:
                continue
            content = cls._plain_desktop_message_content(raw.get("content"))
            if role == "user":
                plain_assistant_index = None
                for overlay in role_overlays:
                    exact = str(overlay or "").strip()
                    if exact and content.startswith(exact):
                        content = content[len(exact):].lstrip()
                        break
                # Old versions serialized the whole profile role contract as
                # operator speech. If that role changed or was removed, fail
                # closed instead of replaying stale profile policy as a user turn.
                if content.startswith("## Active Role:"):
                    continue
            if not content:
                continue
            if role == "assistant" and cls._is_internal_reasoning_text(content):
                continue
            if had_tool_calls and cls._is_tool_preamble_text(content):
                continue
            message = {"role": role, "content": content}
            if isinstance(raw.get(PRESENTATION_KEY), dict):
                message[PRESENTATION_KEY] = dict(raw[PRESENTATION_KEY])
            if role == "assistant" and not had_tool_calls:
                # Provider retries and multi-step visual turns can emit several
                # plain assistant messages before the verified final. Only that
                # last plain message is conversation truth; useful tool-bound
                # walkthrough labels remain separate and are retained.
                if plain_assistant_index is not None:
                    cleaned.pop(plain_assistant_index)
                    plain_assistant_index = len(cleaned)
                    cleaned.append(message)
                else:
                    plain_assistant_index = len(cleaned)
                    cleaned.append(message)
            else:
                cleaned.append(message)
        while (
            cleaned and cleaned[-1].get("role") == "user"
            and not cleaned[-1].get(PRESENTATION_KEY, {}).get("attachments")
        ):
            cleaned.pop()
        return cleaned

    @staticmethod
    def _plain_desktop_message_content(content: Any) -> str:
        if isinstance(content, list):
            parts: list[str] = []
            omitted_images = 0
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "text" and item.get("text"):
                    parts.append(str(item.get("text") or "").strip())
                elif "image" in item_type:
                    omitted_images += 1
            if omitted_images:
                parts.append(
                    f"[{omitted_images} stale desktop image omitted; use computer_observe kind=screen for current screen.]"
                )
            text = "\n".join(part for part in parts if part).strip()
        else:
            text = str(content or "").strip()
        text = _TURN_POLICY_PREFIX_RE.sub("", text, count=1)
        return _VOICE_ROLE_PREFIX_RE.sub("", text, count=1).strip()
