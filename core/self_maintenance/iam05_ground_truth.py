"""IAM05 audit ground truth — live-measured, never estimated.

An IAM05 audit is only as honest as the numbers it starts from. The failure mode this
module exists to kill: the model *guesses* a line count ("run_turn is 1214 lines" — wrong,
that was the file), a coverage claim ("tested only in X" — wrong, three files), or a
duplication claim, and the operator becomes the verifier. The fix mirrors DEVMODE05's
capability preflight: the runtime hands the model measured ground truth *before* it writes
a word, so quantitative / exhaustiveness / duplication claims start from disk, not memory.

Two modes, chosen by the request:
- **Explicit targets** — the request names repo paths and/or symbols. Measure exactly those:
  real file line counts, per-function spans (via ``ast``), and where each symbol is defined
  and referenced (split test vs non-test).
- **Bare ``Run IAM05``** — no target named. Auto-scope from the *live tree* (never from a
  prior DEVMODE05 run's stale per-run snapshot): largest files, largest functions, git churn
  hotspots, and duplication candidates (symbols defined in >1 module).

Everything is bounded (top-N), degrades gracefully (missing git, unparseable file), and is
measured against the current working tree, so it can never be stale or hand-faked.
"""
from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from pathlib import Path

from ..owner_protocols import is_iam05_activation
from ..path_defaults import mo_home

# Source roots an audit cares about. Anything outside these is noise for ground truth.
_SOURCE_DIRS = ("core", "interface", "tools", "tests")
_PATH_RE = __import__("re").compile(r"(?:core|interface|tools|tests)/[\w./-]+\.py")
# snake_case / CamelCase identifiers a request might name as audit symbols. Length >=4 and
# either an underscore (snake_case fn) or an interior capital (CamelCase) keeps out ordinary
# English words ("audit", "cluster") while catching real symbols ("run_turn", "TaskBoard").
_SYMBOL_RE = __import__("re").compile(r"\b(?=\w*[_A-Z])[A-Za-z_][A-Za-z0-9_]{3,}\b")
_STOPWORDS = frozenset({
    "IAM05", "DEVMODE05", "VS05", "IFDEV05", "audit", "cluster", "module", "inline",
    "should", "stay", "move", "alongside", "context", "evidence", "justify",
})

_TOP_FILES = 8
_TOP_FUNCS = 8
_TOP_CHURN = 8
_TOP_DUP = 8
_TOP_REFS = 12
_MAX_TARGET_PATHS = 6
_MAX_TARGET_SYMBOLS = 6


def build_iam05_ground_truth(user_input: str, *, cwd: str | None = None) -> str:
    """Return the live-measured IAM05 ground-truth block, or '' if not an IAM05 turn."""
    if not is_iam05_activation(user_input):
        return ""
    root = Path(cwd or ".").resolve()
    paths = _named_paths(user_input, root)
    symbols = _named_symbols(user_input, root)

    out: list[str] = [
        "### IAM05 Audit Ground Truth (live-measured this turn — start from THESE numbers, never estimate)",
    ]
    if paths or symbols:
        out.append("Targets named in the request:")
        for rel in paths:
            out.extend(_measure_file(root, rel))
        for sym in symbols:
            out.extend(_measure_symbol(root, sym))
    else:
        out.append(
            "No explicit target named — this is your AUDIT QUEUE, not a menu. Audit the full "
            "set below as one comprehensive sweep, deepest/highest-churn first. Do NOT ask the "
            "operator which to pick — proceed and report findings per hotspot. Only pause for "
            "approval before *implementing* a behavior-changing fix (a finished assessment needs "
            "no approval)."
        )
        out.extend(_largest_files(root))
        out.extend(_largest_functions(root))
        out.extend(_churn_hotspots(root))
        out.extend(_duplication_candidates(root))
    out.append(
        "Rule: every count / `only` / `duplicate` claim in your report must match a number "
        "above or be re-measured with a tool (Gate 7). Do not hand-estimate."
    )
    out.extend(_reporting_contract(root))
    return "\n".join(out)


