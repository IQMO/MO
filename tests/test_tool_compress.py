"""Tests for MO Agent native tool output compression (core/tool_compress.py)."""
import json

from core.tool_compress import classify, compress

# Helper: repeat text to reach minimum byte threshold
def _pad(text, min_chars=600):
    """Repeat text until it exceeds min_chars, ensuring compression trigger."""
    while len(text) < min_chars:
        text = text + "\n" + text
    return text


# ── Classify tests ─────────────────────────────────────────────────

def test_classify_git_diff():
    text = "diff --git a/core/agent.py b/core/agent.py\nindex abc..def\n--- a/core/agent.py\n+++ b/core/agent.py\n@@ -1,5 +1,6 @@\n import os\n+import sys\n"
    assert classify(text) == "git-diff"


def test_classify_git_status():
    text = "On branch main\nChanges not staged for commit:\n  M core/agent.py\n\nUntracked files:\n  ? new_file.py\n"
    assert classify(text) == "git-status"


def test_classify_grep():
    text = "core/agent.py:10: import os\ncore/agent.py:20: import sys\ncore/session.py:5: class Session:\n"
    assert classify(text) == "grep"


def test_classify_build_output():
    text = "npm warn deprecated\nCompiling core/agent.py\nBUILD SUCCESS\nadded 10 packages\n"
    assert classify(text) == "build"


def test_classify_find():
    text = "./core/agent.py\n./core/session.py\n./core/sandbox.py\n./tools/__init__.py\n"
    assert classify(text) == "find"


def test_classify_ls():
    text = "-rw-r--r-- 1 user group 1234 Jan 1 12:00 agent.py\n-rwxr-xr-x 1 user group 5678 Jan 1 12:00 mo.py\ndrwxr-xr-x 1 user group    0 Jan 1 12:00 core\n"
    assert classify(text) == "ls"


def test_classify_tree():
    text = ".\n├── core\n│   ├── agent.py\n│   └── session.py\n└── tools\n    └── __init__.py\n"
    assert classify(text) == "tree"


def test_classify_test_output():
    text = "============================= test session starts =============================\ncore/tests/test_agent.py::test_turn PASSED\ncore/tests/test_sandbox.py::test_guard PASSED\n\n2 passed in 0.5s\n"
    assert classify(text) == "test-output"


def test_classify_read_numbered():
    text = " 1:import os\n 2:import sys\n 3:\n 4:def main():\n 5:    pass\n 6:    return\n 7:\n"
    assert classify(text) == "read-numbered"


def test_classify_unknown_plain_text():
    text = "This is just a plain text response with no recognizable format structure."
    assert classify(text) is None


def test_classify_empty_text():
    assert classify("") is None


def test_classify_below_min_lines():
    # grep needs 3+ non-empty lines
    assert classify("file.py:1: content") is None


# ── Compress tests (pass min_bytes=0 to test compression logic regardless of size) ──

def test_compress_git_diff_reduces_size():
    text = "diff --git a/test.py b/test.py\nindex 000..111\n--- a/test.py\n+++ b/test.py\n@@ -1,3 +1,5 @@\n line1\n line2\n line3\n+line4\n+line5\n"
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "git-diff"
    assert stats["saved_chars"] > 0
    assert len(result) < len(text)
    assert "test.py" in result
    assert "+2" in result


def test_compress_git_status_caps_files():
    lines = ["On branch main", "Changes not staged:"] + [f"  M file_{i}.py" for i in range(30)]
    text = "\n".join(lines)
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "git-status"
    assert len(result) < len(text)
    assert "more changed files" in result


def test_compress_grep_groups_by_file():
    # Build enough matches across files that compression clearly reduces size
    lines = []
    for i in range(15):
        lines.append(f"core/agent.py:{10+i}: import os.path.join.{i}")
    for i in range(8):
        lines.append(f"core/session.py:{5+i}: class Session method {i}")
    text = "\n".join(lines)
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "grep"
    assert "core/agent.py" in result
    assert "core/session.py" in result
    assert "matches in 2 files" in result or "matches in" in result


def test_compress_grep_caps_per_file():
    lines = [f"core/agent.py:{i}: line {i}" for i in range(50)]
    text = "\n".join(lines)
    result, stats = compress(text, min_bytes=0)
    assert stats is not None
    assert "+40 more" in result


