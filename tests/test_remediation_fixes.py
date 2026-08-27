"""Unit and regression tests for thinking models, tool-call unwrapping, and lifecycle synchronization."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QTimer

from stet.core.app import StetApp
from stet.core.text_utils import (
    _extract_content_from_response,
    _extract_rewritten_sentence,
    _extract_text_from_tool_call,
)
from stet.llm.model_manager import ModelManager
from stet.llm.worker import StreamWorker


# ── 1. Tool Call Extraction Tests ───────────────────────────────────────────


def test_extract_text_from_tool_call_lfm_single_quotes():
    raw = "<|tool_call_start|>[edit_text(content='I need you to spawn two agents, one to read the backend files.')]<|tool_call_end|>"
    assert _extract_text_from_tool_call(raw) == "I need you to spawn two agents, one to read the backend files."


def test_extract_text_from_tool_call_lfm_double_quotes():
    raw = '<|tool_call_start|>[edit_text(content="I need you to spawn two agents, one to read the backend files.")]<|tool_call_end|>'
    assert _extract_text_from_tool_call(raw) == "I need you to spawn two agents, one to read the backend files."


def test_extract_text_from_tool_call_with_escapes():
    raw = "<|tool_call_start|>[edit_text(content='Line 1\\nLine 2\\nDon\\'t forget.')]<|tool_call_end|>"
    extracted = _extract_text_from_tool_call(raw)
    assert extracted == "Line 1\nLine 2\nDon't forget."


def test_extract_text_from_tool_call_alternate_param_names():
    raw_text = "<|tool_call_start|>[edit_text(text='Clean rewritten sentence.')]<|tool_call_end|>"
    assert _extract_text_from_tool_call(raw_text) == "Clean rewritten sentence."

    raw_input = "<|tool_call_start|>[edit_text(input='Another sentence.')]<|tool_call_end|>"
    assert _extract_text_from_tool_call(raw_input) == "Another sentence."


def test_extract_text_from_tool_call_json_envelope():
    raw_json = json.dumps({"name": "edit_text", "arguments": {"content": "JSON extracted text"}})
    assert _extract_text_from_tool_call(raw_json) == "JSON extracted text"

    raw_nested_json = json.dumps({
        "function": "edit_text",
        "arguments": json.dumps({"text": "Nested JSON text"}),
    })
    assert _extract_text_from_tool_call(raw_nested_json) == "Nested JSON text"


def test_extract_text_from_tool_call_truncated():
    raw_trunc = "<|tool_call_start|>[edit_text(content='Truncated stream text without closing quote"
    assert _extract_text_from_tool_call(raw_trunc) == "Truncated stream text without closing quote"


def test_extract_text_from_tool_call_non_tool_returns_none():
    assert _extract_text_from_tool_call("This is normal prose without tool calls.") is None
    assert _extract_text_from_tool_call("") is None
    assert _extract_text_from_tool_call(None) is None


# ── 2. Response Extraction Tests ────────────────────────────────────────────


def test_extract_content_from_response_with_tool_calls_field():
    resp = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "edit_text",
                                "arguments": json.dumps({"content": "Extracted from tool_calls field"}),
                            }
                        }
                    ],
                },
            }
        ]
    }
    content, reason = _extract_content_from_response(resp)
    assert content == "Extracted from tool_calls field"
    assert reason == "tool_calls"


def test_extract_content_from_response_with_tool_call_in_content():
    resp = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "<|tool_call_start|>[edit_text(content='Content tool call')]<|tool_call_end|>",
                },
            }
        ]
    }
    content, reason = _extract_content_from_response(resp)
    assert content == "Content tool call"
    assert reason == "stop"


def test_extract_content_from_response_reasoning_fallback():
    resp = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": "",
                    "reasoning_content": "<think>Thinking about grammar...</think>Here is the corrected sentence.",
                },
            }
        ]
    }
    content, reason = _extract_content_from_response(resp)
    assert content == "Here is the corrected sentence."


def test_extract_rewritten_sentence_with_tool_call():
    raw = "<|tool_call_start|>[edit_text(content='I need you to spawn two agents, one to read the backend files.')]<|tool_call_end|>"
    res = _extract_rewritten_sentence(raw, original_text="i need you to spawn two agents...")
    assert res == "I need you to spawn two agents, one to read the backend files."


# ── 3. StreamWorker Reasoning Tests ─────────────────────────────────────────


class _FakeChunkedResponse:
    def __init__(self, chunks: list[dict]):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for chunk in self._chunks:
            yield f"data: {json.dumps(chunk)}".encode("utf-8")
        yield b"data: [DONE]"

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_stream_worker_reasoning_content_fallback(qtbot):
    worker = StreamWorker("http://fake-url", {"messages": [], "think": False})
    fake_resp = _FakeChunkedResponse([
        {"choices": [{"delta": {"reasoning_content": "<think>Thinking...</think>Corrected stream"}}]}
    ])

    tokens_received = []
    done_received = []
    worker.token.connect(tokens_received.append)
    worker.done.connect(done_received.append)

    with patch("stet.llm.worker.requests.Session") as MockSession:
        MockSession.return_value.post.return_value = fake_resp
        worker.run()

    assert done_received == ["Corrected stream"]


def test_stream_worker_reasoning_tokens_emission(qtbot):
    worker = StreamWorker("http://fake-url", {"messages": [], "think": True})
    fake_resp = _FakeChunkedResponse([
        {"choices": [{"delta": {"reasoning_content": "Thought 1 "}}]},
        {"choices": [{"delta": {"content": "Final content"}}]},
    ])

    received_reasoning = []
    received_content = []
    done_received = []
    worker.reasoning_token.connect(received_reasoning.append)
    worker.token.connect(received_content.append)
    worker.done.connect(done_received.append)

    with patch("stet.llm.worker.requests.Session") as MockSession:
        MockSession.return_value.post.return_value = fake_resp
        worker.run()

    assert done_received == ["Final content"]
    assert "".join(received_reasoning) == "Thought 1 "
    assert "".join(received_content) == "Final content"


# ── 4. ModelManager Lifecycle & Concurrency Tests ────────────────────────────


def test_model_manager_load_cancellation(tmp_path):
    from stet.core.config import ConfigManager
    cfg = ConfigManager()
    mgr = ModelManager(cfg, label="AC", model_path_key="model_path", keep_loaded_key="keep_model_loaded")
    fake_model = tmp_path / "model.gguf"
    fake_model.write_bytes(b"dummy")
    mgr.cfg.set("model_path", str(fake_model))

    gen_before = mgr._load_generation
    assert mgr._load_cancel_event.is_set() is False

    # Calling unload_model signals the cancel event and increments generation
    mgr.unload_model()
    assert mgr._load_cancel_event.is_set() is True
    assert mgr._load_generation == gen_before + 1
    assert mgr.loading is False
    assert mgr.is_ready() is False


# ── 5. StetApp Deferred Retry Management Tests ──────────────────────────────


@pytest.fixture
def mock_stet_app(monkeypatch, qtbot):
    monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
    with patch("stet.core.app.QSystemTrayIcon"):
        app = StetApp()
        app.ac_model = MagicMock(spec=ModelManager)
        app.ac_model.is_loaded.return_value = False
        app.ac_model.loading = False
        yield app


def test_stet_app_retry_cancellation_on_load(mock_stet_app):
    mock_stet_app._schedule_model_retry_if_needed("Model load error: timeout")
    assert mock_stet_app._retry_scheduled is True

    # Trigger model loaded event
    mock_stet_app._on_model_loaded()
    assert mock_stet_app._retry_scheduled is False
    assert mock_stet_app._retry_count == 0


def test_stet_app_retry_cancellation_on_unload(mock_stet_app):
    mock_stet_app._schedule_model_retry_if_needed("Model not found")
    assert mock_stet_app._retry_scheduled is True

    # User manually unloads model
    mock_stet_app._tray_unload_model()
    assert mock_stet_app._retry_scheduled is False
    assert mock_stet_app._retry_count == 0


def test_stet_app_deferred_retry_skips_when_loading(mock_stet_app):
    mock_stet_app._retry_scheduled = True
    mock_stet_app.ac_model.loading = True

    with patch("threading.Thread") as mock_thread:
        mock_stet_app._deferred_model_retry()
        # Should not launch a new thread
        mock_thread.assert_not_called()
        assert mock_stet_app._retry_scheduled is False
