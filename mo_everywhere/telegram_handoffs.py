"""Confirm-only portable-conversation adapter for authorized Telegram DMs."""
from __future__ import annotations

import re
from typing import Any

from .conversation_handoffs import (
    ConversationHandoff,
    ConversationHandoffError,
    ConversationHandoffStore,
)


_COMMAND_RE = re.compile(
    r"/handoff\s+(accept|refuse)\s+([0-9a-f]{8,32})\s*",
    re.IGNORECASE,
)


class TelegramConversationHandoffAdapter:
    """Bind one canonical portable session only after an exact DM confirms it."""

    def __init__(
        self,
        gateway: Any,
        store: ConversationHandoffStore,
        session_manager: Any,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.session_manager = session_manager

    def targets(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self._running():
            return []
        rows = []
        for mapping in self.gateway.sessions.list_mappings(limit=max(1, min(50, limit))):
            chat_id = str(mapping.get("chat_id") or "")
            if not self._authorized_private_chat(chat_id):
                continue
            rows.append(mapping)
        return [
            {
                "target_id": "telegram:" + str(item["handoff_key"]),
                "label": (
                    "Telegram direct chat"
                    if len(rows) == 1
                    else f"Telegram direct chat · {index}"
                ),
                "kind": "telegram",
                "modes": ["confirm"],
            }
            for index, item in enumerate(rows, 1)
        ]

    def available(self, target_key: str) -> bool:
        if not self._running():
            return False
        mapping = self._mapping_for_target(target_key)
        return bool(
            mapping
            and self._authorized_private_chat(str(mapping.get("chat_id") or ""))
        )

    def request(self, item: ConversationHandoff) -> ConversationHandoff:
        mapping = self._mapping_for_target(item.target_key)
        chat_id = str((mapping or {}).get("chat_id") or "")
        if not self._running() or not self._authorized_private_chat(chat_id):
            self.store.fail_unavailable(
                item.handoff_id,
                reason="The Telegram target is no longer available.",
            )
            return self.store.get(item.handoff_id, item.source_device_id)
        delivered = self.gateway.send_system_message(
            chat_id,
            "\n".join(
                (
                    f"{item.source_label} wants to continue a portable MO conversation here.",
                    "MO has validated the conversation on the hub but has not opened it in this chat.",
                    f"Reply /handoff accept {item.handoff_id[:8]} to continue, or "
                    f"/handoff refuse {item.handoff_id[:8]}.",
                    "This request expires in 10 minutes.",
                )
            ),
        )
        if not delivered:
            self.store.fail_unavailable(
                item.handoff_id,
                reason="Telegram did not accept the handoff notification.",
            )
            return self.store.get(item.handoff_id, item.source_device_id)
        return self.store.acknowledge_system(
            item.handoff_id,
            target_key=item.target_key,
            state="received",
        )

    def handle_command(
        self,
        *,
        sender_id: str,
        chat_id: str,
        chat_type: str,
        text: str,
    ) -> str | None:
        match = _COMMAND_RE.fullmatch(str(text or "").strip())
        if match is None:
            return None
        if (
            str(chat_type or "private") != "private"
            or str(sender_id) != str(chat_id)
            or not self._authorized_private_chat(str(chat_id))
        ):
            return "Conversation handoff confirmation is available only in its authorized direct chat."
        mapping = self.gateway.sessions.handoff_target_for_chat(str(chat_id))
        if not mapping:
            return "That conversation handoff is no longer available."
        target_key = "telegram:" + str(mapping["handoff_key"])
        try:
            item = self.store.find_system_for_target(
                target_key,
                match.group(2),
                states=("requested", "received", "ready"),
            )
        except ConversationHandoffError:
            return "That conversation handoff reference is invalid or ambiguous."
        if item is None:
            return "That conversation handoff is no longer available."
        if match.group(1).lower() == "refuse":
            if item.state == "ready":
                return "That conversation is already ready in this chat."
            refused = self.store.acknowledge_system(
                item.handoff_id,
                target_key=target_key,
                state="refused",
                reason="Refused in the target Telegram chat.",
            )
            return f"Conversation handoff {refused.handoff_id[:8]} was refused."
        try:
            session_name, _data = self.session_manager.load_portable(
                item.conversation_id,
                expected_revision=item.expected_revision,
            )
            if item.state == "requested":
                item = self.store.acknowledge_system(
                    item.handoff_id,
                    target_key=target_key,
                    state="received",
                )
            if item.state == "received":
                item = self.store.acknowledge_system(
                    item.handoff_id,
                    target_key=target_key,
                    state="ready",
                )
            if not self.gateway.sessions.bind_handoff_target(
                str(mapping["handoff_key"]), session_name, item.handoff_id
            ):
                raise RuntimeError("Telegram chat mapping is unavailable")
        except Exception:
            self.store.fail_unavailable(
                item.handoff_id,
                reason="The target could not validate the portable conversation revision.",
            )
            return "The conversation could not be opened because its revision is no longer available."
        return (
            f"Conversation handoff {item.handoff_id[:8]} is ready. "
            "Your next message continues that conversation."
        )

    def mark_running(self, *, chat_id: str, session_name: str) -> None:
        mapping = self.gateway.sessions.handoff_target_for_chat(str(chat_id))
        if not mapping or str(mapping.get("session_name") or "") != str(session_name):
            return
        handoff_id = str(mapping.get("active_handoff_id") or "")
        if not handoff_id:
            return
        target_key = "telegram:" + str(mapping["handoff_key"])
        try:
            item = self.store.find_system_for_target(
                target_key,
                handoff_id,
                states=("ready",),
            )
            if item is not None:
                self.store.acknowledge_system(
                    item.handoff_id,
                    target_key=target_key,
                    state="running",
                )
                self.gateway.sessions.clear_active_handoff(
                    str(mapping["handoff_key"]), item.handoff_id
                )
        except ConversationHandoffError:
            return

    def notify_cancelled(self, item: ConversationHandoff) -> None:
        mapping = self._mapping_for_target(item.target_key)
        chat_id = str((mapping or {}).get("chat_id") or "")
        if self._authorized_private_chat(chat_id):
            self.gateway.send_system_message(
                chat_id,
                f"Conversation handoff {item.handoff_id[:8]} was cancelled by the requesting device.",
            )

    def _mapping_for_target(self, target_key: str) -> dict[str, str] | None:
        key = str(target_key or "").strip().lower()
        if not key.startswith("telegram:"):
            return None
        try:
            return self.gateway.sessions.handoff_target(key.removeprefix("telegram:"))
        except ValueError:
            return None

    def _running(self) -> bool:
        thread = getattr(self.gateway, "_poll_thread", None)
        return bool(
            getattr(self.gateway, "enabled", False)
            and thread is not None
            and thread.is_alive()
        )

    def _authorized_private_chat(self, chat_id: str) -> bool:
        clean = str(chat_id or "")
        return bool(
            clean.isdigit()
            and int(clean) > 0
            and self.gateway._sender_is_authorized(clean, "private")
        )
