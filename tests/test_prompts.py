from stet.constants import DEFAULT_CONFIG
from stet.ui.main_window import CorrectionWindow


_SENTENCE_REWRITE_PROMPT_CONSERVATIVE = DEFAULT_CONFIG["correction_modes"][0]["prompt"]
_SENTENCE_REWRITE_PROMPT = DEFAULT_CONFIG["correction_modes"][1]["prompt"]
_SENTENCE_REWRITE_PROMPT_AGGRESSIVE = DEFAULT_CONFIG["correction_modes"][2]["prompt"]


def test_conservative_prompt_has_core_instruction():
    # The conservative prompt should describe the spelling-only task clearly
    assert "spelling" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE.lower()
    assert "typing error" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE.lower()


def test_conservative_prompt_preservation_rule():
    assert (
        "Preserve every other character exactly"
        in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    )


def test_streaming_conservative_rules_differ_from_patch():
    import inspect

    source = inspect.getsource(CorrectionWindow._start_streaming_correction)
    assert "CONTENT_BEGIN" in source


def test_prompt_word_count_budget():
    words = len(_SENTENCE_REWRITE_PROMPT_CONSERVATIVE.split())
    assert words < 350


def test_smartfix_and_aggressive_prompts_are_instruction_safe():
    for prompt in (_SENTENCE_REWRITE_PROMPT, _SENTENCE_REWRITE_PROMPT_AGGRESSIVE):
        # Prompts should clearly state the correction task
        assert "correct" in prompt.lower() or "rewrite" in prompt.lower()


def test_smartfix_prompt_preserves_author():
    assert "author" in _SENTENCE_REWRITE_PROMPT.lower()


def test_prompts_treat_repetition_as_user_content():
    for prompt in (
        _SENTENCE_REWRITE_PROMPT_CONSERVATIVE,
        _SENTENCE_REWRITE_PROMPT,
    ):
        # New prompts use "repetition" as a preservation concept
        assert "repetition" in prompt.lower() or "repeated" in prompt.lower()


def test_aggressive_prompt_allows_clarity_edits_without_value_changes():
    assert "Rewrite and polish" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE
    assert "factual claims" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE.lower()
    assert "Do not invent" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE


def test_apply_hunk_guard():
    from stet.core.text_utils import apply_hunk_guard

    # Mode 0: Spelling only. Small edits accepted, large edits/deletes/inserts rejected.
    # Typos (edit distance <= 2)
    assert apply_hunk_guard("received", "recieved", 0) == "recieved"
    # Proper noun / brand casing changes accepted
    assert apply_hunk_guard("iphone", "iPhone", 0) == "iPhone"
    assert apply_hunk_guard("antigravbity", "Antigravity", 0) == "Antigravity"
    # Contractions accepted
    assert apply_hunk_guard("cant", "can't", 0) == "can't"
    # General punctuation changes / removals rejected
    assert apply_hunk_guard("UAE.", "UAE", 0) == "UAE."
    assert apply_hunk_guard("yesterday.", "yesterday", 0) == "yesterday."
    # Large replacements rejected (keeps original)
    assert apply_hunk_guard("hello", "goodbye", 0) == "hello"
    # Deletes rejected (keeps original)
    assert apply_hunk_guard("hello world", "hello", 0) == "hello world"
    # Inserts rejected
    assert apply_hunk_guard("hello", "hello world", 0) == "hello"

    # Mode 1: Full correction. Small deletes/inserts accepted.
    # Single-word delete accepted
    assert apply_hunk_guard("hello world", "hello", 1) == "hello"
    # Multi-word delete rejected
    assert apply_hunk_guard("hello beautiful green world", "hello", 1) == "hello beautiful green world"
    # Single-word insert accepted
    assert apply_hunk_guard("hello", "hello world", 1) == "hello world"
    # Multi-word insert rejected
    assert apply_hunk_guard("hello", "hello beautiful world", 1) == "hello"

    # Mode 2: Rewrite & Polish. Uses sequence matcher ratio (<= 0.6 diff ratio accepted).
    # Small rewrite accepted
    assert apply_hunk_guard("hello world", "hi world", 2) == "hi world"
    # Extreme rewrite rejected
    assert apply_hunk_guard("hello world", "completely different text here", 2) == "hello world"


def test_apply_hunk_guard_rejects_sentinel_deletion():
    """Hunk guard must never accept a delete op that removes a sentinel
    (__STET_PROTECTED_1__, __STET_PROTECTED_2__, etc.) — these are masked
    URLs/paths that must survive.
    Mode 1 (Full Correction) uses token-level hunk guard where sentinel
    protection applies. Mode 2 (Rewrite) uses character-level ratio guard
    and takes a different code path.
    """
    from stet.core.text_utils import apply_hunk_guard

    # Mode 1 (Full Correction): single-word delete is normally accepted,
    # but a sentinel-containing hunk must be rejected.
    sentinel_text = "visit __STET_PROTECTED_1__ today"
    assert apply_hunk_guard(sentinel_text, "visit today", 1) == sentinel_text

    # Non-sentinel single-word delete in mode 1 should still be accepted.
    assert apply_hunk_guard("hello world", "hello", 1) == "hello"

    # Sentinel in a replace op should also be preserved (mode 1).
    assert apply_hunk_guard("see __STET_PROTECTED_1__ now", "see now", 1) == "see __STET_PROTECTED_1__ now"


def test_gemma_model_messages_format(monkeypatch):
    from stet.llm.model_manager import ModelManager

    cfg = {
        "model_path": "gemma-4-E2B-it.gguf",
        "context_size": 12800,
        "temperature": 0.0,
        "top_k": 40,
        "top_p": 0.95,
        "min_p": 0.05,
        "repeat_penalty": 1.0,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
    }

    class MockConfig:
        def get(self, key, default=None):
            return cfg.get(key, default)

    mgr = ModelManager(MockConfig(), "model_path")
    mgr.is_loaded = lambda: True
    mgr._chat_url = lambda: "http://fake"

    captured_payload = None

    class MockResponse:
        def json(self):
            return {"choices": [{"message": {"content": "corrected"}}]}
        def raise_for_status(self):
            pass

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_payload
            captured_payload = json
            return MockResponse()
        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._rewrite_sentence_chunk("hello world", None, 1, 1, "full_correction")

    assert captured_payload is not None
    messages = captured_payload["messages"]
    # For Gemma, it should merge system and user prompt into a single user message
    # No assistant prefill — just the user message
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "Correct the text completely" in messages[0]["content"]
    assert "hello world" in messages[0]["content"]

