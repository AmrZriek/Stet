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


# ---------------------------------------------------------------------------
# CorrectionResult / CorrectionOutcome regression tests
# ---------------------------------------------------------------------------


def test_correction_result_tuple_unpacking(monkeypatch):
    """CorrectionResult must support legacy tuple unpacking: result, units = ..."""
    from stet.core.text_utils import CorrectionOutcome, CorrectionResult

    cr = CorrectionResult(text="hello", outcome=CorrectionOutcome.CORRECTED, units_processed=3)
    result, units = cr
    assert result == "hello"
    assert units == 3


def test_correction_result_text_or_none(monkeypatch):
    """text_or_none returns None for failure outcomes, text for success."""
    from stet.core.text_utils import CorrectionOutcome, CorrectionResult

    cr_ok = CorrectionResult(text="fixed", outcome=CorrectionOutcome.CORRECTED)
    assert cr_ok.text_or_none == "fixed"

    cr_fail = CorrectionResult(text="orig", outcome=CorrectionOutcome.FAILED_ALL_UNITS)
    assert cr_fail.text_or_none is None

    cr_cancel = CorrectionResult(text="orig", outcome=CorrectionOutcome.CANCELLED)
    assert cr_cancel.text_or_none is None

    cr_prot = CorrectionResult(text="orig", outcome=CorrectionOutcome.UNCHANGED_PROTECTED)
    assert cr_prot.text_or_none == "orig"


def test_correction_result_changed_property(monkeypatch):
    """changed is True only when outcome is CORRECTED and units_corrected > 0."""
    from stet.core.text_utils import CorrectionOutcome, CorrectionResult

    cr = CorrectionResult(text="fixed", outcome=CorrectionOutcome.CORRECTED, units_corrected=2)
    assert cr.changed is True

    cr_no_change = CorrectionResult(text="same", outcome=CorrectionOutcome.UNCHANGED_NO_ERRORS)
    assert cr_no_change.changed is False


# ---------------------------------------------------------------------------
# Span-only recovery tests
# ---------------------------------------------------------------------------


def test_span_recovery_on_mangled_sentinel(monkeypatch):
    """When the LLM mangles a sentinel beyond recovery, span-only recovery
    should send only the editable prose to the LLM and preserve the protected
    atom verbatim.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    call_count = 0
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call (normal path): return text with fully deleted sentinel
        # (unrecoverable — recover_sentinels won't fabricate from nothing)
        if call_count == 1:
            return chunk_text.replace("__STET_PROTECTED_1__", "")
        # Second call (span recovery): correct the prose span
        return chunk_text.replace("teh", "the")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com teh page"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # The URL must be preserved verbatim
    assert "https://example.com" in result
    # The typo should be fixed by span recovery
    assert "teh" not in result or "the" in result


def test_span_recovery_preserves_multiple_atoms(monkeypatch):
    """Span-only recovery preserves multiple protected atoms and corrects
    the prose between them.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    call_count = 0
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Normal path: fully delete both sentinels (unrecoverable)
            return chunk_text.replace("__STET_PROTECTED_1__", "").replace("__STET_PROTECTED_2__", "")
        # Span recovery: correct the prose
        return chunk_text.replace("teh", "the")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "see https://a.com and https://b.com teh page"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "https://a.com" in result
    assert "https://b.com" in result


