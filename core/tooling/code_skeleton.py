"""Python-AST 'skeleton' compression for OLD read_file-of-code results.

Keeps module/class/function signatures + first-line docstrings and imports; drops
bodies. On real MO source files this is a ~90% size cut (measured) while preserving
the structure the model needs to navigate. It is LOSSY by design, so it is used ONLY
during session-momentum compaction of OLD completed tool chains — where the original
is already archived to ``logs/compacted_chains/`` and stays ``read_file``-recoverable,
and never on a fresh tool result the model just asked for.

Contract: non-Python or unparseable input → returns "" (the caller keeps its existing
behavior); never returns something larger than the input.
"""
from __future__ import annotations

import ast
import re

_NUMBERED_PREFIX_RE = re.compile(r"^\s*\d+:\s?")


def strip_read_file_numbering(text: str) -> str:
    """Strip MO ``read_file`` output's ``N: `` line-number prefixes + ``[Lines …]`` header."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        if line.startswith("[Lines ") and " of " in line:
            continue
        out.append(_NUMBERED_PREFIX_RE.sub("", line, count=1))
    return "\n".join(out)


def _signature(node: "ast.AST") -> str:
    kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        return f"{kw} {node.name}({ast.unparse(node.args)}): ..."
    except Exception:
        return f"{kw} {node.name}(...): ..."


def code_skeleton(text: str, *, max_chars: int = 4000) -> str:
    """Return a Python structure skeleton for ``text``, or "" when not compressible.

    "" means: not Python, unparseable, or no real savings — the caller should keep
    whatever it would have done otherwise.
    """
    src = strip_read_file_numbering(text)
    if not src.strip():
        return ""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return ""
    out: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc:
        out.append(f'"""{module_doc.strip().splitlines()[0]}"""')
    for node in tree.body:
        try:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                out.append(ast.unparse(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(_signature(node))
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                out.append(f"class {node.name}({bases}):" if bases else f"class {node.name}:")
                class_doc = ast.get_docstring(node)
                if class_doc:
                    out.append(f'    """{class_doc.strip().splitlines()[0]}"""')
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out.append("    " + _signature(sub))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                out.append(ast.unparse(node))
        except Exception:
            continue
    skeleton = "\n".join(out).strip()
    if not skeleton or len(skeleton) >= len(text):
        return ""
    if len(skeleton) > max_chars:
        skeleton = skeleton[:max_chars].rstrip() + "\n# … skeleton truncated; read_file for full"
    return skeleton
