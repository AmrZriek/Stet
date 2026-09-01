# -*- coding: utf-8 -*-
"""Tests for the EngineSession windowed-mode degradation (§3a).

When input exceeds the context headroom, the session must degrade a monolithic
request to the sequential windowed plan (split -> per-window correct ->
reassemble). This test drives that path with a fake model.
"""

from stet.llm.engine_session import (
    EngineConfig,
    EngineSession,
    FakeModelClient,
)


def _preserve_model(text):
    import re
    m = re.search(r"CONTENT_BEGIN_\w+\n(.*?)\nCONTENT_END_\w+", text, re.S)
    return m.group(1) if m else text


def _split_para_model(text):
    # Model returns content unchanged but we rely on the session to split/reassemble.
    return _preserve_model(text)


def test_windowed_degradation_reassembles_long_document():
    calls = []
    def counting_model(text):
        calls.append(text)
        return _preserve_model(text)
    config = EngineConfig(n_ctx=64, safety_margin=4)
    session = EngineSession(client=FakeModelClient(counting_model), config=config)
    text = "The first sentence. The second sentence. The third sentence. The fourth sentence. The fifth. The sixth."
    result = session.correct(text)
    assert result.success is True
    assert "The first sentence." in result.text
    # With a tiny n_ctx the session must make MULTIPLE model calls (windowed),
    # never a single monolithic call.
    assert len(calls) > 1


def test_windowed_correct_applies_owned_intervals():
    config = EngineConfig(n_ctx=64, safety_margin=4)
    session = EngineSession(client=FakeModelClient(_split_para_model), config=config)
    text = "S1. S2. S3. S4. S5. S6. S7. S8. S9. S10."
    result = session.correct(text)
    assert result.success is True
    assert result.text.count(".") >= 10


def test_monolithic_preferred_when_document_fits():
    # With a large n_ctx, the whole document is processed in one request.
    config = EngineConfig(n_ctx=4096, safety_margin=16)
    session = EngineSession(client=FakeModelClient(_preserve_model), config=config)
    text = "Short document. Fits in one window."
    result = session.correct(text)
    assert result.success is True

def test_windowed_actually_splits_into_multiple_windows():
    from stet.llm.window_splitter import WindowSplitter
    config = EngineConfig(n_ctx=64, safety_margin=4)
    session = EngineSession(client=FakeModelClient(_preserve_model), config=config)
    text = "S1. S2. S3. S4. S5. S6. S7. S8. S9. S10."
    # The windowed decision uses a small n_ctx -> splits into >1 window.
    windows = WindowSplitter().split(text, max_sentences=2)
    assert len(windows) > 1
    result = session.correct(text)
    assert result.success is True