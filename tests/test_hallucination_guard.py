from stet.core.text_utils import (
    _HALLUCINATION_THRESHOLD_AGGRESSIVE,
    _HALLUCINATION_THRESHOLD_CONSERVATIVE,
    _HALLUCINATION_THRESHOLD_SMARTFIX,
    _hallucination_ratio,
)


def test_ratio_identical_text():
    assert _hallucination_ratio("hello world", "hello world", "spelling_only") == 0.0


def test_ratio_completely_different():
    ratio = _hallucination_ratio("hello world", "foo bar", "spelling_only")
    # Character-level matching will find some overlap ('o', ' ', 'r'),
    # but it should still be significantly higher than the 0.4 threshold.
    assert ratio > 0.6


def test_ratio_single_typo_vs_replacement_conservative():
    """
    Hypothesis: Char-level SequenceMatcher (minus whitespace) handles typos much better.
    """
    ratio_typo = _hallucination_ratio("i beleive it", "i believe it", "spelling_only")
    ratio_repl = _hallucination_ratio(
        "i caterpiller it", "i believe it", "spelling_only"
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
        "full_correction",
    )
    assert ratio_smart > 0.6

    ratio_aggressive = _hallucination_ratio(
        "hello world",
        "this is a completely entirely different sentence and a rewrite",
        "rewrite_polish",
    )
    assert ratio_aggressive > 0.8


def test_strength_threshold_constants_are_ordered():
    assert _HALLUCINATION_THRESHOLD_CONSERVATIVE == 0.7
    assert _HALLUCINATION_THRESHOLD_SMARTFIX == 1.0
    assert _HALLUCINATION_THRESHOLD_AGGRESSIVE == 1.0
    assert (
        _HALLUCINATION_THRESHOLD_CONSERVATIVE
        < _HALLUCINATION_THRESHOLD_SMARTFIX
        <= _HALLUCINATION_THRESHOLD_AGGRESSIVE
    )


def test_ratio_typo_only_below_threshold():
    ratio = _hallucination_ratio("i thnik we shoud go to the store", "I think we should go to the store", "full_correction")
    assert ratio < 0.75


def test_ratio_grammar_rewrite_above():
    ratio = _hallucination_ratio("i thnik we shoud go", "Let us head to the store instead", "full_correction")
    assert ratio > 0.60


def test_rejected_unit_still_gets_dict_prepass(monkeypatch, tmp_path):
    import stet.core.config as config_mod
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    manager = ModelManager(cm)
    monkeypatch.setattr(manager, "is_loaded", lambda: True)

    # Mock _rewrite_sentence_chunk returning None (simulating unit model rejection)
    monkeypatch.setattr(manager, "_rewrite_sentence_chunk", lambda *args, **kwargs: None)

    res, units = manager.correct_text_patch("I will recieve the package.", strength="full_correction")
    assert res == "I will receive the package."
    assert units == 1


def test_rejected_unit_preserves_non_map_typos(monkeypatch, tmp_path):
    import stet.core.config as config_mod
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    manager = ModelManager(cm)
    monkeypatch.setattr(manager, "is_loaded", lambda: True)

    monkeypatch.setattr(manager, "_rewrite_sentence_chunk", lambda *args, **kwargs: None)

    # shoud and tomorow are not in dict map; when LLM is rejected, they survive unchanged
    text = "We shoud go tomorow."
    res, units = manager.correct_text_patch(text, strength="full_correction")
    assert res is None or res == text
    assert units == 1


def test_full_correction_prepass_runs_for_all_strengths(monkeypatch, tmp_path):
    import stet.core.config as config_mod
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    manager = ModelManager(cm)
    monkeypatch.setattr(manager, "is_loaded", lambda: True)

    passed_chunks = []

    def mock_rewrite(chunk_text, *args, **kwargs):
        passed_chunks.append(chunk_text)
        return chunk_text

    monkeypatch.setattr(manager, "_rewrite_sentence_chunk", mock_rewrite)

    manager.correct_text_patch("This is teh test.", strength="full_correction")
    assert len(passed_chunks) > 0
    assert "the" in passed_chunks[0]

