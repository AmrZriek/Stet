from stet.core.text_utils import (
    _COMMON_TYPOS_MAP,
    _dict_prepass,
    _edit_dist,
)


def test_dict_prepass_case_preservation():
    assert _dict_prepass("teh")[0] == "the"
    assert _dict_prepass("Teh")[0] == "The"
    assert _dict_prepass("TEH")[0] == "THE"


def test_dict_fixes_are_always_safe_for_conservative():
    # Check if they are single word (no spaces in keys)
    # Actually there might be some keys with spaces? Let's check
    for k, v in _COMMON_TYPOS_MAP.items():
        assert " " not in k or k in ["arn't"], f"Key '{k}' is not a single word typo"


# ── _edit_dist tests ───────────────────────────────────────────────────


def test_edit_dist_identical():
    assert _edit_dist("hello", "hello") == 0


def test_edit_dist_empty():
    assert _edit_dist("", "abc") == 3
    assert _edit_dist("abc", "") == 3


def test_edit_dist_single_insert():
    assert _edit_dist("abc", "abcd") == 1


def test_edit_dist_single_delete():
    assert _edit_dist("abcd", "abc") == 1


def test_edit_dist_single_replace():
    assert _edit_dist("abc", "axc") == 1


def test_edit_dist_classic():
    assert _edit_dist("kitten", "sitting") == 3
