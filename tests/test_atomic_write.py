"""Tests for core/atomic_write.py — atomic file replacement helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.atomic_write import atomic_write_text, atomic_write_json


class TestAtomicWriteText:
    """Tests for atomic_write_text."""

    def test_basic_write_and_read(self, tmp_path: Path) -> None:
        """Write text to a file and verify content."""
        path = tmp_path / "test.txt"
        atomic_write_text(path, "hello world")
        assert path.read_text() == "hello world"

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        """Overwriting an existing file replaces content atomically."""
        path = tmp_path / "test.txt"
        path.write_text("old content")
        atomic_write_text(path, "new content")
        assert path.read_text() == "new content"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Parent directory is created if it doesn't exist."""
        path = tmp_path / "deep" / "nested" / "file.txt"
        atomic_write_text(path, "data")
        assert path.read_text() == "data"

    def test_temp_file_cleaned_up(self, tmp_path: Path) -> None:
        """Temp file is removed after successful write."""
        path = tmp_path / "cleanup.txt"
        before = set(os.listdir(str(tmp_path)))
        atomic_write_text(path, "data")
        after = set(os.listdir(str(tmp_path)))
        new_files = after - before
        # Only the target file should remain; no .tmp files
        assert new_files == {"cleanup.txt"} or all(
            not f.startswith(".cleanup.txt.") for f in new_files
        )

    def test_temp_file_cleaned_after_exception(self, tmp_path: Path) -> None:
        """Temp file is cleaned up even when the atomic replace FAILS.

        Forces a REAL failure: make the target path a directory so the temp file is
        created and written, then ``os.replace(tmp, target)`` raises (can't replace a
        directory with a file) — exercising the finally-cleanup path. The previous
        version called atomic_write_text on a normal path that simply succeeded, so the
        exception branch was never hit (a false test)."""
        target = tmp_path / "should_clean.txt"
        target.mkdir()  # target is now a directory → os.replace will raise
        before = set(os.listdir(str(tmp_path)))
        with pytest.raises(Exception):
            atomic_write_text(target, "data")
        leftovers = set(os.listdir(str(tmp_path))) - before
        # The write genuinely failed (target dir still present, unchanged)...
        assert target.is_dir()
        # ...and no temp file leaked despite the exception.
        assert leftovers == set(), f"temp file leaked after forced failure: {leftovers}"

    def test_empty_string(self, tmp_path: Path) -> None:
        """Writing an empty string works."""
        path = tmp_path / "empty.txt"
        atomic_write_text(path, "")
        assert path.read_text() == ""

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Unicode content is preserved."""
        path = tmp_path / "unicode.txt"
        text = "héllo wörld 🌍\nline2"
        atomic_write_text(path, text)
        assert path.read_text(encoding="utf-8") == text

    def test_path_as_string(self, tmp_path: Path) -> None:
        """Works with string paths, not just Path objects."""
        path_str = str(tmp_path / "string_path.txt")
        atomic_write_text(path_str, "ok")
        assert Path(path_str).read_text() == "ok"

    def test_encoding_kwarg(self, tmp_path: Path) -> None:
        """Encoding parameter is forwarded."""
        path = tmp_path / "latin1.txt"
        text = "café"
        atomic_write_text(path, text, encoding="latin-1")
        assert path.read_text(encoding="latin-1") == text


class TestAtomicWriteJson:
    """Tests for atomic_write_json."""

    def test_basic_json_roundtrip(self, tmp_path: Path) -> None:
        """Write JSON and read it back."""
        path = tmp_path / "data.json"
        data = {"key": "value", "num": 42}
        atomic_write_json(path, data)
        assert json.loads(path.read_text()) == data

    def test_pretty_print(self, tmp_path: Path) -> None:
        """Indent kwarg produces multi-line JSON."""
        path = tmp_path / "pretty.json"
        data = {"a": 1}
        atomic_write_json(path, data, indent=2)
        raw = path.read_text()
        assert "\n" in raw
        assert json.loads(raw) == data

    def test_nested_structures(self, tmp_path: Path) -> None:
        """Nested dicts/lists roundtrip correctly."""
        path = tmp_path / "nested.json"
        data = {"items": [{"id": 1}, {"id": 2}], "meta": {"count": 2}}
        atomic_write_json(path, data)
        assert json.loads(path.read_text()) == data

    def test_non_ascii_keys(self, tmp_path: Path) -> None:
        """Non-ASCII keys survive roundtrip (ensure_ascii default)."""
        path = tmp_path / "nonascii.json"
        data = {"clé": "valeur"}
        atomic_write_json(path, data)
        loaded = json.loads(path.read_text())
        assert loaded == data

    def test_parent_directory_created(self, tmp_path: Path) -> None:
        """JSON write creates parent directories too."""
        path = tmp_path / "sub" / "deep.json"
        atomic_write_json(path, [1, 2, 3])
        assert json.loads(path.read_text()) == [1, 2, 3]
