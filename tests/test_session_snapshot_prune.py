"""Pre-handoff snapshot retention: cap the historical snapshots so they don't
accumulate unbounded (drift hygiene — hundreds of files made 'last session' ambiguous)."""
import time

from core.session.sessions import SessionManager


def _make_snaps(d, base, n):
    for i in range(n):
        p = d / f"{base}-pre-handoff-2026010{i:02d}-000000.json"
        p.write_text("{}", encoding="utf-8")
        # distinct mtimes so ordering is deterministic
        import os
        os.utime(p, (time.time() - (n - i), time.time() - (n - i)))


def test_prune_caps_handoff_snapshots(tmp_path):
    mgr = SessionManager(sessions_dir=str(tmp_path))
    _make_snaps(tmp_path, "main", 50)
    removed = mgr.prune_handoff_snapshots("main", keep=30)
    remaining = list(tmp_path.glob("main-pre-handoff-*.json"))
    assert removed == 20
    assert len(remaining) == 30


def test_prune_keeps_most_recent(tmp_path):
    mgr = SessionManager(sessions_dir=str(tmp_path))
    _make_snaps(tmp_path, "main", 35)
    mgr.prune_handoff_snapshots("main", keep=30)
    names = sorted(p.name for p in tmp_path.glob("main-pre-handoff-*.json"))
    # the 5 oldest (lowest indices) should be gone
    assert "main-pre-handoff-20260100-000000.json" not in names
    assert len(names) == 30


def test_prune_under_cap_removes_nothing(tmp_path):
    mgr = SessionManager(sessions_dir=str(tmp_path))
    _make_snaps(tmp_path, "main", 10)
    assert mgr.prune_handoff_snapshots("main", keep=30) == 0


def test_prune_only_touches_matching_base(tmp_path):
    mgr = SessionManager(sessions_dir=str(tmp_path))
    _make_snaps(tmp_path, "main", 40)
    (tmp_path / "main.json").write_text("{}", encoding="utf-8")  # active session must survive
    mgr.prune_handoff_snapshots("main", keep=30)
    assert (tmp_path / "main.json").exists()
