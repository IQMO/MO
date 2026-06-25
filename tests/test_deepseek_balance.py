"""Footer balance for the official DeepSeek API — parsing, host-gating, throttle."""
import time

from core.provider import deepseek_balance as db


class _FakeClient:
    def __init__(self, key):
        self.api_key = key


class _FakeProvider:
    def __init__(self, base_url, key="sk-test"):
        self.base_url = base_url
        self.client = _FakeClient(key)


class _RecordThread:
    instances: list = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        _RecordThread.instances.append(self)

    def start(self):
        pass


def _reset_state(**over):
    with db._lock:
        db._state.update({"text": None, "fetched_at": 0.0, "fetching": False, **over})


def test_is_official_deepseek_only_for_official_host():
    assert db.is_official_deepseek(_FakeProvider("https://api.deepseek.com/v1"))
    assert db.is_official_deepseek(_FakeProvider("https://api.deepseek.com"))
    assert not db.is_official_deepseek(_FakeProvider("https://opencode.ai/zen/go/v1"))
    assert not db.is_official_deepseek(_FakeProvider(""))
    assert not db.is_official_deepseek(None)


def test_balance_endpoint_is_at_host_root_not_v1():
    assert db.balance_endpoint("https://api.deepseek.com/v1") == "https://api.deepseek.com/user/balance"
    assert db.balance_endpoint("https://api.deepseek.com") == "https://api.deepseek.com/user/balance"
    assert db.balance_endpoint("") == "https://api.deepseek.com/user/balance"


def test_format_balance_currency_and_low_flag():
    assert db.format_balance({"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "110.00"}]}) == "DeepSeek $110.00"
    assert db.format_balance({"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "110.00"}]}) == "DeepSeek ¥110.00"
    low = db.format_balance({"is_available": False, "balance_infos": [{"currency": "USD", "total_balance": "0.00"}]})
    assert low is not None and "⚠low" in low
    # unknown currency falls back to "<amount> <CUR>"
    assert db.format_balance({"balance_infos": [{"currency": "EUR", "total_balance": "5.00"}]}) == "DeepSeek 5.00 EUR"
    assert db.format_balance({"balance_infos": []}) is None
    assert db.format_balance({}) is None


def test_balance_text_none_for_non_deepseek_or_no_key():
    assert db.balance_text(_FakeProvider("https://opencode.ai/zen/go/v1")) is None
    assert db.balance_text(_FakeProvider("https://api.deepseek.com", key="")) is None
    assert db.balance_text(None) is None


def test_fresh_cache_returns_without_spawning(monkeypatch):
    _RecordThread.instances.clear()
    monkeypatch.setattr(db.threading, "Thread", _RecordThread)
    _reset_state(text="DeepSeek $50.00", fetched_at=time.time())  # fresh
    assert db.balance_text(_FakeProvider("https://api.deepseek.com")) == "DeepSeek $50.00"
    assert _RecordThread.instances == []  # fresh cache -> no fetch


def test_stale_cache_triggers_exactly_one_refresh(monkeypatch):
    _RecordThread.instances.clear()
    monkeypatch.setattr(db.threading, "Thread", _RecordThread)
    _reset_state()  # stale (fetched_at=0)
    p = _FakeProvider("https://api.deepseek.com")
    db.balance_text(p)
    db.balance_text(p)  # second call while a fetch is in flight
    assert len(_RecordThread.instances) == 1  # throttled — only one fetch
    _reset_state()


def test_refresh_writes_cache_and_clears_flag(monkeypatch):
    monkeypatch.setattr(db, "_fetch", lambda url, key: ("DeepSeek $99.00", 99.0))
    _reset_state(fetching=True)
    db._refresh("https://api.deepseek.com/user/balance", "sk-test")
    assert db._state["text"] == "DeepSeek $99.00"
    assert db._state["amount"] == 99.0
    assert db._state["fetching"] is False
    _reset_state()


def test_parse_amount_extracts_numeric():
    assert db.parse_amount({"balance_infos": [{"currency": "USD", "total_balance": "1.73"}]}) == 1.73
    assert db.parse_amount({"balance_infos": []}) is None
    assert db.parse_amount({"balance_infos": [{"total_balance": "x"}]}) is None


def test_balance_amount_host_gated_and_cached():
    # Non-official provider -> None regardless of cache.
    assert db.balance_amount(_FakeProvider("https://opencode.ai/zen/v1")) is None
    with db._lock:
        db._state["amount"] = 1.5
    try:
        assert db.balance_amount(_FakeProvider("https://api.deepseek.com/v1")) == 1.5
    finally:
        with db._lock:
            db._state["amount"] = None
