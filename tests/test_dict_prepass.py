from stet.core.text_utils import (
    _COMMON_TYPOS_MAP,
    _dict_prepass,
    _edit_dist,
    _spell_autocorrect,
    _spell_unknown_words,
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


# ── _spell_unknown_words tests ─────────────────────────────────────────


def test_spell_unknown_detects_nonsense():
    unknown = _spell_unknown_words("I xyzabc think qwertyuiop")
    assert "xyzabc" in unknown
    assert "qwertyuiop" in unknown


def test_spell_unknown_accepts_valid_english():
    unknown = _spell_unknown_words("The quick brown fox jumps")
    assert unknown == set()


def test_spell_unknown_ignores_single_chars():
    unknown = _spell_unknown_words("I a b c go to the store")
    assert unknown == set()


def test_spell_unknown_ignores_numbers():
    unknown = _spell_unknown_words("I have 42 items and 7 boxes")
    assert unknown == set()


def test_spell_unknown_ignores_short_allcaps():
    unknown = _spell_unknown_words("NASA USA FBI GPU CPU")
    assert unknown == set()


def test_spell_unknown_skips_known_typos():
    unknown = _spell_unknown_words("teh teh teh")
    assert unknown == set()


def test_spell_unknown_mixed():
    text = "I packge the xyzabc"
    unknown = _spell_unknown_words(text)
    assert "xyzabc" in unknown
    assert "packge" in unknown


# ── _spell_autocorrect tests ───────────────────────────────────────────


def test_spell_autocorrect_novel_typo():
    # "packge" is NOT in _COMMON_TYPOS_MAP — spell checker should catch it
    fixed, n = _spell_autocorrect("packge")
    assert fixed == "package"
    assert n == 1


def test_spell_autocorrect_preserves_case():
    fixed, n = _spell_autocorrect("Packge")
    assert fixed == "Package"


def test_spell_autocorrect_allcaps():
    fixed, n = _spell_autocorrect("PACKGE")
    assert fixed == "PACKAGE"


def test_spell_autocorrect_valid_text_unchanged():
    text = "The quick brown fox jumps over the lazy dog"
    fixed, n = _spell_autocorrect(text)
    assert fixed == text
    assert n == 0


def test_spell_autocorrect_empty():
    fixed, n = _spell_autocorrect("")
    assert fixed == ""
    assert n == 0


def test_spell_autocorrect_known_typo_map_unchanged():
    # Words already in _COMMON_TYPOS_MAP should be skipped (handled by _dict_prepass)
    fixed, n = _spell_autocorrect("teh")
    assert fixed == "teh"
    assert n == 0


def test_spell_autocorrect_numbers_unchanged():
    fixed, n = _spell_autocorrect("I have 42 items")
    assert fixed == "I have 42 items"
    assert n == 0


def test_spell_autocorrect_short_allcaps_unchanged():
    fixed, n = _spell_autocorrect("NASA USA")
    assert fixed == "NASA USA"
    assert n == 0


def test_spell_autocorrect_sentence():
    fixed, n = _spell_autocorrect("I packge the items yesterday")
    assert "package" in fixed
    assert n >= 1


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
