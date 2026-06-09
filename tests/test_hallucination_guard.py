from stet.core.text_utils import (
    _HALLUCINATION_THRESHOLD_AGGRESSIVE,
    _HALLUCINATION_THRESHOLD_CONSERVATIVE,
    _HALLUCINATION_THRESHOLD_SMARTFIX,
    _hallucination_ratio,
)


def test_ratio_identical_text():
    assert _hallucination_ratio("hello world", "hello world", "conservative") == 0.0


def test_ratio_completely_different():
    ratio = _hallucination_ratio("hello world", "foo bar", "conservative")
    # Character-level matching will find some overlap ('o', ' ', 'r'),
    # but it should still be significantly higher than the 0.4 threshold.
    assert ratio > 0.6


def test_ratio_single_typo_vs_replacement_conservative():
    """
    Hypothesis: Char-level SequenceMatcher (minus whitespace) handles typos much better.
    """
    ratio_typo = _hallucination_ratio("i beleive it", "i believe it", "conservative")
    ratio_repl = _hallucination_ratio(
        "i caterpiller it", "i believe it", "conservative"
    )

    assert ratio_typo < ratio_repl


def test_ratio_thresholds():
    """
    Check that smart_fix and aggressive return proper distances instead of 0.0,
    so their respective thresholds (0.6 and 0.8) can be applied.
    """
    ratio_smart = _hallucination_ratio(
        "hello world",
        "this is a completely entirely different sentence and a rewrite",
        "smart_fix",
    )
    assert ratio_smart > 0.6

    ratio_aggressive = _hallucination_ratio(
        "hello world",
        "this is a completely entirely different sentence and a rewrite",
        "aggressive",
    )
    assert ratio_aggressive > 0.8


def test_strength_threshold_constants_are_ordered():
    assert _HALLUCINATION_THRESHOLD_CONSERVATIVE == 0.4
    assert _HALLUCINATION_THRESHOLD_SMARTFIX == 1.0
    assert _HALLUCINATION_THRESHOLD_AGGRESSIVE == 1.0
    assert (
        _HALLUCINATION_THRESHOLD_CONSERVATIVE
        < _HALLUCINATION_THRESHOLD_SMARTFIX
        <= _HALLUCINATION_THRESHOLD_AGGRESSIVE
    )
