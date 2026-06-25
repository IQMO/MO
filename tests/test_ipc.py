import json
import socket

import pytest

from core import ipc


def _echo_handler(request, emit):
    for i in range(int(request.get("ticks", 0))):
        emit({"tick": i})
    return {"echo": request.get("msg")}


@pytest.fixture
def server(tmp_path):
    srv = ipc.IpcServer(_echo_handler, name="test", mo_home_path=str(tmp_path)).start()
    try:
        yield srv, str(tmp_path)
    finally:
        srv.stop()


def test_request_streams_events_then_response(server):
    _srv, home = server
    client = ipc.IpcClient.connect(name="test", mo_home_path=home)
    try:
        frames = list(client.request({"msg": "hi", "ticks": 3}))
    finally:
        client.close()
    events = [f for f in frames if f["type"] == "event"]
    assert [e["tick"] for e in events] == [0, 1, 2]
    assert frames[-1]["type"] == "response"
    assert frames[-1]["result"] == {"echo": "hi"}


def test_sequential_requests_reuse_one_connection(server):
    _srv, home = server
    client = ipc.IpcClient.connect(name="test", mo_home_path=home)
    try:
        r1 = list(client.request({"msg": "a"}))[-1]["result"]
        r2 = list(client.request({"msg": "b"}))[-1]["result"]
    finally:
        client.close()
    assert r1 == {"echo": "a"}
    assert r2 == {"echo": "b"}


def test_missing_endpoint_is_unavailable(tmp_path):
    with pytest.raises(ipc.IpcUnavailable):
        ipc.IpcClient.connect(name="absent", mo_home_path=str(tmp_path))


def test_bad_token_is_rejected(server):
    _srv, home = server
    path = ipc._endpoint_file("test", home)
    info = json.loads(path.read_text(encoding="utf-8"))
    info["token"] = "wrong"
    path.write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises((ipc.IpcAuthError, ipc.IpcUnavailable)):
        ipc.IpcClient.connect(name="test", mo_home_path=home)


def test_handler_exception_becomes_error_frame(tmp_path):
    def boom(_request, _emit):
        raise ValueError("kaboom")

    srv = ipc.IpcServer(boom, name="boom", mo_home_path=str(tmp_path)).start()
    try:
        client = ipc.IpcClient.connect(name="boom", mo_home_path=str(tmp_path))
        frames = list(client.request({}))
        client.close()
    finally:
        srv.stop()
    assert frames[-1]["type"] == "error"
    assert "kaboom" in frames[-1]["message"]


def test_bad_json_does_not_kill_server(server):
    _srv, home = server
    info = json.loads(ipc._endpoint_file("test", home).read_text(encoding="utf-8"))
    conn = socket.create_connection((info["host"], info["port"]))
    try:
        ipc._send_line(conn, {"token": info["token"]})
        lines = ipc._read_lines(conn)
        assert json.loads(next(lines)) == {"ok": True}
        conn.sendall(b"not-json\n")
        err = json.loads(next(lines))
        assert err["type"] == "error" and err["message"] == "bad-json"
        # The same connection still serves a valid request afterward.
        ipc._send_line(conn, {"id": 9, "msg": "ok"})
        final = None
        for raw in lines:
            frame = json.loads(raw)
            if frame["type"] == "response":
                final = frame
                break
        assert final is not None and final["result"] == {"echo": "ok"}
    finally:
        conn.close()


def test_endpoint_written_locked_and_removed_on_stop(tmp_path):
    srv = ipc.IpcServer(_echo_handler, name="gone", mo_home_path=str(tmp_path)).start()
    path = ipc._endpoint_file("gone", str(tmp_path))
    assert path.exists()
    info = json.loads(path.read_text(encoding="utf-8"))
    assert info["host"] == ipc.LOOPBACK and isinstance(info["port"], int) and info["token"]
    srv.stop()
    assert not path.exists()


def test_never_binds_non_loopback(server):
    srv, _home = server
    assert srv.address[0] == ipc.LOOPBACK
