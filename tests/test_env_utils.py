from core.env_utils import int_env


def test_int_env_preserves_existing_fallback_semantics(monkeypatch):
    monkeypatch.delenv("MO_TEST_INT_ENV", raising=False)
    assert int_env("MO_TEST_INT_ENV", 7) == 7

    monkeypatch.setenv("MO_TEST_INT_ENV", "")
    assert int_env("MO_TEST_INT_ENV", 7) == 7

    monkeypatch.setenv("MO_TEST_INT_ENV", "not-an-int")
    assert int_env("MO_TEST_INT_ENV", 7) == 7

    monkeypatch.setenv("MO_TEST_INT_ENV", "0")
    assert int_env("MO_TEST_INT_ENV", 7) == 0

    monkeypatch.setenv("MO_TEST_INT_ENV", "-3")
    assert int_env("MO_TEST_INT_ENV", 7) == -3
