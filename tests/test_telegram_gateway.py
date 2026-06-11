from contextlib import contextmanager

from core.session.session import Session
from core.session.sessions import SessionManager
from core.telegram.auth import TelegramAuthStore
from core.telegram.gateway import TelegramGateway
from core.telegram.sessions import TelegramSessionStore


class DummyAgent:
    system_message = "system"
    config = {"telegram": {"enabled": False, "dm_policy": "pairing"}}
    provider_name = "test"
    model = "model"

    def __init__(self, sessions_dir=None):
        self.session = Session(self.system_message)
        self._sessions = SessionManager(str(sessions_dir)) if sessions_dir else None
        self.commands = []
        self.steer = []

    @contextmanager
    def isolated_session(self, session):
        previous = self.session
        self.session = session
        try:
            yield
        finally:
            self.session = previous

    def process_slash_command(self, text):
        self.commands.append((self.session.session_id, text))
        if text == "/stop":
            return "stop requested"
        if text == "/status":
            return "status ok"
        return "cmd " + text

    def add_live_steer(self, text, **kwargs):
        self.steer.append((text, kwargs))
        return "steer-id"

    def _provider_context_max_chars(self):
        return 100_000


class LegacyRunApiAgent(DummyAgent):
    def __init__(self, sessions_dir=None):
        super().__init__(sessions_dir)
        self.run_api_calls = []

    def run_api_call(self, text, cancel_check=None, steer_check=None):
        steer = steer_check() if callable(steer_check) else None
        self.run_api_calls.append((text, bool(cancel_check and cancel_check()), steer))
        return "legacy " + text + (f" steer={steer}" if steer else "")


class DummyGateway:
    def __init__(self, agent):
        self.agent = agent
        self.calls = []
        self.last_task_board = None

    def run_turn(self, text, **kwargs):
        self.calls.append((self.agent.session.session_id, text, kwargs))
        self.agent.session.add_user(text)
        self.agent.session.add_assistant("ok " + text)
        return "ok " + text


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, update):
        self.update = update
        self.posts = []

    def get(self, url, params=None, timeout=None):
        result = self.update if isinstance(self.update, list) else [self.update]
        return FakeResponse({"ok": True, "result": result})

    def post(self, url, json=None, data=None, files=None, timeout=None):
        payload = json if json is not None else {"data": data, "files": bool(files)}
        self.posts.append((url, payload))
        if url.endswith("/sendMessage"):
            return FakeResponse({"ok": True, "result": {"message_id": 123}})
        return FakeResponse({"ok": True, "result": {}})


def _gateway(tmp_path, agent=None, *, secret_files=()):
    agent = agent or DummyAgent(tmp_path / "sessions")
    router = DummyGateway(agent)
    tg = TelegramGateway(
        agent=agent,
        gateway=router,
        enabled=True,
        token_env="MISSING",
        dm_policy="pairing",
        auth=TelegramAuthStore(tmp_path / "tg.sqlite"),
        sessions=TelegramSessionStore(tmp_path / "tg.sqlite"),
        secret_files=tuple(str(x) for x in secret_files),
    )
    return tg, agent, router


def _approve(gateway, sender="7"):
    code = gateway.authorize_or_pair(sender)[1].rsplit(" ", 1)[-1]
    assert gateway.approve(code)


def test_telegram_pairing_unknown_user_blocked(tmp_path):
    store = TelegramAuthStore(tmp_path / "tg.sqlite", ttl_seconds=3600)
    code = store.create_pairing("42")
    assert code.sender_id == "42"
    assert store.is_allowed("42") is False
    assert store.approve(code.code) is True
    assert store.is_allowed("42") is True


def test_telegram_gateway_resolves_token_from_configured_secret_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("MISSING=test-token\n", encoding="utf-8")
    gateway, _agent, _router = _gateway(tmp_path, secret_files=[secret_file])

    status = gateway.status()

    assert status["token_present"] is True
    assert status["token_source"].replace("\\", "/").endswith("secrets.env")


