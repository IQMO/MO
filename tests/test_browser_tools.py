"""Computer-use Step 2: native CDP browser tools — registration + arg handling
(headless; does not launch a real browser)."""
import json
import subprocess
import sys
from pathlib import Path

import tools
from tools.browser import BrowserManager, execute_browser_open, execute_browser_eval

BROWSER_TOOLS = ["browser_open", "browser_snapshot", "browser_click", "browser_type", "browser_eval", "browser_close"]


def test_browser_tools_registered():
    for name in BROWSER_TOOLS:
        assert name in tools.TOOL_EXECUTORS, name
        assert any(d["function"]["name"] == name for d in tools.TOOL_DEFINITIONS), name


def test_tools_import_does_not_load_browser_dependencies():
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import json, sys; "
        "import tools; "
        "print(json.dumps({"
        "'browser_module': 'tools.browser' in sys.modules, "
        "'httpx': 'httpx' in sys.modules, "
        "'websocket': 'websocket' in sys.modules"
        "}))"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(proc.stdout) == {"browser_module": False, "httpx": False, "websocket": False}


def test_browser_open_requires_url():
    assert "requires" in execute_browser_open({}).lower()


def test_browser_eval_requires_expression():
    assert "requires" in execute_browser_eval({}).lower()


def test_chrome_path_probe_is_safe():
    # Returns a real path or None — never raises.
    assert BrowserManager._chrome_path() is None or isinstance(BrowserManager._chrome_path(), str)
