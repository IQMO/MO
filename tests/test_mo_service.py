import threading
from types import SimpleNamespace

import mo_service
from core import runtime_lock, service


class DummyMonitor:
    def __init__(self):
        self.events = []

    def emit(self, kind, payload):
        self.events.append((kind, payload))


class DummyGateway:
    def __init__(self, agent):
        self.agent = agent
        self.monitor = DummyMonitor()


class Stoppable:
    def __init__(self):
        self.stopped = False
        self._thread = SimpleNamespace(is_alive=lambda: True)
        self._poll_thread = SimpleNamespace(is_alive=lambda: True)

    def stop(self):
        self.stopped = True


def test_headless_service_starts_surfaces_and_stops_without_tui(monkeypatch):
    agent = SimpleNamespace(config={})
    telegram = Stoppable()
    heartbeat = Stoppable()
    calls = []

    monkeypatch.setattr(service, "create_agent", lambda config_path: calls.append(("agent", config_path)) or agent)
    monkeypatch.setattr(service, "Gateway", DummyGateway)
    monkeypatch.setattr(service, "start_telegram_gateway_if_enabled", lambda a, g: calls.append(("telegram", a is agent, isinstance(g, DummyGateway))) or telegram)
    monkeypatch.setattr(service, "start_heartbeat_service_if_enabled", lambda a, g, surface: calls.append(("heartbeat", surface)) or heartbeat)

    stop = threading.Event()
    stop.set()
    result = service.run_service(config_path="config.test.yaml", surface="telegram_service", stop_event=stop, install_signals=False)

    assert result == 0
    assert ("agent", "config.test.yaml") in calls
    assert ("telegram", True, True) in calls
    assert ("heartbeat", "telegram_service") in calls
    assert telegram.stopped is True
    assert heartbeat.stopped is True


def test_mo_service_entrypoint_uses_runtime_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mo_service,
        "acquire_runtime_lock",
        lambda **kwargs: calls.append(("lock", kwargs)) or object(),
    )
    monkeypatch.setattr(mo_service, "service_main", lambda: calls.append(("service",)) or 0)

    assert mo_service.main() == 0
    assert calls == [("lock", {"lock_name": "mo-service.lock", "label": "MO Agent service"}), ("service",)]


def test_runtime_lock_blocks_legacy_live_lock(tmp_path, monkeypatch):
    legacy = tmp_path / "old-runtime.lock"
    legacy.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(runtime_lock.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(runtime_lock, "_pid_alive", lambda pid: pid == 12345)

    acquired = runtime_lock.acquire_runtime_lock(
        label="MO Agent test", legacy_lock_names=("old-runtime.lock",)
    )

    assert acquired is None
