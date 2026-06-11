from core.worker_scheduler import decide_worker_route
from core.workers import WorkerRegistry


def test_scheduler_runs_background_when_available():
    registry = WorkerRegistry()

    decision = decide_worker_route(
        "review core/agent.py",
        requested_route="background",
        main_busy=False,
        registry=registry,
        background_active_count=0,
        background_limit=2,
    )

    assert decision.action == "run_worker"
    assert decision.claimed_paths == ["core/agent.py"]


def test_scheduler_blocks_background_conflict():
    registry = WorkerRegistry()
    active = registry.create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="running")

    decision = decide_worker_route(
        "review `core/agent.py`",
        requested_route="background",
        main_busy=False,
        registry=registry,
        background_active_count=1,
        background_limit=2,
    )

    assert decision.action == "blocked_conflict"
    assert decision.conflicts == [active]


def test_scheduler_blocks_background_capacity():
    decision = decide_worker_route(
        "scan docs",
        requested_route="background",
        main_busy=False,
        background_active_count=3,
        background_limit=3,
    )

    assert decision.action == "blocked_capacity"


def test_scheduler_keeps_risky_work_with_main_or_queue():
    assert decide_worker_route("commit changes", requested_route="background", main_busy=False, risky=True).action == "run_main"
    assert decide_worker_route("commit changes", requested_route="background", main_busy=True, risky=True).action == "queue_main"