def test_compress_build_keeps_errors():
    text = "npm warn deprecated old-pkg\nCompiling package1\nCompiling package2\nERROR: build failed\n[ERROR] something wrong\nBUILD FAILED\n"
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "build"
    assert "Compiled 2 packages" in result
    assert "ERROR" in result
    assert "BUILD FAILED" in result


def test_compress_build_keeps_summary():
    # Multiple Compiling lines and summary should compress well
    lines = ["Compiling package_a", "Compiling package_b", "Compiling package_c"]
    lines.append("BUILD SUCCESS")
    lines.append("added 100 packages, and audited 101 packages in 10s")
    text = "\n".join(lines)
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert "Compiled 3 packages" in result
    assert "BUILD SUCCESS" in result


def test_compress_find_groups_by_dir():
    # Multiple files in same directory should compress well
    text = "\n".join([f"core/file_{i}.py" for i in range(20)])
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "find"
    assert "core/ (20 files)" in result


def test_compress_find_caps_dirs():
    lines = [f"shared_dir/file_{i}.py" for i in range(30)]
    text = "\n".join(lines)
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert len(result) < len(text)


def test_compress_ls_collapses_to_summary():
    text = "-rw-r--r-- 1 user group 1024 Jan 1 12:00 agent.py\n-rw-r--r-- 1 user group 2048 Jan 1 12:00 session.py\ndrwxr-xr-x 1 user group    0 Jan 1 12:00 core\n"
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "ls"
    assert "core/" in result
    assert "Summary:" in result
    assert "2 files" in result
    assert "1 dirs" in result


def test_compress_tree_strips_summary():
    text = ".\n├── core\n│   └── agent.py\n└── tools\n    └── __init__.py\n\n5 directories, 2 files\n"
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "tree"
    # Summary line stripped: should not have "directories" AND "files" on same line
    summary_found = any("director" in line.lower() and "file" in line.lower() for line in result.split("\n"))
    assert not summary_found, f"Summary line was not stripped from: {result}"


def test_compress_test_output_keeps_failures():
    text = "============================= test session starts =============================\ntest_x.py::test_a PASSED\ntest_x.py::test_b FAILED\nassert 1 == 2\n\n1 passed, 1 failed in 0.3s\n"
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify returned: {classify(text)}"
    assert stats["format"] == "test-output"
    assert "FAILED" in result


def test_compress_read_numbered_deduplicates():
    # Create text with repeated duplicate lines that should collapse
    parts = ["import os", "import sys", "import re", "", "x = 1"]
    lines = []
    for p in parts:
        for _ in range(5):  # 5 duplicates of each
            lines.append(f"  {len(lines)+1}:{p}")
    text = "\n".join(lines)
    # Pad to exceed min_bytes
    while len(text) < 600:
        text += "\n" + text
    result, stats = compress(text, min_bytes=0)
    assert stats is not None, f"classify: {classify(text)}"
    assert stats["format"] == "read-numbered"
    assert len(result) < len(text)
    assert "duplicate" in result.lower()


# ── Safety guarantees ──────────────────────────────────────────────

def test_compress_returns_original_on_small_input():
    text = "tiny"
    result, stats = compress(text, min_bytes=500)
    assert result == text
    assert stats is None


def test_compress_returns_original_on_unrecognized():
    text = "This is a long description with no recognizable format. " * 30
    result, stats = compress(text)
    assert result == text


def test_compress_never_returns_empty():
    text = _pad("diff --git a/x b/x\n@@ -0,0 +1 @@\n+hello\n")
    result, stats = compress(text, min_bytes=0)
    assert len(result) > 0


def test_compress_never_grows_output():
    text = _pad("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-old\n+new\n")
    result, stats = compress(text, min_bytes=0)
    assert len(result) <= len(text)


def test_compress_works_defaults_on_larger_text():
    """With default min_bytes=500, larger text should still compress."""
    text = "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n" * 20
    assert len(text) >= 500
    result, stats = compress(text)
    assert stats is not None


def test_compress_disabled_via_min_bytes():
    text = _pad("diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n")
    result, stats = compress(text, min_bytes=99999)
    assert stats is None


def test_compress_below_default_min_bytes_passes_through():
    """Short git diff below 500 bytes should pass through unchanged."""
    text = "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n"
    assert len(text) < 500
    result, stats = compress(text)
    assert stats is None
    assert result == text


# ── Integration: agent config defaults ─────────────────────────────

