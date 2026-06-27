"""Memory recall benchmark — measure MO's episodic recall quality (recall@K).

MO is a personalization-first agent: it must *know* how well it recalls what the
operator said, not assume it. This builds a small, reproducible operator-style
corpus, runs `EpisodicMemory.recall` over paraphrased queries, and reports
recall@1/3/5 and MRR — establishing a tracked number that improvements (e.g.
hybrid semantic+keyword fusion) can be measured against.

Standalone:  python -m core.learning.recall_benchmark
Programmatic: run_recall_benchmark() -> dict
The default run uses NO embedder (the always-available bm25 keyword path), so the
number is deterministic and dependency-free. Pass an embedder to score the
semantic path.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .memory import EpisodicMemory

# Each entry: (turn_id, operator statement, MO ack, [paraphrased queries that
# should retrieve this turn]). Queries deliberately differ in wording from the
# stored turn (real retrieval, not exact match) while sharing key content terms.
_RELEVANT: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("deploy", "I deploy MO by pushing to origin then ssh into the VPS and restarting the mo-agent service",
     "Noted: deploy = push origin, ssh VPS, restart mo-agent.",
     ("what are the steps to ship MO to the VPS", "how do I deploy to production")),
    ("main-project", "My main project is the trading platform running on the production server",
     "Got it — primary project is the trading platform.",
     ("what is my primary project", "which project do I care most about")),
    ("editor", "I always use neovim, never vscode, for editing code",
     "Understood: neovim is your editor of choice.",
     ("which code editor do I prefer", "what do I use to edit files")),
    ("tone", "Keep your answers short and direct, I dislike long hedging explanations",
     "Will keep responses short and direct.",
     ("how do I like you to respond", "what response style do I want")),
    ("tests", "Run the suite with pytest dash q and use minus n auto for the full parallel run",
     "Noted the pytest invocation for the suite.",
     ("how should I run the test suite", "what command runs all the tests")),
    ("python-only", "This project has no node, use python for all scripting tasks",
     "Understood: python for all scripting, no node.",
     ("what language do I script in", "can I use node in this project")),
    ("db", "The learning data lives in a sqlite database under the dot mo memory folder",
     "Got it — learning data is sqlite under ~/.mo/memory.",
     ("where is the learning data stored", "what database holds memory")),
    ("review-pref", "Before any commit I want the diff reviewed for correctness and duplication",
     "Will review diffs for correctness and duplication before commits.",
     ("what do I want checked before committing", "how should code be reviewed pre-commit")),
    ("timezone", "Stamp everything in local time, never utc, it breaks my ordering",
     "Noted: local-time stamps, never UTC.",
     ("what timezone for timestamps", "should stamps be utc or local")),
    ("privacy", "Profile-owned extension files must never be pushed to the public repo",
     "Understood: profile-owned extension content stays private, never pushed.",
     ("what must stay out of the public repo", "rules for private profile files")),
    ("branch", "Always branch before committing when on the default main branch",
     "Will branch first before committing on main.",
     ("commit workflow on the main branch", "do I commit directly to main")),
    ("secrets", "Never print secret values, only report presence or validity",
     "Understood: never print secrets, report presence only.",
     ("how should secrets be handled", "can you show me a credential value")),
    ("graph", "Use the structural code graph to find files before broad grep sweeps",
     "Noted: code graph before broad grep.",
     ("how to locate files efficiently", "what to use before grepping the tree")),
    ("verify", "Verify every claim against live state, never report done from assumption",
     "Will verify against live state before any done claim.",
     ("rule about reporting done", "how do I want claims verified")),
    ("backups", "Keep backups of important bundles under the E backups folder on disk",
     "Noted: backups go under E:\\backups.",
     ("where do backups go", "location for important backup bundles")),
)

# Distractor turns: plausible but unrelated, so recall is non-trivial.
_DISTRACTORS: tuple[tuple[str, str], ...] = tuple(
    (f"the weather discussion number {i} about clouds and rain and forecasts and seasons", "ack distractor weather")
    for i in range(20)
) + tuple(
    (f"a recipe note number {i} about pasta tomatoes basil and olive oil cooking", "ack distractor recipe")
    for i in range(20)
)


def _seed(memory: EpisodicMemory) -> None:
    for tid, user, asst, _q in _RELEVANT:
        memory.index_turn(tid, user, asst)
    for i, (user, asst) in enumerate(_DISTRACTORS):
        memory.index_turn(f"distractor-{i}", user, asst)


def run_recall_benchmark(
    embedder: Callable[[str], list[float]] | None = None,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict:
    """Seed a throwaway corpus, run every paraphrased query, return recall@K + MRR."""
    max_k = max(ks)
    with tempfile.TemporaryDirectory() as tmp:
        memory = EpisodicMemory(path=Path(tmp) / "bench.sqlite", embedder=embedder)
        _seed(memory)

        queries: list[tuple[str, str]] = []
        for tid, _u, _a, qs in _RELEVANT:
            for q in qs:
                queries.append((q, tid))

        hits_at = {k: 0 for k in ks}
        rr_total = 0.0
        misses: list[str] = []
        for query, expected in queries:
            results = memory.recall(query, limit=max_k)
            ranks = [r.get("turn_id") for r in results]
            rank = ranks.index(expected) + 1 if expected in ranks else 0
            if rank:
                rr_total += 1.0 / rank
            else:
                misses.append(query)
            for k in ks:
                if rank and rank <= k:
                    hits_at[k] += 1

        n = len(queries)
        return {
            "queries": n,
            "corpus_turns": len(_RELEVANT) + len(_DISTRACTORS),
            "mode": "hybrid" if embedder else "bm25",  # recall() fuses semantic+bm25 when an embedder is present
            "recall_at": {k: round(hits_at[k] / n, 3) for k in ks},
            "mrr": round(rr_total / n, 3),
            "misses": misses,
        }


# --- Controlled concept-embedder (stand-in for a real embedding model) ----------
# fastembed/API embeddings are not always present in CI, so this maps text to a
# multi-hot vector over synonym groups drawn from the corpus. It is NOT a real
# semantic model — it exists only to DEMONSTRATE that the hybrid fusion path
# recovers the lexical-gap paraphrases bm25 misses. The production lift number
# comes from running with embeddings enabled (fastembed/API).
_CONCEPTS: tuple[frozenset[str], ...] = tuple(frozenset(g.split()) for g in (
    "deploy ship shipping production vps origin ssh restart mo-agent",
    "project main primary trading platform server",
    "editor neovim vscode edit editing",
    "respond response answer answers style tone short direct hedging",
    "test tests suite pytest parallel",
    "language python node script scripting scripts",
    "data database sqlite learning memory store stored",
    "review reviewed diff correctness duplication commit committing",
    "time timezone timestamp timestamps stamp stamps utc local",
    "private owner public repo push pushed",
    "branch main commit committing",
    "secret secrets credential key value values print presence",
    "graph grep grepping file files locate find",
    "verify verified claim claims done assumption live",
    "backup backups bundle bundles folder",
))


def concept_embedder():
    """Return a deterministic embedder mapping text -> multi-hot concept vector."""
    import re as _re

    def embed(text: str) -> list[float]:
        toks = set(_re.findall(r"[a-z0-9-]+", str(text or "").lower()))
        return [1.0 if (g & toks) else 0.0 for g in _CONCEPTS]

    return embed


def main() -> None:
    base = run_recall_benchmark()
    demo = run_recall_benchmark(embedder=concept_embedder())
    print(f"MO memory recall benchmark — {base['queries']} queries over {base['corpus_turns']} turns\n")
    print(f"  {'metric':<10} {'bm25 (real)':>12} {'hybrid (demo)':>14}")
    for k in base["recall_at"]:
        print(f"  recall@{k:<3} {base['recall_at'][k]:>12} {demo['recall_at'][k]:>14}")
    print(f"  {'MRR':<10} {base['mrr']:>12} {demo['mrr']:>14}")
    print(f"\n  bm25 misses recovered by fusion: {len(base['misses']) - len(demo['misses'])} of {len(base['misses'])}")
    print("  (hybrid column uses a controlled concept-embedder; production number = run with fastembed/API embeddings)")


if __name__ == "__main__":
    main()
