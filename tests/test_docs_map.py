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


def test_boundary_docs_do_not_advertise_old_nested_operator_layout():
    stale = "gitignored `operator/` + `docs/` + `~/.mo`"
    for path in (Path("MAP.md"), Path("CLAUDE.md")):
        text = path.read_text(encoding="utf-8")
        assert stale not in text
        assert "~/.mo/operator" in text


def test_companion_voice_docs_separate_capture_from_transcription():
    config = Path("config.example.yaml").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "faster-whisper + sounddevice" not in config
    assert "mic capture uses sounddevice" in config
    assert "transcription requires faster-whisper" in config
    assert "microphone capture uses" in readme
    assert "transcription requires `faster-whisper`" in readme
