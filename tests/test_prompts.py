from stet.constants import DEFAULT_CONFIG
from stet.ui.main_window import CorrectionWindow


_SENTENCE_REWRITE_PROMPT_CONSERVATIVE = DEFAULT_CONFIG["correction_modes"][0]["prompt"]
_SENTENCE_REWRITE_PROMPT = DEFAULT_CONFIG["correction_modes"][1]["prompt"]
_SENTENCE_REWRITE_PROMPT_AGGRESSIVE = DEFAULT_CONFIG["correction_modes"][2]["prompt"]


def test_conservative_prompt_has_fewshot():
    # Does the conservative prompt contain any <<<START>>> examples?
    assert "<<<START>>>" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    assert "EXAMPLE" in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE


def test_conservative_prompt_abstract_terms_count():
    assert (
        "Fix spelling mistakes. Change nothing else."
        in _SENTENCE_REWRITE_PROMPT_CONSERVATIVE
    )
    assert (
        "Copy punctuation, capitalization, grammar, word order, line breaks, and spacing exactly as given."
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
        assert "output" in prompt.lower()


def test_smartfix_prompt_adds_missing_terminal_punctuation():
    assert "Add missing terminal punctuation" in _SENTENCE_REWRITE_PROMPT


def test_prompts_treat_repetition_as_user_content():
    for prompt in (
        _SENTENCE_REWRITE_PROMPT_CONSERVATIVE,
        _SENTENCE_REWRITE_PROMPT,
    ):
        assert "repeated words" in prompt.lower()
        assert "repeated sentences" in prompt.lower()


def test_aggressive_prompt_allows_clarity_edits_without_value_changes():
    assert "reads clearly and smoothly" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE
    assert (
        "Keep every fact, claim, name, and number exactly as given. Invent nothing."
        in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE
    )
    assert "OUTPUT" in _SENTENCE_REWRITE_PROMPT_AGGRESSIVE


def test_apply_hunk_guard():
    from stet.core.text_utils import apply_hunk_guard

    # Mode 0: Spelling only. Small edits accepted, large edits/deletes/inserts rejected.
    # Typos (edit distance <= 2)
    assert apply_hunk_guard("received", "recieved", 0) == "recieved"
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
    mgr._rewrite_sentence_chunk("hello world", None, 1, 1, "smart_fix")

    assert captured_payload is not None
    messages = captured_payload["messages"]
    # For Gemma, it should merge system and user prompt into a single user message
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "<<<START>>>\n"
    assert "Fix spelling, grammar, punctuation, and capitalization" in messages[0]["content"]
    assert "hello world" in messages[0]["content"]

