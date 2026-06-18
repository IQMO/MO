"""A minimal real MCP stdio server for tests — tools `echo` and `add`.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout, implementing the subset MO
uses: initialize, notifications/initialized, tools/list, tools/call.
"""
import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the given text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock", "version": "1"},
            }})
        elif method == "notifications/initialized":
            continue  # notification, no reply
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(args.get("text", ""))}]}})
            elif name == "add":
                total = (args.get("a") or 0) + (args.get("b") or 0)
                _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(total)}]}})
            else:
                _send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}})
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
