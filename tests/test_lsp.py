import sys
import textwrap

import pytest

from core import lsp
from core.lsp.client import LspClient
from core.lsp.manager import LspManager, language_for, summarize_diagnostics

# A real subprocess that speaks LSP Content-Length framing. It replies to
# initialize/shutdown and, on didOpen, publishes one diagnostic iff the file text
# contains "BUG" — so tests cover both the error-detected and clean paths.
_FAKE_SERVER = textwrap.dedent('''
    import sys, json

    def read_message():
        length = 0
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                break
            k, _, v = line.partition(b":")
            if k.strip().lower() == b"content-length":
                length = int(v.strip())
        if length <= 0:
            return {}
        return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))

    def send(payload):
        body = json.dumps(payload).encode("utf-8")
        sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(body))
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    while True:
        msg = read_message()
        if msg is None:
            break
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}})
        elif method == "textDocument/didOpen":
            td = (msg.get("params") or {}).get("textDocument") or {}
            uri, text = td.get("uri"), td.get("text") or ""
            diags = []
            if "BUG" in text:
                diags = [{"range": {"start": {"line": 0, "character": 0},
                                    "end": {"line": 0, "character": 3}},
                          "severity": 1, "message": "fake error: BUG found"}]
            send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                  "params": {"uri": uri, "diagnostics": diags}})
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
        elif method == "exit":
            break
''')


@pytest.fixture
def fake_server(tmp_path):
    p = tmp_path / "fake_lsp.py"
    p.write_text(_FAKE_SERVER, encoding="utf-8")
    return str(p)


def test_language_for_maps_extensions():
    assert language_for("a.py") == "python"
    assert language_for("a.tsx") == "typescriptreact"
    assert language_for("a.go") == "go"
    assert language_for("a.unknown") is None


def test_summarize_diagnostics_counts_by_severity():
    diags = [{"severity": 1}, {"severity": 1}, {"severity": 2}, {}]  # {} defaults to error
    assert summarize_diagnostics(diags) == {"error": 3, "warning": 1}


def test_client_captures_error_diagnostic(tmp_path, fake_server):
    target = tmp_path / "thing.py"
    target.write_text("x = BUG\n", encoding="utf-8")
    client = LspClient("fake", sys.executable, [fake_server], root_path=str(tmp_path)).start()
    try:
        client.did_open(str(target), "x = BUG\n", "python")
        diags = client.wait_for_diagnostics(str(target), timeout=5.0)
    finally:
        client.stop()
    assert len(diags) == 1
    assert diags[0]["severity"] == 1
    assert "BUG" in diags[0]["message"]


def test_client_clean_file_yields_empty(tmp_path, fake_server):
    target = tmp_path / "clean.py"
    client = LspClient("fake", sys.executable, [fake_server], root_path=str(tmp_path)).start()
    try:
        client.did_open(str(target), "x = 1\n", "python")
        diags = client.wait_for_diagnostics(str(target), timeout=5.0)
    finally:
        client.stop()
    assert diags == []


def test_manager_no_servers_is_clean_noop(tmp_path):
    mgr = LspManager({}, root_path=str(tmp_path))
    assert mgr.enabled is False
    f = tmp_path / "x.py"
    f.write_text("x = BUG\n", encoding="utf-8")
    assert mgr.file_diagnostics(str(f)) == []


def test_manager_routes_file_to_server(tmp_path, fake_server):
    mgr = LspManager({"python": {"command": sys.executable, "args": [fake_server]}}, root_path=str(tmp_path))
    assert mgr.enabled is True
    f = tmp_path / "buggy.py"
    f.write_text("y = BUG\n", encoding="utf-8")
    try:
        diags = mgr.file_diagnostics(str(f), timeout=5.0)
    finally:
        mgr.stop_all()
    assert summarize_diagnostics(diags) == {"error": 1}


def test_manager_unconfigured_language_returns_empty(tmp_path, fake_server):
    mgr = LspManager({"python": {"command": sys.executable, "args": [fake_server]}}, root_path=str(tmp_path))
    f = tmp_path / "a.go"  # no go server configured
    f.write_text("package main", encoding="utf-8")
    try:
        assert mgr.file_diagnostics(str(f)) == []
    finally:
        mgr.stop_all()


def test_package_exports():
    assert hasattr(lsp, "LspClient") and hasattr(lsp, "LspManager")
