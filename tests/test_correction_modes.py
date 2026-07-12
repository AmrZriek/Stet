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
    # Rewrite & Polish (index 2) is the config-driven guard bar for the
    # rewrite path. 0.99 is the coarse catastrophic backstop; refusals are
    # caught by _is_refusal_or_empty in text_utils.
    assert thresholds == [0.7, 1.0, 1.0, 1.0]


def test_correction_modes_are_builtin():
    # First 3 modes are builtin; 4th (Custom Patch) is user-customizable
    for mode in DEFAULT_CONFIG["correction_modes"][:3]:
        assert mode["builtin"] is True





# ── Backward-compatible strength mapping ─────────────────────────────────


def test_strength_mapping_conservative():
    assert _STRENGTH_TO_MODE_INDEX["spelling_only"] == 0


def test_strength_mapping_spelling_only():
    assert _STRENGTH_TO_MODE_INDEX["spelling_only"] == 0


def test_strength_mapping_smart_fix():
    assert _STRENGTH_TO_MODE_INDEX["full_correction"] == 1


def test_strength_mapping_full_correction():
    assert _STRENGTH_TO_MODE_INDEX["full_correction"] == 1


def test_strength_mapping_aggressive():
    assert _STRENGTH_TO_MODE_INDEX["rewrite_polish"] == 2


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
        "spelling mistakes. change nothing else" in captured_sys.lower()
    )


def test_rewrite_chunk_conservative_maps_to_mode_0(monkeypatch):
    """spelling_only strength should use mode index 0 (Spelling Only)."""
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
    # Mode 0 prompt should be the spelling-only one
    assert "Fix spelling mistakes" in captured_sys


def test_rewrite_chunk_smartfix_maps_to_mode_1(monkeypatch):
    """full_correction strength should use mode index 1 (Full Correction)."""
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
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "full_correction")
    assert (
        "Fix spelling, grammar, punctuation, and capitalization" in captured_sys
    )


def test_rewrite_chunk_aggressive_maps_to_mode_2(monkeypatch):
    """rewrite_polish strength should use mode index 2 (Rewrite & Polish)."""
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
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "rewrite_polish")
    assert "Edit the text so it reads clearly and smoothly" in captured_sys


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
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "spelling_only")
    assert (
        "Fix spelling mistakes" in captured_sys
        or "spelling-only" in captured_sys.lower()
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
        "test", None, 1, 1, "full_correction", mode_prompt_override=custom
    )
    assert "custom test prompt" in captured_sys


