from types import SimpleNamespace

from core.learning.terms_learning import extract_term_definitions, record_terms_learning


class ProfileStub(SimpleNamespace):
    def ensure_operator_profile(self):
        pdir = self._path.parent / "profile"
        pdir.mkdir(parents=True, exist_ok=True)
        terms = pdir / "terms.md"
        if not terms.exists():
            terms.write_text("# Operator Terms\n\n", encoding="utf-8")


def test_extract_terms_requires_explicit_definition():
    assert extract_term_definitions("PRT came up in the conversation") == []

    terms = extract_term_definitions("When I say PRT, mean Project Review Team.")

    assert len(terms) == 1
    assert terms[0].term == "PRT"
    assert terms[0].definition == "Project Review Team"


def test_record_terms_learning_writes_explicit_operator_term(tmp_path):
    profile = ProfileStub(_path=tmp_path / "mo.db")

    changed = record_terms_learning(profile, "When I say PRT, mean Project Review Team.")

    text = (tmp_path / "profile" / "terms.md").read_text(encoding="utf-8")
    assert changed == ["PRT"]
    assert "## Learned Terms" in text
    assert "- **PRT** — Project Review Team" in text


def test_record_terms_learning_updates_existing_term_without_duplicate(tmp_path):
    profile = ProfileStub(_path=tmp_path / "mo.db")

    assert record_terms_learning(profile, "When I say PRT, mean Project Review Team.") == ["PRT"]
    assert record_terms_learning(profile, "When I say PRT, mean Production Readiness Team.") == ["PRT"]

    text = (tmp_path / "profile" / "terms.md").read_text(encoding="utf-8")
    assert text.count("- **PRT**") == 1
    assert "Production Readiness Team" in text
    assert "Project Review Team" not in text


def test_record_terms_learning_blocks_secret_values(tmp_path):
    profile = ProfileStub(_path=tmp_path / "mo.db")

    changed = record_terms_learning(profile, "When I say token, mean api_key=abc123.")

    assert changed == []
    text = (tmp_path / "profile" / "terms.md").read_text(encoding="utf-8")
    assert "api_key" not in text