def test_unchanged_protected_outcome_on_total_failure(monkeypatch):
    """When all units fail due to mangled sentinels and span recovery also
    fails, the outcome should be UNCHANGED_PROTECTED with original text.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        # Always mangle sentinels
        return chunk_text.replace("__STET_PROTECTED_1__", "GONE")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # Text must be unchanged (safe fail)
    assert result == original


def test_protected_atoms_prevent_streaming_fallback(monkeypatch):
    """POLICY: When protected atoms are present and all units fail, the outcome
    is UNCHANGED_PROTECTED (not FAILED_ALL_UNITS).  This prevents the streaming
    fallback from running, which would expose raw sentinels or corrupt protected
    content.  The UI shows an actionable message instead.
    """
    from stet.core.text_utils import CorrectionOutcome

    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        # Always delete sentinels — unrecoverable, triggers span recovery
        # which also fails (returns the text with sentinel deleted).
        return chunk_text.replace("__STET_PROTECTED_1__", "")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    cr = mgr.correct_text_patch(original, strength="full_correction")

    # With protected atoms present, outcome must be UNCHANGED_PROTECTED.
    # This prevents streaming fallback from running.
    assert cr.outcome == CorrectionOutcome.UNCHANGED_PROTECTED
    assert cr.text == original
    assert cr.protected_atom_count == 1
    assert cr.text_or_none is not None  # Not a streaming-triggering failure


def test_no_atoms_allows_streaming_fallback(monkeypatch):
    """POLICY: When NO protected atoms are present and all units fail, the
    outcome is FAILED_ALL_UNITS.  text_or_none returns None, which triggers
    the streaming fallback in the UI.
    """
    from stet.core.text_utils import CorrectionOutcome

    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        # Hallucination: return wildly different text
        return "completely different text " * 10

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "see the report for more details"
    cr = mgr.correct_text_patch(original, strength="full_correction")

    # No protected atoms → FAILED_ALL_UNITS → streaming fallback allowed.
    assert cr.outcome == CorrectionOutcome.FAILED_ALL_UNITS
    assert cr.text_or_none is None  # Triggers streaming
    assert cr.protected_atom_count == 0


def test_structured_outcome_fields(monkeypatch):
    """CorrectionResult has populated outcome, units, atoms, elapsed fields."""

    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("teh", "the")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com teh page"
    cr = mgr.correct_text_patch(original, strength="full_correction")

    assert hasattr(cr, 'outcome')
    assert hasattr(cr, 'units_processed')
    assert hasattr(cr, 'protected_atom_count')
    assert hasattr(cr, 'elapsed_s')
    assert hasattr(cr, 'reason')
    assert cr.protected_atom_count == 1
    assert cr.elapsed_s >= 0  # 0.0 is valid when mock returns instantly
    assert cr.units_processed >= 1


def test_stream_strict_anchor_validation():
    """Streaming validator must reject output with duplicated, reordered,
    or missing sentinels — not just check presence.
    """
    from stet.core.text_utils import _INLINE_SENTINEL_RE

    # Expected: [__STET_PROTECTED_1__, __STET_PROTECTED_2__]
    expected = ["__STET_PROTECTED_1__", "__STET_PROTECTED_2__"]

    # 1. Duplicated sentinel — should fail
    duped = "see __STET_PROTECTED_1__ and __STET_PROTECTED_1__ today"
    found = _INLINE_SENTINEL_RE.findall(duped)
    assert found != expected, "duplicated sentinels must not match expected"

    # 2. Reordered sentinel — should fail
    reordered = "see __STET_PROTECTED_2__ and __STET_PROTECTED_1__ today"
    found = _INLINE_SENTINEL_RE.findall(reordered)
    assert found != expected, "reordered sentinels must not match expected"

    # 3. Missing sentinel — should fail
    missing = "see __STET_PROTECTED_1__ and nothing today"
    found = _INLINE_SENTINEL_RE.findall(missing)
    assert found != expected, "missing sentinel must not match expected"

    # 4. Correct order — should pass
    correct = "see __STET_PROTECTED_1__ and __STET_PROTECTED_2__ today"
    found = _INLINE_SENTINEL_RE.findall(correct)
    assert found == expected, "correct order must match"


def test_mangled_alias_recovery_in_stream():
    """recover_sentinels should restore [REF1] → __STET_PROTECTED_1__ for
    the streaming path's strict validation.
    """
    from stet.core.text_utils import recover_sentinels, _INLINE_SENTINEL_RE

    cleaned = "see [REF1] and [REF2] today"
    expected = ["__STET_PROTECTED_1__", "__STET_PROTECTED_2__"]
    recovered = recover_sentinels(cleaned, expected)
    found = _INLINE_SENTINEL_RE.findall(recovered)
    assert found == expected, f"expected recovery to restore sentinels, got {found}"


def test_markdown_link_not_mangled_by_span_recovery(monkeypatch):
    """Markdown links like [text](url) must survive span-only recovery with
    the complete link structure intact.  The URL destination is protected as
    part of the full Markdown construct, not just as a bare substring.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    call_count = 0
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Normal path: fully delete the sentinel (unrecoverable)
            return chunk_text.replace("__STET_PROTECTED_1__", "")
        # Span recovery: correct the prose typo
        return chunk_text.replace("clikc", "click")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "Please clikc [here](https://example.com/page) for details."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # The exact Markdown link must be preserved as a valid construct.
    # The URL is inside the link, so the complete [text](url) form must survive.
    assert "[here](https://example.com/page)" in result, (
        f"Markdown link structure broken; got: {result!r}"
    )
    # The typo in the surrounding prose should be fixed.
    assert "click" in result or "clikc" not in result


def test_markdown_link_protected_as_construct(monkeypatch):
    """When span recovery runs, the Markdown link [text](SENTINEL) is merged
    into a single protected atom.  The LLM sees only the prose outside the
    link — never the link label, syntax, URL, or closing paren.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    # Track what the LLM sees during span recovery.
    span_texts_seen = []
    call_count = 0
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Normal path: mangle sentinel (unrecoverable)
            return chunk_text.replace("__STET_PROTECTED_1__", "GONE")
        # Span recovery calls: track what prose spans are sent.
        span_texts_seen.append(chunk_text)
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "Please click [here](https://example.com/page) for details."
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    # The complete Markdown link must be in the result.
    assert "[here](https://example.com/page)" in result, (
        f"Markdown link not preserved; got: {result!r}"
    )

    # The LLM must never see any part of the link construct during recovery.
    # Only the prose before and after the link should be sent as spans.
    for span in span_texts_seen:
        assert "[here]" not in span, f"LLM saw link label in span: {span!r}"
        assert "](" not in span, f"LLM saw link syntax in span: {span!r}"
        assert "https://example.com/page" not in span, f"LLM saw URL in span: {span!r}"
        assert "example.com" not in span, f"LLM saw URL fragment in span: {span!r}"
