"""Guard against the telegram-/status bug class: a function-local (deferred)
import whose module path does not exist, hidden by a bare ``except``.

The original bug was ``from .telegram.gateway import TelegramGateway`` inside a
method in ``core/agent/agent_status.py`` — the single-dot path resolved to the
non-existent ``core.agent.telegram`` and a bare ``except`` swallowed the
``ModuleNotFoundError``, silently degrading ``/status`` forever. A static call
graph cannot see deferred imports, so nothing flagged it.

This test statically resolves every first-party/relative deferred import in the
shipped product code and fails loudly if a module target does not exist (the
exact bug class) or an imported name is absent from its module. Third-party and
stdlib deferred imports are skipped — they can't be verified without importing.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIP_DIRS = ("core", "interface", "tools")
ROOT_FILES = ("mo.py", "mo_service.py")
FIRST_PARTY = ("core", "interface", "tools")


def _iter_files():
    for d in SHIP_DIRS:
        yield from (ROOT / d).rglob("*.py")
    for name in ROOT_FILES:
        p = ROOT / name
        if p.is_file():
            yield p


def _module_resolves(mod: str) -> bool:
    base = ROOT.joinpath(*mod.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _file_package(f: Path) -> list[str]:
    return list(f.relative_to(ROOT).parts[:-1])


def _resolve_relative(f: Path, level: int, mod: str | None) -> str | None:
    pkg = _file_package(f)
    if level - 1 > len(pkg):
        return None
    base = pkg[: len(pkg) - (level - 1)] if level - 1 > 0 else pkg
    parts = list(base) + (mod.split(".") if mod else [])
    return ".".join(parts) if parts else None


def _target_symbols(mod: str):
    """Top-level names of a module, or None if the module's namespace is opaque
    (it does a star-import, so names can't be statically enumerated)."""
    base = ROOT.joinpath(*mod.split("."))
    target = base.with_suffix(".py")
    if not target.is_file():
        target = base / "__init__.py"
    if not target.is_file():
        return set()
    try:
        tree = ast.parse(target.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None  # unparseable -> treat as opaque, don't false-positive
    names: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign):  # COMMANDS: tuple[...] = (...)
            if isinstance(n.target, ast.Name):
                names.add(n.target.id)
        elif isinstance(n, ast.ImportFrom):
            if any(a.name == "*" for a in n.names):
                return None  # star-import -> opaque namespace
            names.update(a.asname or a.name for a in n.names)
        elif isinstance(n, ast.Import):
            names.update((a.asname or a.name.split(".")[0]) for a in n.names)
    return names


def _name_present(parent_mod: str, name: str, syms) -> bool:
    if syms is None:
        return True  # opaque namespace — cannot disprove
    if name in syms:
        return True
    # submodule import: `from core.graph import code_graph` where code_graph.py exists
    return _module_resolves(f"{parent_mod}.{name}")


def _deferred_imports(tree: ast.AST):
    """Function-local ``from X import Y`` AND plain ``import X`` statements."""
    class V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0
            self.hits: list[ast.ImportFrom | ast.Import] = []

        def visit_FunctionDef(self, n):
            self.depth += 1
            self.generic_visit(n)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ImportFrom(self, n):
            if self.depth > 0:
                self.hits.append(n)
            self.generic_visit(n)

        def visit_Import(self, n):
            if self.depth > 0:
                self.hits.append(n)
            self.generic_visit(n)

    v = V()
    v.visit(tree)
    return v.hits


def test_deferred_first_party_imports_resolve():
    violations: list[str] = []
    checked = 0
    for f in _iter_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            violations.append(f"{f.relative_to(ROOT)}: parse failed: {exc}")
            continue
        for n in _deferred_imports(tree):
            rel = f.relative_to(ROOT)
            if isinstance(n, ast.Import):
                # plain `import a.b.c` — verify first-party module targets exist
                for alias in n.names:
                    mod = alias.name
                    if mod.split(".")[0] not in FIRST_PARTY:
                        continue  # third-party / stdlib
                    checked += 1
                    if not _module_resolves(mod):
                        violations.append(
                            f"{rel}:{n.lineno}: deferred `import {mod}` target module does not "
                            f"exist — telegram-/status bug class"
                        )
                continue
            level = n.level or 0
            mod = n.module
            if level > 0:
                resolved = _resolve_relative(f, level, mod)
            elif mod and mod.split(".")[0] in FIRST_PARTY:
                resolved = mod
            else:
                continue  # third-party / stdlib — skip
            checked += 1
            rel = f.relative_to(ROOT)
            if resolved is None or not _module_resolves(resolved):
                violations.append(
                    f"{rel}:{n.lineno}: deferred import target module does not exist "
                    f"(level={level} module={mod!r} -> {resolved!r}) — telegram-/status bug class"
                )
                continue
            syms = _target_symbols(resolved)
            missing = [
                a.name for a in n.names
                if a.name != "*" and not _name_present(resolved, a.name, syms)
            ]
            if missing:
                violations.append(
                    f"{rel}:{n.lineno}: deferred import names absent from {resolved}: {missing}"
                )
    assert checked > 0, "scanner found no deferred imports — coverage regression?"
    assert not violations, "Unresolved deferred imports:\n" + "\n".join(violations)