def test_telegram_session_mapping_persists(tmp_path):
    sessions = TelegramSessionStore(tmp_path / "tg.sqlite")
    assert sessions.get_or_create("chat1") == "telegram-chat1"
    assert sessions.get_or_create("chat1") == "telegram-chat1"
    assert sessions.count() == 1
    mappings = sessions.list_mappings()
    assert mappings[0]["chat_id"] == "chat1"
    assert mappings[0]["session_name"] == "telegram-chat1"


def test_gateway_blocks_then_allows_approved_sender_and_routes_through_gateway(tmp_path):
    gateway, _agent, router = _gateway(tmp_path)
    blocked = gateway.handle_text(sender_id="7", chat_id="c", text="hi")
    assert "Pairing required" in blocked
    code = blocked.rsplit(" ", 1)[-1]
    assert gateway.approve(code)

    assert gateway.handle_text(sender_id="7", chat_id="c", text="build a thing") == "ok build a thing"

    assert router.calls
    assert router.calls[0][1] == "build a thing"
    assert router.calls[0][2]["route_source"] == "telegram"
    assert router.calls[0][0].startswith("mo-telegram-")


def test_gateway_uses_isolated_chat_session_and_restores_main_session(tmp_path):
    agent = DummyAgent(tmp_path / "sessions")
    main_session = agent.session
    gateway, _agent, router = _gateway(tmp_path, agent=agent)
    _approve(gateway)

    assert gateway.handle_text(sender_id="7", chat_id="c", text="do work") == "ok do work"

    assert agent.session is main_session
    assert router.calls[0][0] != main_session.session_id
    saved = agent._sessions.load("telegram-c")
    assert saved is not None
    assert any(msg.get("content") == "do work" for msg in saved["messages"])


def test_gateway_auto_reply_heartbeat_does_not_call_provider(tmp_path):
    gateway, _agent, router = _gateway(tmp_path)
    _approve(gateway)

    reply = gateway.handle_text(sender_id="7", chat_id="c", text="/heartbeat")

    assert "Heartbeat:" in reply
    assert router.calls == []


def test_gateway_group_requires_mention(tmp_path):
    gateway, _agent, _router = _gateway(tmp_path)
    gateway.bot_username = "mo_bot"
    _approve(gateway)

    assert gateway.handle_text(sender_id="7", chat_id="g", text="hi", chat_type="group") == ""
    assert gateway.handle_text(sender_id="7", chat_id="g", text="@mo_bot build game", chat_type="group") == "ok build game"


def test_gateway_polling_ignores_unmentioned_group_without_working_message(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "test-token")
    gateway, _agent, _router = _gateway(tmp_path)
    gateway.token_env = "TG_TOKEN"
    gateway.bot_username = "mo_bot"
    update = {"update_id": 10, "message": {"text": "hi", "chat": {"id": "g", "type": "group"}, "from": {"id": "7"}}}
    client = FakeClient(update)

    gateway.run_polling(once=True, client=client)

    assert client.posts == []


def test_gateway_active_chat_message_becomes_live_steer(tmp_path):
    gateway, agent, _router = _gateway(tmp_path)
    client = FakeClient({})
    gateway.active_chats.add("c")

    gateway.enqueue_text(sender_id="7", chat_id="c", text="extra guidance", chat_type="private", client=client, base="https://api.telegram.org/botTOKEN", message_id=1)

    assert gateway.pop_steer("c") is None
    assert agent.steer[0][0] == "extra guidance"
    assert any("queued as steer" in payload.get("text", "") for _, payload in client.posts)


def test_gateway_active_chat_steer_buffer_remains_for_legacy_agent_without_live_steer(tmp_path):
    agent = LegacyRunApiAgent(tmp_path / "sessions")
    agent.add_live_steer = None
    gateway, _agent, _router = _gateway(tmp_path, agent=agent)
    client = FakeClient({})
    gateway.active_chats.add("c")

    gateway.enqueue_text(sender_id="7", chat_id="c", text="legacy steer", chat_type="private", client=client, base="https://api.telegram.org/botTOKEN", message_id=1)

    assert gateway.pop_steer("c") == "legacy steer"
    assert any("queued as steer" in payload.get("text", "") for _, payload in client.posts)


