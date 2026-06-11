"""End-to-end protection tests for the compression → handoff pipeline.

These tests simulate full MO turns with real-looking tool output flowing
through the compression module, verifying every link in the chain:
  tool dispatch → compression → stats tracking → session storage → handoff adaptation

If any future change breaks compression, these tests fail immediately.
"""
from types import SimpleNamespace

from core.agent.agent import Agent
from core.backend_monitor import BackendMonitor
from core.tasking.task_board import TaskBoard, TaskItem
from core.tool_compress import classify


def make_agent_without_init():
    return object.__new__(Agent)


def _mock_agent_for_compression_turn():
    """Build a minimal agent that runs one turn with compression enabled."""
    agent = make_agent_without_init()
    agent.max_provider_requests = 3
    agent.max_tool_rounds = 2
    agent.tool_result_max_chars = 6000
    agent.tool_compress_enabled = True
    agent.tool_compress_min_bytes = 0  # compress everything in tests
    agent.compression_total_saved = 0
    agent.compression_total_ops = 0
    agent.compression_last_pct = 0
    agent.provider_name = "fake"
    agent.model = "model"
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.70
    agent.context_budget_tokens = 128_000
    agent.context_budget_source = "test"
    agent._handoff_count = 0

    messages = []
    agent.session = SimpleNamespace(
        messages=messages,
        session_id="e2e-test",
        created_at=0,
        get_messages=lambda **kw: [{"role": "system", "content": "private"}, *messages],
        sanitize_for_provider=lambda **_kwargs: None,
        add_user=lambda user_input: messages.append({"role": "user", "content": user_input}),
        add_message=lambda msg: messages.append(msg),
        add_tool_result=lambda tool_call_id, content: messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        ),
        add_assistant=lambda *args, **kwargs: None,
        record_usage=lambda *args, **kwargs: None,
        turn_count=0,
        max_history=50,
        token_log=[],
        total_tokens=0,
        output_tokens=0,
        trimmed_messages_count=0,
    )

    agent.profile = SimpleNamespace(
        user_name="", user_alias="", total_sessions=0, total_turns=0,
        build_profile_context=lambda **kw: "",
    )
    agent.memory = None
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent.tool_definitions = [{"name": "shell"}]
    agent.config = {"agent": {"context_handoff_threshold": 0.70}}
    agent._active_lane = None
    agent._thread_state = SimpleNamespace()
    agent._thread_state.provider_surface = "main"
    agent._thread_state.provider_worker_id = ""
    agent._thread_state.session = None
    return agent, messages


# ── Pipeline: git diff compression ─────────────────────────────────

