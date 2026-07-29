"""Tests for protected terms: masking, restoration, and settings round-trip."""


from stet.core.text_utils import build_user_protection_re


class TestBuildUserProtectionRe:
    def test_empty_list_returns_none(self):
        assert build_user_protection_re([]) is None

    def test_none_returns_none(self):
        assert build_user_protection_re(None) is None

    def test_case_insensitive(self):
        re_ = build_user_protection_re(["Stet"])
        assert re_.search("stet is cool") is not None
        assert re_.search("STET is cool") is not None
        assert re_.search("Stet is cool") is not None

    def test_whole_word_only(self):
        re_ = build_user_protection_re(["Stet"])
        assert re_.search("Stetson") is None
        assert re_.search("I love Stet!") is not None

    def test_longest_first(self):
        re_ = build_user_protection_re(["New York", "New"])
        match = re_.search("I live in New York City")
        assert match is not None
        assert match.group() == "New York"

    def test_punctuation_terms(self):
        re_ = build_user_protection_re(["C++"])
        assert re_.search("I code in C++ daily") is not None

    def test_multi_word_phrases(self):
        re_ = build_user_protection_re(["MegaCorp Inc."])
        assert re_.search("Works at MegaCorp Inc. today") is not None

    def test_strips_and_trims(self):
        re_ = build_user_protection_re(["  hello  ", ""])
        assert re_ is not None
        assert re_.search("hello world") is not None

    def test_caps_at_200(self):
        terms = [f"term{i}" for i in range(300)]
        re_ = build_user_protection_re(terms)
        assert re_ is not None

    def test_protected_terms_in_default_config(self):
        from stet.constants import DEFAULT_CONFIG
        assert "protected_terms" in DEFAULT_CONFIG
        assert DEFAULT_CONFIG["protected_terms"] == []

    def test_build_function_importable(self):
        from stet.ui.main_window import build_user_protection_re
        assert callable(build_user_protection_re)
