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
from pathlib import Path
from typing import Any

from .structural_graph import _node_map, _terms, load_or_build_graph_data, project_root

# Standard BM25 tuning constants (Okapi BM25 defaults)
K1 = 1.5
B = 0.75


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens using MO's existing term extractor."""
    return _terms(text)


def _compute_idf(
    doc_tokens_list: list[list[str]],
    query_terms: list[str],
) -> dict[str, float]:
    """Compute inverse document frequency for query terms across the corpus."""
    N = len(doc_tokens_list)
    if N == 0:
        return {}
    df: dict[str, int] = {}
    for doc in doc_tokens_list:
        seen: set[str] = set()
        for token in doc:
            if token not in seen:
                df[token] = df.get(token, 0) + 1
                seen.add(token)

    idf: dict[str, float] = {}
    for term in query_terms:
        n = df.get(term, 0)
        idf[term] = math.log((N - n + 0.5) / (n + 0.5) + 1.0) if n > 0 else 0.0
    return idf


def _bm25_score(
    doc_tokens: list[str],
    doc_len: int,
    avg_doc_len: float,
    query_tokens: list[str],
    idf: dict[str, float],
) -> float:
    """BM25 relevance score for a single document against a query."""
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1

    score = 0.0
    for term in query_tokens:
        if term not in idf:
            continue
        f = tf.get(term, 0)
        if f == 0:
            continue
        numerator = f * (K1 + 1)
        denominator = f + K1 * (1 - B + B * doc_len / max(avg_doc_len, 1.0))
        score += idf[term] * numerator / denominator
    return score


def search(
    query: str,
    *,
    cwd: str | Path | None = None,
    top_n: int = 10,
    min_score: float = 0.1,
) -> list[dict[str, Any]]:
    """Search structural graph nodes with BM25 relevance ranking.

    Returns top-N results as list of dicts with keys:
        id, label, source_file, source_location, score, doc_snippet
    """
    root = project_root(cwd)
    data = load_or_build_graph_data(root)
    if not data:
        return []

    query_terms = _tokenize(query)
    if not query_terms:
        return []

    nodes = _node_map(data)
    # Build documents: one per node, using label + source_file + id as the text body
    doc_pairs: list[tuple[str, str]] = []  # (node_id, text_blob)
    for nid, node in nodes.items():
        label = str(node.get("label") or node.get("name") or "")
        source = str(node.get("source_file") or "")
        text = f"{label} {source} {nid}"
        doc_pairs.append((nid, text))

    doc_tokens_list: list[list[str]] = [_tokenize(text) for _, text in doc_pairs]
    avg_dl = sum(len(dt) for dt in doc_tokens_list) / max(len(doc_tokens_list), 1)
    idf = _compute_idf(doc_tokens_list, query_terms)
    # Unless the query is about tests, de-prioritize test files so product code
    # ranks first (mirrors the structural-graph context scorer's 0.55 weight).
    query_wants_tests = any(t in {"test", "tests", "testing"} for t in query_terms)

    results: list[dict[str, Any]] = []
    for (nid, text), doc_tokens in zip(doc_pairs, doc_tokens_list):
        score = _bm25_score(doc_tokens, len(doc_tokens), avg_dl, query_terms, idf)
        if not query_wants_tests:
            src = str(nodes[nid].get("source_file") or nid).replace("\\", "/")
            if "/tests/" in src or src.startswith("tests/") or "test_" in src.rsplit("/", 1)[-1]:
                score *= 0.55
        if score < min_score:
            continue
        node = nodes[nid]
        results.append({
            "id": nid,
            "label": node.get("label") or node.get("name") or nid,
            "source_file": node.get("source_file") or "",
            "source_location": node.get("source_location") or "",
            "score": round(score, 3),
            "doc_snippet": text[:240],
        })

    results.sort(key=lambda r: (-r["score"], r["id"]))
    return results[:top_n]
