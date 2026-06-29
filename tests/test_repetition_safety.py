from stet.core.text_utils import _apply_post_fixes
from stet.llm.model_manager import ModelManager


class MockConfig:
    def get(self, key, default=None):
        return default


def test_post_fixes_removes_model_introduced_duplicate_sentence():
    original = "We ship today."
    corrected = "We ship today. We ship today."

    assert _apply_post_fixes(corrected, original=original) == original


def test_post_fixes_preserves_duplicate_sentence_already_in_original():
    original = "Stop. Stop."

    assert _apply_post_fixes(original, original=original) == original


def test_conservative_preserves_user_repeated_word():
    original = "This is very very important."

    assert (
        _apply_post_fixes(original, original=original, strength="conservative")
        == original
    )


def test_post_fixes_preserves_existing_duplicate_word_behavior():
    assert (
        _apply_post_fixes(
            "the the test",
            original="the test",
            strength="conservative",
        )
        == "the the test"
    )
    assert (
        _apply_post_fixes("had had enough", original="had had enough")
        == "had had enough"
    )


def test_aggressive_patch_accepts_model_judgment_on_repeated_word(monkeypatch):
    original = "This is very very important."
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            "This is very important."
        )
    )

    result, units = mgr.correct_text_patch(original, strength="aggressive")

    # Repetition-loss guard is relaxed (log-only for aggressive).
    # The AI model's judgment is trusted to handle repetition appropriately.
    assert result == "This is very important."
    assert units == 1


def test_aggressive_patch_accepts_model_judgment_on_repeated_sentence(monkeypatch):
    original = "Stop. Stop."
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            "Stop."
        )
    )

    result, units = mgr.correct_text_patch(original, strength="aggressive")

    # Repetition-loss guard is relaxed (log-only for aggressive).
    assert result == "Stop."
    assert units == 1
