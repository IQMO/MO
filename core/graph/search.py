"""BM25 fuzzy search over MO's structural code graph — internal tool for MO.

When MO needs to find files/symbols from a loose natural-language query ("find the
auth logic", "where is rate limiting?"), this ranks graph nodes by BM25 relevance.
Zero dependencies, pure Python math — same algorithm used in Lucene/Elasticsearch.

Exposed to MO as the first-class ``code_search`` tool (tools/__init__.py); the
shell one-liner below is only a manual/debug fallback:
    python -c "from core.graph.search import search; import json; \\
        print(json.dumps(search('auth logic')[:5], indent=2))"
"""
from __future__ import annotations

import math
import re
import threading
from pathlib import Path
from typing import AbstractSet, Any

from .structural_graph import (
    _node_map,
    _source_role,
    _terms,
    load_or_build_graph_data,
    project_root,
)

# Standard BM25 tuning constants (Okapi BM25 defaults)
K1 = 1.5
B = 0.75
_INDEX_LOCK = threading.Lock()
_DOCUMENT_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_QUERY_ATOM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:]{1,127}")
_DIRECT_TARGET_PROHIBITION_RE = re.compile(
    r"""
    \b(?:
        (?:do\s+not|don't|never)\s+
        (?:use|call|invoke|open|touch|modify|change|edit)
        |
        (?:without|avoid)\s+
        (?:using|calling|invoking|opening|touching|modifying|changing|editing)
    )
    \s+(?:the\s+)?[A-Za-z_][A-Za-z0-9_.:-]{1,127}\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_TOKEN_TRANSLATION = str.maketrans("_/.-\\", "     ")
_CONCEPT_FAMILIES = {
    "approval": frozenset({
        "approval", "approve", "approved", "confirm", "confirmed", "confirmation",
    }),
    "authorization": frozenset({
        "authority", "authorization", "authorisation", "authorize", "authorise",
        "authorized", "authorised", "permission", "permit", "permitted",
    }),
    "control": frozenset({
        "actuate", "actuation", "control", "controlling", "drive", "driving",
        "interact", "interaction", "operate", "operating",
    }),
    "request": frozenset({
        "ask", "asked", "asking", "prompt", "prompted", "request", "requested",
        "require", "required", "requirement",
    }),
    "rejection": frozenset({
        "block", "blocked", "blocking", "denial", "denied", "deny", "refuse",
        "refused", "reject", "rejected",
    }),
}
_CONCEPT_TOKEN_BY_TERM = {
    term: f"__concept_{concept}"
    for concept, terms in _CONCEPT_FAMILIES.items()
    for term in terms
}
_EMPTY_IDENTITY_CONCEPTS: frozenset[str] = frozenset()
_GENERIC_SOURCE_SCOPES = frozenset({
    "app", "core", "lib", "project", "source", "src", "test", "tests",
})
_GENERIC_PATH_TERMS = _GENERIC_SOURCE_SCOPES | frozenset({
    "init", "json", "md", "py", "python", "text", "toml", "txt", "yaml", "yml",
})
_SEARCH_INDEX: dict[int, tuple[
    dict[str, dict[str, Any]], dict[str, tuple[str, int, set[str], AbstractSet[str]]],
    dict[str, tuple[tuple[str, int], ...]], float,
]] = {}
_RANK_RESULT_CACHE: dict[
    tuple[int, str, float, bool],
    tuple[int, tuple[dict[str, Any], ...]],
] = {}


def clear_search_index() -> None:
    """Release the BM25 index when its source graph object is replaced."""
    with _INDEX_LOCK:
        _SEARCH_INDEX.clear()
        _RANK_RESULT_CACHE.clear()


def exact_file_node_id(nodes: dict[str, dict[str, Any]], query: str) -> str:
    """Return the exact file node named by a plain or ``file:`` path query."""
    raw = str(query or "").strip().lower().replace("\\", "/")
    if not raw or any(char.isspace() for char in raw):
        return ""
    candidate = raw if raw.startswith("file:") else f"file:{raw}"
    if candidate in nodes:
        return candidate
    return next((node_id for node_id in nodes if node_id.lower() == candidate), "")


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens using MO's existing term extractor."""
    normalized = _without_prohibited_direct_targets(text).translate(_TOKEN_TRANSLATION)
    terms = _terms(normalized)
    # Structural-context stopwords include action words such as ``build``. Those
    # are noise in prose but are often the most important part of a code symbol.
    # BM25 already down-weights common corpus terms, so retain the raw query words.
    for term in _document_tokens(normalized):
        if term not in terms:
            terms.append(term)
    return _with_concept_tokens(terms, limit=48)


def _without_prohibited_direct_targets(text: str) -> str:
    """Remove explicit tool/path prohibitions from positive search evidence.

    The graph is a relevance ranker rather than a semantic instruction parser.
    Leaving a symbol-like target in phrases such as ``do not use MCP`` gives
    that prohibited target the strongest exact-atom boost. Keep this narrow to
    direct action verbs so statements such as ``do not trust tests as proof``
    can still use tests as requested supporting scope.
    """
    return _DIRECT_TARGET_PROHIBITION_RE.sub(" ", str(text or ""))


def _document_tokens(text: str) -> list[str]:
    """Fast corpus tokenizer; query expansion still uses MO's richer term extractor."""
    text = str(text or "")
    normalized = text.lower().translate(_TOKEN_TRANSLATION)
    # Preserve identifier atoms and expose their words too. Otherwise names
    # such as EncryptedDraftStore never meet a query about encrypted drafts.
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
    if expanded != text:
        normalized += " " + expanded.lower().translate(_TOKEN_TRANSLATION)
    return _with_concept_tokens(_DOCUMENT_TOKEN_RE.findall(normalized))


def _with_concept_tokens(tokens: list[str], *, limit: int | None = None) -> list[str]:
    """Add a small deterministic vocabulary bridge without a second search index.

    The structural graph deliberately stores identifiers and bounded code
    vocabulary rather than whole function bodies or embeddings.  Natural issue
    reports therefore need a few stable code concepts to meet their common
    implementation names (for example ``permission`` -> ``authorization`` and
    ``approval`` -> ``confirmation``).  Synthetic tokens preserve every raw
    term and let BM25 keep corpus-frequency weighting; this is not an unbounded
    synonym list or a semantic source of truth.
    """
    expanded: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        term = str(raw or "").lower().strip()
        if not term:
            continue
        for value in (term, _CONCEPT_TOKEN_BY_TERM.get(term, "")):
            if value and value not in seen:
                seen.add(value)
                expanded.append(value)
                if limit is not None and len(expanded) >= limit:
                    return expanded
    return expanded


def _query_atoms(text: str) -> tuple[str, ...]:
    """Return explicit symbol-like chunks without flattening their identity.

    BM25 still handles loose prose. These atoms preserve names such as
    ``ReplyBubble`` and ``_on_assistant_text`` when they appear inside a larger
    review query, so one long query does not erase its explicitly named symbols.
    """
    atoms: list[str] = []
    for match in _QUERY_ATOM_RE.finditer(_without_prohibited_direct_targets(text)):
        # ``.`` and ``:`` are valid inside qualified names, but ordinary prose
        # punctuation at the end must not turn every sentence-final word into
        # an apparent explicit symbol.
        value = match.group(0).rstrip(".:")
        if len(value) < 2:
            continue
        if len(value) == 2 and value.isupper():
            # Short product/acronym prose (notably MO) is too ambiguous for an
            # exact-symbol boost and can otherwise match unrelated suffixes
            # such as the ``.mo`` private-home path. BM25 still retains it.
            continue
        if not (
            any(marker in value for marker in ("_", ".", ":"))
            or any(char.isupper() for char in value[1:])
        ):
            continue
        lowered = value.lower()
        if lowered not in atoms:
            atoms.append(lowered)
    return tuple(atoms[:24])


def _search_index(data: dict[str, Any]) -> tuple[
    dict[str, dict[str, Any]], dict[str, tuple[str, int, set[str], AbstractSet[str]]],
    dict[str, tuple[tuple[str, int], ...]], float,
]:
    """Build one compact immutable corpus per loaded graph object.

    Posting lists use tuples instead of a dict for every term. That avoids the
    largest Python-object multiplier in a long-lived graph server while keeping
    query-time work bounded to the terms that were actually requested.
    """
    key = id(data)
    with _INDEX_LOCK:
        cached = _SEARCH_INDEX.get(key)
        if cached is not None:
            return cached
    nodes = _node_map(data)
    docs: dict[str, tuple[str, int, set[str], AbstractSet[str]]] = {}
    mutable_postings: dict[str, list[tuple[str, int]]] = {}
    total_tokens = 0
    for nid, node in nodes.items():
        raw_search_terms = node.get("search_terms") or ""
        search_text = (
            " ".join(str(term) for term in raw_search_terms[:18])
            if isinstance(raw_search_terms, list)
            else str(raw_search_terms)[:160]
        )
        text = " ".join(str(node.get(field) or "") for field in (
            "label", "short_name", "qualified_name", "source_file", "summary"
        )) + " " + search_text + f" {nid}"
        tokens = _document_tokens(text)
        identity_names = {
            str(node.get("label") or "").lower(),
            str(node.get("short_name") or "").lower(),
            str(node.get("qualified_name") or "").lower(),
        }
        names = {*identity_names, nid.lower()}
        identity_concepts: AbstractSet[str] = {
            token.removeprefix("__concept_")
            for name in identity_names
            for token in _document_tokens(name)
            if token.startswith("__concept_")
        }
        if not identity_concepts:
            identity_concepts = _EMPTY_IDENTITY_CONCEPTS
        docs[nid] = (text, len(tokens), names, identity_concepts)
        total_tokens += len(tokens)
        # The tokenizer already emits each raw/concept token once.
        for token in tokens:
            mutable_postings.setdefault(token, []).append((nid, 1))
    postings = {
        token: tuple(rows)
        for token, rows in mutable_postings.items()
    }
    result = (nodes, docs, postings, total_tokens / max(len(docs), 1))
    with _INDEX_LOCK:
        _SEARCH_INDEX.clear()
        _RANK_RESULT_CACHE.clear()
        _SEARCH_INDEX[key] = result
    return result


def _copy_ranked_nodes(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the one-result cache private from caller mutation."""
    return [
        {
            key: list(value) if isinstance(value, list) else value
            for key, value in row.items()
        }
        for row in rows
    ]


def rank_graph_nodes(
    data: dict[str, Any],
    query: str,
    *,
    top_n: int = 10,
    min_score: float = 0.1,
    distinct_sources: bool = False,
) -> list[dict[str, Any]]:
    """Rank nodes from one already-loaded graph with the shared BM25 index.

    Structural context and symbol-query fallbacks call this helper directly so
    they reuse the same graph object and in-process index instead of maintaining
    a second flat scorer or reloading the cache. ``distinct_sources`` keeps only
    the highest-ranked node for each owning file.
    """
    query_terms = _tokenize(query)
    if not query_terms:
        return []
    raw_query_terms = set(_document_tokens(_without_prohibited_direct_targets(query)))
    lexical_query_terms = {
        term for term in raw_query_terms
        if len(term) >= 3
        and not term.startswith("__concept_")
        and term not in _GENERIC_PATH_TERMS
        and term not in {"and", "for", "from", "our", "the", "with"}
    }

    nodes, docs, postings, avg_dl = _search_index(data)
    available_source_scopes = {
        str(node.get("source_file") or "").replace("\\", "/").split("/", 1)[0].casefold()
        for node in nodes.values()
        if "/" in str(node.get("source_file") or "").replace("\\", "/")
    }
    requested_source_scopes = (
        set(query_terms) & available_source_scopes
    ) - _GENERIC_SOURCE_SCOPES
    cache_key = (id(data), str(query or ""), float(min_score), distinct_sources)
    with _INDEX_LOCK:
        cached = _RANK_RESULT_CACHE.get(cache_key)
    if cached is not None:
        requested, rows = cached
        if requested >= top_n or len(rows) < requested:
            return _copy_ranked_nodes(rows[:top_n])

    total_docs = len(docs)
    idf = {
        term: math.log(
            (total_docs - len(postings.get(term, ())) + 0.5)
            / (len(postings.get(term, ())) + 0.5)
            + 1.0
        )
        if postings.get(term) else 0.0
        for term in query_terms
    }
    # Tests and documentation are supporting evidence. A query that names them
    # raises their relevance without allowing them to displace product owners.
    query_wants_tests = any(t in {"test", "tests", "testing"} for t in query_terms)
    query_wants_docs = any(t in {"doc", "docs", "documentation", "readme"} for t in query_terms)

    results: list[dict[str, Any]] = []
    raw = _without_prohibited_direct_targets(query).strip().lower()
    query_atoms = _query_atoms(query)
    bm25_scores: dict[str, float] = {}
    concept_matches: dict[str, set[str]] = {}
    for term in query_terms:
        term_idf = idf[term]
        for nid, frequency in postings.get(term, ()):
            doc_len = docs[nid][1]
            numerator = frequency * (K1 + 1)
            denominator = frequency + K1 * (1 - B + B * doc_len / max(avg_dl, 1.0))
            bm25_scores[nid] = bm25_scores.get(nid, 0.0) + term_idf * numerator / denominator
            if term.startswith("__concept_"):
                concept_matches.setdefault(nid, set()).add(term.removeprefix("__concept_"))
    candidate_ids = set(bm25_scores)
    candidate_ids.update(
        nid for nid, (_text, _length, names, _identity_concepts) in docs.items()
        if raw in names or (raw and any(raw in name for name in names))
    )
    for nid in candidate_ids:
        text, _doc_len, names, identity_concepts = docs[nid]
        score = bm25_scores.get(nid, 0.0)
        node = nodes[nid]
        match_type = "terms"
        matched_concepts = sorted(concept_matches.get(nid, set()))
        matched_name_concepts = sorted(set(matched_concepts) & identity_concepts)
        if matched_concepts:
            # Natural issue reports often contain implementation-equivalent
            # concepts but little literal source vocabulary. Rewarding distinct
            # concept coverage makes one node connecting approval + request beat
            # several files that happen to repeat a lone word such as "drive".
            # Test-file weighting below still keeps product owners ahead.
            score += 8.0 * len(matched_concepts)
            score += 16.0 * max(0, len(matched_concepts) - 1)
        if matched_name_concepts:
            # Identity evidence is stronger than a term found only in a bounded
            # function-body vocabulary or file summary. This keeps natural
            # policy questions on symbols that actually name the intersection
            # (for example request + confirmation), not incidental consumers.
            score += 8.0 * len(matched_name_concepts)
            score += 8.0 * max(0, len(matched_name_concepts) - 1)
        matched_atoms = tuple(atom for atom in query_atoms if atom in names)
        suffix_atoms = tuple(
            atom for atom in query_atoms
            if atom not in matched_atoms
            and any(
                name.endswith((f"_{atom}", f".{atom}", f":{atom}"))
                for name in names
            )
        )
        partial_atoms = tuple(
            atom for atom in query_atoms
            if atom not in matched_atoms
            and atom not in suffix_atoms
            and any(atom in name for name in names)
        )
        raw_behavior_terms = node.get("search_terms") or node.get("searchTerms") or ""
        behavior_terms = {
            str(term).casefold()
            for term in (
                raw_behavior_terms
                if isinstance(raw_behavior_terms, list)
                else str(raw_behavior_terms).split()
            )
            if str(term).strip()
        }
        behavior_atoms = tuple(
            atom for atom in query_atoms
            if atom not in matched_atoms
            and atom not in suffix_atoms
            and atom in behavior_terms
        )
        if raw in names:
            score += 50.0
            match_type = "exact"
        elif raw and any(raw in name for name in names):
            score += 10.0
            match_type = "substring"
        elif matched_atoms:
            # Exact symbols named inside a broader query outrank helpers whose
            # qualified names merely contain one of those symbols.
            score += 30.0 * len(matched_atoms)
            match_type = "symbol_atom"
        elif suffix_atoms:
            # Tool names commonly map to an execution verb plus the public
            # name (``code_search`` -> ``execute_code_search``). That suffix is
            # stronger ownership evidence than an arbitrary substring while
            # remaining weaker than an exact symbol.
            score += 20.0 * len(suffix_atoms)
            match_type = "symbol_suffix"
        elif behavior_atoms:
            # Bounded symbol/file vocabulary is implementation evidence, but
            # remains weaker than the exact public symbol or its executor
            # suffix. File-level registry keys avoid one graph node per entry.
            score += 15.0 * len(behavior_atoms)
            match_type = "behavior_atom"
        elif partial_atoms:
            score += 5.0 * len(partial_atoms)
        src = str(nodes[nid].get("source_file") or nid).replace("\\", "/")
        source_scope = src.split("/", 1)[0].casefold() if "/" in src else ""
        path_terms = set(_DOCUMENT_TOKEN_RE.findall(src.lower().translate(_TOKEN_TRANSLATION)))
        path_terms.discard(source_scope)
        matched_path_terms = sorted(lexical_query_terms & path_terms)
        if matched_path_terms:
            # A word in the owning path is stronger direction than the same
            # word appearing incidentally in a summary. Weight rare path words
            # more heavily while keeping exact symbols and files authoritative.
            score += sum(
                min(16.0, 6.0 + (2.0 * idf.get(term, 0.0)))
                for term in matched_path_terms
            )
        matched_source_scopes = (
            [source_scope] if source_scope in requested_source_scopes else []
        )
        if matched_source_scopes:
            # An explicitly named top-level product surface (for example
            # ``interface``) is concrete project direction. Give that owner a
            # bounded path boost without treating generic roots such as
            # ``core`` or ``tests`` as equally specific intent.
            score += 10.0
        source_role = _source_role(src)
        if source_role == "verification":
            score *= 0.70 if query_wants_tests else 0.55
        elif match_type == "terms" and source_role == "documentation":
            score *= 0.70 if query_wants_docs else 0.40
        if score < min_score:
            continue
        results.append({
            "id": nid,
            "label": node.get("label") or node.get("name") or nid,
            "source_file": node.get("source_file") or "",
            "source_location": node.get("source_location") or "",
            "score": round(score, 3),
            "match_type": match_type,
            "matched_atoms": list(matched_atoms),
            "matched_suffix_atoms": list(suffix_atoms),
            "matched_behavior_atoms": list(behavior_atoms),
            "matched_source_scopes": matched_source_scopes,
            "matched_path_terms": matched_path_terms,
            "matched_concepts": matched_concepts,
            "matched_name_concepts": matched_name_concepts,
            "doc_snippet": text[:240],
        })

    results.sort(key=lambda r: (-r["score"], r["id"]))
    direct_file_id = exact_file_node_id(nodes, query)
    if direct_file_id:
        direct = next((item for item in results if item["id"] == direct_file_id), None)
        if direct is None:
            node = nodes[direct_file_id]
            text, _length, _names, _identity_concepts = docs[direct_file_id]
            direct = {
                "id": direct_file_id,
                "label": node.get("label") or node.get("name") or direct_file_id,
                "source_file": node.get("source_file") or "",
                "source_location": node.get("source_location") or "",
                "score": 100.0,
                "match_type": "exact",
                "doc_snippet": text[:240],
            }
        else:
            direct["score"] = max(float(direct.get("score") or 0.0), 100.0)
            direct["match_type"] = "exact"
        results = [direct, *(item for item in results if item["id"] != direct_file_id)]
    if distinct_sources:
        grouped: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for row in results:
            source = str(row.get("source_file") or row.get("id") or "").replace("\\", "/").casefold()
            if source in seen_sources:
                continue
            seen_sources.add(source)
            grouped.append(row)
        results = grouped
    ranked = results[:top_n]
    cached_rows = tuple(_copy_ranked_nodes(ranked))
    with _INDEX_LOCK:
        previous = _RANK_RESULT_CACHE.get(cache_key)
        if previous is None or top_n >= previous[0]:
            _RANK_RESULT_CACHE.clear()
            _RANK_RESULT_CACHE[cache_key] = (top_n, cached_rows)
    return ranked


def search(
    query: str,
    *,
    cwd: str | Path | None = None,
    top_n: int = 10,
    min_score: float = 0.1,
) -> list[dict[str, Any]]:
    """Search for distinct source owners with BM25 relevance ranking.

    Each owner retains its best matching graph node as a symbol-level focus.
    Returns top-N results as list of dicts with keys:
        id, label, source_file, source_location, score, doc_snippet
    """
    root = project_root(cwd)
    data = load_or_build_graph_data(root, build_if_missing=False, refresh_if_stale=False)
    if not data:
        return []
    return rank_graph_nodes(
        data,
        query,
        top_n=top_n,
        min_score=min_score,
        distinct_sources=True,
    )
