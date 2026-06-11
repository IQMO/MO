from core.workers import WorkerRegistry, extract_worker_paths, paths_conflict


def test_worker_registry_lifecycle_and_summary():
    registry = WorkerRegistry()

    record = registry.create(kind="goal", source="ghost", route="background", objective="scan docs", state="offered")
    registry.update(record.id, "accepted", "worker accepted")
    registry.update(record.id, "running", "goal plan accepted")

    assert registry.get(record.id).state == "running"
    assert registry.active()[0].id == record.id
    assert "goal/background: running" in registry.summary()

    registry.update(record.id, "completed", "done", result_summary="finished safely", evidence=["read docs"])

    stored = registry.get(record.id)
    assert registry.active() == []
    assert stored.result_summary == "finished safely"
    assert stored.evidence == ["read docs"]
    assert stored.finished_at > 0
    assert "completed" in registry.summary()
    assert "finished safely" in registry.summary()


def test_worker_registry_extracts_paths_and_detects_conflicts():
    registry = WorkerRegistry()
    active = registry.create(kind="worker", source="ghost", route="background", objective="edit `core/agent.py` safely", state="running")

    assert active.claimed_paths == ["core/agent.py"]
    assert set(extract_worker_paths("review README.md and core/workers.py")) == {"README.md", "core/workers.py"}
    assert paths_conflict("core", "core/agent.py") is True
    assert paths_conflict("core/agent.py", "core/workers.py") is False
    assert registry.conflicts(["core/agent.py"])[0].id == active.id
