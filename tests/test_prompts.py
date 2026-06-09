from stet.core.text_utils import (
    _SENTENCE_REWRITE_PROMPT,
    _SENTENCE_REWRITE_PROMPT_AGGRESSIVE,
    _SENTENCE_REWRITE_PROMPT_CONSERVATIVE,
)
from stet.ui.main_window import CorrectionWindow


def test_conservative_prompt_has_fewshot():
    # Does the conservative prompt contain any <<<START>>> examples?
    assert "<<<START>>>" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "EXAMPLES:" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE


def test_conservative_prompt_abstract_terms_count():
    assert (
        "Do NOT change capitalization, punctuation, grammar, word choice, or word ordering"
        in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    )
    assert (
        "Fix ONLY clear misspellings, typos, and accidental keyboard slips"
        in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    )


def test_streaming_conservative_rules_differ_from_patch():
    import inspect

    source = inspect.getsource(CorrectionWindow._start_streaming_correction)
    assert "<<<TEXT>>>" in source
    assert "<<<START>>>" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE


def test_prompt_word_count_budget():
    words = len(_SENTENCE_REWRITE_PROMPT_CONSERVATIVE.split())
    assert words < 350


def test_smartfix_and_aggressive_prompts_are_marker_safe():
    for prompt in (_SENTENCE_REWRITE_PROMPT, _SENTENCE_REWRITE_PROMPT_AGGRESSIVE):
        assert "<<<START>>>" in prompt
        assert "<<<END>>>" in prompt
        assert "Output ONLY" in prompt


def test_prompts_treat_repetition_as_user_content():
    for prompt in (
        _SENTENCE_REWRITE_PROMPT,
        _SENTENCE_REWRITE_PROMPT_CONSERVATIVE,
        _SENTENCE_REWRITE_PROMPT_AGGRESSIVE,
    ):
        assert (
            "repeated words and repeated sentences are user content" in prompt.lower()
        )


def test_aggressive_prompt_allows_clarity_edits_without_value_changes():
    assert "expert editor" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE
    assert (
        "NEVER change numbers, dates, URLs, code, or specific values"
        in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE
    )
    assert "Output ONLY" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE
