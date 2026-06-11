from tools import execute_find_files, execute_grep, execute_read_file


def test_read_file_returns_numbered_lines(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\ngamma", encoding="utf-8")

    output = execute_read_file({"path": str(path)})

    assert output.startswith("[Lines 1-3 of 3]")
    assert "1: alpha" in output
    assert "2: beta" in output
    assert "3: gamma" in output


def test_read_file_offset_preserves_real_line_numbers(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\ngamma", encoding="utf-8")

    output = execute_read_file({"path": str(path), "offset": 2, "limit": 1})

    assert output.startswith("[Lines 2-2 of 3]")
    assert "2: beta" in output
    assert "1: alpha" not in output


def test_read_file_does_not_count_trailing_newline_as_phantom_line(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\n", encoding="utf-8")

    output = execute_read_file({"path": str(path)})

    assert output.startswith("[Lines 1-2 of 2]")
    assert "1: alpha" in output
    assert "2: beta" in output
    assert "3:" not in output


def test_find_files_skips_runtime_generated_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("needle = True\n", encoding="utf-8")
    for runtime_dir in ["logs", "memory", ".ruff_cache"]:
        path = tmp_path / runtime_dir
        path.mkdir()
        (path / "artifact.py").write_text("needle = False\n", encoding="utf-8")

    output = execute_find_files({"root": str(tmp_path), "pattern": "", "limit": 20})

    assert "src/app.py" in output
    assert "logs/artifact.py" not in output
    assert "memory/artifact.py" not in output
    assert ".ruff_cache/artifact.py" not in output


def test_grep_skips_runtime_generated_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("needle = True\n", encoding="utf-8")
    for runtime_dir in ["logs", "memory", ".ruff_cache"]:
        path = tmp_path / runtime_dir
        path.mkdir()
        (path / "artifact.py").write_text("needle = False\n", encoding="utf-8")

    output = execute_grep({"root": str(tmp_path), "pattern": "needle", "limit": 20})

    assert "src/app.py:1:" in output
    assert "logs/artifact.py" not in output
    assert "memory/artifact.py" not in output
    assert ".ruff_cache/artifact.py" not in output
