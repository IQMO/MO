from core.auto_reply import maybe_auto_reply


def test_auto_reply_handles_how_are_you_as_status_chat():
    reply = maybe_auto_reply("how are you ?", surface="telegram")

    assert reply is not None
    assert reply.reason == "greeting"
    assert "MO is online" in reply.text


def test_auto_reply_does_not_intercept_task_shaped_request():
    assert maybe_auto_reply("how are you and fix the tests", surface="telegram") is None


def test_auto_reply_does_not_intercept_task_shaped_identity_question():
    agent = type("Agent", (), {"provider_name": "mock", "model": "model"})()

    assert maybe_auto_reply("if i say deploy what are you going to do ?", agent=agent, surface="telegram") is None
    assert maybe_auto_reply("who are you deploying for?", agent=agent, surface="telegram") is None


def test_auto_reply_identity_is_deterministic():
    agent = type("Agent", (), {"provider_name": "mock", "model": "model"})()
    reply = maybe_auto_reply("who are you and what model are you using?", agent=agent, surface="telegram")

    assert reply is not None
    assert reply.reason == "identity"
    assert "I'm MO" in reply.text
    assert "mock/model" in reply.text
    assert "runtime engine, not my identity" in reply.text
