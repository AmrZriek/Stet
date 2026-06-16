import pytest
from stet.llm.model_manager import ModelManager

class MockConfig:
    def get(self, key, default=None):
        if key == "correction_modes":
            return [
                {"name": "Spelling Only"},
                {"name": "Full Correction"},
                {"name": "Rewrite & Polish"}
            ]
        return default

def test_url_masking_single(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com/path?q=1 today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "⟦U1⟧" in captured_text
    assert "https://example.com" not in captured_text
    assert result == original

def test_url_masking_multiple(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "see https://a.com and http://b.org and www.c.io"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "⟦U1⟧" in captured_text
    assert "⟦U2⟧" in captured_text
    assert "⟦U3⟧" in captured_text
    assert result == original

def test_url_masking_mixed(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "mail me at a@b.com or see https://x.io"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert "⟦U1⟧" in captured_text
    assert "⟦U2⟧" in captured_text
    assert result == original

def test_url_masking_paths(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    captured_text = ""
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal captured_text
        captured_text = chunk_text
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    win_path = r"open C:\Users\me\file.txt now"
    result, _ = mgr.correct_text_patch(win_path, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert result == win_path

    unix_path = "run /usr/local/bin/foo now"
    result, _ = mgr.correct_text_patch(unix_path, strength="full_correction")
    assert "⟦U1⟧" in captured_text
    assert result == unix_path

def test_url_masking_bare_https(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"
    
    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "use https for security"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")
    assert result == original

def test_url_masking_missing_sentinel(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")
    
    # When sentinel is lost, it falls back to streaming, which returns None in unit test without mock,
    # or chunk rejection means it retains original.
    # The actual implementation of correct_text_patch says:
    # "If dict pre-pass changed nothing AND no unit ever succeeded, report total failure so the caller falls back to streaming. "
    # So it returns None, len(chunks).
    assert result is None or result == original

def test_url_masking_mangled_sentinel(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "⟦u1⟧")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")
    
    assert result is None or result == original
