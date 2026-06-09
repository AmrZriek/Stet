from stet.core.text_utils import _extract_rewritten_sentence


def test_extract_normal_markers():
    assert _extract_rewritten_sentence("<<<START>>>text<<<END>>>") == "text"


def test_extract_no_markers():
    # Fallback can kick in here if it's a clean line
    # If it's a single clean line, it might return "text"
    assert _extract_rewritten_sentence("text") == "text"


def test_extract_only_start_marker():
    # Without END, regex fails, and fallback rejects it because it contains raw markers
    res = _extract_rewritten_sentence("<<<START>>> text")
    assert res is None


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
