from contextlib import contextmanager
import json
from types import SimpleNamespace

from core.scheduler import SCHEDULED_TASK_CREATION_FOLLOWUP, SchedulerPaths, SchedulerService, start_scheduler_service_if_enabled
from core.session.sessions import SessionManager


class FakeAgent:
    def __init__(self, tmp_path):
        self.config = {}
        self.system_message = "You are MO."
        self._sessions = SessionManager(str(tmp_path / "sessions"))
        self.thread_session = None

    @contextmanager
    def isolated_session(self, session):
        previous = self.thread_session
        self.thread_session = session
        try:
            yield
        finally:
            self.thread_session = previous


class FakeGateway:
    def __init__(self):
        self.calls = []

    def run_turn(self, prompt, *, route_source="user", **_kwargs):
        self.calls.append((prompt, route_source))
        return f"done: {prompt}"


def _paths(tmp_path):
    return SchedulerPaths(
        jobs=tmp_path / "jobs.json",
        runs=tmp_path / "runs.jsonl",
        lock=tmp_path / "tick.lock",
    )


def test_scheduler_runs_due_turn_job_and_records_log(tmp_path):
    paths = _paths(tmp_path)
    paths.jobs.write_text(json.dumps({"jobs": [{
        "id": "check",
        "enabled": True,
        "kind": "turn",
        "prompt": "say ok",
        "session": "sched-check",
        "schedule": {"type": "interval", "interval_seconds": 60},
        "next_run_at": 1,
    }]}), encoding="utf-8")
    agent = FakeAgent(tmp_path)
    gateway = FakeGateway()
    scheduler = SchedulerService(agent=agent, gateway=gateway, paths=paths, enabled=True)

    runs = scheduler.tick(now=10)

    assert len(runs) == 1
    assert runs[0].status == "ok"
    assert gateway.calls == [("say ok", "scheduler")]
    log_lines = paths.runs.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    assert json.loads(log_lines[0])["job_id"] == "check"
    saved = agent._sessions.load("sched-check")
    assert saved is not None
    data = json.loads(paths.jobs.read_text(encoding="utf-8"))
    job = data["jobs"][0]
    assert job["run_count"] == 1
    assert job["last_status"] == "ok"
    assert job["next_run_at"] > 10
    assert "running_since" not in job


def test_scheduler_bootstraps_interval_without_immediate_run(tmp_path):
    paths = _paths(tmp_path)
    paths.jobs.write_text(json.dumps({"jobs": [{
        "id": "later",
        "enabled": True,
        "kind": "turn",
        "prompt": "not yet",
        "schedule": {"type": "interval", "interval_seconds": 60},
    }]}), encoding="utf-8")
    agent = FakeAgent(tmp_path)
    gateway = FakeGateway()
    scheduler = SchedulerService(agent=agent, gateway=gateway, paths=paths, enabled=True)

    runs = scheduler.tick(now=100)

    assert runs == []
    assert gateway.calls == []
    job = json.loads(paths.jobs.read_text(encoding="utf-8"))["jobs"][0]
    assert job["next_run_at"] == 160


def test_scheduler_service_disabled_by_default(tmp_path):
    agent = SimpleNamespace(config={})
    assert start_scheduler_service_if_enabled(agent, gateway=SimpleNamespace()) is None


def test_scheduler_sends_review_prompt_after_configured_run(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.jobs.write_text(json.dumps({"jobs": [{
        "id": "review-me",
        "enabled": True,
        "kind": "turn",
        "prompt": "say ok",
        "session": "sched-review",
        "schedule": {"type": "once"},
        "next_run_at": 1,
        "deliver": {"telegram_chat_id": "123"},
        "review": {"ask_later": True, "after_runs": 1, "repeat_after_seconds": 3600},
    }]}), encoding="utf-8")
    sent = []
    monkeypatch.setattr("core.scheduler._send_telegram_message", lambda _agent, chat_id, text: sent.append((chat_id, text)) or True)
    agent = FakeAgent(tmp_path)
    agent.telegram_gateway = SimpleNamespace(enabled=True, token_env="TELEGRAM_BOT_TOKEN", secret_files=())
    gateway = FakeGateway()
    scheduler = SchedulerService(agent=agent, gateway=gateway, paths=paths, enabled=True)

    runs = scheduler.tick(now=10)

    assert runs[0].status == "ok"
    assert any("because you might reconsider" in text and "keep it, change it, or remove it" in text for _chat, text in sent)
    job = json.loads(paths.jobs.read_text(encoding="utf-8"))["jobs"][0]
    assert job["review"]["last_asked_at"] == runs[0].finished_at
    assert job["review"]["next_ask_at"] > runs[0].finished_at


def test_scheduler_startup_check_clears_stale_claim_and_review_due(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.jobs.write_text(json.dumps({"jobs": [{
        "id": "stale-review",
        "enabled": True,
        "kind": "turn",
        "prompt": "later",
        "schedule": {"type": "interval", "interval_seconds": 3600},
        "next_run_at": 999999,
        "running_since": 1,
        "run_count": 2,
        "deliver": {"telegram_chat_id": "123"},
        "review": {"ask_later": True, "after_runs": 1},
    }]}), encoding="utf-8")
    sent = []
    monkeypatch.setattr("core.scheduler._send_telegram_message", lambda _agent, chat_id, text: sent.append((chat_id, text)) or True)
    agent = FakeAgent(tmp_path)
    agent.telegram_gateway = SimpleNamespace(enabled=True, token_env="TELEGRAM_BOT_TOKEN", secret_files=())
    scheduler = SchedulerService(agent=agent, gateway=FakeGateway(), paths=paths, enabled=True)

    summary = scheduler.startup_check(now=2000)

    assert summary["stale_claims_cleared"] == 1
    assert summary["review_due"] == 1
    assert any("because you might reconsider" in text and "keep it, change it, or remove it" in text for _chat, text in sent)
    job = json.loads(paths.jobs.read_text(encoding="utf-8"))["jobs"][0]
    assert "running_since" not in job


def test_scheduled_task_creation_followup_uses_task_language():
    assert "scheduled task" in SCHEDULED_TASK_CREATION_FOLLOWUP
    assert "job" not in SCHEDULED_TASK_CREATION_FOLLOWUP.lower()
    assert SCHEDULED_TASK_CREATION_FOLLOWUP == "Do you want me to remind you about this scheduled task later?"


def test_scheduler_service_starts_from_config(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    agent = SimpleNamespace(config={"scheduler": {
        "enabled": True,
        "tick_seconds": 999,
        "jobs_path": str(paths.jobs),
        "runs_path": str(paths.runs),
        "lock_path": str(paths.lock),
    }})
    monkeypatch.setattr(SchedulerService, "start", lambda self: True)

    service = start_scheduler_service_if_enabled(agent, gateway=SimpleNamespace())

    assert service is not None
    assert service.paths.jobs == paths.jobs
    assert service.tick_seconds == 999
    assert getattr(agent, "scheduler_service") is service