def _reporting_contract(root: Path) -> list[str]:
    """The report is judged against the run's instrumented truth, not the model's
    impression. Pins the three self-claim honesty rules that this pass exposed MO
    breaking: scope overclaim ('Full Codebase' on a 12-file sample), self-report
    drift ('~22 tool calls' when the monitor showed 56, unowned tool errors), and
    state-boundary leak (ledger written to repo-local memory/ instead of ~/.mo)."""
    corpus = len(_iter_py_files(root))
    ledger_dir = (mo_home() / "memory" / "iam05").as_posix()
    return [
        "",
        "### IAM05 Reporting Contract (your report is checked against this run's instrumented truth)",
        f"- Scope honesty: the source corpus is {corpus} files. Do NOT title or describe the audit "
        f"\"Full Codebase\" / \"entire\" / \"complete\" unless you actually read all {corpus}. State "
        f"coverage as \"sampled N of {corpus}\" and list ONLY files you genuinely read this run.",
        "- Self-report truth: report your EXACT tool-call count and tool-error count from your real "
        "tool history this run — never estimate (\"~N\") and never omit a recovered/retried error "
        "(it still counts and must be named). \"No tool errors\" is allowed only if there were none.",
        f"- Ledger location: write the evidence ledger under `{ledger_dir}/` (private runtime home) "
        "with a session-unique filename (include the session id or HHMMSS timestamp), NEVER repo-local "
        "`memory/` and never a date-only `evidence_ledger_YYYYMMDD.md` path.",
    ]


def iam05_source_corpus_count(*, cwd: str | None = None) -> int:
    """Return the live source corpus denominator used by IAM05 reporting."""
    root = Path(cwd or ".").resolve()
    return len(_iter_py_files(root))


def iam05_function_span_index(*, cwd: str | None = None) -> dict[str, set[int]]:
    """Return production function/class-method spans keyed by safe names.

    Test fixtures routinely define tiny same-named functions (``run_turn`` stubs,
    ``__init__`` helpers), so answer-time line-count checks must not pool tests with
    production code. Qualified names are always kept; bare names are kept only when
    they resolve to one production span. Ambiguous bare names are present with an empty
    span set so answer-time gates can reject the claim without inventing a bad hint.
    """
    root = Path(cwd or ".").resolve()
    qualified: dict[str, set[int]] = defaultdict(set)
    bare_candidates: dict[str, set[int]] = defaultdict(set)
    for rel in _iter_py_files(root):
        if rel.startswith("tests/"):
            continue
        tree = _parse(root / rel)
        if tree is None:
            continue
        for qualname, start, end in _qualified_functions(tree):
            span = end - start + 1
            qualified[qualname].add(span)
            bare_candidates[qualname.rsplit(".", 1)[-1]].add(span)
    result: dict[str, set[int]] = dict(qualified)
    for bare, values in bare_candidates.items():
        if len(values) == 1:
            result[bare] = set(values)
        else:
            result[bare] = set()
    return result


# --- target extraction -------------------------------------------------------

def _named_paths(user_input: str, root: Path) -> list[str]:
    seen: list[str] = []
    for raw in _PATH_RE.findall(str(user_input or "")):
        rel = raw.strip("`*_.,;: ")
        if rel and rel not in seen and (root / rel).is_file():
            seen.append(rel)
        if len(seen) >= _MAX_TARGET_PATHS:
            break
    return seen


def _named_symbols(user_input: str, root: Path) -> list[str]:
    defined = _defined_symbols(root)
    seen: list[str] = []
    for tok in _SYMBOL_RE.findall(str(user_input or "")):
        if tok in _STOPWORDS or tok in seen:
            continue
        if tok in defined:
            seen.append(tok)
        if len(seen) >= _MAX_TARGET_SYMBOLS:
            break
    return seen


# --- file/tree iteration -----------------------------------------------------

def _iter_py_files(root: Path) -> list[str]:
    """Tracked .py paths under the source dirs. Prefer git (tracked only); fall back to a
    filesystem walk so it still works in a non-git checkout or a sandbox without git."""
    try:
        res = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            files = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
            files = [f for f in files if f.split("/", 1)[0] in _SOURCE_DIRS]
            if files:
                return files
    except Exception:
        pass
    out: list[str] = []
    for d in _SOURCE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            try:
                out.append(p.relative_to(root).as_posix())
            except ValueError:
                continue
    return out


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except Exception:
        return 0


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _functions(tree: ast.AST) -> list[tuple[str, int, int]]:
    """(qualname-ish, start_line, end_line) for every def/async def in the tree."""
    out: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            out.append((node.name, node.lineno, end))
    return out


