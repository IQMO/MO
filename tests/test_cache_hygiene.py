import sys
from pathlib import Path

from conftest import _remove_checkout_generated_caches


def test_pytest_session_disables_python_bytecode_caches():
    assert sys.dont_write_bytecode is True


def test_test_and_lint_tools_do_not_use_root_cache_dirs():
    config = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'addopts = "-p no:cacheprovider"' in config
    assert 'cache-dir = "tmp/ruff-cache"' in config


def test_entrypoints_disable_bytecode_before_project_imports():
    for entrypoint in ("mo.py", "mo_service.py"):
        text = Path(entrypoint).read_text(encoding="utf-8")
        bytecode_index = text.index("sys.dont_write_bytecode = True")
        project_import_indexes = [
            idx
            for needle in ("from core.", "from interface.", "import core.", "import interface.")
            if (idx := text.find(needle)) != -1
        ]
        assert project_import_indexes, entrypoint
        assert bytecode_index < min(project_import_indexes)


def test_pytest_session_cleanup_removes_generated_cache_dirs(tmp_path):
    nested = tmp_path / "core" / "__pycache__"
    nested.mkdir(parents=True)
    (nested / "module.pyc").write_bytes(b"cache")

    for name in (".pytest_cache", ".ruff_cache"):
        cache = tmp_path / name
        cache.mkdir()
        (cache / "marker").write_text("cache", encoding="utf-8")

    _remove_checkout_generated_caches(tmp_path)

    assert not nested.exists()
    assert not (tmp_path / ".pytest_cache").exists()
    assert not (tmp_path / ".ruff_cache").exists()
