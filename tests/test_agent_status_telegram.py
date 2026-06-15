from types import SimpleNamespace

from core.agent.agent_status import AgentStatusCommands


def test_status_telegram_summary_import_resolves_not_swallowed():
    """Regression: _status_telegram_summary used `from .telegram.gateway` (single
    dot), which resolves to a nonexistent core.agent.telegram package; the bare
    except then permanently degraded the /status telegram line to the generic
    'needs attention' fallback. With the correct `..telegram.gateway` import, a
    configured gateway's real status must be reported."""
    fake_gateway = SimpleNamespace(status=lambda: {
        "enabled": True,
        "running": True,
        "token_present": True,
        "token_env": "MO_TELEGRAM_TOKEN",
        "pending_jobs": 0,
        "unfinished_jobs": 0,
        "active_chats": [],
    })
    fake_self = SimpleNamespace(telegram_gateway=fake_gateway, _telegram_gateway=None)

    summary = AgentStatusCommands._status_telegram_summary(fake_self)

    assert summary.startswith("running"), summary
    assert summary != "needs attention · detail /telegram status"