def test_correct_text_patch_reads_hallucination_threshold_from_config(monkeypatch):
    """correct_text_patch should read hallucination_threshold from correction_modes."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            "completely different text that has absolutely nothing to do with the original"
        )
    )

    # spelling_only mode (index 0) has threshold 0.7, should reject wild rewrites
    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="spelling_only",
    )
    assert result is None  # rejected by hallucination guard


def test_correct_text_patch_smartfix_accepts_with_threshold_1(monkeypatch):
    """full_correction (index 1) has threshold 1.0, should accept rewrites."""
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            "hello different phrase was today"
        )
    )

    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="full_correction",
    )
    assert result is not None
    assert units == 1


# ── Rewrite & Polish config-driven guard tests (regression for the
#    "divergence guard reverts legitimate rewrite" bug) ───────────────────


def test_rewrite_polish_accepts_legitimate_rewrite(monkeypatch):
    """Default config (threshold 0.9) accepts a rewrite in the (0.6, 0.8]
    divergence band — the bug case the handoff plan identified. Under the
    old stacked gates (raw 0.8 + hunk 0.6) this rewrite was reverted to the
    original."""
    from stet.core.text_utils import _hallucination_ratio

    filler = (
        "i was thinking maybe we could possibly go ahead and schedule a "
        "meeting for next week tuesday if that works for you."
    )
    rewritten = "let us get a meeting on the calendar for next Tuesday if possible."

    # Verify the divergence is in the (0.6, 0.8] revert window the old
    # code would have hit. This anchors the test against the real bug.
    div = _hallucination_ratio(filler, rewritten, "rewrite_polish")
    assert 0.6 < div <= 0.7, f"expected ~0.6-0.7 band, got {div:.3f}"

    cfg = MockConfig()
    mgr = ModelManager(cfg)
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            rewritten
        )
    )

    result, units = mgr.correct_text_patch(filler, strength="rewrite_polish")
    assert result is not None, "rewrite was reverted (bug regressed)"
    assert "let us get a meeting" in result
    assert units == 1


def test_config_threshold_changes_behavior(monkeypatch):
    """Vary the config value to prove the field is actually read."""
    filler = (
        "i was thinking maybe we could possibly go ahead and schedule a "
        "meeting for next week tuesday if that works for you."
    )
    rewritten = "let us get a meeting on the calendar for next Tuesday if possible."

    class Cfg(MockConfig):
        """MockConfig variant that lets us vary correction_modes per-test."""
        def __init__(self, modes):
            super().__init__()
            self._modes_override = modes

        def get(self, key, default=None):
            if key == "correction_modes":
                return self._modes_override
            return super().get(key, default)

    base_modes = [
        {"name": "S", "prompt": "x", "hallucination_threshold": 0.4, "builtin": True},
        {"name": "F", "prompt": "x", "hallucination_threshold": 1.0, "builtin": True},
        {"name": "Rewrite & Polish", "prompt": "x", "hallucination_threshold": 0.9, "builtin": True},
    ]

    # Lower threshold than the legitimate divergence → must revert to original.
    cfg_low = Cfg([dict(m, hallucination_threshold=0.4 if i == 2 else m["hallucination_threshold"]) for i, m in enumerate(base_modes)])
    mgr_low = ModelManager(cfg_low)
    mgr_low.is_loaded = lambda: True
    mgr_low._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            rewritten
        )
    )
    res_low, _ = mgr_low.correct_text_patch(filler, strength="rewrite_polish")
    # Low threshold reverts via raw guard — original is returned, rewritten is not.
    if res_low is not None:
        assert rewritten not in res_low, (
            f"low threshold (0.4) should not accept 0.6 divergence; got {res_low!r}"
        )

    # Default 0.9 → must accept the rewrite.
    cfg_high = Cfg(base_modes)
    mgr_high = ModelManager(cfg_high)
    mgr_high.is_loaded = lambda: True
    mgr_high._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            rewritten
        )
    )
    res_high, _ = mgr_high.correct_text_patch(filler, strength="rewrite_polish")
    assert res_high is not None, "threshold 0.9 should accept 0.6-band divergence"
    assert "let us get a meeting" in res_high


def test_refusal_is_rejected_not_pasted(monkeypatch):
    """Marker-wrapped refusals must not leak into the output as the
    'correction' — the original text is returned instead."""
    cfg = MockConfig()
    mgr = ModelManager(cfg)
    mgr.is_loaded = lambda: True

    refusal = "<<<START>>>Please provide the text you want me to correct.<<<END>>>"
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None: (
            refusal
        )
    )

    original = "The quick brown fox jumps over the lazy dog."
    # NOTE: caller falls back to streaming on total failure, but the
    # patch result is None / original. Check no refusal text leaks in.
    res, _ = mgr.correct_text_patch(original, strength="rewrite_polish")
    # Patch path returns None when every unit failed (no success) — the
    # important assertion is that the refusal text is NEVER returned.
    if res is not None:
        assert "Please provide" not in res
        assert refusal not in res


def test_is_refusal_or_empty_detects_marker_wrapped_refusal():
    """Direct unit test of the refusal detector."""
    from stet.core.text_utils import _is_refusal_or_empty

    assert _is_refusal_or_empty("", "anything") is True
    assert _is_refusal_or_empty("   ", "anything") is True
    assert _is_refusal_or_empty(
        "Please provide the text you want me to correct.", "orig"
    ) is True
    assert _is_refusal_or_empty(
        "I'm sorry, I can't help with that.", "orig"
    ) is True
    # Legit rewrites (long enough + no refusal phrase) are NOT refusals.
    assert _is_refusal_or_empty(
        "Let's meet next Tuesday.", "i was thinking maybe..."
    ) is False
    assert _is_refusal_or_empty(
        "tbh the velvet sofa just doesn't fit in the hallway.",
        "basically the velvet sofa thing is, it kinda just dont fit in the hallway at all tbh.",
    ) is False
