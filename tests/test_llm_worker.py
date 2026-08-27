"""Tests for stet.llm.worker — StreamWorker signal emission and stop control."""

import json
from unittest.mock import patch

from PyQt6.QtCore import QThread

from stet.llm.worker import StreamWorker


def _make_payload(messages=None):
    """Build a valid StreamWorker payload dict."""
    return {
        "model": "test",
        "messages": messages or [{"role": "user", "content": "test"}],
    }


class _FakeChunkedResponse:
    """Simulates a requests.Response with streaming iter_lines."""

    def __init__(self, chunks: list[dict], status_code: int = 200):
        self._chunks = chunks
        self.status_code = status_code
        self.ok = status_code == 200

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.exceptions.HTTPError(f"Status: {self.status_code}")

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


# ── Construction and signal wiring ────────────────────────────────────────


class TestStreamWorkerSignals:
    """StreamWorker emits token/done/error signals correctly."""

    def test_is_qthread(self):
        w = StreamWorker("http://localhost:8080/v1/chat/completions", _make_payload())
        assert isinstance(w, QThread)

    def test_has_required_signals(self):
        assert hasattr(StreamWorker, "token")
        assert hasattr(StreamWorker, "done")
        assert hasattr(StreamWorker, "error")

    def test_stop_flag(self):
        w = StreamWorker("http://localhost:8080/v1/chat/completions", _make_payload())
        assert w._stop is False
        w.stop()
        assert w._stop is True

    def test_run_emits_tokens_and_done(self, qtbot):
        """Full streaming cycle: token signals for each chunk, done at end."""
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
        ]
        fake_resp = _FakeChunkedResponse(chunks)

        w = StreamWorker(
            "http://localhost:8080/v1/chat/completions",
            _make_payload(),
        )

        tokens_received = []
        done_received = []
        w.token.connect(tokens_received.append)
        w.done.connect(done_received.append)

        with patch("stet.llm.worker.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.return_value = fake_resp
            w.run()

        assert tokens_received == ["Hello", " world"]
        assert len(done_received) == 1
        assert "Hello world" in done_received[0]

    def test_run_emits_error_on_exception(self, qtbot):
        """Network error triggers error signal, not crash."""
        w = StreamWorker(
            "http://localhost:8080/v1/chat/completions",
            _make_payload(),
        )

        errors = []
        w.error.connect(errors.append)

        with patch("stet.llm.worker.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.side_effect = ConnectionError("refused")
            w.run()

        assert len(errors) == 1

    def test_stop_interrupts_streaming(self, qtbot):
        """Calling stop() mid-stream should halt token emission."""
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
        ]

        w = StreamWorker(
            "http://localhost:8080/v1/chat/completions",
            _make_payload(),
        )

        tokens = []

        def capture_and_stop(t):
            tokens.append(t)
            w.stop()

        w.token.connect(capture_and_stop)

        with patch("stet.llm.worker.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.return_value = _FakeChunkedResponse(chunks)
            w.run()

        # Should have stopped after first token
        assert len(tokens) <= 2

    def test_worker_timeout(self, qtbot):
        """Watchdog timeout triggers StetStreamTimeout error and prevents done emission."""
        import time

        class _BlockingResponse:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                # Simulates deadlocked server that blocks on iter_lines
                time.sleep(1.0)
                yield b"data: {\"choices\": [{\"delta\": {\"content\": \"late\"}}]}"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        w = StreamWorker(
            "http://localhost:8080/v1/chat/completions",
            _make_payload(),
            watchdog_timeout=0.05,
        )

        tokens = []
        dones = []
        errors = []
        w.token.connect(tokens.append)
        w.done.connect(dones.append)
        w.error.connect(errors.append)

        with patch("stet.llm.worker.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.return_value = _BlockingResponse()
            w.run()

        assert len(errors) == 1
        assert errors[0].startswith("StetStreamTimeout")
        assert len(dones) == 0
        assert len(tokens) == 0

    def test_worker_timeout_uses_separate_flag(self, qtbot):
        """Normal stop() sets _stop and suppresses error emission, distinct from abort_timeout()."""
        w = StreamWorker(
            "http://localhost:8080/v1/chat/completions",
            _make_payload(),
            watchdog_timeout=10.0,
        )
        errors = []
        w.error.connect(errors.append)

        # Call stop() before/during run
        w.stop()
        assert w._stop is True
        assert w._timeout_aborted is False

        with patch("stet.llm.worker.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.side_effect = ConnectionError("cancelled")
            w.run()

        # Suppressed because _stop is True and _timeout_aborted is False
        assert len(errors) == 0

    def test_worker_watchdog_cancelled_on_token(self, qtbot):
        """Arrival of tokens cancels the watchdog timer so streaming finishes cleanly."""
        chunks = [
            {"choices": [{"delta": {"content": "Fast"}}]},
            {"choices": [{"delta": {"content": " response"}}]},
        ]
        w = StreamWorker(
            "http://localhost:8080/v1/chat/completions",
            _make_payload(),
            watchdog_timeout=0.2,
        )
        tokens = []
        dones = []
        errors = []
        w.token.connect(tokens.append)
        w.done.connect(dones.append)
        w.error.connect(errors.append)

        with patch("stet.llm.worker.requests.Session") as MockSession:
            session_instance = MockSession.return_value
            session_instance.post.return_value = _FakeChunkedResponse(chunks)
            w.run()

        assert tokens == ["Fast", " response"]
        assert len(dones) == 1
        assert len(errors) == 0
        assert w._watchdog is None

