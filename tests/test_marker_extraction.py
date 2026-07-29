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
