

# ── SEC-2 regression: browser tools must be sandbox-gated ──────────────

def test_sec2a_browser_open_blocked_when_web_fetch_disabled():
    """SEC-2a: browser_open respects web_fetch_enabled config."""
    from core.sandbox import guard_tool_call
    cfg = {"enabled": True, "web_fetch_enabled": False}
    reason = guard_tool_call("browser_open", {"url": "https://example.com"},
                             lane=None, allowed_roots=["."], sandbox_config=cfg)
    assert reason is not None
    assert "network access disabled" in reason


def test_sec2a_browser_open_respects_host_allowlist():
    """SEC-2a: browser_open respects web_fetch_allowed_hosts."""
    from core.sandbox import guard_tool_call
    cfg = {"enabled": True, "web_fetch_enabled": True, "web_fetch_allowed_hosts": ["github.com"]}
    assert guard_tool_call("browser_open", {"url": "https://github.com/IQMO"},
                           lane=None, allowed_roots=["."], sandbox_config=cfg) is None
    reason = guard_tool_call("browser_open", {"url": "https://example.com"},
                             lane=None, allowed_roots=["."], sandbox_config=cfg)
    assert reason is not None
    assert "host not allowed" in reason


def test_sec2b_browser_actuation_blocked_in_read_only_lanes():
    """SEC-2b: browser_open/click/type are in ACTUATION_TOOLS and blocked in RO lanes."""
    from core.sandbox import guard_tool_call
    cfg = {"enabled": True, "web_fetch_enabled": True}
    for name in ("browser_open", "browser_click", "browser_type"):
        reason = guard_tool_call(name, {"url": "https://x.com"} if name == "browser_open" else {"ref": "e1"},
                                 lane="investigate", allowed_roots=["."], sandbox_config=cfg)
        assert reason is not None, f"{name} should be blocked in investigate lane"
        assert "LANE LOCKED" in reason, f"{name}: {reason}"


def test_sec2c_browser_open_rejects_file_urls():
    """SEC-2c: browser_open rejects file: URLs (bypasses path scope)."""
    from core.sandbox import guard_tool_call
    cfg = {"enabled": True, "web_fetch_enabled": True}
    reason = guard_tool_call("browser_open", {"url": "file:///etc/passwd"},
                             lane=None, allowed_roots=["."], sandbox_config=cfg)
    assert reason is not None
    assert "file:" in reason or "file" in reason.lower()


def test_sec2c_browser_open_rejects_data_urls():
    """SEC-2c: browser_open rejects data: URLs."""
    from core.sandbox import guard_tool_call
    cfg = {"enabled": True, "web_fetch_enabled": True}
    reason = guard_tool_call("browser_open", {"url": "data:text/html,<script>alert(1)</script>"},
                             lane=None, allowed_roots=["."], sandbox_config=cfg)
    assert reason is not None
    assert "data:" in reason or "data" in reason.lower()


def test_sec2_browser_open_allowed_for_https():
    """Sanity: browser_open with https:// and web_fetch enabled passes."""
    from core.sandbox import guard_tool_call
    cfg = {"enabled": True, "web_fetch_enabled": True}
    assert guard_tool_call("browser_open", {"url": "https://example.com"},
                           lane=None, allowed_roots=["."], sandbox_config=cfg) is None


def test_sec2_desktop_actuation_still_in_actuation_tools():
    """Verify desktop actuation tools unchanged in ACTUATION_TOOLS."""
    from core.tool_constants import ACTUATION_TOOLS
    assert "move_pointer" in ACTUATION_TOOLS
    assert "mouse_click" in ACTUATION_TOOLS
    assert "type_text" in ACTUATION_TOOLS
    assert "press_key" in ACTUATION_TOOLS


def test_sec2_browser_snapshot_is_read_only():
    """browser_snapshot reads the DOM only — NOT actuation; passes read-only lanes."""
    from core.tool_constants import ACTUATION_TOOLS
    from core.sandbox import guard_tool_call
    assert "browser_snapshot" not in ACTUATION_TOOLS
    cfg = {"enabled": True}
    assert guard_tool_call("browser_snapshot", {}, lane="investigate",
                           allowed_roots=["."], sandbox_config=cfg) is None


def test_browser_eval_is_actuation_gated_in_read_only_lanes():
    """browser_eval runs arbitrary JS (can navigate/click/mutate/read page state),
    so it is gated as actuation (review F1): allowed in normal lanes, blocked in
    read-only lanes. Supersedes the original SEC-2 read-only classification."""
    from core.tool_constants import ACTUATION_TOOLS
    from core.sandbox import guard_tool_call
    assert "browser_eval" in ACTUATION_TOOLS
    cfg = {"enabled": True}
    assert guard_tool_call("browser_eval", {"expression": "1+1"}, lane="investigate",
                           allowed_roots=["."], sandbox_config=cfg)  # blocked in read-only lane
    assert guard_tool_call("browser_eval", {"expression": "1+1"}, lane=None,
                           allowed_roots=["."], sandbox_config=cfg) is None  # allowed normally
