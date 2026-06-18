"""Tests for the offline `/doctor` health check (core/doctor.py)."""
from __future__ import annotations

import json

from core.doctor import (
    FAIL,
    OK,
    WARN,
    build_doctor_report,
    render_doctor_json,
    render_doctor_report,
)


def _status(report, name):
    for c in report.checks:
        if c.name == name:
            return c.status
    raise AssertionError(f"check {name!r} missing")


def test_doctor_offline_basics(tmp_path):
    report = build_doctor_report(home=tmp_path, config_path=tmp_path / "config.yaml", config={})
    # python and core imports must be healthy in a working tree
    assert _status(report, "python") == OK
    assert _status(report, "core_imports") == OK
    # home exists (tmp), but no config / no providers -> warnings
    assert _status(report, "mo_home") == OK
    assert _status(report, "config") == WARN
    assert _status(report, "providers") == WARN


def test_doctor_provider_key_present_vs_missing(tmp_path, monkeypatch):
    cfg = {
        "providers": [
            {"name": "opencode", "type": "chat_completions",
             "base_url": "https://x/v1", "api_key_env": "DOCTOR_TEST_KEY"},
        ],
        "model": {"default": "deepseek-v4-pro"},
    }
    monkeypatch.delenv("DOCTOR_TEST_KEY", raising=False)
    missing = build_doctor_report(home=tmp_path, config=cfg)
    assert _status(missing, "providers") == FAIL  # configured but no key anywhere
    assert _status(missing, "default_model") == OK

    monkeypatch.setenv("DOCTOR_TEST_KEY", "x-not-printed")
    present = build_doctor_report(home=tmp_path, config=cfg)
    assert _status(present, "providers") == OK


def test_doctor_never_prints_key_values(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCTOR_TEST_KEY", "super-secret-value")
    cfg = {"providers": [{"name": "p", "api_key_env": "DOCTOR_TEST_KEY"}]}
    report = build_doctor_report(home=tmp_path, config=cfg)
    text = render_doctor_report(report)
    assert "super-secret-value" not in text
    assert "super-secret-value" not in render_doctor_json(report)


def test_doctor_missing_home_warns(tmp_path):
    report = build_doctor_report(home=tmp_path / "nope", config={})
    assert _status(report, "mo_home") == WARN


def test_doctor_json_is_valid_and_shaped(tmp_path):
    report = build_doctor_report(home=tmp_path, config={})
    data = json.loads(render_doctor_json(report))
    assert set(data) >= {"ok", "worst", "home", "config_path", "checks"}
    assert isinstance(data["checks"], list) and data["checks"]
    assert all({"name", "status", "detail"} <= set(c) for c in data["checks"])
    assert data["worst"] in {OK, WARN, FAIL}


def test_doctor_registered_as_command():
    from interface.command_registry import COMMANDS

    names = {spec.name for spec in COMMANDS}
    assert "/doctor" in names


def test_doctor_mcp_status(tmp_path):
    off = build_doctor_report(home=tmp_path, config={})
    assert _status(off, "mcp") == OK  # disabled by absence
    disabled = build_doctor_report(home=tmp_path, config={"mcp": {"enabled": False}})
    assert _status(disabled, "mcp") == OK
    configured = build_doctor_report(home=tmp_path, config={"mcp": {"enabled": True, "servers": [{"name": "x", "command": "y"}]}})
    assert _status(configured, "mcp") == OK
    empty = build_doctor_report(home=tmp_path, config={"mcp": {"enabled": True, "servers": []}})
    assert _status(empty, "mcp") == WARN  # enabled but nothing valid configured