def test_gateway_legacy_run_api_fallback_receives_buffered_steer(tmp_path):
    agent = LegacyRunApiAgent(tmp_path / "sessions")
    gateway = TelegramGateway(
        agent=agent,
        gateway=None,
        enabled=True,
        token_env="MISSING",
        dm_policy="pairing",
        auth=TelegramAuthStore(tmp_path / "tg.sqlite"),
        sessions=TelegramSessionStore(tmp_path / "tg.sqlite"),
    )
    _approve(gateway)
    gateway.steer_buffers["c"] = ["legacy steer"]

    reply = gateway.handle_text(sender_id="7", chat_id="c", text="continue")

    assert reply == "legacy continue steer=legacy steer"
    assert agent.run_api_calls[-1][2] == "legacy steer"


def test_gateway_stop_sets_cancel_event_for_chat(tmp_path):
    gateway, agent, _router = _gateway(tmp_path)
    _approve(gateway)

    assert "stop requested" in gateway.handle_text(sender_id="7", chat_id="c", text="/stop")
    assert "stop requested" in gateway.handle_text(sender_id="7", chat_id="c", text="cancel")

    assert gateway.cancel_events["c"].is_set()
    assert agent.commands[-1][1] == "/stop"


def test_gateway_active_chat_cancel_stops_instead_of_steering(tmp_path):
    gateway, agent, _router = _gateway(tmp_path)
    _approve(gateway)
    client = FakeClient({})
    gateway.active_chats.add("c")

    gateway.enqueue_text(sender_id="7", chat_id="c", text="cancel", chat_type="private", client=client, base="https://api.telegram.org/botTOKEN", message_id=1)

    assert gateway.cancel_events["c"].is_set()
    assert gateway.pop_steer("c") is None
    assert agent.steer == []
    assert any("stop requested" in payload.get("text", "") for _, payload in client.posts)


def test_gateway_polling_handles_one_update(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "test-token")
    agent = DummyAgent(tmp_path / "sessions")
    router = DummyGateway(agent)
    update = {"update_id": 10, "message": {"text": "build", "chat": {"id": "c"}, "from": {"id": "7"}}}
    gateway = TelegramGateway(
        agent=agent,
        gateway=router,
        enabled=True,
        token_env="TG_TOKEN",
        dm_policy="pairing",
        auth=TelegramAuthStore(tmp_path / "tg.sqlite"),
        sessions=TelegramSessionStore(tmp_path / "tg.sqlite"),
    )
    _approve(gateway)
    client = FakeClient(update)

    gateway.run_polling(once=True, client=client)

    assert client.posts[0][1]["text"] == "MO working…"
    assert client.posts[1][0].endswith("/editMessageText")
    assert client.posts[1][1]["text"] == "ok build"


def test_gateway_long_reply_sends_document(tmp_path):
    gateway, _agent, _router = _gateway(tmp_path)
    client = FakeClient({})

    gateway._deliver_reply(client, "https://api.telegram.org/botTOKEN", "c", "x" * 5000, message_id=1)

    assert any(url.endswith("/sendDocument") for url, _ in client.posts)


def test_telegram_reply_appends_task_board_for_work_turns():
    """Remote surface parity: the evidence-gated board must reach Telegram replies."""
    from types import SimpleNamespace
    from core.telegram.gateway import TelegramGateway
    from core.tasking.task_board import TaskBoard, TaskItem

    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect files", "completed", kind="inspect"),
        TaskItem("2", "Fix bug", "active", kind="edit"),
    ])
    router = SimpleNamespace(last_task_board=board)

    text = TelegramGateway._append_task_board("Done with inspection.", router)

    assert text.startswith("Done with inspection.")
    assert "Inspect files" in text
    assert "Fix bug" in text


def test_telegram_reply_unchanged_without_board():
    from types import SimpleNamespace
    from core.telegram.gateway import TelegramGateway

    assert TelegramGateway._append_task_board("hi", SimpleNamespace(last_task_board=None)) == "hi"
    assert TelegramGateway._append_task_board("hi", None) == "hi"
