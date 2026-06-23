"""Tests for core/agent_utils.py."""
from __future__ import annotations

import pytest
from core.agent.agent_utils import (
    TurnCancelled,
    _call_on_first_tool,
    _looks_like_identity_question,
    _looks_like_term_lookup,
    WORKFLOW_ADOPTION_RE,
    WORKFLOW_APPROVAL_RE,
    URL_RE,
    WORKFLOW_SOURCE_PATH_RE,
)


class TestTurnCancelled:
    def test_is_exception(self):
        assert issubclass(TurnCancelled, Exception)

    def test_can_raise_and_catch(self):
        with pytest.raises(TurnCancelled):
            raise TurnCancelled("abort requested")

    def test_message_preserved(self):
        exc = TurnCancelled("stop turn")
        assert str(exc) == "stop turn"


class TestCallOnFirstTool:
    def test_no_arg_callback(self):
        called = []
        def cb():
            called.append(1)
            return "ok"
        result = _call_on_first_tool(cb, "read_file", {"path": "x"})
        assert result == "ok"
        assert called == [1]

    def test_one_positional_callback(self):
        def cb(tool_name):
            return tool_name
        result = _call_on_first_tool(cb, "shell", {"cmd": "ls"})
        assert result == "shell"

    def test_two_positional_callback(self):
        def cb(tool_name, arguments):
            return (tool_name, arguments)
        result = _call_on_first_tool(cb, "edit_file", {"old": "a", "new": "b"})
        assert result == ("edit_file", {"old": "a", "new": "b"})

    def test_varargs_callback(self):
        def cb(*args):
            return args
        result = _call_on_first_tool(cb, "grep", {"pattern": "x"})
        assert result == ("grep", {"pattern": "x"})

    def test_callback_raises_typeerror_propagates(self):
        def cb(a, b, c):
            return (a, b, c)
        # _call_on_first_tool catches TypeError from the 2-arg call,
        # then falls through to callback() with 0 args, which raises again.
        with pytest.raises(TypeError):
            _call_on_first_tool(cb, "x", {"y": 1})

    def test_keyword_only_params_raises_typeerror(self):
        def cb(*, name):
            return name
        with pytest.raises(TypeError):
            _call_on_first_tool(cb, "x", {})


class TestLooksLikeTermLookup:
    def test_what_does_x_mean(self):
        assert _looks_like_term_lookup("what does SFF mean")

    def test_define_term(self):
        assert _looks_like_term_lookup("define the term handoff")

    def test_remind_me_definition(self):
        # "remind me what is the definition of X" — matches pattern
        assert _looks_like_term_lookup("remind me what is the definition of PRT")

    def test_meaning_of_term(self):
        # "meaning of" followed by a definition word
        assert _looks_like_term_lookup("meaning of the term handoff")

    def test_not_term_lookup_build_request(self):
        assert not _looks_like_term_lookup("build a new CLI parser")

    def test_not_term_lookup_fix_request(self):
        assert not _looks_like_term_lookup("fix the login bug in auth.py")

    def test_empty_string(self):
        assert not _looks_like_term_lookup("")

    def test_none_input(self):
        assert not _looks_like_term_lookup(None)


class TestLooksLikeIdentityQuestion:
    """Identity questions must pull the operator profile into context —
    observed live: without this, 'what do you know about me?' cost 4 provider
    round-trips re-reading profile files."""

    def test_what_do_you_know_about_me(self):
        assert _looks_like_identity_question("what do you know about me ?")

    def test_who_am_i(self):
        assert _looks_like_identity_question("who am I")

    def test_my_profile(self):
        assert _looks_like_identity_question("show my profile")

    def test_my_preferences(self):
        assert _looks_like_identity_question("what are my preferences")

    def test_who_are_you(self):
        assert _looks_like_identity_question("who are you?")

    def test_about_yourself(self):
        assert _looks_like_identity_question("tell me about yourself")

    def test_remember_me(self):
        assert _looks_like_identity_question("do you remember me")

    def test_not_identity_build_request(self):
        assert not _looks_like_identity_question("build a new CLI parser")

    def test_not_identity_casual_greeting(self):
        assert not _looks_like_identity_question("hi")

    def test_not_identity_file_question(self):
        assert not _looks_like_identity_question("what does session.py do")

    def test_empty_and_none(self):
        assert not _looks_like_identity_question("")
        assert not _looks_like_identity_question(None)


class TestRegexPatterns:
    def test_workflow_adoption_re_matches(self):
        assert WORKFLOW_ADOPTION_RE.search("adopt this workflow for code reviews")

    def test_workflow_adoption_re_matches_learn(self):
        assert WORKFLOW_ADOPTION_RE.search("learn a new workflow style")

    def test_workflow_approval_re_matches(self):
        assert WORKFLOW_APPROVAL_RE.search("approve this workflow candidate")

    def test_url_re_matches_http(self):
        match = URL_RE.search("check https://example.com/path for details")
        assert match
        assert match.group(0) == "https://example.com/path"

    def test_url_re_matches_https_with_query(self):
        match = URL_RE.search("see https://github.com/IQMO/MO?tab=readme now")
        assert match
        assert "https://github.com/IQMO/MO?tab=readme" in match.group(0)

    def test_workflow_source_path_re_matches(self):
        assert WORKFLOW_SOURCE_PATH_RE.search("from workflows.md")