def _qualified_functions(tree: ast.AST) -> list[tuple[str, int, int]]:
    """(qualified_name, start_line, end_line) for defs, including class methods."""
    out: list[tuple[str, int, int]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast visitor API
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor API
            self._record(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._record(node)
            self.generic_visit(node)

        def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            qualname = ".".join([*self.class_stack, node.name]) if self.class_stack else node.name
            out.append((qualname, node.lineno, end))

    Visitor().visit(tree)
    return out


def _defined_symbols(root: Path) -> set[str]:
    names: set[str] = set()
    for rel in _iter_py_files(root):
        tree = _parse(root / rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
    return names


# --- explicit-target measurement ---------------------------------------------

def _measure_file(root: Path, rel: str) -> list[str]:
    path = root / rel
    lines = _line_count(path)
    out = [f"- {rel}: {lines} lines (file)"]
    tree = _parse(path)
    if tree is not None:
        funcs = sorted(_functions(tree), key=lambda f: (f[2] - f[1]), reverse=True)[:3]
        for name, start, end in funcs:
            out.append(f"    - {name}() spans :{start}-:{end} ({end - start + 1} lines)")
    return out


def _measure_symbol(root: Path, sym: str) -> list[str]:
    def_sites: list[str] = []
    ref_files: list[str] = []
    test_ref = 0
    nontest_ref = 0
    for rel in _iter_py_files(root):
        tree = _parse(root / rel)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == sym:
                    def_sites.append(f"{rel}:{node.lineno}")
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if _word_in(sym, text):
            ref_files.append(rel)
            if rel.startswith("tests/") or "/tests/" in rel or "test_" in rel.rsplit("/", 1)[-1]:
                test_ref += 1
            else:
                nontest_ref += 1
    if def_sites:
        shown_defs = def_sites[:_TOP_REFS]
        defs_str = ", ".join(shown_defs)
        if len(def_sites) > len(shown_defs):
            defs_str += f" (+{len(def_sites) - len(shown_defs)} more)"
    else:
        defs_str = "(no def found)"
    out = [
        f"- symbol `{sym}`: defined at {defs_str}; "
        f"referenced in {len(ref_files)} files ({test_ref} test, {nontest_ref} non-test)"
    ]
    if ref_files:
        shown = sorted(ref_files)[:_TOP_REFS]
        more = f" (+{len(ref_files) - len(shown)} more)" if len(ref_files) > len(shown) else ""
        out.append(f"    refs: {', '.join(shown)}{more}")
    return out


def _word_in(sym: str, text: str) -> bool:
    import re
    return re.search(r"\b" + re.escape(sym) + r"\b", text) is not None


# --- bare-mode auto-scope ----------------------------------------------------

def _largest_files(root: Path) -> list[str]:
    sized = [(rel, _line_count(root / rel)) for rel in _iter_py_files(root)]
    sized.sort(key=lambda x: x[1], reverse=True)
    out = [f"Largest files (top {_TOP_FILES} by line count):"]
    for rel, n in sized[:_TOP_FILES]:
        out.append(f"    - {rel}: {n} lines")
    return out


def _largest_functions(root: Path) -> list[str]:
    funcs: list[tuple[str, str, int]] = []
    for rel in _iter_py_files(root):
        tree = _parse(root / rel)
        if tree is None:
            continue
        for name, start, end in _functions(tree):
            funcs.append((rel, name, end - start + 1))
    funcs.sort(key=lambda x: x[2], reverse=True)
    out = [f"Largest functions (top {_TOP_FUNCS} by span):"]
    for rel, name, span in funcs[:_TOP_FUNCS]:
        out.append(f"    - {rel}: {name}() {span} lines")
    return out


def _churn_hotspots(root: Path) -> list[str]:
    try:
        res = subprocess.run(
            ["git", "log", "--pretty=format:", "--name-only", "-n", "300", "--", "*.py"],
            cwd=str(root), capture_output=True, text=True, timeout=15,
        )
        if res.returncode != 0:
            return ["Churn hotspots: (git unavailable)"]
        counts: dict[str, int] = defaultdict(int)
        for ln in res.stdout.splitlines():
            rel = ln.strip()
            if rel and rel.split("/", 1)[0] in _SOURCE_DIRS:
                counts[rel] += 1
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:_TOP_CHURN]
        if not ranked:
            return ["Churn hotspots: (none in last 300 commits)"]
        out = [f"Churn hotspots (most-changed .py, last 300 commits, top {_TOP_CHURN}):"]
        out.extend(f"    - {rel}: {n} commits" for rel, n in ranked)
        return out
    except Exception:
        return ["Churn hotspots: (git unavailable)"]


def _duplication_candidates(root: Path) -> list[str]:
    sites: dict[str, set[str]] = defaultdict(set)
    for rel in _iter_py_files(root):
        tree = _parse(root / rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Skip dunder/private boilerplate that is legitimately redefined everywhere.
                if node.name.startswith("__") or node.name in ("setUp", "tearDown"):
                    continue
                sites[node.name].add(rel)
    multi = [(name, files) for name, files in sites.items() if len(files) > 1]
    multi.sort(key=lambda x: len(x[1]), reverse=True)
    out = [f"Duplication candidates (same symbol name defined in >1 file, top {_TOP_DUP} — verify before claiming true duplication):"]
    if not multi:
        out.append("    - (none)")
    for name, files in multi[:_TOP_DUP]:
        out.append(f"    - {name}: {len(files)} files ({', '.join(sorted(files)[:4])}{'…' if len(files) > 4 else ''})")
    return out
