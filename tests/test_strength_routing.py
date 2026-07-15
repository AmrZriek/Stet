import threading

from stet.llm.model_manager import ModelManager


class MockConfig:
    def get(self, key, default=None):
        return default


class MockResponse:
    ok = True
    status_code = 200

    def __init__(self, content="<<<START>>>test<<<END>>>"):
        self._content = content

    def json(self):
        return {
            "choices": [
                {
                    "message": {"content": self._content},
                    "finish_reason": "stop",
                }
            ]
        }

    def raise_for_status(self):
        pass


def test_rewrite_chunk_selects_conservative_prompt(monkeypatch):
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            return MockResponse()

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "spelling_only")
    assert "Correct every clear spelling" in captured_sys


def test_rewrite_chunk_selects_smartfix_prompt(monkeypatch):
    mgr = ModelManager(MockConfig())
    captured_sys = ""

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_sys
            captured_sys = json["messages"][0]["content"]
            return MockResponse()

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"
    mgr._rewrite_sentence_chunk("test", None, 1, 1, "full_correction")
    assert (
        "Correct the text completely" in captured_sys
    )


def test_rewrite_chunk_selects_aggressive_prompt(monkeypatch):
    mgr = ModelManager(MockConfig())
    captured_payload = {}

    class MockSession:
        def post(self, url, json, timeout):
            nonlocal captured_payload
            captured_payload = json
            return MockResponse("<<<START>>>Improved test<<<END>>>")

        def close(self):
            pass

    monkeypatch.setattr("requests.Session", MockSession)
    mgr._chat_url = lambda: "http://fake"

    result = mgr._rewrite_sentence_chunk("rough test", None, 1, 1, "rewrite_polish")

    assert result == "Improved test"
    system_prompt = captured_payload["messages"][0]["content"]
    assert "flow" in system_prompt
    assert "clarity" in system_prompt
    assert captured_payload["think"] is False


def test_correct_text_patch_passes_strength_to_chunks(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    captured_strength = ""

    def mock_rewrite(
        chunk_text,
        custom_sys,
        idx,
        total,
        strength,
        cancel_event=None,
        mode_prompt_override=None,
        session=None,
        profile=None,
    ):
        nonlocal captured_strength
        captured_strength = strength
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    # Need structurally dirty so it bypasses fast-path
    mgr.correct_text_patch("test text without caps", strength="spelling_only")
    assert captured_strength == "spelling_only"


def test_correct_text_patch_smartfix_accepts_rewrite_with_guard_disabled(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None, profile=None: (
            "hello different this is test"
        )
    )

    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="full_correction",
    )

    # Hallucination guard is disabled for full_correction (threshold = 1.0).
    # The rewrite should be accepted. Capitalization is left to the LLM
    # (the deterministic cap post-fix was removed 2026-06-23), so the mock's
    # lowercase casing is preserved verbatim.
    assert result == "hello different this is test"
    assert units == 1


def test_correct_text_patch_aggressive_accepts_rewrite_with_guard_disabled(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None, profile=None: (
            "hello new world this is test"
        )
    )

    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="rewrite_polish",
    )

    # Both hallucination guard and repetition-loss guard are disabled
    # for rewrite_polish mode. The rewrite should be accepted. Capitalization is
    # left to the LLM (cap post-fix removed 2026-06-23); mock casing preserved.
    assert result == "hello new world this is test"
    assert units == 1


def test_correct_text_patch_conservative_rejects_wild_rewrite(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr._rewrite_sentence_chunk = (
        lambda chunk_text, custom_sys, idx, total, strength, cancel_event=None, mode_prompt_override=None, session=None, profile=None: (
            "completely different text that has absolutely nothing to do with the original"
        )
    )

    result, units = mgr.correct_text_patch(
        "hello world this is test",
        strength="spelling_only",
    )

    # spelling_only hallucination guard (threshold = 0.7) still active —
    # reject wild rewrites to protect names/places/values.
    assert result is None
    assert units == 1


def test_correction_window_initial_strength_overrides_global_config(monkeypatch):
    from stet.ui.main_window import CorrectionWindow

    class WindowCfg:
        def get(self, key, default=None):
            values = {
                "streaming_strength": "spelling_only",
                "system_prompt": "",
            }
            return values.get(key, default)

    class Model:
        def __init__(self):
            self.calls = []

        def is_loaded(self):
            return True

        def correct_text_patch(
            self,
            text,
            custom_sys=None,
            strength=None,
            cancel_event=None,
            mode_prompt_override=None,
        ):
            self.calls.append(strength)
            return "clean text", 1

    emitted = []
    model = Model()
    win = CorrectionWindow.__new__(CorrectionWindow)
    win.original = "clean text"
    win.ac_model = model
    win.cfg = WindowCfg()
    win._cancel_event = threading.Event()
    win._correction_cancelled = False
    win._correction_ready = type(
        "Emitter", (), {"emit": lambda self, *args: emitted.append(args)}
    )()
    win._start_streaming_correction = lambda *args: None
    win._initial_strength = "rewrite_polish"

    CorrectionWindow._do_correction(win)

    assert model.calls == ["rewrite_polish"]


def test_mock_returns_conservative_output_via_chunk(monkeypatch):
    """Default conftest mock returns spelling_only-style output for spelling_only strength."""
    mgr = ModelManager(MockConfig())
    mgr._chat_url = lambda: "http://fake/v1/chat/completions"
    result = mgr._rewrite_sentence_chunk(
        "Teh project recieved teh update.", None, 1, 1, "spelling_only"
    )
    assert result is not None
    assert "Teh" in result
    assert "recieved" in result


def test_mock_returns_smartfix_output_via_chunk(monkeypatch):
    """Default conftest mock returns full_correction-style output for full_correction strength."""
    mgr = ModelManager(MockConfig())
    mgr._chat_url = lambda: "http://fake/v1/chat/completions"
    result = mgr._rewrite_sentence_chunk(
        "Teh project recieved teh update.", None, 1, 1, "full_correction"
    )
    assert result is not None
    assert "The project received" in result


def test_mock_returns_aggressive_output_via_chunk(monkeypatch):
    """Default conftest mock returns rewrite_polish-style output for rewrite_polish strength."""
    mgr = ModelManager(MockConfig())
    mgr._chat_url = lambda: "http://fake/v1/chat/completions"
    result = mgr._rewrite_sentence_chunk(
        "Teh project recieved teh update.", None, 1, 1, "rewrite_polish"
    )
    assert result is not None
    assert "Project update" in result


def test_mock_output_differs_by_strength_via_patch():
    """Verify each strength produces a different final output via correct_text_patch.

    Relies on the conftest mock that inspects the system prompt and returns
    a strength-appropriate response. The dict pre-pass is disabled so the
    LLM path is exercised for all strengths.
    """
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True

    results = {}
    for s in ("spelling_only", "full_correction", "rewrite_polish"):
        result, _ = mgr.correct_text_patch(
            "Teh project recieved teh update.", strength=s
        )
        results[s] = result

    assert len(set(results.values())) == 3, (
        f"Each strength should produce a unique output, got: {results}"
    )
