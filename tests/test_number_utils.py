from core.number_utils import as_int, as_non_negative_int, as_optional_int


def test_as_int_preserves_zero_default_local_helper_semantics():
    assert as_int(None) == 0
    assert as_int("") == 0
    assert as_int("not-int") == 0
    assert as_int([]) == 0
    assert as_int("0") == 0
    assert as_int(False) == 0
    assert as_int(True) == 1
    assert as_int("-4") == -4


def test_as_int_preserves_custom_default_semantics():
    assert as_int(None, 18) == 18
    assert as_int("", 18) == 18
    assert as_int([], 18) == 18
    assert as_int("bad", 18) == 18
    assert as_int(False, 18) == 0
    assert as_int("0", 18) == 0


def test_as_non_negative_int_matches_closeout_clamping():
    assert as_non_negative_int("-4") == 0
    assert as_non_negative_int(-1) == 0
    assert as_non_negative_int("6") == 6
    assert as_non_negative_int("bad") == 0


def test_as_optional_int_matches_structural_graph_semantics():
    assert as_optional_int(None) is None
    assert as_optional_int("") is None
    assert as_optional_int("bad") is None
    assert as_optional_int([]) is None
    assert as_optional_int("0") == 0
    assert as_optional_int("-2") == -2
