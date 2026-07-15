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

    assert "__STET_PROTECTED_1__" in captured_text
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

    assert "__STET_PROTECTED_1__" in captured_text
    assert "__STET_PROTECTED_2__" in captured_text
    assert "__STET_PROTECTED_3__" in captured_text
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

    assert "__STET_PROTECTED_1__" in captured_text
    assert "__STET_PROTECTED_2__" in captured_text
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
    assert "__STET_PROTECTED_1__" in captured_text
    assert result == win_path

    unix_path = "run /usr/local/bin/foo now"
    result, _ = mgr.correct_text_patch(unix_path, strength="full_correction")
    assert "__STET_PROTECTED_1__" in captured_text
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
    """A FULLY deleted sentinel (__STET_PROTECTED_1__->'') is unrecoverable — the chunk
    must still be rejected and the original returned unchanged. This test
    pins the safe-fail behaviour: recover_sentinels never fabricates a
    sentinel out of nothing; it only restores mangled variants.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("__STET_PROTECTED_1__", "")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == original

def test_url_masking_mangled_sentinel(monkeypatch):
    """When _rewrite_sentence_chunk returns text with sentinels intact and
    a real correction applied, the chunk is accepted.  (Recovery of mangled
    sentinels is tested in the unit-level recover_sentinels tests below;
    this integration test confirms the happy path end-to-end.)
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        # Simulate what _rewrite_sentence_chunk does after successful recovery:
        # sentinels are intact, correction applied.
        return chunk_text.replace("today", "tomorrow")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # The correction must have applied — sentinels survived, chunk accepted.
    assert result == "visit https://example.com tomorrow", (
        f"expected correction to apply; got {result!r}"
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
    assert "__STET_PROTECTED_1__" in captured_text
    assert "https://example.com" not in captured_text
    assert result_paren == original_paren

    # 2. Markdown Link (wrapped in longer sentence to pass looks_like_prose gate)
    original_md = "Please visit this markdown link [link](https://example.com) to find all of the relevant documentation for the project deployment."
    result_md, _ = mgr.correct_text_patch(original_md, strength="full_correction")
    assert "__STET_PROTECTED_1__" in captured_text
    assert "https://example.com" not in captured_text
    assert result_md == original_md

    # 3. Wikipedia-style URL with balanced parentheses (wrapped in longer sentence to pass looks_like_prose gate)
    original_wiki = "You can read more on the Wikipedia page https://en.wikipedia.org/wiki/Stet_(disambiguation) for further context."
    result_wiki, _ = mgr.correct_text_patch(original_wiki, strength="full_correction")
    assert "__STET_PROTECTED_1__" in captured_text
    assert "https://en.wikipedia.org" not in captured_text
    assert result_wiki == original_wiki


# ---------------------------------------------------------------------------
# Sentinel recovery tests (recover_sentinels)
#
# These validate the fix for the bug where small models in aggressive modes
# strip or reformat underscores around masked-hazard sentinels, causing
# the sentinel survival check to silently reject the chunk and return the
# original text uncorrected. Recovery restores mangled variants so
# the chunk is accepted instead of rejected.
# ---------------------------------------------------------------------------


def test_url_masking_recover_single_underscore(monkeypatch):
    """When the mock returns corrected text with sentinels intact, the chunk
    is accepted.  (Mangling recovery is tested at unit level below.)
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("today", "tomorrow")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == "visit https://example.com tomorrow"


def test_url_masking_recover_case_collapse(monkeypatch):
    """When the mock returns corrected text with sentinels intact, the chunk
    is accepted.  (Case-collapse recovery is tested at unit level below.)
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("today", "tomorrow")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == "visit https://example.com tomorrow"


def test_url_masking_recover_bare_stet(monkeypatch):
    """When the mock returns corrected text with sentinels intact, the chunk
    is accepted.  (Bare STET recovery is tested at unit level below.)
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return (
            chunk_text
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
    # The correction must have applied.
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


def test_recover_sentinels_no_false_positive_on_legit_text():
    """Legitimate bare text must NOT become a sentinel
    when no sentinel was expected for that chunk.
    """
    from stet.core.text_utils import recover_sentinels

    text = "the STET series processor is fast"
    # expected has index 2, not 1 — recovery must not touch the bare text
    assert recover_sentinels(text, ["__STET_PROTECTED_2__"]) == text


def test_recover_sentinels_bare_mangled_variant():
    """Bare _STET_PROTECTED_1_ (underscore-stripped variant) is recovered
    when the expected sentinel is __STET_PROTECTED_1__.
    """
    from stet.core.text_utils import recover_sentinels

    # Standalone variant — recovered.
    assert recover_sentinels("visit _STET_PROTECTED_1_ today", ["__STET_PROTECTED_1__"]) == "visit __STET_PROTECTED_1__ today"
    # Already correct — no-op.
    assert recover_sentinels("visit __STET_PROTECTED_1__ today", ["__STET_PROTECTED_1__"]) == "visit __STET_PROTECTED_1__ today"


def test_recover_sentinels_fully_deleted_no_recovery():
    """When the sentinel fully disappeared (no mangled variant present),
    recover_sentinels must NOT fabricate one — the caller's survival check
    then rejects the chunk as before.
    """
    from stet.core.text_utils import recover_sentinels

    # No sentinel or variant anywhere in the text.
    text = "visit the link today"
    out = recover_sentinels(text, ["__STET_PROTECTED_1__"])
    assert out == text, f"expected no fabrication; got {out!r}"
    # Caller-side survival check would correctly fail on this output.


def test_recover_sentinels_multiple_indices():
    """Two hazards mangled differently are each recovered
    independently, and partial recovery (one restored, one missing) leaves
    the missing one untouched so the survival check still rejects.
    """
    from stet.core.text_utils import recover_sentinels

    # Both mangled, both recoverable.
    out = recover_sentinels("see _STET_PROTECTED_1_ and _STET_PROTECTED_2_ today", ["__STET_PROTECTED_1__", "__STET_PROTECTED_2__"])
    assert out == "see __STET_PROTECTED_1__ and __STET_PROTECTED_2__ today"

    # One mangled, one fully missing — the mangled one is restored, the
    # missing one stays missing (no fabrication).
    out_partial = recover_sentinels("see _STET_PROTECTED_1_ and nothing today", ["__STET_PROTECTED_1__", "__STET_PROTECTED_2__"])
    assert "__STET_PROTECTED_1__" in out_partial
    assert "__STET_PROTECTED_2__" not in out_partial


def test_recover_sentinels_already_present_no_op():
    """When the sentinel is already verbatim in the text, recover_sentinels
    must short-circuit and not run any mangling regex against it.
    """
    from stet.core.text_utils import recover_sentinels

    text = "visit __STET_PROTECTED_1__ today"
    assert recover_sentinels(text, ["__STET_PROTECTED_1__"]) == text


# ---------------------------------------------------------------------------
# file:/// URI masking tests (handoff 2026-07-13 fix)
# ---------------------------------------------------------------------------


def test_file_uri_masking(monkeypatch):
    """file:/// URIs must be masked as sentinels, not sent raw to the LLM."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "Check this file:///C:/Users/test/document.pdf for details."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "__STET_PROTECTED_1__" in captured_text
    assert "file:///C:/Users/test/document.pdf" not in captured_text
    assert result == original


def test_ftp_uri_masking(monkeypatch):
    """ftp:// URIs must be masked as sentinels."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "Download from ftp://files.example.com/pub/doc.txt today."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "__STET_PROTECTED_1__" in captured_text
    assert "ftp://files.example.com" not in captured_text
    assert result == original


def test_ssh_uri_masking(monkeypatch):
    """ssh:// URIs must be masked as sentinels."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "Clone from ssh://git@github.com/user/repo.git now."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "__STET_PROTECTED_1__" in captured_text
    assert "ssh://git@github.com" not in captured_text
    assert result == original


def test_mixed_uri_schemes_masking(monkeypatch):
    """Multiple URI schemes in one text must all be masked independently."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "See file:///C:/a.txt and ftp://b.com/c.txt and https://d.io now."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "__STET_PROTECTED_1__" in captured_text
    assert "__STET_PROTECTED_2__" in captured_text
    assert "__STET_PROTECTED_3__" in captured_text
    assert result == original


def test_file_uri_at_line_start(monkeypatch):
    """file:/// at the very start of text (no preceding space) must be masked."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "file:///D:/path/to/file.txt is the path."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "__STET_PROTECTED_1__" in captured_text
    assert "file:///D:/path/to/file.txt" not in captured_text
    assert result == original
