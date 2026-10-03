"""Detect unowned source duplication without importing product modules.

The check intentionally favors stable, explainable signals over fuzzy scoring:
whole-file hashes, normalized Python/Kotlin function bodies, bounded token
windows, and exact normalized blocks in non-code text. Intentional public-source
similarity must have a reviewed allowlist row; stale rows fail with the code they
used to excuse.
"""
from __future__ import annotations

import argparse
import ast
import bisect
import hashlib
import io
import json
import keyword
import re
import tokenize
from collections import defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from .source_inventory import SourceDocument, SourceInventory, inventory_summary, load_source_inventory


PYTHON_FUNCTION_MIN_LINES = 6
KOTLIN_FUNCTION_MIN_LINES = 6
KOTLIN_FUNCTION_MIN_TOKENS = 35
TOKEN_WINDOW = 50
TOKEN_STEP = 5
EXACT_TOKEN_MIN_WINDOWS = 4
RENAMED_TOKEN_MIN_WINDOWS = 8

# CPython may add empty AST fields in a newer grammar. They do not change the
# source meaning and must not make allowlist IDs depend on the interpreter used
# by a maintainer or CI. Non-empty values remain part of the fingerprint.
_VERSION_OPTIONAL_EMPTY_AST_FIELDS = frozenset({"type_params"})
TEXT_BLOCK_LINES = 8
TEXT_BLOCK_MIN_CHARS = 180

_ROLLING_BITS = 128
_ROLLING_MODULUS = 1 << _ROLLING_BITS
_ROLLING_MASK = _ROLLING_MODULUS - 1
_ROLLING_BASE_A = 1_000_003
_ROLLING_BASE_B = 1_000_033
_ROLLING_SALT = 0x9E3779B97F4A7C15F39CC0605CEDC835

_CODE_SUFFIXES = {".py", ".kt", ".kts", ".java", ".js", ".jsx", ".ts", ".tsx"}
_GENERIC_TOKEN = re.compile(
    r'//[^\n]*|/\*.*?\*/|""".*?"""|\"(?:\\.|[^\"\\])*\"|'
    r"'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|"
    r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|===|!==|==|!=|<=|>=|->|&&|\|\||::|\.\.|"
    r"[{}()\[\].,:;?@=+\-*/%<>!]",
    re.DOTALL,
)
_GENERIC_KEYWORDS = frozenset(
    "as break case catch class const continue data default do else enum export extends false final finally for fun "
    "function if import in interface is let new null object package private protected public return sealed static "
    "super suspend switch this throw true try typealias val var when while by constructor get set async await typeof"
    .split()
)


@dataclass(frozen=True, order=True)
class FindingMember:
    path: str
    symbol: str = ""
    line: int = 0
    span: int = 0

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}::{self.symbol}" if self.symbol else location


@dataclass(frozen=True)
class RedundancyFinding:
    finding_id: str
    kind: str
    fingerprint: str
    members: tuple[FindingMember, ...]
    detail: str

    def render(self) -> str:
        joined = ", ".join(member.render() for member in self.members)
        return f"{self.finding_id}: {self.detail}: {joined}"


@dataclass(frozen=True)
class ScanProblem:
    source: str
    reason: str

    def render(self) -> str:
        return f"{self.source}: {self.reason}"


@dataclass(frozen=True)
class Allowance:
    finding_id: str
    members: tuple[str, ...]
    reason: str
    behavior_owner: str
    removal: str


@dataclass(frozen=True)
class ScanResult:
    findings: tuple[RedundancyFinding, ...]
    problems: tuple[ScanProblem, ...]


