from core import ipc, ipc_service


class _FakeAgent:
    session = type("S", (), {"session_id": "sess-1"})()
    provider_name = "fake"
    model = "fake-model"


class FakeGateway:
    """Minimal Gateway stand-in: drives the streaming callbacks then returns text.

    No real agent / provider / model call — this exercises only the IPC bridge.
    """

    def __init__(self):
        self.agent = _FakeAgent()
        self.calls = []

    def run_turn(
        self,
        user_input,
        *,
        on_token=None,
        on_activity=None,
        on_board_update=None,
        on_proposal=None,
        route_source="user",
        **_ignored,
    ):
        self.calls.append((user_input, route_source))
        if on_proposal:
            on_proposal("plan: do the thing")
        if on_activity:
            on_activity("thinking")
        if on_token:
            for ch in "hello":
                on_token(ch)
        if on_board_update:
            on_board_update("[board] 1/1")
        return f"done: {user_input}"


def _serve(tmp_path, gateway, name="gw"):
    return ipc_service.serve_gateway(gateway, name=name, mo_home_path=str(tmp_path))


def _request(tmp_path, payload, name="gw"):
    client = ipc.IpcClient.connect(name=name, mo_home_path=str(tmp_path))
    try:
        return list(client.request(payload))
    finally:
        client.close()


def test_ping_reports_identity(tmp_path):
    srv = _serve(tmp_path, FakeGateway())
    try:
        frames = _request(tmp_path, {"type": "ping"})
    finally:
        srv.stop()
    result = frames[-1]
    assert result["type"] == "response"
    assert result["result"]["pong"] is True
    assert result["result"]["provider"] == "fake"
    assert result["result"]["model"] == "fake-model"
    assert result["result"]["session_id"] == "sess-1"


def test_run_turn_streams_events_then_text(tmp_path):
    gw = FakeGateway()
    srv = _serve(tmp_path, gw)
    try:
        frames = _request(tmp_path, {"type": "run_turn", "input": "hi", "route_source": "user"})
    finally:
        srv.stop()
    kinds = [f.get("kind") for f in frames if f["type"] == "event"]
    assert "proposal" in kinds
    assert "activity" in kinds
    assert kinds.count("token") == 5
    assert "board" in kinds
    tokens = "".join(f["text"] for f in frames if f.get("kind") == "token")
    assert tokens == "hello"
    assert frames[-1]["type"] == "response"
    assert frames[-1]["result"]["text"] == "done: hi"
    assert gw.calls == [("hi", "user")]


def test_route_source_defaults_to_user(tmp_path):
    gw = FakeGateway()
    srv = _serve(tmp_path, gw)
    try:
        _request(tmp_path, {"type": "run_turn", "input": "x"})
    finally:
        srv.stop()
    assert gw.calls == [("x", "user")]


def test_unknown_request_type_is_error(tmp_path):
    srv = _serve(tmp_path, FakeGateway())
    try:
        frames = _request(tmp_path, {"type": "bogus"})
    finally:
        srv.stop()
    assert frames[-1]["type"] == "error"
    assert "unknown request type" in frames[-1]["message"]


def test_turn_exception_becomes_error_frame(tmp_path):
    class BoomGateway(FakeGateway):
        def run_turn(self, *_a, **_k):
            raise RuntimeError("turn blew up")

    srv = _serve(tmp_path, BoomGateway())
    try:
        frames = _request(tmp_path, {"type": "run_turn", "input": "x"})
    finally:
        srv.stop()
    assert frames[-1]["type"] == "error"
    assert "turn blew up" in frames[-1]["message"]


def test_non_serializable_event_value_does_not_abort_turn(tmp_path):
    """A board update carrying a non-JSON object must degrade to str, not crash."""

    class Renderable:
        def __str__(self):
            return "RICH-OBJECT"

    class RichGateway(FakeGateway):
        def run_turn(self, user_input, *, on_board_update=None, **_k):
            if on_board_update:
                on_board_update(Renderable())
            return "ok"

    srv = _serve(tmp_path, RichGateway())
    try:
        frames = _request(tmp_path, {"type": "run_turn", "input": "x"})
    finally:
        srv.stop()
    board = [f for f in frames if f.get("kind") == "board"]
    assert board and board[0]["rich"] == "RICH-OBJECT"
    assert frames[-1]["type"] == "response"
    assert frames[-1]["result"]["text"] == "ok"