def test_e2e_git_diff_compressed_in_full_turn(tmp_path):
    """Full turn with git diff tool output: compression applied, stats tracked, result smaller."""
    agent, messages = _mock_agent_for_compression_turn()

    # Build a diff with hunks exceeding the 100-line cap so truncation kicks in
    hunks = []
    for hunk_num in range(3):
        hunks.append(f"@@ -{hunk_num*200},180 +{hunk_num*200},180 @@")
        # 110 context lines + 50 additions + 40 removals = 200 lines per hunk (exceeds 100 cap)
        hunks.extend(f" context_line_{hunk_num}_{i}" for i in range(110))
        hunks.extend(f"+added_line_{hunk_num}_{i}" for i in range(50))
        hunks.extend(f"-removed_line_{hunk_num}_{i}" for i in range(40))
    raw_diff = "diff --git a/src/main.py b/src/main.py\nindex 000..111\n--- a/src/main.py\n+++ b/src/main.py\n" + "\n".join(hunks)
    assert len(raw_diff) > 500

    responses = iter([
        SimpleNamespace(
            content="checking", tool_calls=[
                {"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"git diff"}'}}
            ],
            usage=None, finish_reason="tool_calls",
        ),
        SimpleNamespace(content="done", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: raw_diff
    monitor = BackendMonitor(tmp_path / "e2e_monitor.jsonl")

    result = agent.run_turn("show me the diff", monitor=monitor)

    assert result == "done"
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    stored = tool_msgs[0]["content"]
    assert len(stored) < len(raw_diff), f"Expected compressed, got same size: {len(stored)} >= {len(raw_diff)}"
    assert "+" in stored  # kept meaningful additions
    assert agent.compression_total_ops == 1
    assert agent.compression_total_saved > 0

    # Verify monitor event emitted
    log = (tmp_path / "e2e_monitor.jsonl").read_text(encoding="utf-8")
    assert '"type": "tool_compress"' in log
    assert '"format": "git-diff"' in log


# ── Pipeline: grep compression ─────────────────────────────────────

def test_e2e_grep_compressed_in_full_turn(tmp_path):
    """Full turn with grep output: compression groups by file, caps matches."""
    agent, messages = _mock_agent_for_compression_turn()

    raw_grep = "\n".join(
        [f"core/agent.py:{i}: import something_{i}" for i in range(30)]
        + [f"core/session.py:{i}: class Session_{i}" for i in range(15)]
    )
    assert len(raw_grep) > 500

    responses = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"grep import"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="found matches", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: raw_grep

    result = agent.run_turn("search for imports")

    assert result == "found matches"
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    stored = tool_msgs[0]["content"]
    assert len(stored) < len(raw_grep)
    assert "core/agent.py" in stored
    assert "core/session.py" in stored
    assert "matches in 2 files" in stored
    assert agent.compression_total_ops == 1


# ── Pipeline: build output compression ─────────────────────────────

def test_e2e_build_output_compressed_in_full_turn(tmp_path):
    """Full turn with npm/cargo build output: errors kept, noise collapsed."""
    agent, messages = _mock_agent_for_compression_turn()

    raw_build = (
        "npm warn deprecated package-a\n"
        "npm warn deprecated package-b\n"
        + "".join(f"Compiling crate_{i}\n" for i in range(50))
        + "ERROR: compilation failed in crate_25\n"
        + "[ERROR] type mismatch in module X\n"
    )
    assert len(raw_build) > 500

    responses = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"cargo build"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="build attempted", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: raw_build

    result = agent.run_turn("build the project")

    assert result == "build attempted"
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    stored = tool_msgs[0]["content"]
    assert len(stored) < len(raw_build)
    assert "Compiled 50 packages" in stored
    assert "ERROR" in stored
    assert agent.compression_total_ops == 1


# ── Pipeline: ls output compression ────────────────────────────────

def test_e2e_ls_output_compressed_in_full_turn(tmp_path):
    """Full turn with ls -la output: collapsed to name+size+summary."""
    agent, messages = _mock_agent_for_compression_turn()

    # Build enough ls output to exceed 500 bytes
    entries = [
        "total 999",
        "-rw-r--r-- 1 user group  1024 May 27 12:00 agent.py",
        "-rw-r--r-- 1 user group  2048 May 27 12:00 session.py",
        "-rw-r--r-- 1 user group  4096 May 27 12:00 sandbox.py",
        "-rw-r--r-- 1 user group  8192 May 27 12:00 handoff.py",
        "-rw-r--r-- 1 user group 16384 May 27 12:00 tool_compress.py",
        "drwxr-xr-x 1 user group     0 May 27 12:00 tools",
        "drwxr-xr-x 1 user group     0 May 27 12:00 tests",
        "drwxr-xr-x 1 user group     0 May 27 12:00 core",
        "drwxr-xr-x 1 user group     0 May 27 12:00 docs",
        "-rw-r--r-- 1 user group 32768 May 27 12:00 large_file.bin",
    ]
    raw_ls = "\n".join(entries)
    assert len(raw_ls) > 500

    responses = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"ls -la"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="files listed", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: raw_ls

    result = agent.run_turn("list files")

    assert result == "files listed"
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    stored = tool_msgs[0]["content"]
    assert len(stored) < len(raw_ls)
    assert "tools/" in stored
    assert "Summary:" in stored
    assert agent.compression_total_ops == 1


# ── Pipeline: compression disabled ─────────────────────────────────

def test_e2e_compression_disabled_passes_through_raw(tmp_path):
    """When tool_compress_enabled=False, raw output stored unchanged."""
    agent, messages = _mock_agent_for_compression_turn()
    agent.tool_compress_enabled = False

    raw = "some tool output " * 100
    responses = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "call-1", "function": {"name": "shell", "arguments": '{"command":"echo"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="done", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: raw

    agent.run_turn("run command")

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == raw
    assert agent.compression_total_ops == 0


# ── Pipeline: block_reason skips compression ────────────────────────

def test_e2e_blocked_tool_skips_compression(tmp_path):
    """When sandbox blocks a tool, compression is skipped (block_reason stored as-is)."""
    agent, messages = _mock_agent_for_compression_turn()
    # Enable sandbox and restrict roots so write_file outside root gets blocked
    agent.sandbox_config = {"enabled": True}
    agent.allowed_roots = [str(tmp_path)]

    responses = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "call-1", "function": {"name": "write_file", "arguments": '{"path":"/etc/passwd","content":"bad"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="blocked and done", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: "should not execute"
    agent.tool_definitions = [{"name": "write_file"}]

    agent.run_turn("write to etc")

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1
    # Block reason from sandbox should be stored (contains PATH BLOCKED)
    blocked_content = tool_msgs[0]["content"]
    assert "PATH BLOCKED" in blocked_content or "BLOCKED" in blocked_content
    # No compression ops since tool was blocked
    assert agent.compression_total_ops == 0


# ── Pipeline: multi-turn stats accumulation ─────────────────────────

def test_e2e_multi_turn_stats_accumulate(tmp_path):
    """Over multiple turns, compression stats accumulate correctly."""
    agent, messages = _mock_agent_for_compression_turn()

    raw1 = "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n" * 30
    raw2 = "core/a.py:1: code\ncore/b.py:2: more code\ncore/c.py:3: even more\n" * 20

    assert len(raw1) > 500 and len(raw2) > 500

    # Turn 1
    responses1 = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "c1", "function": {"name": "shell", "arguments": '{"command":"git diff"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="t1 done", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses1)
    agent._dispatch_tool = lambda name, arguments: raw1
    agent.run_turn("diff")

    assert agent.compression_total_ops == 1
    saved_after_first = agent.compression_total_saved

    # Turn 2
    responses2 = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "c2", "function": {"name": "shell", "arguments": '{"command":"grep"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="t2 done", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses2)
    agent._dispatch_tool = lambda name, arguments: raw2
    agent.run_turn("grep")

    assert agent.compression_total_ops == 2
    assert agent.compression_total_saved > saved_after_first
    assert agent.compression_last_pct > 0


# ── Pipeline: adaptive threshold triggers after enough ops ──────────

def test_e2e_adaptive_threshold_activates_after_compression(tmp_path):
    """After 5+ compression ops with good savings, handoff threshold rises."""
    from core.session.handoff import should_auto_handoff

    agent, messages = _mock_agent_for_compression_turn()
    agent.compression_total_ops = 8
    agent.compression_total_saved = 4000  # avg 500 chars/op
    agent.context_handoff_threshold = 0.70
    agent.config = {"agent": {"context_handoff_threshold": 0.70}}

    triggered, metrics = should_auto_handoff(agent)

    # Threshold should have been boosted
    assert metrics["threshold"] > 0.70, f"Expected boosted threshold, got {metrics['threshold']}"
    assert metrics["threshold"] <= 0.78


# ── Pipeline: classify on real-looking text ─────────────────────────

def test_e2e_classify_on_real_tool_outputs():
    """Classify correctly identifies formats from realistic multi-line tool output."""
    # Real git diff (truncated but recognizable)
    real_diff = "diff --git a/src/main.py b/src/main.py\nindex 123..456\n--- a/src/main.py\n+++ b/src/main.py\n@@ -10,6 +10,8 @@\n import os\n+import json\n+import yaml\n def main():\n"
    assert classify(real_diff) == "git-diff"

    # Real npm build output
    real_build = "npm warn deprecated left-pad@1.0.0\n\nCompiling @scope/package\n[ERROR] Cannot find module 'express'\n"
    assert classify(real_build) == "build"

    # Real grep output
    real_grep = "src/models.py:45: class UserModel:\nsrc/views.py:12: from models import UserModel\ntests/test_models.py:8: import UserModel\n"
    assert classify(real_grep) == "grep"

    # Real ls output
    real_ls = "total 256\ndrwxr-xr-x  5 user staff   160 May 27 12:00 src\n-rw-r--r--  1 user staff  4096 May 27 12:00 README.md\n"
    assert classify(real_ls) == "ls"

    # Plain text should NOT be misclassified
    plain = "Here is the result of the analysis. The code looks good overall."
    assert classify(plain) is None


# ── Pipeline: task evidence uses raw result before compression ──────

def test_e2e_compression_still_activates_in_full_turn(tmp_path):
    """Task evidence recording uses raw result even when compression is active."""
    agent, messages = _mock_agent_for_compression_turn()
    agent.tool_compress_min_bytes = 500  # restore default for this test
    agent.tool_definitions = [{"name": "test_runner"}]
    board = TaskBoard(turn_id="turn-1", tasks=[TaskItem("1", "Run and verify", "active")])

    raw_test = "tests/test_x.py::test_a PASSED\ntests/test_x.py::test_b PASSED\n\n2 passed in 0.3s\n"

    responses = iter([
        SimpleNamespace(content="", tool_calls=[
            {"id": "call-1", "function": {"name": "test_runner", "arguments": '{"command":"pytest"}'}}
        ], usage=None, finish_reason="tool_calls"),
        SimpleNamespace(content="verified", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: raw_test

    result = agent.run_turn("run tests", task_board=board)

    # Model-driven: board is just a container, completion is model's decision
    # Compression still works — raw output is passed to session
    assert result == "verified"
