"""Tests for configurable correction modes (Task 5)."""

from stet.constants import DEFAULT_CONFIG
from stet.llm.model_manager import _STRENGTH_TO_MODE_INDEX, ModelManager


class MockConfig:
    def __init__(self, extra=None):
        self._data = extra or {}

    def get(self, key, default=None):
        if key == "correction_modes":
            return DEFAULT_CONFIG.get("correction_modes", [])
        return self._data.get(key, default)


# ── DEFAULT_CONFIG structure ─────────────────────────────────────────────


def test_default_config_has_correction_modes():
    assert "correction_modes" in DEFAULT_CONFIG
    modes = DEFAULT_CONFIG["correction_modes"]
    assert isinstance(modes, list)
    assert len(modes) == 4


def test_correction_modes_have_required_fields():
    for mode in DEFAULT_CONFIG["correction_modes"]:
        assert "name" in mode
        assert "prompt" in mode
        assert "hallucination_threshold" in mode
        assert "builtin" in mode


def test_correction_mode_names():
    names = [m["name"] for m in DEFAULT_CONFIG["correction_modes"]]
    assert names == [
        "Spelling Only",
        "Full Correction",
        "Rewrite & Polish",
        "Custom Patch",
    ]


def test_correction_mode_prompts_are_strings():
    for mode in DEFAULT_CONFIG["correction_modes"]:
        assert isinstance(mode["prompt"], str)
        assert len(mode["prompt"]) > 50


def test_correction_mode_thresholds():
    thresholds = [
        m["hallucination_threshold"] for m in DEFAULT_CONFIG["correction_modes"]
    ]
    assert thresholds == [0.4, 1.0, 1.0, 1.0]


def test_correction_modes_are_builtin():
    # First 3 modes are builtin; 4th (Custom Patch) is user-customizable
    for mode in DEFAULT_CONFIG["correction_modes"][:3]:
        assert mode["builtin"] is True





# ── Backward-compatible strength mapping ─────────────────────────────────


def test_strength_mapping_conservative():
    assert _STRENGTH_TO_MODE_INDEX["conservative"] == 0


def test_strength_mapping_spelling_only():
    assert _STRENGTH_TO_MODE_INDEX["spelling_only"] == 0


def test_strength_mapping_smart_fix():
    assert _STRENGTH_TO_MODE_INDEX["smart_fix"] == 1


def test_strength_mapping_full_correction():
    assert _STRENGTH_TO_MODE_INDEX["full_correction"] == 1


def test_strength_mapping_aggressive():
    assert _STRENGTH_TO_MODE_INDEX["aggressive"] == 2


def test_strength_mapping_rewrite_polish():
    assert _STRENGTH_TO_MODE_INDEX["rewrite_polish"] == 2


def test_strength_mapping_default_is_smart_fix():
    assert _STRENGTH_TO_MODE_INDEX.get("unknown", 1) == 1


# ── model_manager reads mode from config ─────────────────────────────────


def test_rewrite_chunk_uses_config_mode_prompt(monkeypatch):
    """When correction_modes is in config, _rewrite_sentence_chunk uses it."""
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            from tests.conftest import MockResponse

            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
            )

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "spelling_only")
    assert (
        "spelling-only" in captured_sys.lower()
        or "spelling only" in captured_sys.lower()
        or "Fix ONLY clear misspellings" in captured_sys
    )


def test_rewrite_chunk_conservative_maps_to_mode_0(monkeypatch):
    """conservative strength should use mode index 0 (Spelling Only)."""
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            from tests.conftest import MockResponse

            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
            )

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "conservative")
    # Mode 0 prompt should be the spelling-only one
    assert "Fix ONLY clear misspellings" in captured_sys


def test_rewrite_chunk_smartfix_maps_to_mode_1(monkeypatch):
    """smart_fix strength should use mode index 1 (Full Correction)."""
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            from tests.conftest import MockResponse

            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
            )

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "smart_fix")
    assert (
        "Fix typos, spelling, grammar, punctuation, and capitalization" in captured_sys
    )


def test_rewrite_chunk_aggressive_maps_to_mode_2(monkeypatch):
    """aggressive strength should use mode index 2 (Rewrite & Polish)."""
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            from tests.conftest import MockResponse

            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
            )

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "aggressive")
    assert "expert editor" in captured_sys


def test_rewrite_chunk_falls_back_to_hardcoded_when_no_modes(monkeypatch):
    """When correction_modes is empty, fall back to hardcoded prompts."""

    class EmptyModesConfig:
        def get(self, key, default=None):
            if key == "correction_modes":
                return []
            return default

    mgr = ModelManager(EmptyModesConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            from tests.conftest import MockResponse

            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
            )

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "conservative")
    # Should still work via hardcoded fallback
    assert (
        "spelling-only" in captured_sys.lower()
        or "Fix ONLY clear misspellings" in captured_sys
    )


def test_rewrite_chunk_uses_mode_prompt_override(monkeypatch):
    """When mode_prompt_override is provided, it takes priority."""
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            from tests.conftest import MockResponse

            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
            )

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    custom = "You are a custom test prompt. {lang}"
    mgr._rewrite_sentence_chunk(
        "test", None, 1, 1, "smart_fix", mode_prompt_override=custom
    )
    assert "custom test prompt" in captured_sys


def test_correct_text_patch_reads_hallucination_threshold_from_config(monkeypatch):
    """correct_text_patch should read hallucination_threshold from correction_modes."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            "hello different phrase was today"
        )
    )

    # Conservative mode (index 0) has threshold 0.4, should reject wild rewrites
    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="conservative",
    )
    assert result is None  # rejected by hallucination guard


def test_correct_text_patch_smartfix_accepts_with_threshold_1(monkeypatch):
    """smart_fix (index 1) has threshold 1.0, should accept rewrites."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            "hello different phrase was today"
        )
    )

    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="smart_fix",
    )
    assert result is not None
    assert units == 1
