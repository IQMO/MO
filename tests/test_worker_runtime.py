import time
from types import SimpleNamespace


from core.session.session import Session
from core.worker_runtime import BackgroundWorkerRuntime, build_background_worker_prompt, format_worker_completion_notice, summarize_worker_result
from core.workers import WorkerRegistry


class FakeAgent:
    def __init__(self):
        self.system_message = "You are MO."
        self.workers = WorkerRegistry()
        self.session = Session(self.system_message)
        self.gateway = SimpleNamespace(monitor=None)
        self.seen_sessions = []
        self.prompts = []

    def isolated_session(self, session):
        agent = self

        class _Ctx:
            def __enter__(self):
                agent.seen_sessions.append(session)
                return session

            def __exit__(self, _exc_type, _exc, _tb):
                return False

        return _Ctx()

    def run_turn(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return "Result: done\nEvidence: tested\nFiles changed: none\nBlocked/Next: none"


def test_format_worker_completion_notice_is_compact_and_native_friendly():
    record = SimpleNamespace(kind="prt", state="completed", result_summary="PRT finished: 4.5/5.0 · 2 unresolved", note="", objective="Reviewing HEAD")

    notice = format_worker_completion_notice(record)

    assert notice == "PRT completed: PRT finished: 4.5/5.0 · 2 unresolved · detail /status"


def test_background_worker_runtime_emits_native_notice_when_installed():
    agent = FakeAgent()
    notices = []
    agent._native_async_notice = notices.append
    runtime = BackgroundWorkerRuntime(agent, max_workers=2)

    record = runtime.start("scan docs", source="ghost")
    for _ in range(100):
        if notices:
            break
        time.sleep(0.01)

    assert agent.workers.get(record.id).state == "completed"
    assert notices == ["Worker completed: done · detail /status"]


def test_background_worker_runtime_runs_isolated_and_completes():
    agent = FakeAgent()
    runtime = BackgroundWorkerRuntime(agent, max_workers=2)
    finished = []

    record = runtime.start("scan docs", source="ghost", on_finish=lambda rec, result: finished.append((rec, result)))
    for _ in range(100):
        if finished:
            break
        time.sleep(0.01)

    assert agent.prompts == [build_background_worker_prompt("scan docs")]
    assert "Background Worker Protocol" in agent.seen_sessions[0].system_message
    stored = agent.workers.get(record.id)
    assert stored.state == "completed"
    assert stored.result_summary == "done"
    assert stored.evidence == ["tested"]
    assert stored.finished_at > 0
    assert finished and finished[0][0].id == record.id


def test_summarize_worker_result_extracts_compact_card():
    summary, evidence = summarize_worker_result("Result: reviewed files\nEvidence: read core/a.py, ran tests\nFiles changed: none")

    assert summary == "reviewed files"
    assert evidence == ["read core/a.py", "ran tests"]


def test_background_review_worker_prompt_forces_read_only_lane():
    prompt = build_background_worker_prompt("review `core/agent.py`")

    assert "Review only" in prompt
    assert "no edits" in prompt
    assert "Complexity:" in prompt


def test_background_non_review_worker_prompt_does_not_force_read_only_lane():
    prompt = build_background_worker_prompt("fix checkout bug")

    assert "Review only" not in prompt
    assert "Complexity: simple" in prompt


def test_background_review_with_change_intent_keeps_edit_capability():
    # Regression: "audit X and harden it" / "investigate the crash and patch it" were
    # classified deep_review and told "no edits", stripping the change the operator
    # explicitly asked for. A review objective that also carries change intent must
    # keep edit capability (the sandbox still gates actual writes).
    for objective in ("audit auth and harden it", "investigate the crash and patch it",
                      "review the parser and refactor it"):
        assert "Review only" not in build_background_worker_prompt(objective), objective
    # A PURE review still forbids edits.
    assert "Review only" in build_background_worker_prompt("audit the repo")


def test_background_worker_runtime_marks_provider_error_blocked():
    agent = FakeAgent()
    agent.run_turn = lambda prompt, **kwargs: "MO provider error: unavailable"
    runtime = BackgroundWorkerRuntime(agent, max_workers=1)

    record = runtime.start("scan docs", source="ghost")
    for _ in range(100):
        if agent.workers.get(record.id).state == "blocked":
            break
        time.sleep(0.01)

    assert agent.workers.get(record.id).state == "blocked"


def test_background_worker_runtime_marks_real_blocked_next_as_blocked():
    agent = FakeAgent()
    agent.run_turn = lambda prompt, **kwargs: (
        "Result: partial\n"
        "Evidence: read core/a.py\n"
        "Files changed: none\n"
        "Blocked/Next: needs operator approval before editing secrets"
    )
    runtime = BackgroundWorkerRuntime(agent, max_workers=1)

    record = runtime.start("scan docs", source="ghost")
    for _ in range(100):
        if agent.workers.get(record.id).state == "blocked":
            break
        time.sleep(0.01)

    stored = agent.workers.get(record.id)
    assert stored.state == "blocked"
    assert "blocked/next" in " ".join(stored.evidence).lower()


def test_background_worker_limit_blocks_new_worker_when_full():
    agent = FakeAgent()
    runtime = BackgroundWorkerRuntime(agent, max_workers=1)
    agent.workers.create(kind="worker", source="ghost", route="background", objective="already running", state="running")

    record = runtime.start("second worker", source="ghost")

    assert record.state == "blocked"
    assert "limit" in record.note


def test_background_worker_blocks_path_conflict_with_active_worker():
    agent = FakeAgent()
    runtime = BackgroundWorkerRuntime(agent, max_workers=2)
    agent.workers.create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="running")

    record = runtime.start("review `core/agent.py`", source="ghost")

    assert record.state == "blocked"
    assert "workspace conflict" in record.note


def test_background_worker_runtime_counts_prt_as_capacity():
    agent = FakeAgent()
    runtime = BackgroundWorkerRuntime(agent, max_workers=1)
    agent.workers.create(kind="prt", source="user", route="background", objective="Reviewing HEAD", state="running")

    record = runtime.start("scan docs", source="ghost")

    assert record.state == "blocked"
    assert "limit" in record.note


def test_background_worker_runtime_uses_existing_prt_claimed_paths_for_conflict():
    agent = FakeAgent()
    runtime = BackgroundWorkerRuntime(agent, max_workers=3)
    agent.workers.create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="running")
    prt = agent.workers.create(
        kind="prt",
        source="user",
        route="background",
        objective="Reviewing HEAD",
        state="offered",
        worker_id="prt-1",
        claimed_paths=["core/agent.py"],
    )

    record = runtime.start("Reviewing HEAD", source="user", worker_id=prt.id)

    assert record.state == "blocked"
    assert "workspace conflict" in record.note