def _stable_id(kind: str, fingerprint: str, members: Iterable[FindingMember]) -> str:
    member_key = "|".join(f"{item.path}::{item.symbol}" for item in sorted(members))
    digest = hashlib.sha256(f"{kind}|{fingerprint}|{member_key}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{digest}"


def _finding(
    kind: str,
    fingerprint: str,
    members: Iterable[FindingMember],
    detail: str,
) -> RedundancyFinding:
    ordered = tuple(sorted(members))
    return RedundancyFinding(_stable_id(kind, fingerprint, ordered), kind, fingerprint, ordered, detail)


def _member_identity(member: FindingMember) -> str:
    return f"{member.path}::{member.symbol}" if member.symbol else member.path


def _disambiguate_member_identities(members: Iterable[FindingMember]) -> list[FindingMember]:
    """Keep path/symbol identities unique for repeated definitions in one file."""
    materialized = list(members)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for member in materialized:
        counts[(member.path, member.symbol)] += 1
    return [
        FindingMember(
            member.path,
            f"{member.symbol}@{member.line}" if counts[(member.path, member.symbol)] > 1 else member.symbol,
            member.line,
            member.span,
        )
        for member in materialized
    ]


def _whole_file_findings(inventory: SourceInventory) -> list[RedundancyFinding]:
    raw: dict[str, list[SourceDocument]] = defaultdict(list)
    normalized: dict[str, list[SourceDocument]] = defaultdict(list)
    for document in inventory.documents:
        raw[document.sha256].append(document)
        if document.normalized_sha256 is not None:
            normalized[document.normalized_sha256].append(document)
    findings: list[RedundancyFinding] = []
    for fingerprint, documents in raw.items():
        if len(documents) > 1:
            findings.append(
                _finding(
                    "exact-file",
                    fingerprint,
                    (FindingMember(document.relative_path) for document in documents),
                    "byte-identical files",
                )
            )
    for fingerprint, documents in normalized.items():
        if len(documents) < 2 or len({document.sha256 for document in documents}) == 1:
            continue
        findings.append(
            _finding(
                "normalized-file",
                fingerprint,
                (FindingMember(document.relative_path) for document in documents),
                "files differ only by line endings or trailing whitespace",
            )
        )
    return findings


class _LocalNames(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: list[str] = []

    def _remember(self, name: str) -> None:
        if name not in self.names:
            self.names.append(name)

    def visit_arg(self, node: ast.arg) -> None:
        self._remember(node.arg)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._remember(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._remember(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._remember(node.name)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        if node.asname:
            self._remember(node.asname)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._remember(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._remember(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._remember(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._remember(node.rest)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._remember(node.id)


def _canonical_ast_value(
    value: Any,
    *,
    local_names: dict[str, str],
    root_function: bool = False,
) -> Any:
    if isinstance(value, ast.AST):
        fields: list[tuple[str, Any]] = []
        for field, child in ast.iter_fields(value):
            if field in _VERSION_OPTIONAL_EMPTY_AST_FIELDS and not child:
                continue
            if field == "name" and isinstance(
                value,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                child = "function" if root_function else local_names.get(str(child), child)
            elif root_function and field == "decorator_list":
                child = []
            elif root_function and field == "body" and isinstance(child, list) and child:
                first = child[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    child = child[1:]
            elif isinstance(value, ast.arg) and field == "arg":
                child = local_names.get(str(child), child)
            elif isinstance(value, ast.Name) and field == "id":
                child = local_names.get(str(child), child)
            elif isinstance(value, ast.alias) and field == "asname" and child:
                child = local_names.get(str(child), child)
            elif isinstance(value, ast.ExceptHandler) and field == "name" and child:
                child = local_names.get(str(child), child)
            elif isinstance(value, (ast.MatchAs, ast.MatchStar)) and field == "name" and child:
                child = local_names.get(str(child), child)
            elif isinstance(value, ast.MatchMapping) and field == "rest" and child:
                child = local_names.get(str(child), child)
            fields.append(
                (
                    field,
                    _canonical_ast_value(child, local_names=local_names),
                )
            )
        return (type(value).__name__, tuple(fields))
    if isinstance(value, list):
        return tuple(_canonical_ast_value(item, local_names=local_names) for item in value)
    return value


def _python_function_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    names = _LocalNames()
    names.visit(node)
    mapping = {name: f"local_{index}" for index, name in enumerate(names.names)}
    canonical = _canonical_ast_value(node, local_names=mapping, root_function=True)
    return hashlib.sha256(repr(canonical).encode("utf-8")).hexdigest()


def _python_function_findings(
    inventory: SourceInventory,
) -> tuple[list[RedundancyFinding], list[ScanProblem], dict[str, list[FindingMember]]]:
    groups: dict[str, list[FindingMember]] = defaultdict(list)
    scopes: dict[str, list[FindingMember]] = defaultdict(list)
    problems: list[ScanProblem] = []
    for document in inventory.text_documents:
        if document.suffix != ".py":
            continue
        try:
            tree = ast.parse(document.text or "", filename=document.relative_path)
        except SyntaxError as exc:
            problems.append(ScanProblem(document.relative_path, f"Python syntax error at line {exc.lineno}"))
            continue

        class Collector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.parents: list[str] = []

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.parents.append(node.name)
                self.generic_visit(node)
                self.parents.pop()

            def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                end_line = int(getattr(node, "end_lineno", node.lineno))
                span = end_line - int(node.lineno) + 1
                member = FindingMember(
                    document.relative_path,
                    ".".join([*self.parents, node.name]),
                    int(node.lineno),
                    span,
                )
                scopes[document.relative_path].append(member)
                if span >= PYTHON_FUNCTION_MIN_LINES:
                    fingerprint = _python_function_fingerprint(node)
                    groups[fingerprint].append(member)
                self.parents.append(node.name)
                self.generic_visit(node)
                self.parents.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

        Collector().visit(tree)
    findings = [
        _finding(
            "python-function",
            fingerprint,
            _disambiguate_member_identities(members),
            "same Python function after local-name normalization",
        )
        for fingerprint, members in groups.items()
        if len(members) > 1
    ]
    return findings, problems, scopes


def _generic_matches(text: str) -> list[tuple[str, int]]:
    newline_offsets = [index for index, character in enumerate(text) if character == "\n"]
    return [
        (match.group(0), bisect.bisect_right(newline_offsets, match.start()) + 1)
        for match in _GENERIC_TOKEN.finditer(text)
        if not match.group(0).startswith(("//", "/*"))
    ]


_KOTLIN_DECLARATION = re.compile(
    r"^(?:(?:public|private|protected|internal|override|open|final|abstract|"
    r"suspend|inline|tailrec|operator|infix|external|const|lateinit|data|sealed|"
    r"value|expect|actual|companion)\s+)*(?:fun|val|var|class|object|interface|"
    r"enum|annotation|typealias|constructor|init)\b"
)


def _kotlin_expression_end(
    tokens: list[tuple[str, int]],
    start: int,
    *,
    declaration_line: int,
    source_lines: list[str],
) -> tuple[int, int]:
    """Return inclusive body end and the next token for a Kotlin ``=`` body."""
    declaration_raw = source_lines[declaration_line - 1] if declaration_line <= len(source_lines) else ""
    declaration_indent = len(declaration_raw) - len(declaration_raw.lstrip())
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    previous_line = declaration_line
    for cursor in range(start, len(tokens)):
        value, line = tokens[cursor]
        at_expression_level = not any(depths.values())
        if cursor > start and at_expression_level and line > previous_line:
            raw = source_lines[line - 1] if line <= len(source_lines) else ""
            stripped = raw.lstrip()
            indent = len(raw) - len(stripped)
            if indent <= declaration_indent and (
                stripped.startswith("}") or _KOTLIN_DECLARATION.match(stripped)
            ):
                return cursor - 1, cursor
        if at_expression_level and value == ";":
            return cursor - 1, cursor + 1
        if value in depths:
            depths[value] += 1
        elif value in closing:
            opener = closing[value]
            if depths[opener] == 0:
                return cursor - 1, cursor
            depths[opener] -= 1
        previous_line = line
    return len(tokens) - 1, len(tokens)


def _canonical_kotlin_body(body: list[str]) -> list[str]:
    mapped: dict[str, str] = {}
    canonical: list[str] = []
    for token_index, value in enumerate(body):
        previous = body[token_index - 1] if token_index else ""
        following = body[token_index + 1] if token_index + 1 < len(body) else ""
        if value.startswith(("\"", "'")):
            canonical.append("<string>")
        elif re.fullmatch(r"\d+(?:\.\d+)?", value):
            canonical.append("<number>")
        elif re.fullmatch(r"[A-Za-z_]\w*", value):
            if (
                value in _GENERIC_KEYWORDS
                or previous in {".", "::", "@"}
                or following == "("
                or (following == "=" and previous in {"(", ","})
                or value[:1].isupper()
            ):
                canonical.append(value)
            else:
                canonical.append(mapped.setdefault(value, f"local_{len(mapped)}"))
        else:
            canonical.append(value)
    return canonical


def _kotlin_brace_owner(tokens: list[tuple[str, int]], brace_index: int) -> str | None:
    """Return the stable enclosing type name introduced by one opening brace."""
    start = brace_index - 1
    while start >= 0 and tokens[start][0] not in {"{", "}", ";"}:
        start -= 1
    segment = [value for value, _line in tokens[start + 1 : brace_index]]
    for index in range(len(segment) - 1, -1, -1):
        value = segment[index]
        if value not in {"class", "interface", "object"}:
            continue
        for candidate in segment[index + 1 :]:
            if re.fullmatch(r"[A-Za-z_]\w*", candidate) and candidate not in _GENERIC_KEYWORDS:
                return candidate
        if value == "object" and index and segment[index - 1] == "companion":
            return "companion"
        return value
    return None


def _kotlin_type_scopes(tokens: list[tuple[str, int]]) -> list[tuple[str, ...]]:
    """Map each token to its enclosing named Kotlin class/object scopes."""
    stack: list[str | None] = []
    result: list[tuple[str, ...]] = []
    for index, (value, _line) in enumerate(tokens):
        result.append(tuple(owner for owner in stack if owner))
        if value == "{":
            stack.append(_kotlin_brace_owner(tokens, index))
        elif value == "}" and stack:
            stack.pop()
    return result


def _kotlin_functions(
    document: SourceDocument,
    *,
    tokens: list[tuple[str, int]] | None = None,
) -> list[tuple[str, FindingMember]]:
    tokens = tokens if tokens is not None else _generic_matches(document.text or "")
    type_scopes = _kotlin_type_scopes(tokens)
    source_lines = (document.text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    results: list[tuple[str, FindingMember]] = []
    index = 0
    while index < len(tokens):
        if tokens[index][0] != "fun":
            index += 1
            continue
        signature = index + 1
        paren = signature
        while paren < len(tokens) and tokens[paren][0] not in {"(", "{", "=", ";"}:
            paren += 1
        if paren >= len(tokens) or tokens[paren][0] != "(":
            index += 1
            continue
        name = next(
            (value for value, _line in reversed(tokens[signature:paren]) if re.fullmatch(r"[A-Za-z_]\w*", value)),
            "function",
        )
        depth = 0
        cursor = paren
        while cursor < len(tokens):
            value = tokens[cursor][0]
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        body_start = cursor + 1
        while body_start < len(tokens) and tokens[body_start][0] not in {"{", "=", ";"}:
            body_start += 1
        if body_start >= len(tokens) or tokens[body_start][0] not in {"{", "="}:
            index += 1
            continue
        if tokens[body_start][0] == "{":
            depth = 0
            body_end = body_start
            while body_end < len(tokens):
                value = tokens[body_end][0]
                if value == "{":
                    depth += 1
                elif value == "}":
                    depth -= 1
                    if depth == 0:
                        break
                body_end += 1
            if body_end >= len(tokens):
                index += 1
                continue
            body_values = [value for value, _line in tokens[body_start + 1 : body_end]]
            next_index = body_end + 1
        else:
            body_end, next_index = _kotlin_expression_end(
                tokens,
                body_start + 1,
                declaration_line=tokens[index][1],
                source_lines=source_lines,
            )
            if body_end < body_start + 1:
                index += 1
                continue
            body_values = [value for value, _line in tokens[body_start + 1 : body_end + 1]]
        start_line = tokens[index][1]
        end_line = tokens[body_end][1]
        if end_line - start_line + 1 >= KOTLIN_FUNCTION_MIN_LINES and len(body_values) >= KOTLIN_FUNCTION_MIN_TOKENS:
            canonical = _canonical_kotlin_body(body_values)
            fingerprint = hashlib.sha256(" ".join(canonical).encode("utf-8")).hexdigest()
            signature = " ".join(value for value, _line in tokens[index:body_start])
            signature_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:8]
            qualified_name = ".".join([*type_scopes[index], name])
            results.append(
                (
                    fingerprint,
                    FindingMember(
                        document.relative_path,
                        f"{qualified_name}#{signature_id}",
                        start_line,
                        end_line - start_line + 1,
                    ),
                )
            )
        index = next_index
    return results


def _kotlin_function_findings(
    inventory: SourceInventory,
    *,
    token_documents: dict[str, list[tuple[str, int]]] | None = None,
) -> tuple[list[RedundancyFinding], dict[str, list[FindingMember]]]:
    groups: dict[str, list[FindingMember]] = defaultdict(list)
    scopes: dict[str, list[FindingMember]] = defaultdict(list)
    for document in inventory.text_documents:
        if document.suffix not in {".kt", ".kts"}:
            continue
        cached_tokens = token_documents.get(document.relative_path) if token_documents is not None else None
        for fingerprint, member in _kotlin_functions(document, tokens=cached_tokens):
            groups[fingerprint].append(member)
            scopes[document.relative_path].append(member)
    findings = [
        _finding("kotlin-function", fingerprint, members, "same Kotlin function after local-name normalization")
        for fingerprint, members in groups.items()
        if len(members) > 1
    ]
    return findings, scopes


def _source_slice(text: str, start: tuple[int, int], end: tuple[int, int]) -> str:
    lines = text.splitlines(keepends=True)
    start_line, start_column = start
    end_line, end_column = end
    if start_line == end_line:
        return lines[start_line - 1][start_column:end_column]
    return "".join(
        [
            lines[start_line - 1][start_column:],
            *lines[start_line : end_line - 1],
            lines[end_line - 1][:end_column],
        ]
    )


def _collapse_python_fstrings(
    raw: list[tokenize.TokenInfo],
    text: str,
) -> list[tokenize.TokenInfo]:
    """Restore the single STRING token emitted before Python 3.12/PEP 701."""
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    if fstring_start is None or fstring_end is None:
        return raw

    collapsed: list[tokenize.TokenInfo] = []
    depth = 0
    first: tokenize.TokenInfo | None = None
    for item in raw:
        if item.type == fstring_start:
            if depth == 0:
                first = item
            depth += 1
            continue
        if depth:
            if item.type == fstring_end:
                depth -= 1
                if depth == 0 and first is not None:
                    collapsed.append(
                        tokenize.TokenInfo(
                            tokenize.STRING,
                            _source_slice(text, first.start, item.end),
                            first.start,
                            item.end,
                            first.line,
                        )
                    )
                    first = None
            continue
        collapsed.append(item)
    return raw if depth else collapsed


def _python_token_variants(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    raw = _collapse_python_fstrings(
        list(tokenize.generate_tokens(io.StringIO(text).readline)),
        text,
    )
    significant = [
        item
        for item in raw
        if item.type
        not in {
            tokenize.ENCODING, tokenize.ENDMARKER, tokenize.INDENT, tokenize.DEDENT,
            tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT,
        }
    ]
    exact: list[tuple[str, int]] = []
    relaxed: list[tuple[str, int]] = []
    for index, item in enumerate(significant):
        exact.append((item.string, item.start[0]))
        value = item.string
        if item.type == tokenize.STRING:
            value = "<string>"
        elif item.type == tokenize.NUMBER:
            value = "<number>"
        elif item.type == tokenize.NAME and not keyword.iskeyword(value):
            previous = significant[index - 1].string if index else ""
            following = significant[index + 1].string if index + 1 < len(significant) else ""
            is_keyword_argument = following == "=" and previous in {"(", ","}
            if (
                previous != "."
                and following != "("
                and not is_keyword_argument
                and not value[:1].isupper()
            ):
                value = "<id>"
        relaxed.append((value, item.start[0]))
    return exact, relaxed


def _generic_token_variants(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    tokens = _generic_matches(text)
    exact = list(tokens)
    relaxed: list[tuple[str, int]] = []
    for index, (raw_value, line) in enumerate(tokens):
        value = raw_value
        if value.startswith(("\"", "'")):
            value = "<string>"
        elif re.fullmatch(r"\d+(?:\.\d+)?", value):
            value = "<number>"
        elif re.fullmatch(r"[A-Za-z_]\w*", value) and value not in _GENERIC_KEYWORDS:
            previous = tokens[index - 1][0] if index else ""
            following = tokens[index + 1][0] if index + 1 < len(tokens) else ""
            is_named_argument = following == "=" and previous in {"(", ","}
            is_object_key = following == ":" and previous in {"{", ","}
            if (
                previous not in {".", "::", "@"}
                and following != "("
                and not is_named_argument
                and not is_object_key
                and not value[:1].isupper()
            ):
                value = "<id>"
        relaxed.append((value, line))
    return exact, relaxed


def _token_documents(
    inventory: SourceInventory,
) -> tuple[
    dict[str, list[tuple[str, int]]],
    dict[str, list[tuple[str, int]]],
    list[ScanProblem],
]:
    exact: dict[str, list[tuple[str, int]]] = {}
    relaxed: dict[str, list[tuple[str, int]]] = {}
    problems: list[ScanProblem] = []
    for document in inventory.text_documents:
        if document.suffix not in _CODE_SUFFIXES:
            continue
        try:
            variants = (
                _python_token_variants(document.text or "")
                if document.suffix == ".py"
                else _generic_token_variants(document.text or "")
            )
        except (tokenize.TokenError, IndentationError) as exc:
            problems.append(ScanProblem(document.relative_path, f"tokenization failed ({type(exc).__name__})"))
            continue
        exact[document.relative_path], relaxed[document.relative_path] = variants
    return exact, relaxed, problems


def _covered_by_function(
    left: FindingMember,
    right: FindingMember,
    function_findings: Iterable[RedundancyFinding],
) -> bool:
    for finding in function_findings:
        left_functions = [
            member
            for member in finding.members
            if member.path == left.path
            and member.line <= left.line < member.line + member.span
        ]
        right_functions = [
            member
            for member in finding.members
            if member.path == right.path
            and member.line <= right.line < member.line + member.span
        ]
        if any(left_function != right_function for left_function in left_functions for right_function in right_functions):
            return True
    return False


def _within_same_function(
    left: FindingMember,
    right: FindingMember,
    function_scopes: dict[str, list[FindingMember]],
) -> bool:
    if left.path != right.path:
        return False
    return any(
        scope.line <= left.line < scope.line + scope.span
        and scope.line <= right.line < scope.line + scope.span
        for scope in function_scopes.get(left.path, ())
    )


def _token_window_placements(
    tokens: list[tuple[str, int]],
    *,
    token_codes: dict[str, int],
) -> Iterable[tuple[str, int, int]]:
    """Yield stable double rolling fingerprints for bounded token windows."""
    if len(tokens) < TOKEN_WINDOW:
        return

    def code(value: str) -> int:
        cached = token_codes.get(value)
        if cached is not None:
            return cached
        digest = hashlib.blake2b(value.encode("utf-8"), digest_size=16).digest()
        result = int.from_bytes(digest, "big")
        # Zero is reserved only to make prefix reasoning easier; replacing its
        # vanishingly unlikely digest with one remains deterministic.
        token_codes[value] = result or 1
        return result or 1

    ids = [code(value) for value, _line in tokens]
    prefix_a = [0]
    prefix_b = [0]
    for token_id in ids:
        prefix_a.append((prefix_a[-1] * _ROLLING_BASE_A + token_id) & _ROLLING_MASK)
        second = ((token_id << 1) | (token_id >> (_ROLLING_BITS - 1))) & _ROLLING_MASK
        prefix_b.append(
            (prefix_b[-1] * _ROLLING_BASE_B + (second ^ _ROLLING_SALT))
            & _ROLLING_MASK
        )
    power_a = pow(_ROLLING_BASE_A, TOKEN_WINDOW, _ROLLING_MODULUS)
    power_b = pow(_ROLLING_BASE_B, TOKEN_WINDOW, _ROLLING_MODULUS)
    for start in range(0, len(tokens) - TOKEN_WINDOW + 1, TOKEN_STEP):
        end = start + TOKEN_WINDOW
        if len(set(ids[start:end])) < 12:
            continue
        first = (prefix_a[end] - prefix_a[start] * power_a) & _ROLLING_MASK
        second = (prefix_b[end] - prefix_b[start] * power_b) & _ROLLING_MASK
        yield f"{first:032x}{second:032x}", tokens[start][1], start


def _token_pair_findings(
    token_documents: dict[str, list[tuple[str, int]]],
    *,
    relaxed: bool,
    function_findings: Iterable[RedundancyFinding],
    function_scopes: dict[str, list[FindingMember]],
    excluded_pairs: set[tuple[str, str]] | None = None,
) -> list[RedundancyFinding]:
    # Keep only the first placement per path and discard fingerprints that are
    # ubiquitous across more than twelve files. Retaining every overlapping
    # occurrence made relaxed scans spend most of their time and memory on
    # generic boilerplate that is intentionally below the reporting boundary.
    placements: dict[str, dict[str, list[tuple[int, int]]] | None] = {}
    token_codes: dict[str, int] = {}
    for relative_path, tokens in token_documents.items():
        for fingerprint, line, start in _token_window_placements(tokens, token_codes=token_codes):
            by_path = placements.get(fingerprint)
            if by_path is None and fingerprint in placements:
                continue
            if by_path is None:
                by_path = {}
                placements[fingerprint] = by_path
            occurrences = by_path.setdefault(relative_path, [])
            if len(occurrences) < 12:
                occurrences.append((start, line))
            if len(by_path) > 12:
                placements[fingerprint] = None

    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    same_file_pairs: dict[tuple[str, int], dict[str, Any]] = {}
    for fingerprint, by_path in placements.items():
        if by_path is None:
            continue
        if len(by_path) >= 2:
            for left_path, right_path in combinations(sorted(by_path), 2):
                pair = (left_path, right_path)
                if excluded_pairs and pair in excluded_pairs:
                    continue
                item = pairs.setdefault(
                    pair,
                    {
                        "fingerprints": set(),
                        "left": FindingMember(left_path, line=by_path[left_path][0][1]),
                        "right": FindingMember(right_path, line=by_path[right_path][0][1]),
                    },
                )
                item["fingerprints"].add(fingerprint)
        for path, occurrences in by_path.items():
            if excluded_pairs and (path, path) in excluded_pairs:
                continue
            for (left_start, left_line), (right_start, right_line) in combinations(occurrences, 2):
                if right_start - left_start < TOKEN_WINDOW:
                    continue
                left_member = FindingMember(path, f"token@{left_start}", left_line)
                right_member = FindingMember(path, f"token@{right_start}", right_line)
                # Renamed local-variable patterns inside one function are often
                # sibling renderer/dispatcher branches (for example icon drawing
                # primitives), not duplicate behavior owners. Exact copies still
                # report, and renamed copies across functions/files still report.
                if relaxed and _within_same_function(left_member, right_member, function_scopes):
                    continue
                item = same_file_pairs.setdefault(
                    (path, right_start - left_start),
                    {
                        "fingerprints": set(),
                        "left": left_member,
                        "right": right_member,
                    },
                )
                item["fingerprints"].add(fingerprint)

    minimum = RENAMED_TOKEN_MIN_WINDOWS if relaxed else EXACT_TOKEN_MIN_WINDOWS
    kind = "renamed-token-block" if relaxed else "exact-token-block"
    findings: list[RedundancyFinding] = []
    for item in [*pairs.values(), *same_file_pairs.values()]:
        fingerprints = sorted(item["fingerprints"])
        if len(fingerprints) < minimum:
            continue
        left = item["left"]
        right = item["right"]
        if _covered_by_function(left, right, function_findings):
            continue
        group_fingerprint = hashlib.sha256("|".join(fingerprints).encode("utf-8")).hexdigest()
        findings.append(
            _finding(
                kind,
                group_fingerprint,
                (left, right),
                (
                    f"{len(fingerprints)} shared {'renamed' if relaxed else 'exact'} token windows"
                    f"{' within one file' if left.path == right.path else ''}"
                ),
            )
        )
    return findings


def _normalized_block_lines(text: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        value = re.sub(r"\s+", " ", raw.strip())
        if not value or value in {"---", "```", "```text", "```powershell", "```python", "```kotlin"}:
            continue
        result.append((value, number))
    return result


def _text_block_findings(inventory: SourceInventory) -> list[RedundancyFinding]:
    placements: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for document in inventory.text_documents:
        # Every decoded non-code file participates, including extensionless
        # scripts and dotfiles. Git discovery already excludes ignored build
        # outputs; binary documents are absent from ``text_documents``.
        if document.suffix in _CODE_SUFFIXES:
            continue
        lines = _normalized_block_lines(document.text or "")
        for start in range(0, len(lines) - TEXT_BLOCK_LINES + 1):
            values = [value for value, _line in lines[start : start + TEXT_BLOCK_LINES]]
            if len(set(values)) < 6 or sum(len(value) for value in values) < TEXT_BLOCK_MIN_CHARS:
                continue
            fingerprint = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()
            placements[fingerprint].append((document.relative_path, lines[start][1]))

    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for fingerprint, locations in placements.items():
        by_path: dict[str, int] = {}
        for path, line in locations:
            by_path.setdefault(path, line)
        if len(by_path) < 2 or len(by_path) > 12:
            continue
        for left_path, right_path in combinations(sorted(by_path), 2):
            item = pairs.setdefault(
                (left_path, right_path),
                {
                    "fingerprints": set(),
                    "left": FindingMember(left_path, line=by_path[left_path]),
                    "right": FindingMember(right_path, line=by_path[right_path]),
                },
            )
            item["fingerprints"].add(fingerprint)

    findings: list[RedundancyFinding] = []
    for item in pairs.values():
        fingerprints = sorted(item["fingerprints"])
        group_fingerprint = hashlib.sha256("|".join(fingerprints).encode("utf-8")).hexdigest()
        findings.append(
            _finding(
                "exact-text-block",
                group_fingerprint,
                (item["left"], item["right"]),
                f"{len(fingerprints)} shared normalized {TEXT_BLOCK_LINES}-line block(s)",
            )
        )
    return findings


def scan_redundancy(inventory: SourceInventory) -> ScanResult:
    findings = _whole_file_findings(inventory)
    python_findings, problems, python_scopes = _python_function_findings(inventory)
    exact_tokens, relaxed_tokens, token_problems = _token_documents(inventory)
    kotlin_findings, kotlin_scopes = _kotlin_function_findings(
        inventory,
        token_documents=exact_tokens,
    )
    function_scopes = dict(python_scopes)
    for path, scopes in kotlin_scopes.items():
        function_scopes.setdefault(path, []).extend(scopes)
    function_findings = [*python_findings, *kotlin_findings]
    findings.extend(function_findings)
    exact_token_findings = _token_pair_findings(
        exact_tokens,
        relaxed=False,
        function_findings=function_findings,
        function_scopes=function_scopes,
    )
    exact_pairs = {tuple(member.path for member in finding.members) for finding in exact_token_findings}
    renamed_token_findings = _token_pair_findings(
        relaxed_tokens,
        relaxed=True,
        function_findings=function_findings,
        function_scopes=function_scopes,
        excluded_pairs=exact_pairs,
    )
    findings.extend(exact_token_findings)
    findings.extend(renamed_token_findings)
    findings.extend(_text_block_findings(inventory))
    problems.extend(token_problems)
    problems.extend(ScanProblem(problem.source, problem.reason) for problem in inventory.problems)
    ordered = tuple(sorted(findings, key=lambda finding: (finding.kind, finding.finding_id)))
    return ScanResult(ordered, tuple(problems))


def load_allowlist(path: Path) -> tuple[dict[str, Allowance], list[ScanProblem]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [ScanProblem(path.as_posix(), "allowlist is missing")]
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [ScanProblem(path.as_posix(), f"allowlist is unreadable ({type(exc).__name__})")]
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("entries"), list):
        return {}, [ScanProblem(path.as_posix(), "allowlist must contain version 1 and an entries list")]
    allowances: dict[str, Allowance] = {}
    problems: list[ScanProblem] = []
    for index, raw in enumerate(payload["entries"]):
        source = f"{path.as_posix()}#entries[{index}]"
        if not isinstance(raw, dict):
            problems.append(ScanProblem(source, "entry must be an object"))
            continue
        values = {
            key: str(raw.get(key) or "").strip()
            for key in ("id", "reason", "behavior_owner", "removal")
        }
        if not all(values.values()):
            problems.append(
                ScanProblem(source, "id, members, reason, behavior_owner, and removal are required")
            )
            continue
        raw_members = raw.get("members")
        if not isinstance(raw_members, list) or any(not isinstance(item, str) for item in raw_members):
            problems.append(ScanProblem(source, "members must be a list of exact path or path::symbol strings"))
            continue
        members = tuple(sorted(item.strip().replace("\\", "/") for item in raw_members if item.strip()))
        if len(members) < 2 or len(set(members)) != len(members):
            problems.append(ScanProblem(source, "members must contain at least two unique source identities"))
            continue
        if not (
            values["removal"].startswith("intentional-permanent: ")
            or values["removal"].startswith("remove-when: ")
        ):
            problems.append(
                ScanProblem(source, "removal must start with intentional-permanent: or remove-when:")
            )
            continue
        if values["id"] in allowances:
            problems.append(ScanProblem(source, f"duplicate finding id {values['id']}"))
            continue
        allowances[values["id"]] = Allowance(
            values["id"], members, values["reason"], values["behavior_owner"], values["removal"]
        )
    return allowances, problems


def evaluate_allowlist(
    findings: Iterable[RedundancyFinding],
    allowances: dict[str, Allowance],
) -> tuple[list[RedundancyFinding], list[ScanProblem]]:
    findings_by_id = {finding.finding_id: finding for finding in findings}
    unapproved: list[RedundancyFinding] = []
    problems: list[ScanProblem] = []
    for finding in findings_by_id.values():
        allowance = allowances.get(finding.finding_id)
        if allowance is None:
            unapproved.append(finding)
            continue
        actual_members = tuple(sorted(_member_identity(member) for member in finding.members))
        if allowance.members != actual_members:
            unapproved.append(finding)
            problems.append(
                ScanProblem(
                    "core/diagnostics/redundancy_allowlist.json",
                    f"allowance membership mismatch for {finding.finding_id}",
                )
            )
    problems.extend(
        ScanProblem("core/diagnostics/redundancy_allowlist.json", f"stale allowance {finding_id}")
        for finding_id in sorted(set(allowances) - set(findings_by_id))
    )
    return sorted(unapproved, key=lambda finding: finding.finding_id), problems


def _is_overlay_finding(finding: RedundancyFinding, inventory: SourceInventory) -> bool:
    origins = {document.relative_path: document.origin for document in inventory.documents}
    return any(origins.get(member.path) == "test-overlay" for member in finding.members)


def _json_payload(
    inventory: SourceInventory,
    result: ScanResult,
    unapproved: list[RedundancyFinding],
    problems: list[ScanProblem],
) -> dict[str, Any]:
    return {
        "inventory": inventory_summary(inventory),
        "findings": [asdict(finding) for finding in result.findings],
        "unapproved": [finding.finding_id for finding in unapproved],
        "problems": [asdict(problem) for problem in problems],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tracked-only", action="store_true", help="exclude new non-ignored files")
    parser.add_argument("--include-test-overlay", action="store_true", help="report ignored maintainer tests")
    parser.add_argument("--allowlist", type=Path)
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    inventory = load_source_inventory(
        root,
        include_untracked=not args.tracked_only,
        include_test_overlay=args.include_test_overlay,
    )
    result = scan_redundancy(inventory)
    public_findings = [finding for finding in result.findings if not _is_overlay_finding(finding, inventory)]
    overlay_findings = [finding for finding in result.findings if _is_overlay_finding(finding, inventory)]
    allowlist_path = (args.allowlist or root / "core/diagnostics/redundancy_allowlist.json").resolve()
    allowances, allowlist_problems = load_allowlist(allowlist_path)
    unapproved, stale_problems = evaluate_allowlist(public_findings, allowances)
    problems = [*result.problems, *allowlist_problems, *stale_problems]

    if args.json:
        print(json.dumps(_json_payload(inventory, result, unapproved, problems), indent=2, sort_keys=True))
    else:
        summary = inventory_summary(inventory)
        print(
            "[redundancy] "
            f"{summary['total']} file(s), {len(public_findings)} public finding(s), "
            f"{len(overlay_findings)} overlay finding(s), {len(unapproved)} unapproved, "
            f"{len(problems)} problem(s)"
        )
        for finding in unapproved:
            print(finding.render())
        for problem in problems:
            print(problem.render())
    return 1 if unapproved or problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
