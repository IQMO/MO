from pathlib import Path


def test_root_map_stays_compact_and_points_to_authoritative_surfaces():
    path = Path("MAP.md")
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]

    assert len(lines) <= 40
    assert "AGENTS.md" in text
    assert "core/prompts/system.md" in text
    # protocols are operator-private (untracked devmode/); the map must say so
    # without advertising the pack's file layout
    assert "operator-private" in text
    assert "core/graph/structural_graph.py" in text
    assert "core/tasking/task_board.py" in text
