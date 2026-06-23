import json

import core.instance as instance


def test_instance_id_uses_env_and_session_slot(monkeypatch):
    monkeypatch.setattr(instance, "_INSTANCE_ID", None)
    monkeypatch.setenv(instance.ENV_MO_INSTANCE_ID, "abc_123")

    assert instance.get_instance_id() == "abc_123"
    assert instance.instance_session_slot() == "main-abc_123"


def test_recent_instance_snapshots_reads_heartbeat_and_legacy_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "_pid_alive", lambda pid: pid == 222)
    monkeypatch.setattr(instance.tempfile, "gettempdir", lambda: str(tmp_path))
    home = tmp_path / "home"
    hb = home / "memory" / "heartbeat" / "heartbeats.jsonl"
    hb.parent.mkdir(parents=True)
    hb.write_text(
        json.dumps({
            "pid": 222,
            "created_at": 1000.0,
            "surface": "terminal",
            "instance_id": "live222",
            "slot": "main-live222",
            "session_id": "mo-live",
        }) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "mo-agent.lock").write_text("333", encoding="utf-8")
    monkeypatch.setattr(instance.time, "time", lambda: 1060.0)

    found = instance.recent_instance_snapshots({"runtime": {"home": str(home)}}, current_pid=111)

    assert [item["pid"] for item in found] == [222, 333]
    assert found[0]["pid_alive"] is True
    assert found[1]["surface"] == "legacy-lock"
    assert found[1]["pid_alive"] is False


def test_render_existing_instances_notice_names_isolated_start(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "_pid_alive", lambda _pid: True)
    home = tmp_path / "home"
    hb = home / "memory" / "heartbeat" / "heartbeats.jsonl"
    hb.parent.mkdir(parents=True)
    hb.write_text(
        json.dumps({
            "pid": 222,
            "created_at": 1000.0,
            "surface": "terminal",
            "instance_id": "other",
            "slot": "main-other",
            "session_id": "mo-other",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(instance.time, "time", lambda: 1005.0)

    notice = instance.render_existing_instances_notice({"runtime": {"home": str(home)}}, current_pid=111)

    assert "starts as an isolated instance" in notice
    assert "pid 222" in notice
    assert "instance other" in notice