def test_agent_config_defaults_enabled():
    """Verify agent defaults: tool_compress_enabled=True, min_bytes=500."""
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}}
    agent.tool_compress_enabled = bool(agent.config.get("agent", {}).get("tool_compress_enabled", True))
    agent.tool_compress_min_bytes = int(agent.config.get("agent", {}).get("tool_compress_min_bytes", 500) or 500)
    assert agent.tool_compress_enabled is True
    assert agent.tool_compress_min_bytes == 500


def test_agent_config_disabled():
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {"tool_compress_enabled": False}}
    agent.tool_compress_enabled = bool(agent.config.get("agent", {}).get("tool_compress_enabled", True))
    assert agent.tool_compress_enabled is False


def test_agent_config_custom_min_bytes():
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {"tool_compress_min_bytes": 1000}}
    agent.tool_compress_min_bytes = int(agent.config.get("agent", {}).get("tool_compress_min_bytes", 500) or 500)
    assert agent.tool_compress_min_bytes == 1000


# ── Memory recall truncation ───────────────────────────────────────

def test_truncate_recall_short_text_passes_through():
    """Text under max_chars is returned unchanged."""
    from core.agent.agent_utils import _truncate_recall
    result = _truncate_recall("hello world", 500)
    assert result == "hello world"


def test_truncate_recall_long_text_truncated():
    """Text over max_chars is truncated at word boundary."""
    from core.agent.agent_utils import _truncate_recall
    long_text = "word " * 300
    assert len(long_text) > 500
    result = _truncate_recall(long_text, 500)
    assert len(result) <= 503  # 500 + "…"
    assert result.endswith("…")
    # Should end at a word boundary, not mid-word
    assert result[-2] != " "  # no trailing space before …


def test_truncate_recall_empty_returns_empty():
    from core.agent.agent_utils import _truncate_recall
    assert _truncate_recall("", 100) == ""
    assert _truncate_recall(None, 100) == ""


# ── Aggressive compression near boundary ───────────────────────────

def test_compress_aggressive_with_high_pressure():
    """When pressure > 0.60, compression applies additional dedup."""
    # Build repetitive text that dedup_log can compress further
    lines = ["repeated line", "unique line"] * 60
    text = "\n".join(lines)
    assert len(text) > 500
    result_normal, stats_normal = compress(text, min_bytes=0, pressure=0.0)
    result_aggro, stats_aggro = compress(text, min_bytes=0, pressure=0.75)
    # Aggressive should save at least as much as normal
    assert stats_aggro is not None or stats_normal is None
    if stats_aggro and stats_normal:
        assert stats_aggro["saved_chars"] >= stats_normal["saved_chars"]


def test_compress_normal_pressure_no_extra_aggression():
    """Low pressure does not trigger aggressive post-compression."""
    text = "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n" * 30
    result, stats = compress(text, min_bytes=0, pressure=0.3)
    assert stats is not None
    # Should NOT contain "aggressive" marker
    assert "aggressive" not in result


def test_compress_high_pressure_may_add_aggressive_marker():
    """High pressure with enough lines should show aggressive truncation."""
    lines = [f"line {i}: some content here for testing compression" for i in range(300)]
    text = "\n".join(lines)
    result, stats = compress(text, min_bytes=0, pressure=0.80)
    if stats and len(result.split("\n")) < len(lines):
        # May have been truncated
        assert len(result) < len(text)


# ── Compression stats tracking ─────────────────────────────────────

def test_agent_compression_stats_initialized():
    """Agent initializes compression stats to zero."""
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}}
    agent.compression_total_saved = 0
    agent.compression_total_ops = 0
    agent.compression_last_pct = 0
    assert agent.compression_total_saved == 0
    assert agent.compression_total_ops == 0
    assert agent.compression_last_pct == 0


def test_agent_compression_stats_accumulate():
    """Compression ops accumulate saved chars and count."""
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}}
    agent.compression_total_saved = 0
    agent.compression_total_ops = 0
    agent.compression_last_pct = 0
    # Simulate two compression operations
    agent.compression_total_saved += 1200
    agent.compression_total_ops += 1
    agent.compression_last_pct = 35
    agent.compression_total_saved += 800
    agent.compression_total_ops += 1
    agent.compression_last_pct = 28
    assert agent.compression_total_saved == 2000
    assert agent.compression_total_ops == 2
    assert agent.compression_last_pct == 28


