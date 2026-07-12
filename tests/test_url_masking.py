from stet.llm.model_manager import ModelManager

class MockConfig:
    def get(self, key, default=None):
        if key == "correction_modes":
            return [
                {"name": "Spelling Only"},
                {"name": "Full Correction"},
                {"name": "Rewrite & Polish"}
            ]
        return default

def test_url_masking_single(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com/path?q=1 today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "⟦U1⟧" in captured_text
    assert "https://example.com" not in captured_text
    assert result == original

def test_url_masking_multiple(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "see https://a.com and http://b.org and www.c.io"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "⟦U1⟧" in captured_text
    assert "⟦U2⟧" in captured_text
    assert "⟦U3⟧" in captured_text
    assert result == original

def test_url_masking_mixed(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "mail me at a@b.com or see https://x.io"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "⟦U1⟧" in captured_text
    assert "⟦U2⟧" in captured_text
    assert result == original

def test_url_masking_paths(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    win_path = r"open C:\Users\me\file.txt now"
    result, _ = mgr.correct_text_patch(win_path, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert result == win_path

    unix_path = "run /usr/local/bin/foo now"
    result, _ = mgr.correct_text_patch(unix_path, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert result == unix_path

def test_url_masking_bare_https(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"
    
    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "use https for security"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")
    assert result == original

def test_url_only_text_is_preserved_without_rewrite(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    rewrite_calls = 0
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal rewrite_calls
        rewrite_calls += 1
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "https://a.example/path?q=1 https://b.example/watch?v=2"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == original
    assert rewrite_calls == 0

def test_url_masking_missing_sentinel(monkeypatch):
    """A FULLY deleted sentinel (⟦U1⟧→'') is unrecoverable — the chunk
    must still be rejected and the original returned unchanged. This test
    pins the safe-fail behaviour: recover_sentinels never fabricates a
    sentinel out of nothing; it only restores mangled variants.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == original

def test_url_masking_mangled_sentinel(monkeypatch):
    """A CASE-COLLAPSED sentinel (⟦U1⟧→⟦u1⟧) is recoverable. The mock
    also makes a real text correction ("today"→"tomorrow") alongside the
    mangling so the assertion can distinguish recovery-accept (output
    contains the correction) from rejection-fallback (output unmodified).
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "⟦u1⟧").replace("today", "tomorrow")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # Recovery must have accepted the chunk → the model's correction survives.
    assert result == "visit https://example.com tomorrow", (
        f"expected recovery to accept the correction; got {result!r}"
    )

def test_url_masking_nested_and_markdown(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    # 1. Parenthesized URL (wrapped in longer sentence to pass looks_like_prose gate)
    original_paren = "This is a paragraph with a parenthesized URL (see https://example.com) at the end of a sentence."
    result_paren, _ = mgr.correct_text_patch(original_paren, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert "https://example.com" not in captured_text
    assert result_paren == original_paren

    # 2. Markdown Link (wrapped in longer sentence to pass looks_like_prose gate)
    original_md = "Please visit this markdown link [link](https://example.com) to find all of the relevant documentation for the project deployment."
    result_md, _ = mgr.correct_text_patch(original_md, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert "https://example.com" not in captured_text
    assert result_md == original_md

    # 3. Wikipedia-style URL with balanced parentheses (wrapped in longer sentence to pass looks_like_prose gate)
    original_wiki = "You can read more on the Wikipedia page https://en.wikipedia.org/wiki/Stet_(disambiguation) for further context."
    result_wiki, _ = mgr.correct_text_patch(original_wiki, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert "https://en.wikipedia.org" not in captured_text
    assert result_wiki == original_wiki


# ---------------------------------------------------------------------------
# Sentinel recovery tests (recover_sentinels)
#
# These validate the fix for the bug where small models in aggressive modes
# strip or reformat the ⟦⟧ brackets around masked-hazard sentinels, causing
# the sentinel survival check to silently reject the chunk and return the
# original text uncorrected. Recovery restores mangled variants to ⟦Ui⟧ so
# the chunk is accepted instead of rejected.
# ---------------------------------------------------------------------------


def test_url_masking_recover_brackets(monkeypatch):
    """⟦U1⟧ mangled to [U1] is recovered; the correction is accepted."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "[U1]").replace("today", "tomorrow")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == "visit https://example.com tomorrow"
    assert "[U1]" not in result


def test_url_masking_recover_parens(monkeypatch):
    """⟦U1⟧ mangled to (U1) is recovered; the correction is accepted."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "(U1)").replace("today", "tomorrow")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == "visit https://example.com tomorrow"
    assert "(U1)" not in result


def test_url_masking_recover_bare_with_quotes(monkeypatch):
    """⟦U1⟧ mangled to a bare, quote-flanked `U1` (the exact scenario from
    app_debug.log: `"U1" for this can you clean it up?`) is recovered.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        # Mirror the observed mangling: brackets stripped, but inner U1
        # preserved inside the user's surrounding double-quotes. Also
        # apply a real correction so the assertion can tell apart a
        # recovery-accept (typo fixed) from a rejection-fallback (typo
        # survives untouched in the original text).
        return (
            chunk_text
            .replace("⟦U1⟧", "U1")
            .replace("recepit", "receipt")
            .replace("send by", "sent by")
        )

    mgr._rewrite_sentence_chunk = mock_rewrite

    # Long enough to clear looks_like_prose; mirrors the real-world case.
    original = (
        '"D:\\Projects\\Software\\Stet\\marketing\\local_ai_utilization.svg" '
        "for this can you clean it up the recepit needs to be send by friday"
    )
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # The path must be preserved verbatim (unmasked from the recovered sentinel)
    assert "local_ai_utilization.svg" in result
    # The correction must have applied — if recovery failed the chunk is
    # rejected and the original (typo included) is returned unchanged.
    assert "recepit" not in result, (
        f"expected spelling correction to apply; got {result!r}"
    )
    assert "sent by" in result


# ---------------------------------------------------------------------------
# Direct unit tests for recover_sentinels — these exercise the helper in
# isolation so the false-positive guards and multi-sentinel logic can be
# tested without the full patch pipeline.
# ---------------------------------------------------------------------------


def test_recover_sentinels_empty_expected_no_op():
    """When no sentinels are expected, recover_sentinels must not touch text."""
    from stet.core.text_utils import recover_sentinels

    text = "the U1 series processor is fast"
    assert recover_sentinels(text, []) == text
    assert recover_sentinels(text, None) == text


def test_recover_sentinels_no_false_positive_on_legit_u1():
    """Legitimate bare `U1` in user content must NOT become a sentinel
    when no ⟦U1⟧ was expected for that chunk.
    """
    from stet.core.text_utils import recover_sentinels

    text = "the U1 series processor is fast"
    # expected has U2, not U1 — recovery must not touch the bare U1
    assert recover_sentinels(text, ["⟦U2⟧"]) == text


def test_recover_sentinels_bare_un_word_boundary_guard():
    """Bare `U1` recovery is restricted to standalone word occurrences —
    embedded forms like `v1U1x` are left alone so user content is not
    corrupted.
    """
    from stet.core.text_utils import recover_sentinels

    # Standalone — recovered.
    assert recover_sentinels("visit U1 today", ["⟦U1⟧"]) == "visit ⟦U1⟧ today"
    # Quote-flanked (the observed log scenario) — recovered.
    assert recover_sentinels('"U1" for this', ["⟦U1⟧"]) == '"⟦U1⟧" for this'
    # Embedded in a larger alphanumeric token — NOT recovered.
    assert recover_sentinels("model v1U1x is fast", ["⟦U1⟧"]) == "model v1U1x is fast"
    # Adjacent to a hyphen on one side only — recovered (hyphen is not \w).
    assert recover_sentinels("see U1-backed system", ["⟦U1⟧"]) == "see ⟦U1⟧-backed system"


def test_recover_sentinels_fully_deleted_no_recovery():
    """When the sentinel fully disappeared (no mangled variant present),
    recover_sentinels must NOT fabricate one — the caller's survival check
    then rejects the chunk as before.
    """
    from stet.core.text_utils import recover_sentinels

    # No U1 or bracketed variant anywhere in the text.
    text = "visit the link today"
    out = recover_sentinels(text, ["⟦U1⟧"])
    assert out == text, f"expected no fabrication; got {out!r}"
    # Caller-side survival check would correctly fail on this output.


def test_recover_sentinels_multiple_indices():
    """Two hazards ⟦U1⟧ and ⟦U2⟧ mangled differently are each recovered
    independently, and partial recovery (one restored, one missing) leaves
    the missing one untouched so the survival check still rejects.
    """
    from stet.core.text_utils import recover_sentinels

    # Both mangled, both recoverable.
    out = recover_sentinels("see [U1] and (U2) today", ["⟦U1⟧", "⟦U2⟧"])
    assert out == "see ⟦U1⟧ and ⟦U2⟧ today"

    # One mangled, one fully missing — the mangled one is restored, the
    # missing one stays missing (no fabrication).
    out_partial = recover_sentinels("see [U1] and nothing today", ["⟦U1⟧", "⟦U2⟧"])
    assert "⟦U1⟧" in out_partial
    assert "⟦U2⟧" not in out_partial


def test_recover_sentinels_already_present_no_op():
    """When the sentinel is already verbatim in the text, recover_sentinels
    must short-circuit and not run any mangling regex against it.
    """
    from stet.core.text_utils import recover_sentinels

    text = "visit ⟦U1⟧ today"
    assert recover_sentinels(text, ["⟦U1⟧"]) == text


