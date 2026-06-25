from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from UX.adapters import rows_from_gateway_board, snapshot_from_runtime
from UX.app import run_smoke
from UX.layout import STATUS_MARKERS, render_text
from UX.models import BoardRow, demo_snapshot

REPO = Path(__file__).resolve().parents[1]
UX_ROOT = REPO / "UX"
EXPECTED_PACKAGE_DIRS = {"state", "runtime", "render", "shell"}
ROOT_COMPAT_SHIMS = {"app.py", "models.py", "controller.py", "layout.py", "theme.py", "adapters.py"}


def _ux_python_files() -> list[Path]:
    return sorted(path for path in UX_ROOT.rglob("*.py") if path.is_file())


def test_ux_does_not_import_current_interface_package():
    offenders: list[str] = []
    for path in _ux_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "interface" or alias.name.startswith("interface."):
                        offenders.append(str(path.relative_to(REPO)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "interface" or module.startswith("interface."):
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_ux_has_layered_package_structure():
    assert {path.name for path in UX_ROOT.iterdir() if path.is_dir()} >= EXPECTED_PACKAGE_DIRS
    for name in ROOT_COMPAT_SHIMS:
        text = (UX_ROOT / name).read_text(encoding="utf-8")
        assert "Compatibility exports" in text
        assert len(text.splitlines()) <= 24


def test_state_layer_has_no_runtime_render_or_product_imports():
    forbidden = ("UX.runtime", "UX.render", "interface", "core")
    offenders: list[str] = []
    for path in (UX_ROOT / "state").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module == "core" or any(module == token or module.startswith(f"{token}.") for token in forbidden):
                        offenders.append(str(path.relative_to(REPO)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "core" or any(module == token or module.startswith(f"{token}.") for token in forbidden):
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_render_layer_has_no_runtime_or_product_imports():
    forbidden = ("UX.runtime", "interface", "core")
    offenders: list[str] = []
    for path in (UX_ROOT / "render").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module == "core" or any(module == token or module.startswith(f"{token}.") for token in forbidden):
                        offenders.append(str(path.relative_to(REPO)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "core" or any(module == token or module.startswith(f"{token}.") for token in forbidden):
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_production_entrypoint_does_not_import_experimental_ux():
    mo_text = (REPO / "mo.py").read_text(encoding="utf-8")
    terminal_loop = (REPO / "interface" / "terminal_loop.py").read_text(encoding="utf-8")
    assert "import UX" not in mo_text
    assert "from UX" not in mo_text
    assert "import UX" not in terminal_loop
    assert "from UX" not in terminal_loop


def test_windows_launcher_targets_isolated_package():
    text = (UX_ROOT / "run_ux.bat").read_text(encoding="utf-8")
    assert "-m UX" in text
    assert "set \"UX_ARGS=--live --width %UX_WIDTH%\"" in text
    assert "set \"UX_ARGS=--live --width %UX_WIDTH% %*\"" in text
    assert "set \"UX_WIDTH=120\"" in text
    assert "PYTHONUTF8=1" in text
    assert "mo.py" not in text
    assert "interface" not in text


def test_ux_package_import_is_light_and_isolated():
    code = "import sys; import UX; print('interface' in sys.modules); print('core.agent.agent' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, text=True, capture_output=True, check=True)
    assert result.stdout.splitlines() == ["False", "False"]


def test_demo_snapshot_renders_expected_panes():
    text = render_text(demo_snapshot(), width=100)
    assert "MO" in text
    assert "Agent Lanes" in text
    assert "Task Board" in text
    assert "Transcript" in text
    assert "Composer" in text
    assert "THINKING" in text
    assert "EXECUTION" in text
    assert "COMPACTION" in text


def test_local_smoke_path_advances_preview_transcript():
    text = run_smoke(width=90)
    assert "smoke input" in text
    assert "Preview only" in text
    assert text.count("Session") == 1
    assert "[x]  Inspect interface contracts" in text


def test_ux_statuses_are_display_defined_and_stable():
    assert STATUS_MARKERS == {
        "completed": "[x]",
        "active": ">",
        "blocked": "!",
        "pending": "[ ]",
    }


def test_board_row_normalizes_unknown_status_to_pending():
    row = BoardRow.from_mapping({"id": "1", "title": "Bad status", "status": "done-ish"})
    assert row.status == "pending"


def test_gateway_board_adapter_normalizes_object_task_status():
    task = SimpleNamespace(id="1", title="Object task", status="done-ish", blocker="", kind="")
    rows = rows_from_gateway_board(SimpleNamespace(tasks=[task]))
    assert rows[0].status == "pending"


def test_gateway_board_adapter_is_display_only_duck_typing():
    board = SimpleNamespace(
        summary=lambda: {
            "tasks": [
                {"id": "1", "title": "Inspect", "status": "completed", "kind": "inspect"},
                {"id": "2", "title": "Fix", "status": "active", "kind": "edit"},
            ]
        }
    )
    rows = rows_from_gateway_board(board)
    assert [row.title for row in rows] == ["Inspect", "Fix"]
    assert [row.status for row in rows] == ["completed", "active"]


def test_runtime_snapshot_adapter_uses_duck_typed_public_state():
    board = SimpleNamespace(summary=lambda: {"tasks": [{"id": "1", "title": "Verify", "status": "blocked", "blocker": "tests"}]}, open_count=lambda: 1)
    gateway = SimpleNamespace(last_task_board=board)
    agent = SimpleNamespace(
        project_cwd="E:\\MO-clean",
        runtime_home="~/.mo",
        provider_name="opencode",
        model="deepseek-v4-pro",
        messages=[
            {"role": "system", "content": "internal prompt must not render"},
            {"role": "tool", "content": "tool payload must not render"},
            {"role": "user", "content": "hello"},
        ],
    )

    snapshot = snapshot_from_runtime(agent, gateway)

    assert snapshot.project == "E:\\MO-clean"
    assert snapshot.model_label == "opencode / deepseek-v4-pro"
    assert snapshot.board[0].status == "blocked"
    assert [item.speaker for item in snapshot.transcript] == ["user"]
    assert "internal prompt" not in " ".join(item.text for item in snapshot.transcript)


def test_old_and_new_board_surfaces_read_same_taskboard_truth():
    from core.tasking.task_board import TaskBoard, TaskItem
    from interface.task_board_view import render_plain

    board = TaskBoard(
        tasks=[
            TaskItem("1", "Inspect shared truth", "completed", kind="inspect"),
            TaskItem("2", "Render active work", "active", kind="execute"),
            TaskItem("3", "Report blocker", "blocked", blocker="waiting on evidence", kind="verify"),
        ]
    )

    old_text = render_plain(board)
    new_rows = rows_from_gateway_board(board)

    for title in ("Inspect shared truth", "Render active work", "Report blocker"):
        assert title in old_text
        assert title in {row.title for row in new_rows}
    assert [row.status for row in new_rows] == ["completed", "active", "blocked"]
    assert new_rows[2].blocker == "waiting on evidence"