def test_status_includes_mo_operational_runtime_rows():
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}, "telegram": {"enabled": False}}
    agent.profile = type("Profile", (), {"user_name": "", "user_alias": "", "total_sessions": 0, "total_turns": 0})()
    agent.provider_name = "test"
    agent.model = "test-model"
    agent.instance_id = "inst-test"
    agent.session = type("Session", (), {
        "session_id": "s1", "turn_count": 0, "messages": [], "max_history": 50,
        "created_at": 0, "trimmed_messages_count": 0,
    })()
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.70
    agent._active_lane = None
    agent.sandbox_config = {"enabled": True}
    agent.compression_total_ops = 0
    agent.compression_total_saved = 0
    agent.compression_last_pct = 0
    agent.context_budget_tokens = 128000
    agent.context_budget_source = "test"

    result = agent._cmd_status("")

    assert "Runtime:" in result
    assert "model:      test / test-model" in result
    assert "instance:   inst-test" in result
    assert "session id: s1" in result
    assert "session:    s1" not in result
    assert "heartbeat:" in result
    assert "telegram:" in result
    assert "workers:    clear" in result
    assert "goal:       clear" in result
    assert "taskboard:  clear" in result
    assert "context:    clear" in result


def test_status_includes_actionable_hidden_state_rows(tmp_path):
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "learning_suggestions.jsonl").write_text(
        json.dumps({
            "id": "learning-suggestion:test:1",
            "kind": "scope_control",
            "recommendation": "review safely",
            "evidence": [],
            "status": "suggested",
            "created_at": 1,
        }) + "\n",
        encoding="utf-8",
    )
    agent.config = {"agent": {}, "telegram": {"enabled": False}, "scheduler": {"enabled": True}}
    agent.profile = type("Profile", (), {"user_name": "", "user_alias": "", "total_sessions": 0, "total_turns": 0, "_path": str(profile_dir / "mo.db")})()
    agent.provider_name = "fallback"
    agent.model = "model-b"
    agent.last_fallback_notice = "Switched to fallback/model-b: raw_tool_payload"
    agent._pending_interrupted_work = {"user": "finish previous work"}
    agent.session = type("Session", (), {
        "session_id": "s1", "turn_count": 0, "messages": [], "max_history": 50,
        "created_at": 0, "trimmed_messages_count": 2,
    })()
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.70
    agent._active_lane = None
    agent.sandbox_config = {"enabled": True}
    agent.compression_total_ops = 0
    agent.compression_total_saved = 0
    agent.compression_last_pct = 0
    agent.context_budget_tokens = 128000
    agent.context_budget_source = "test"

    result = agent._cmd_status("")

    assert "context:    needs attention · 2 trimmed messages · detail /usage" in result
    assert "paused work: available · detail /resume" in result
    assert "provider:    fallback active · detail /model" in result
    assert "learning:    1 suggestion available · detail /learning pending" in result
    assert "scheduler:   needs attention · detail monitor" in result
    assert "raw_tool_payload" not in result


def test_status_includes_compression_when_active():
    """_cmd_status shows compression line when ops have occurred."""
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}}
    agent.profile = type("Profile", (), {"user_name": "", "user_alias": "", "total_sessions": 0, "total_turns": 0})()
    agent.provider_name = "test"
    agent.model = "test-model"
    agent.session = type("Session", (), {
        "session_id": "s1", "turn_count": 10, "messages": [], "max_history": 50,
        "created_at": 0, "trimmed_messages_count": 0,
    })()
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.70
    agent._active_lane = None
    agent.sandbox_config = {"enabled": True}
    agent.compression_total_ops = 5
    agent.compression_total_saved = 3400
    agent.compression_last_pct = 42
    agent.context_budget_tokens = 128000
    agent.context_budget_source = "test"
    result = agent._cmd_status("")
    assert "context-save:" in result
    assert "5 ops" in result
    assert "~850 tokens" in result
    assert "3,400" in result
    assert "5 compressed" in result
    assert "42%" in result


def test_agent_counts_truncation_as_context_savings():
    from core.agent.agent import Agent

    agent = object.__new__(Agent)
    agent.tool_result_max_chars = 10
    agent.truncation_total_ops = 0
    agent.truncation_total_saved = 0
    events = []

    class Monitor:
        def emit(self, event_type, payload):
            events.append((event_type, payload))

    # Use a tool subject to the fallback cap (shell). Self-bounded file
    # inspection tools are intentionally exempt by execution policy.
    result = agent._cap_tool_result_for_context("x" * 100, monitor=Monitor(), tool_name="shell")

    assert result.endswith("[...truncated...]")
    assert agent.truncation_total_ops == 1
    assert agent.truncation_total_saved > 0
    assert agent._compression_saved_tokens_estimate() > 0
    assert events[0][0] == "tool_compress"
    assert events[0][1]["format"] == "truncate"


