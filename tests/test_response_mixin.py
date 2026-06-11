from interface.response_mixin import ResponseMixin


class ResponseHarness(ResponseMixin):
    def __init__(self):
        self.lines = []

    def _add_fragments_line(self, fragments):
        self.lines.append(fragments)


def _plain(lines):
    return ["".join(text for _style, text in fragments) for fragments in lines]


def test_response_mixin_preserves_proposal_text_filtering():
    proposal = "Proposal: Build it\nPlan:\n- hidden"

    assert ResponseHarness._proposal_chat_text(proposal) == "Build it"


def test_response_mixin_appends_response_block_fragments():
    harness = ResponseHarness()

    harness._add_response_block("Done:\n- alpha beta gamma")

    assert _plain(harness.lines)[0] == "* Done:"
    assert _plain(harness.lines)[1].startswith("  - alpha beta")
