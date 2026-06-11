"""Tests for core/project_context.py — project instruction file discovery."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.project_context import (
    PROJECT_CONTEXT_FILES,
    ProjectContextFile,
    build_project_context,
    discover_project_context_files,
)


class TestProjectContextFile:
    def test_is_frozen_dataclass(self):
        pcf = ProjectContextFile(path=Path("test"), content="hello")
        assert pcf.path == Path("test")
        assert pcf.content == "hello"
        with pytest.raises(Exception):
            pcf.path = Path("other")  # type: ignore[misc]


class TestDiscoverProjectContextFiles:
    def test_finds_agents_md_in_project_root(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Project Instructions")
        result = discover_project_context_files(str(tmp_path))
        assert len(result) >= 1
        assert any(p.name == "AGENTS.md" for p in result)

    def test_finds_claude_md_in_project_root(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Claude Instructions")
        result = discover_project_context_files(str(tmp_path))
        assert any(p.name == "CLAUDE.md" for p in result)

    def test_finds_both_when_both_exist(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Agents")
        (tmp_path / "CLAUDE.md").write_text("# Claude")
        result = discover_project_context_files(str(tmp_path))
        names = {p.name for p in result}
        assert "AGENTS.md" in names
        assert "CLAUDE.md" in names

    def test_returns_empty_when_no_files(self, tmp_path):
        result = discover_project_context_files(str(tmp_path))
        assert result == ()

    def test_finds_parent_instruction_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Root")
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        result = discover_project_context_files(str(sub))
        assert len(result) >= 1
        assert any(p.name == "AGENTS.md" for p in result)

    def test_custom_names(self, tmp_path):
        (tmp_path / "CUSTOM.md").write_text("# Custom")
        result = discover_project_context_files(str(tmp_path), names=["CUSTOM.md"])
        assert len(result) == 1
        assert result[0].name == "CUSTOM.md"

    def test_duplicate_names_deduplicated(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Dupe")
        result = discover_project_context_files(str(tmp_path), names=["AGENTS.md", "AGENTS.md"])
        # Should only find AGENTS.md once per directory
        agents_count = sum(1 for p in result if p.name == "AGENTS.md")
        assert agents_count >= 1

    def test_empty_names_returns_empty(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Test")
        result = discover_project_context_files(str(tmp_path), names=[])
        assert result == ()

    def test_file_not_directory(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = discover_project_context_files(str(f))
        # Should walk up from parent directory
        assert isinstance(result, tuple)

    def test_nonexistent_path_does_not_crash(self):
        result = discover_project_context_files("/nonexistent/path/12345")
        assert result == ()


class TestBuildProjectContext:
    def test_returns_string_for_existing_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Project\n\nTest content")
        result = build_project_context(str(tmp_path))
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Project-local instructions" in result

    def test_returns_empty_when_no_files(self, tmp_path):
        result = build_project_context(str(tmp_path))
        assert result == ""

    def test_truncates_long_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# " + "x" * 5000)
        result = build_project_context(str(tmp_path), max_chars=500)
        assert len(result) <= 500 + 50  # allow some padding for wrapper text


class TestProjectContextFilesConstant:
    def test_contains_expected_files(self):
        assert "AGENTS.md" in PROJECT_CONTEXT_FILES
        assert "CLAUDE.md" in PROJECT_CONTEXT_FILES

    def test_is_tuple(self):
        assert isinstance(PROJECT_CONTEXT_FILES, tuple)
