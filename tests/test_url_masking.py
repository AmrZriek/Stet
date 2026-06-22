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

def test_url_only_text_is_preserved_without_rewrite(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    rewrite_calls = 0
    def mock_rewrite(chunk_text, *args, **kwargs):
        nonlocal rewrite_calls
        rewrite_calls += 1
        return chunk_text

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "https://a.example/path?q=1 https://b.example/watch?v=2"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == original
    assert rewrite_calls == 0

def test_url_masking_missing_sentinel(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == original

def test_url_masking_mangled_sentinel(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr.is_loaded = lambda: True
    mgr.label = "Mock"

    def mock_rewrite(chunk_text, *args, **kwargs):
        return chunk_text.replace("⟦U1⟧", "⟦u1⟧")

    mgr._rewrite_sentence_chunk = mock_rewrite

    original = "visit https://example.com today"
    result, _ = mgr.correct_text_patch(original, strength="full_correction")

    assert result == original