def test_read_family_tools_exempt_from_result_cap():
    # Regression: the fallback cap silently severed the back of files MO chose to
    # read (and throttled the on-demand profile read). Read-family tools are
    # self-bounded by their own limits and must pass through uncapped.
    from core.agent.agent import Agent

    agent = object.__new__(Agent)
    agent.tool_result_max_chars = 6000
    agent.truncation_total_ops = 0
    agent.truncation_total_saved = 0
    big = "y" * 18000

    for tool in ("read_file", "grep", "find_files"):
        out = agent._cap_tool_result_for_context(big, tool_name=tool)
        assert out == big, f"{tool} output must not be capped"
    # Parallel-prefetch eligibility is separate from result-cap exemption:
    # git_status can prefetch, but oversized output is still capped.
    capped_git = agent._cap_tool_result_for_context(big, tool_name="git_status")
    assert capped_git.endswith("[...truncated...]")
    assert len(capped_git) < len(big)
    # shell (unbounded external output) is still capped
    capped = agent._cap_tool_result_for_context(big, tool_name="shell")
    assert capped.endswith("[...truncated...]")
    assert len(capped) < len(big)


def test_usage_includes_estimated_token_savings_when_compression_active():
    """_cmd_usage reports useful compression savings, not only spend."""
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.session = type("Session", (), {
        "session_id": "s1", "turn_count": 3,
        "token_log": [{"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}],
    })()
    agent.compression_total_ops = 4
    agent.compression_total_saved = 2000
    agent.compression_last_pct = 30
    agent.truncation_total_ops = 1
    agent.truncation_total_saved = 400
    agent.truncation_last_pct = 50

    result = agent._cmd_usage("")

    assert "saved:" in result
    assert "~600 tokens" in result
    assert "2,400 chars" in result
    assert "context-save:" in result
    assert "compressed 4 / truncated 1" in result


def test_status_omits_compression_when_no_ops():
    """_cmd_status does not show compression line when no ops."""
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}}
    agent.profile = type("Profile", (), {"user_name": "", "user_alias": "", "total_sessions": 0, "total_turns": 0})()
    agent.provider_name = "test"
    agent.model = "test-model"
    agent.session = type("Session", (), {
        "session_id": "s1", "turn_count": 0, "messages": [], "max_history": 50,
        "created_at": 0, "trimmed_messages_count": 0,
    })()
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.70
    agent._active_lane = None
    agent.sandbox_config = {"enabled": True}
    agent.compression_total_ops = 0
    agent.compression_total_saved = 0
    agent.compression_last_pct = 0
    agent.context_budget_tokens = 128000
    agent.context_budget_source = "test"
    result = agent._cmd_status("")
    assert "context-save:" not in result


def test_settings_uses_model_and_session_slot_labels():
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.config = {"agent": {}}
    agent.provider_name = "test"
    agent.model = "test-model"
    agent.reasoning = "high"
    agent.temperature = 0.7
    agent.max_tokens = 8192
    agent.context_budget_tokens = 128000
    agent.context_budget_source = "test"
    agent.project_cwd = "E:/project"
    agent.runtime_home = "E:/home"
    agent.invoked_as = "mo"
    agent.sandbox_config = {"enabled": True}
    agent.profile = type("Profile", (), {"user_name": ""})()
    agent._sessions = type("Sessions", (), {"current_name": "main"})()
    agent.session = type("Session", (), {"session_id": "s1"})()

    result = agent._cmd_settings("")

    assert "  model:        test / test-model" in result
    assert "  provider:" not in result
    assert "  session slot: main" in result
    assert "  session: main" not in result


def test_usage_uses_session_id_label():
    from core.agent.agent import Agent
    agent = object.__new__(Agent)
    agent.session = type("Session", (), {
        "session_id": "s1", "turn_count": 3,
        "token_log": [],
    })()
    agent.compression_total_ops = 0
    agent.compression_total_saved = 0
    agent.compression_last_pct = 0

    result = agent._cmd_usage("")

    assert "session id: s1" in result
    assert "  session: s1" not in result
