from core.secrets import resolve_secret, secret_status


def test_resolve_secret_from_explicit_file_without_printing_value(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_SECRET", raising=False)
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("MY_SECRET='value-123'\n", encoding="utf-8")

    assert resolve_secret("MY_SECRET", files=[secret_file]) == "value-123"
    status = secret_status("MY_SECRET", files=[secret_file])
    assert status.present is True
    assert status.source.replace("\\", "/").endswith("secrets.env")


def test_env_secret_wins_over_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("MY_SECRET=file-value\n", encoding="utf-8")
    monkeypatch.setenv("MY_SECRET", "env-value")

    assert resolve_secret("MY_SECRET", files=[secret_file]) == "env-value"
    assert secret_status("MY_SECRET", files=[secret_file]).source == "env"
