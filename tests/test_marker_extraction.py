from stet.core.text_utils import _extract_rewritten_sentence


def test_extract_normal_markers():
    assert _extract_rewritten_sentence("<<<START>>>text<<<END>>>") == "text"


def test_extract_no_markers():
    # Fallback can kick in here if it's a clean line
    # If it's a single clean line, it might return "text"
    assert _extract_rewritten_sentence("text") == "text"


def test_extract_only_start_marker():
    # Without END, regex succeeds by tolerating missing END (as a stop sequence)
    res = _extract_rewritten_sentence("<<<START>>> text")
    assert res == "text"


def test_extract_extra_whitespace_in_markers():
    # The regex \s* handles whitespace
    assert _extract_rewritten_sentence("<<<  START  >>>  text  <<< END >>>") == "text"


def test_extract_model_preamble_then_markers():
    assert (
        _extract_rewritten_sentence(
            "Here is the text: \n<<<START>>>text<<<END>>>\nHope it helps!"
        )
        == "text"
    )


def test_extract_fallback_on_preamble():
    # Model forgets markers and gives preamble
    # Fallback should reject it
    assert _extract_rewritten_sentence("Sure! hello") is None


def test_extract_preamble_okay_without_original():
    """'Okay ...' is rejected when no original_text is provided."""
    assert _extract_rewritten_sentence("Okay, here is the corrected text.") is None


def test_extract_preamble_okay_with_different_original():
    """'Okay ...' is rejected when the original does NOT start with 'okay'."""
    result = _extract_rewritten_sentence(
        "Okay, here is the corrected version.",
        original_text="Please fix this sentence.",
    )
    assert result is None


def test_extract_preamble_okay_with_matching_original():
    """'Okay ...' is NOT rejected when the original itself starts with 'Okay'."""
    result = _extract_rewritten_sentence(
        "Okay, please use your findings to rewrite my description.",
        original_text="Okay, please use your findings to rewrite my descrption.",
    )
    assert result == "Okay, please use your findings to rewrite my description."


def test_extract_preamble_sure_with_matching_original():
    """'Sure ...' is NOT rejected when the original itself starts with 'Sure'."""
    result = _extract_rewritten_sentence(
        "Sure, I can help with that.",
        original_text="Sure, I can hlep with that.",
    )
    assert result == "Sure, I can help with that."


def test_extract_preamble_ok_comma_with_okay_original():
    """'Ok, ...' is NOT rejected when the original starts with 'okay'."""
    result = _extract_rewritten_sentence(
        "Ok, now please apply the changes by rerouting to antigravity; ensure it has all the research and information immediately accessible to it.",
        original_text="okay now please apply the changes by rerouting to antigravity, ensure it has all the research an dinformation immedietly",
    )
    assert result == "Ok, now please apply the changes by rerouting to antigravity; ensure it has all the research and information immediately accessible to it."


def test_extract_preamble_here_is_with_here_are_original():
    """'Here is ...' is NOT rejected when the original starts with 'here'."""
    result = _extract_rewritten_sentence(
        "Here is the list of changes.",
        original_text="here are the list of changes",
    )
    assert result == "Here is the list of changes."


# ── Reported bug (2026-08-11): preamble-strip deleted real leading text ─────
# A chunk whose FIRST sentence starts with a preamble phrase ("The corrected
# version …", "Here is the corrected version …") had that real content stripped
# by strip_meta_commentary, because it treats the phrase as model commentary.
# The original-aware fix preserves it when the original itself starts with it.


def test_extract_preamble_the_corrected_version_with_matching_original():
    """Chunk first sentence legitimately starts with 'The corrected version' —
    must be preserved, not stripped."""
    sentence = "The corrected version was released on Friday. Send it out immediately."
    result = _extract_rewritten_sentence(sentence, original_text=sentence)
    assert result == sentence


def test_extract_preamble_the_corrected_version_without_matching_original():
    """A model-added 'The corrected version …' lead-in IS commentary when the
    original does NOT start with it — still stripped."""
    result = _extract_rewritten_sentence(
        "The corrected version was released on Friday.",
        original_text="Please fix this sentence.",
    )
    assert result == "was released on Friday."


def test_extract_preamble_here_is_corrected_version_with_matching_original():
    """The long-phrase repro from the reported bug: 'Here is the corrected
    version of the final quarterly report …' must survive intact."""
    sentence = (
        "Here is the corrected version of the final quarterly report that the "
        "board reviewed and approved yesterday. Send it out immediately."
    )
    result = _extract_rewritten_sentence(sentence, original_text=sentence)
    assert result == sentence


def test_extract_preamble_here_is_corrected_version_without_matching_original():
    """Without a matching original the model-added lead-in is stripped — only
    the tail survives (pre-fix behaviour for genuine commentary)."""
    result = _extract_rewritten_sentence(
        "Here is the corrected version of the final report. Send it out.",
        original_text="Please fix this sentence.",
    )
    assert result == "of the final report. Send it out."


def test_extract_hrule_preserved_when_original_matches():
    """A leading '---' divider that the original starts with is echoed content,
    not a model separator — must survive extraction."""
    chunk = "---\nActual body text that must survive."
    result = _extract_rewritten_sentence(chunk, original_text=chunk)
    assert result == chunk
