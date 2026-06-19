"""Semantic memory recall via an optional embedder, with keyword fallback."""
from core.learning.embeddings import cosine, build_embedder
from core.learning.memory import EpisodicMemory


# Deterministic fake embedder: maps text to a 3-dim concept vector by synonym buckets,
# so a query worded DIFFERENTLY from a turn can still match it by meaning.
def _fake_embed(text: str) -> list[float]:
    t = str(text).lower()
    auth = sum(t.count(w) for w in ("auth", "login", "credential", "password", "sign-in"))
    pay = sum(t.count(w) for w in ("payment", "billing", "invoice", "charge"))
    deploy = sum(t.count(w) for w in ("deploy", "release", "ship", "vps"))
    return [float(auth), float(pay), float(deploy)]


def test_cosine():
    assert cosine([1, 0, 0], [1, 0, 0]) == 1.0
    assert cosine([1, 0, 0], [0, 1, 0]) == 0.0
    assert cosine([], [1]) == 0.0
    assert cosine([0, 0], [0, 0]) == 0.0


def test_build_embedder_off_by_default():
    assert build_embedder({}) is None
    assert build_embedder({"embeddings": {"enabled": False}}) is None
    # enabled but unconfigured → still None (no base_url/model)
    assert build_embedder({"embeddings": {"enabled": True}}) is None


def test_build_embedder_configured():
    emb = build_embedder({"embeddings": {"enabled": True, "base_url": "http://x/v1", "model": "m"}})
    assert callable(emb)


def test_semantic_recall_matches_by_meaning(tmp_path):
    mem = EpisodicMemory(path=tmp_path / "m.sqlite", embedder=_fake_embed)
    mem.index_turn("auth", "the authentication flow validates the password on sign-in",
                   "We fixed the credential check in the login handler.")
    mem.index_turn("pay", "process the billing invoice and charge the card",
                   "Payment posted to the billing ledger successfully.")
    # Query uses DIFFERENT words ("login credentials") than the auth turn — semantic match.
    out = mem.recall("how do I handle user login credentials", limit=1)
    assert out and out[0]["turn_id"] == "auth"


def test_falls_back_to_keyword_when_embedder_raises(tmp_path):
    def boom(_text):
        raise RuntimeError("embed endpoint down")

    mem = EpisodicMemory(path=tmp_path / "m.sqlite", embedder=boom)
    # index_turn must not crash, and recall must fall back to keyword search.
    mem.index_turn("a", "explain the taskboard contract gate",
                   "The contract gate enforces evidence before a board closes.")
    out = mem.recall("taskboard contract gate", limit=3)
    assert any(r["turn_id"] == "a" for r in out)


def test_no_embedder_uses_keyword(tmp_path):
    mem = EpisodicMemory(path=tmp_path / "m.sqlite")  # embedder=None
    mem.index_turn("a", "deepseek provider api key configuration",
                   "Set api_key_env in the provider block.")
    out = mem.recall("provider api key", limit=3)
    assert any(r["turn_id"] == "a" for r in out)
