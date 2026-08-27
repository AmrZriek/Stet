"""Tests for stet.llm.model_manager -- subprocess management, Job Object, health checks."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from stet.core.config import ConfigManager
from stet.core.text_utils import _extract_rewritten_sentence
from stet.llm.gguf_info import GgufModelInfo, GgufReadError
from stet.llm.model_manager import (
    _STRENGTH_TO_MODE_INDEX,
    _detect_loaded_backend,
    ModelManager,
    _create_job_object_for_subprocess,
    _estimate_tokens,
)
from tests.conftest import MockResponse

# Store the un-mocked load_model method at import time so tests can restore it
_ORIGINAL_LOAD_MODEL = ModelManager.load_model


def _make_min_cfg(model_path: str):
    data = {
        "model_path": model_path,
        "ac_model_path": model_path,
        "server_host": "127.0.0.1",
        "server_port": 8080,
        "context_size": 4096,
        "gpu_layers": 0,
        "temperature": 0.1,
        "top_k": 40,
        "top_p": 0.95,
        "min_p": 0.05,
        "keep_model_loaded": False,
        "idle_timeout_seconds": 300,
    }
    cfg = MagicMock()
    cfg.get = lambda key, default=None: data.get(key, default)
    cfg.set = MagicMock()
    return cfg


# -- Helpers ------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Return a ConfigManager with a temp config file."""
    config_file = tmp_path / "config.json"
    model_path = tmp_path / "fake.gguf"
    model_path.touch()
    config_file.write_text(
        json.dumps(
            {
                "model_path": str(model_path),
                "ac_model_path": str(model_path),
                "server_binary": "",
                "port": 8080,
                "context_size": 4096,
                "gpu_layers": 0,
                "temperature": 0.1,
                "top_k": 40,
                "top_p": 0.95,
                "min_p": 0.05,
                "keep_model_loaded": False,
                "idle_timeout_seconds": 300,
                "custom_templates": [],
            }
        ),
        encoding="utf-8",
    )

    import stet.core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return ConfigManager()


@pytest.fixture
def manager(cfg):
    """Return a ModelManager instance."""
    return ModelManager(cfg)


# -- Construction -------------------------------------------------------------


class TestModelManagerConstruction:
    """ModelManager initializes with correct defaults."""

    def test_server_process_is_none(self, manager):
        assert manager.server_process is None

    def test_loading_is_false(self, manager):
        assert manager.loading is False

    def test_actual_ctx_is_none(self, manager):
        assert manager.actual_ctx_size is None

    def test_has_lock(self, manager):
        assert isinstance(manager._lock, type(threading.Lock()))

    def test_has_signals(self):
        assert hasattr(ModelManager, "status_changed")
        assert hasattr(ModelManager, "model_loaded")
        assert hasattr(ModelManager, "model_unloaded")
        assert hasattr(ModelManager, "model_warning")


# -- is_loaded() --------------------------------------------------------------


class TestIsLoaded:
    """is_loaded() checks subprocess state."""

    def test_not_loaded_when_no_process(self, manager):
        assert manager.is_loaded() is False

    def test_loaded_when_process_alive(self, manager):
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        manager.server_process = proc
        assert manager.is_loaded() is True

    def test_not_loaded_when_process_dead(self, manager):
        proc = MagicMock()
        proc.poll.return_value = 1  # exited
        manager.server_process = proc
        assert manager.is_loaded() is False

    def test_not_ready_until_model_startup_completes(self, manager):
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc
        assert manager.is_ready() is False
        manager._server_ready = True
        assert manager.is_ready() is True


class TestBackendLogDetection:
    def test_recognizes_current_macos_mtl_device_format(self):
        assert _detect_loaded_backend("MTL0: Apple M2") == "metal"

    def test_quiet_or_cpu_log_is_never_reported_as_metal(self):
        assert _detect_loaded_backend("llama_server: model loaded") == "cpu"
        assert _detect_loaded_backend("loaded CPU backend") == "cpu"

    def test_uses_a_fresh_successful_metal_probe_for_quiet_current_logs(self):
        assert _detect_loaded_backend(
            "llama_server: model loaded", metal_verified=True
        ) == "metal"


# -- _base_url / _health_url / _chat_url --------------------------------------


class TestURLHelpers:
    """URL construction from config."""

    def test_base_url(self, manager):
        url = manager._base_url()
        assert "127.0.0.1" in url
        assert "8080" in url

    def test_health_url(self, manager):
        assert manager._health_url().endswith("/health")

    def test_chat_url(self, manager):
        assert manager._chat_url().endswith("/v1/chat/completions")


# -- _estimate_tokens() -------------------------------------------------------


class TestEstimateTokens:
    """Token estimation heuristic."""

    def test_empty_text(self):
        assert _estimate_tokens("") == 1  # minimum 1

    def test_short_text(self):
        result = _estimate_tokens("Hello world")
        assert result >= 1
        assert result <= 15

    def test_long_text(self):
        text = "word " * 1000
        result = _estimate_tokens(text)
        assert result > 100

    def test_cjk_text(self):
        text = "你好世界" * 100
        result = _estimate_tokens(text)
        assert result > 50


# -- _create_job_object_for_subprocess() --------------------------------------


class TestJobObject:
    """Job Object attachment for subprocess lifecycle on Windows."""

    def test_skips_on_non_windows(self):
        with patch("stet.llm.model_manager.WINDOWS", False):
            proc = MagicMock()
            _create_job_object_for_subprocess(proc)
            # Should return immediately, no kernel32 calls

    def test_handles_kernel32_failure(self):
        with patch("stet.llm.model_manager.WINDOWS", True):
            mock_kernel32 = MagicMock()
            mock_kernel32.CreateJobObjectW.return_value = 0  # failure
            import ctypes

            with patch.object(ctypes, "windll", create=True) as mock_windll:
                mock_windll.kernel32 = mock_kernel32
                proc = MagicMock()
                proc._handle = 1234
                _create_job_object_for_subprocess(proc)
                # Should not crash even when Job Object creation fails


# -- Strength routing ---------------------------------------------------------


class TestStrengthModeIndex:
    """_STRENGTH_TO_MODE_INDEX maps all variants correctly."""

    def test_conservative_variants(self):
        assert _STRENGTH_TO_MODE_INDEX["spelling_only"] == 0

    def test_smart_fix_variants(self):
        assert _STRENGTH_TO_MODE_INDEX["full_correction"] == 1

    def test_aggressive_variants(self):
        assert _STRENGTH_TO_MODE_INDEX["rewrite_polish"] == 2


# -- load_model() guarded behavior --------------------------------------------


class TestLoadModelGuards:
    """load_model() real behavioral tests -- guards and early-return paths.

    The conftest autouse fixture patches load_model to a no-op. Each test here
    restores the real method via monkeypatch so we can verify real behavior.
    The local monkeypatch.setattr wins over the autouse fixture.
    """

    def test_returns_false_on_empty_model_path(self, cfg, monkeypatch):
        """If model_path is empty string, load_model returns False immediately."""
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model", _ORIGINAL_LOAD_MODEL
        )

        cfg.config["model_path"] = ""
        cfg.config["ac_model_path"] = ""
        mgr = ModelManager(cfg, model_path_key="model_path")

        emitted = []
        mgr.status_changed.connect(lambda msg: emitted.append(msg))
        result = mgr.load_model()

        assert result is False
        assert any("configured" in m.lower() for m in emitted), (
            f"Expected 'configured' in status message, got: {emitted}"
        )

    def test_returns_false_when_model_file_missing(self, cfg, tmp_path, monkeypatch):
        """If model_path points to nonexistent file, load_model returns False after retries."""
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model", _ORIGINAL_LOAD_MODEL
        )

        missing_path = tmp_path / "nonexistent_model.gguf"
        cfg.config["model_path"] = str(missing_path)
        mgr = ModelManager(cfg, model_path_key="model_path")

        # Skip the 5x2s retry delays
        monkeypatch.setattr("stet.llm.model_manager.time.sleep", lambda s: None)

        emitted = []
        mgr.status_changed.connect(lambda msg: emitted.append(msg))
        result = mgr.load_model()

        assert result is False
        assert len(emitted) > 0, f"Expected status message(s), got: {emitted}"
        assert mgr.server_process is None

    def test_missing_model_path_fails_fast_and_emits_loading_first(self, monkeypatch):
        """Manual load attempts should not block in retry sleeps when the path is missing."""
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model", _ORIGINAL_LOAD_MODEL
        )

        missing_path = Path(__file__).with_name("missing-fast.gguf")
        mgr = ModelManager(_make_min_cfg(str(missing_path)), model_path_key="model_path")

        emitted = []
        sleep_calls = []
        mgr.status_changed.connect(lambda msg: emitted.append(msg))
        monkeypatch.setattr(
            "stet.llm.model_manager.time.sleep", lambda seconds: sleep_calls.append(seconds)
        )

        result = mgr.load_model()

        assert result is False
        assert sleep_calls == []
        assert emitted[0] == "Loading…"
        assert emitted[-1] == "Model file not found"

    def test_missing_model_path_retry_mode_reports_retry(self, monkeypatch):
        """Autoload retries should use the explicit retry status message."""
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model", _ORIGINAL_LOAD_MODEL
        )

        missing_path = Path(__file__).with_name("missing-retry.gguf")
        mgr = ModelManager(
            _make_min_cfg(str(missing_path)), model_path_key="model_path"
        )

        emitted = []
        mgr.status_changed.connect(lambda msg: emitted.append(msg))

        result = mgr.load_model(retry_missing_path=True)

        assert result is False
        assert emitted[0] == "Loading…"
        assert emitted[-1] == "Model file not found — will retry"

    def test_returns_false_when_already_loading(self, manager, monkeypatch):
        """If loading flag is True, load_model returns False immediately."""
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model", _ORIGINAL_LOAD_MODEL
        )

        manager.loading = True
        result = manager.load_model()
        assert result is False

    def test_returns_true_when_already_loaded(self, manager, monkeypatch):
        """If server process is running, load_model returns True without starting a new one."""
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model", _ORIGINAL_LOAD_MODEL
        )

        proc = MagicMock()
        proc.poll.return_value = None  # still running
        manager.server_process = proc
        result = manager.load_model()
        assert result is True



# -- unload_model() -----------------------------------------------------------


class TestUnloadModel:
    """unload_model() terminates the subprocess."""

    def test_unload_when_no_process(self, manager):
        """Should not crash when no server is running."""
        manager.unload_model()
        assert manager.server_process is None

    def test_unload_terminates_process(self, manager):
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc
        manager.unload_model()
        assert proc.terminate.called or proc.kill.called


class TestShouldRetryLoad:
    """ModelManager.should_retry_load guides callers on whether to retry."""

    def test_returns_false_when_already_loaded(self, cfg):
        """should_retry_load returns False when model is running."""
        manager = ModelManager(cfg)
        manager.server_process = MagicMock()
        manager.server_process.poll.return_value = None
        assert manager.should_retry_load() is False

    def test_returns_false_when_loading(self, cfg):
        """should_retry_load returns False when a load is in progress."""
        manager = ModelManager(cfg)
        manager.loading = True
        assert manager.should_retry_load() is False

    def test_returns_false_when_no_path_configured(self, cfg):
        """should_retry_load returns False when no model path is set."""
        manager = ModelManager(cfg)
        manager.server_process = None
        cfg.get = lambda key, default=None: "" if key == "model_path" else default
        assert manager.should_retry_load() is False

    def test_returns_false_when_file_missing(self, tmp_path, cfg):
        """should_retry_load returns False when path is set but file missing."""
        missing = tmp_path / "nonexistent.gguf"
        manager = ModelManager(cfg)
        manager.server_process = None
        cfg.get = lambda key, default=None: str(missing) if key == "model_path" else default
        assert manager.should_retry_load() is False

    def test_returns_true_when_file_exists_but_not_loaded(self, tmp_path, cfg):
        """should_retry_load returns True: file exists, server not running."""
        model_file = tmp_path / "model.gguf"
        model_file.touch()
        manager = ModelManager(cfg)
        manager.server_process = None
        manager.loading = False
        cfg.get = lambda key, default=None: str(model_file) if key == "model_path" else default
        assert manager.should_retry_load() is True

    def test_returns_true_with_ac_model_path_key(self, tmp_path, cfg):
        """should_retry_load works with ac_model_path key."""
        model_file = tmp_path / "ac.gguf"
        model_file.touch()
        manager = ModelManager(cfg, model_path_key="ac_model_path")
        manager.server_process = None
        manager.loading = False
        def cfg_get(key, default=None):
            if key == "ac_model_path":
                return str(model_file)
            return default
        cfg.get = cfg_get
        assert manager.should_retry_load() is True


# -- _warmup_prompt_cache() ---------------------------------------------------


class TestWarmupPromptCache:
    """_warmup_prompt_cache() pre-fills the KV cache with a minimal request."""

    def test_sends_minimal_post_with_cache_prompt(self, manager, monkeypatch):
        """Warmup posts to /v1/chat/completions with max_tokens=1, cache_prompt=True, once per parallel slot."""
        captured_calls = []

        def fake_post(self, url, *args, **kwargs):
            captured_calls.append({
                "url": url,
                "json": kwargs.get("json"),
                "timeout": kwargs.get("timeout"),
            })
            return MockResponse({"choices": [{"message": {"content": ""}}]})

        monkeypatch.setattr("requests.Session.post", fake_post)
        manager._warmup_prompt_cache()

        parallel = manager.cfg.get("parallel", 4)
        assert len(captured_calls) == parallel, (
            f"Expected {parallel} warmup requests (one per slot), got {len(captured_calls)}"
        )
        for call in captured_calls:
            assert call["url"] == manager._chat_url()
            payload = call["json"]
            assert payload["max_tokens"] == 1
            assert payload["cache_prompt"] is True
            assert call["timeout"] == 10
            # System message should contain the real correction prompt, not a
            # throwaway "warmup" prompt.
            sys_msg = payload["messages"][0]
            assert sys_msg["role"] == "system"
            assert len(sys_msg["content"]) > 50, "System prompt should be the real correction prompt"
            # User message should contain the input delimiters
            user_msg = payload["messages"][1]
            assert user_msg["role"] == "user"
            assert "CONTENT_BEGIN" in user_msg["content"]
            # No assistant prefill — messages are [system, user] only
            assert len(payload["messages"]) == 2

    def test_gemma_warmup_uses_real_correction_message_shape(self, monkeypatch):
        """Gemma warmup must match real correction prompts for cache hits."""
        cfg_data = {
            "model_path": "gemma-4-E2B-it.gguf",
            "server_host": "127.0.0.1",
            "server_port": 8080,
            "parallel": 1,
            "streaming_strength": "full_correction",
            "correction_modes": [],
        }

        class GemmaCfg:
            def get(self, key, default=None):
                return cfg_data.get(key, default)

        manager = ModelManager(GemmaCfg())
        captured_calls = []

        def fake_post(url, *args, **kwargs):
            captured_calls.append(kwargs.get("json"))
            return MockResponse({"choices": [{"message": {"content": ""}}]})

        monkeypatch.setattr("requests.Session.post", fake_post)
        manager._warmup_prompt_cache()

        assert len(captured_calls) == 1
        messages = captured_calls[0]["messages"]
        # Gemma: single user message with system folded in, no assistant prefill
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "Correct the text completely" in messages[0]["content"]
        assert "CONTENT_BEGIN\nwarmup\nCONTENT_END" in messages[0]["content"]

    def test_swallows_exceptions(self, manager, monkeypatch):
        """A failed warmup must not raise — load must still complete."""
        def fake_post(url, *args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("requests.Session.post", fake_post)
        # Should not raise
        manager._warmup_prompt_cache()


# -- Persistent HTTP session reuse --------------------------------------------


class TestPersistentSession:
    """ModelManager reuses a single requests.Session across corrections.

    Avoids per-call TCP handshake overhead. Session is created lazily, is
    thread-safe (single HTTPAdapter with pool_maxsize=8 for 4 parallel slots
    plus headroom), and is intentionally NOT closed after each patch.
    """

    def test_session_is_none_until_first_access(self, manager):
        """Lazy initialization: no socket is opened until something asks for it."""
        assert manager._session is None

    def test_get_session_creates_session_on_first_call(self, manager):
        """_get_session() instantiates a Session on first access."""
        session = manager._get_session()
        assert session is not None
        assert manager._session is session

    def test_get_session_returns_same_instance(self, manager):
        """Subsequent calls return the same Session instance (identity check)."""
        s1 = manager._get_session()
        s2 = manager._get_session()
        assert s1 is s2

    def test_session_has_http_adapter_with_pool_8(self, manager):
        """The persistent session is mounted with HTTPAdapter pool_maxsize=8.

        Supports the 4 parallel server slots plus headroom for retries.
        """
        from requests.adapters import HTTPAdapter

        session = manager._get_session()
        adapter = session.get_adapter("http://127.0.0.1:8080")
        assert isinstance(adapter, HTTPAdapter)
        # pool settings are stored on the adapter's init kwargs
        assert adapter._pool_maxsize == 8
        assert adapter._pool_connections == 4

    def test_session_not_closed_after_patch(self, manager):
        """After correct_text_patch returns, the persistent session must still be live."""
        # Pretend the server is loaded so correct_text_patch proceeds.
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        # Stub out the per-chunk worker so we don't depend on the LLM mock.
        # Must return something different from the input so any_success=True.
        manager._rewrite_sentence_chunk = (
            lambda chunk_text, custom_sys, idx, total, strength,
            cancel_event=None, mode_prompt_override=None, session=None, profile=None: (
                "Hello world this is test."
            )
        )

        # Pre-warm so a session exists; capture it
        pre = manager._get_session()

        result, units = manager.correct_text_patch(
            "hello world this is test", strength="full_correction"
        )
        assert result is not None
        # The session must be the same object — not recreated, not closed
        assert manager._session is pre
        # Sanity: a live session is usable (post would work in real flow)
        assert not _is_session_closed(manager._session)

    def test_session_reused_across_multiple_patches(self, manager):
        """Multiple correct_text_patch calls share the same persistent session."""
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        manager._rewrite_sentence_chunk = (
            lambda chunk_text, custom_sys, idx, total, strength,
            cancel_event=None, mode_prompt_override=None, session=None, profile=None: (
                "Hello world this is test."
            )
        )

        s1 = manager._get_session()
        mgr_id_after_first = id(manager._session)

        manager.correct_text_patch("hello world this is test", strength="full_correction")
        assert id(manager._session) == mgr_id_after_first

        manager.correct_text_patch("another sentence to fix", strength="full_correction")
        assert id(manager._session) == mgr_id_after_first

        manager.correct_text_patch("yet another chunk here", strength="full_correction")
        assert id(manager._session) == mgr_id_after_first

        assert manager._session is s1

    def test_close_session_releases_and_clears(self, manager):
        """close_session() closes the underlying session and resets the slot."""
        from requests.adapters import HTTPAdapter

        session = manager._get_session()
        assert manager._session is session
        # Adapter is mounted
        assert isinstance(
            session.get_adapter("http://127.0.0.1:8080"), HTTPAdapter
        )

        manager.close_session()

        assert manager._session is None
        # Calling again creates a fresh session
        new_session = manager._get_session()
        assert new_session is not session

    def test_close_session_safe_when_never_created(self, manager):
        """close_session() is a no-op if no session was ever created."""
        assert manager._session is None
        manager.close_session()  # must not raise
        assert manager._session is None

    def test_close_session_idempotent(self, manager):
        """Calling close_session() twice is safe."""
        manager._get_session()
        manager.close_session()
        manager.close_session()  # must not raise
        assert manager._session is None

    def test_fallback_chunk_path_uses_persistent_session(self, manager, monkeypatch):
        """_rewrite_sentence_chunk called without a session uses the persistent one."""
        import requests

        captured_session = {}

        def fake_post(self, url, *args, **kwargs):
            captured_session["session"] = self
            return MockResponse(
                {"choices": [{"message": {"content": "<<<START>>>ok<<<END>>>"}}]}
            )

        monkeypatch.setattr(requests.Session, "post", fake_post)

        manager._rewrite_sentence_chunk("hello world", None, 1, 1, "full_correction")

        # The session that actually posted must be the manager's persistent one
        assert captured_session["session"] is manager._get_session()


def _is_session_closed(session) -> bool:
    """Best-effort check: a closed Session's adapters return a NullAdapter."""
    try:
        from requests.adapters import HTTPAdapter

        adapter = session.get_adapter("http://127.0.0.1:8080")
        return not isinstance(adapter, HTTPAdapter)
    except Exception:
        return True


class TestTerminalPunctuationGuard:
    """Tests for the per-chunk terminal-punctuation guard in ModelManager._rewrite_sentence_chunk."""

    def test_terminal_punctuation_restored(self, manager, monkeypatch):
        """If the LLM drops trailing punctuation from a chunk, it is restored."""
        import requests

        mock_content = "<<<START>>>Hello world<<<END>>>"

        def fake_post(self, url, *args, **kwargs):
            return MockResponse(
                {"choices": [{"message": {"content": mock_content}}]}
            )

        monkeypatch.setattr(requests.Session, "post", fake_post)

        # Period dropped -> restored
        res = manager._rewrite_sentence_chunk("Hello world.", None, 1, 1, "full_correction")
        assert res == "Hello world."

        # Exclamation dropped -> restored
        res = manager._rewrite_sentence_chunk("Hello world!", None, 1, 1, "full_correction")
        assert res == "Hello world!"

        # Question mark dropped -> restored
        res = manager._rewrite_sentence_chunk("Hello world?", None, 1, 1, "full_correction")
        assert res == "Hello world?"

    def test_terminal_punctuation_preserved_with_whitespace(self, manager, monkeypatch):
        """Preserves trailing whitespace when restoring punctuation."""
        import requests
        import stet.llm.model_manager as mm

        mock_content = "<<<START>>>Hello world <<<END>>>"

        def fake_post(self, url, *args, **kwargs):
            return MockResponse(
                {"choices": [{"message": {"content": mock_content}}]}
            )

        monkeypatch.setattr(requests.Session, "post", fake_post)
        # Force the extracted sentence to have trailing whitespace to test preservation
        monkeypatch.setattr(mm, "_extract_rewritten_sentence", lambda raw, original_text="": "Hello world ")

        res = manager._rewrite_sentence_chunk("Hello world. ", None, 1, 1, "full_correction")
        assert res == "Hello world. "

    def test_terminal_punctuation_not_duplicated(self, manager, monkeypatch):
        """Does not duplicate punctuation if the LLM correctly preserves it."""
        import requests

        mock_content = "<<<START>>>Hello world.<<<END>>>"

        def fake_post(self, url, *args, **kwargs):
            return MockResponse(
                {"choices": [{"message": {"content": mock_content}}]}
            )

        monkeypatch.setattr(requests.Session, "post", fake_post)

        res = manager._rewrite_sentence_chunk("Hello world.", None, 1, 1, "full_correction")
        assert res == "Hello world."

    def test_terminal_punctuation_skipped_for_multi_chunk(self, manager, monkeypatch):
        """Per-chunk guard does NOT fire when total > 1 (multi-chunk input)."""
        import requests

        # LLM drops the period
        mock_content = "<<<START>>>Hello world<<<END>>>"

        def fake_post(self, url, *args, **kwargs):
            return MockResponse(
                {"choices": [{"message": {"content": mock_content}}]}
            )

        monkeypatch.setattr(requests.Session, "post", fake_post)

        # total=3 -> multi-chunk -> guard should NOT restore the period
        res = manager._rewrite_sentence_chunk("Hello world.", None, 1, 3, "full_correction")
        assert res == "Hello world"  # period stays dropped

    def test_rewrite_polish_paragraph_chunking_exceeding_max_words(self, manager):
        """If a paragraph exceeds 250 words in rewrite_polish mode, it gets split at sentence boundaries.

        Note: the fixture's context_size is 4096, so the per-slot budget is
        1024 tokens (< 2048) and the adaptive chunk cap applies — the split
        still happens (cap 120 words), and the assertions below hold either way.
        """
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        captured_chunks = []
        def mock_rewrite(chunk_text, *args, **kwargs):
            captured_chunks.append(chunk_text)
            return chunk_text

        manager._rewrite_sentence_chunk = mock_rewrite

        # 30 sentences, each 10 words, total 300 words without newlines.
        # Rewrite profile chunk_words=250, so this should split.
        sentence = "This is a sentence of ten words for testing this. "
        long_paragraph = sentence * 30

        result, units = manager.correct_text_patch(long_paragraph, strength="rewrite_polish")
        assert units > 1
        assert len(captured_chunks) > 1
        # The reconstructed result should match the original text
        assert result.strip() == long_paragraph.strip()

    def test_rewrite_chunk_cap_drops_to_120_words_on_small_slot_budget(self, manager):
        """With a small per-slot budget (<2048 tokens) the rewrite chunk cap
        falls to min(profile.chunk_words, 120): a 250-word rewrite plus thinking
        budget plus answer can't fit in one 4k-GGUF/parallel=4 slot."""
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc
        manager.actual_ctx_size = 1024  # 1024 // 4 = 256 tokens/slot < 2048

        captured_chunks = []
        def mock_rewrite(chunk_text, *args, **kwargs):
            captured_chunks.append(chunk_text)
            return chunk_text

        manager._rewrite_sentence_chunk = mock_rewrite

        sentence = "This is a sentence of ten words for testing this. "
        long_paragraph = sentence * 30  # 300 words

        result, units = manager.correct_text_patch(long_paragraph, strength="rewrite_polish")
        assert units > 1
        assert len(captured_chunks) > 1
        assert all(len(c.split()) <= 120 for c in captured_chunks)
        assert result.strip() == long_paragraph.strip()

    def test_rewrite_chunk_cap_keeps_profile_words_on_roomy_slot_budget(self, manager):
        """When the per-slot budget is >=2048 tokens the profile cap (250 words)
        stays — at least one chunk must exceed 120 words."""
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc
        manager.actual_ctx_size = 8192  # 8192 // 4 = 2048 tokens/slot — not < 2048

        captured_chunks = []
        def mock_rewrite(chunk_text, *args, **kwargs):
            captured_chunks.append(chunk_text)
            return chunk_text

        manager._rewrite_sentence_chunk = mock_rewrite

        sentence = "This is a sentence of ten words for testing this. "
        long_paragraph = sentence * 30  # 300 words

        result, units = manager.correct_text_patch(long_paragraph, strength="rewrite_polish")
        assert units > 1
        assert any(len(c.split()) > 120 for c in captured_chunks)

    def test_agentic_tool_call_output_rejected(self, manager, monkeypatch):
        """LFM-style agentic finetunes sometimes emit <|tool_call_start|>[...]
        instead of a corrected sentence — the unit must be rejected exactly like
        a refusal (original kept via the dict fallback), never spliced in."""
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        def mock_rewrite(chunk_text, *args, **kwargs):
            return (
                "<|tool_call_start|>[edit_text(input='I will recieve the package.')]"
                "<|tool_call_end|>"
            )

        monkeypatch.setattr(manager, "_rewrite_sentence_chunk", mock_rewrite)

        res, units = manager.correct_text_patch(
            "I will recieve the package.", strength="rewrite_polish"
        )
        assert units == 1
        # Unit kept original (dict fallback), not the tool-call garbage
        assert res == "I will receive the package."
        assert "tool_call" not in res
        assert manager.last_patch_error is not None
        assert "agentic tool-call" in manager.last_patch_error

    def test_no_change_declaration_output_rejected(self, manager, monkeypatch):
        """LFM 2.5 spills 'already correct' / task-restatement commentary into
        content — the unit must be rejected (original kept via dict fallback),
        never spliced into the result."""
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        def mock_rewrite(chunk_text, *args, **kwargs):
            return (
                "The text appears to be already correct. This text is "
                "grammatically correct and well written."
            )

        monkeypatch.setattr(manager, "_rewrite_sentence_chunk", mock_rewrite)

        res, units = manager.correct_text_patch(
            "I will recieve the package.", strength="rewrite_polish"
        )
        assert units == 1
        # Unit kept original (dict fallback), not the commentary
        assert res == "I will receive the package."
        assert "already correct" not in res
        assert manager.last_patch_error is not None
        assert "no-change declaration" in manager.last_patch_error


class TestModelManagerPrefixAndPayload:
    """Tests verify that prefix-specific configs (autocorrect vs. chat) resolve correctly, and payloads use them."""

    def test_get_param_resolves_prefixes(self, cfg):
        # Setup config manager with both prefixed and non-prefixed values
        cfg.set("temperature", 0.15)
        cfg.set("chat_temperature", 0.85)

        # Autocorrect manager (model_path)
        ac_manager = ModelManager(cfg, model_path_key="model_path")
        assert ac_manager._get_param("temperature") == 0.15

        # Chat manager (chat_model_path)
        chat_manager = ModelManager(cfg, model_path_key="chat_model_path")
        assert chat_manager._get_param("temperature") == 0.85

    def test_make_stream_worker_uses_prefixed_payload(self, cfg, monkeypatch):
        cfg.set("temperature", 0.15)
        cfg.set("chat_temperature", 0.85)
        
        chat_manager = ModelManager(cfg, model_path_key="chat_model_path")
        worker = chat_manager.make_stream_worker(messages=[])
        assert worker.payload["temperature"] == 0.85

    def test_rewrite_sentence_chunk_uses_config_parameters(self, cfg, monkeypatch):
        import requests
        # Correction-specific params take precedence over general params
        cfg.set("correction_temperature", 0.25)
        cfg.set("correction_top_k", 5)
        cfg.set("repeat_penalty", 1.2)

        ac_manager = ModelManager(cfg, model_path_key="model_path")
        captured_payload = {}

        def fake_post(self, url, json, *args, **kwargs):
            captured_payload["payload"] = json
            return MockResponse(
                {"choices": [{"message": {"content": "ok"}}]}
            )

        monkeypatch.setattr(requests.Session, "post", fake_post)
        ac_manager._rewrite_sentence_chunk("hello", None, 1, 1, "full_correction")

        payload = captured_payload.get("payload", {})
        assert payload.get("temperature") == 0.25
        assert payload.get("top_k") == 5
        assert payload.get("repeat_penalty") == 1.2


class TestGpuOomFallback:
    """Regression tests for GPU OOM process exit and CPU-only retry routing."""

    def test_gpu_oom_immediate_exit_triggers_single_cpu_retry(self, cfg, monkeypatch):
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))
        cfg.set("gpu_layers", 99)
        cfg.set("mtp_enabled", False)
        mgr = ModelManager(cfg)

        retry_calls = []
        original_load = _ORIGINAL_LOAD_MODEL
        real_read_text = Path.read_text

        log_text = "ggml_cuda_init: CUDA error: out of memory\nfailed to allocate buffer"
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda self, encoding=None, errors=None: log_text if self == mgr.server_log_path else real_read_text(self, encoding=encoding, errors=errors),
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Exited immediately

        def fake_popen(*args, **kwargs):
            return mock_proc

        monkeypatch.setattr("subprocess.Popen", fake_popen)

        # Track calls to load_model
        def spy_load_model(self, force_cpu=False, **kwargs):
            retry_calls.append(force_cpu)
            return original_load(self, force_cpu=force_cpu, **kwargs)

        monkeypatch.setattr(ModelManager, "load_model", spy_load_model)

        res = mgr.load_model()
        assert res is False
        # Expect first call force_cpu=False, second call force_cpu=True (exactly 1 retry)
        assert retry_calls == [False, True]

    def test_non_gpu_immediate_exit_does_not_trigger_cpu_retry(self, cfg, monkeypatch):
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))
        cfg.set("gpu_layers", 99)
        cfg.set("mtp_enabled", False)
        mgr = ModelManager(cfg)

        retry_calls = []
        original_load = _ORIGINAL_LOAD_MODEL
        real_read_text = Path.read_text

        log_text = "error: failed to open model file\n"
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda self, encoding=None, errors=None: log_text if self == mgr.server_log_path else real_read_text(self, encoding=encoding, errors=errors),
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: mock_proc)

        def spy_load_model(self, force_cpu=False, **kwargs):
            retry_calls.append(force_cpu)
            return original_load(self, force_cpu=force_cpu, **kwargs)

        monkeypatch.setattr(ModelManager, "load_model", spy_load_model)

        res = mgr.load_model()
        assert res is False
        assert retry_calls == [False]

    def test_gpu_unreadable_or_empty_log_returns_normal_failure(self, cfg, monkeypatch):
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))
        cfg.set("gpu_layers", 99)
        mgr = ModelManager(cfg)
        real_read_text = Path.read_text

        monkeypatch.setattr(
            Path,
            "read_text",
            lambda self, encoding=None, errors=None: "" if self == mgr.server_log_path else real_read_text(self, encoding=encoding, errors=errors),
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: mock_proc)

        res = mgr.load_model()
        assert res is False


class TestServerLaunchCommand:
    """The llama-server launch command must carry --reasoning-budget so
    thinking templates that hard-prime <think> (e.g. LFM 2.5) are forced to
    emit </think> and produce real content instead of burning all tokens."""

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_gpu_launch_command_has_reasoning_budget(self, mock_get, mock_popen, cfg):
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert "--reasoning" in cmd
        assert cmd[cmd.index("--reasoning") + 1] == "off"
        assert "--reasoning-budget" in cmd
        assert cmd[cmd.index("--reasoning-budget") + 1] == "0"
        # Budget sits directly after --reasoning off and --reasoning-format
        assert "--reasoning-budget" in cmd

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_hard_primed_template_adds_chat_template_file(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """A model whose embedded chat template hard-primes <think> must get
        --chat-template-file pointing at a sanitized copy (generic flow —
        no model-name special-casing)."""
        model = tmp_path / "AnyModel-Q4_K_M.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="lfm2",
            name="AnyModel",
            chat_template=(
                '{%- if add_generation_prompt -%}'
                '{{- "<|im_start|>assistant\\n<think>" -}}'
                '{%- endif -%}'
            ),
            n_ctx_train=8192,
            reasoning_capable=True,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert "--chat-template-file" in cmd
        template_path = Path(cmd[cmd.index("--chat-template-file") + 1])
        assert template_path.is_absolute()
        assert template_path.exists()
        # The sanitized copy has the <think> prime removed
        assert "<think>" not in template_path.read_text(encoding="utf-8")
        # Base reasoning suppression is preserved alongside the override
        assert cmd[cmd.index("--reasoning") + 1] == "off"
        assert cmd[cmd.index("--reasoning-budget") + 1] == "0"

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_non_reasoning_template_omits_chat_template_file(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """A model whose template does not prime thinking must NOT get a
        --chat-template-file override."""
        model = tmp_path / "PlainModel.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="qwen2",
            name="PlainModel",
            chat_template=(
                '{%- if add_generation_prompt -%}'
                '{{- "<|im_start|>assistant\\n" -}}'
                '{%- endif -%}'
            ),
            n_ctx_train=4096,
            reasoning_capable=False,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert "--chat-template-file" not in cmd
        # Base reasoning suppression is still present
        assert cmd[cmd.index("--reasoning") + 1] == "off"

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_gguf_read_failure_omits_chat_template_file_no_crash(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """GGUF metadata read failure must degrade gracefully: no template
        flag, no crash, load still succeeds."""
        model = tmp_path / "BrokenModel.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        def _raise_gguf_error(_path):
            raise GgufReadError("gguf package not installed")

        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", _raise_gguf_error
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert "--chat-template-file" not in cmd
        assert cmd[cmd.index("--reasoning") + 1] == "off"

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_user_set_context_size_is_respected(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """A user-set context_size must win over GGUF-derived defaults."""
        model = tmp_path / "CtxModel.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        cfg.set("context_size", 8192)  # user explicitly chose 8192
        cfg.set("context_size_auto", False)
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 8192}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="qwen2",
            name="CtxModel",
            chat_template="{{prompt}}",
            n_ctx_train=4096,  # would cap to 4096 if the default were used
            reasoning_capable=False,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("--ctx-size") + 1] == "8192"

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_default_ctx_derived_from_gguf_capped_at_12800(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """Untouched default ctx (12800) is derived from GGUF n_ctx_train,
        capped at 12800."""
        model = tmp_path / "CtxModel.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        cfg.set("context_size_auto", True)
        cfg.set("context_size", 12800)  # untouched default
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="qwen2",
            name="CtxModel",
            chat_template="{{prompt}}",
            n_ctx_train=32768,  # min(32768, 12800) -> 12800
            reasoning_capable=False,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("--ctx-size") + 1] == "12800"

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_default_ctx_derived_from_gguf_floored_at_2048(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """Untouched default ctx (12800) is derived from GGUF n_ctx_train,
        floored at 2048."""
        model = tmp_path / "CtxModel.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        cfg.set("context_size_auto", True)
        cfg.set("context_size", 12800)  # untouched default
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 2048}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="qwen2",
            name="CtxModel",
            chat_template="{{prompt}}",
            n_ctx_train=512,  # max(min(512, 4096), 2048) -> 2048
            reasoning_capable=False,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("--ctx-size") + 1] == "2048"

    def _launch_hard_primed(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch, session_post=None
    ):
        """Run the real load_model flow with a hard-primed template so the
        post-load /apply-template validation actually executes.

        *session_post* controls the mocked session's post() response; when
        None, a clean prompt (no think open-tag) is returned. The session is
        patched at the class level so no real localhost socket is ever opened.
        """
        model = tmp_path / "AnyModel-Q4_K_M.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="lfm2",
            name="AnyModel",
            chat_template=(
                '{%- if add_generation_prompt -%}'
                '{{- "<|im_start|>assistant\\n<think>" -}}'
                '{%- endif -%}'
            ),
            n_ctx_train=8192,
            reasoning_capable=True,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        mock_session = MagicMock()
        if session_post is None:
            mock_session.post.return_value = MockResponse(
                {"prompt": "<|im_start|>assistant\n"}
            )
        else:
            mock_session.post.side_effect = session_post
        monkeypatch.setattr(
            ModelManager, "_get_session", lambda self: mock_session
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        mgr = ModelManager(cfg)
        assert mgr.load_model() is True
        return mgr, mock_session

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_apply_template_validation_clean_prompt_keeps_flag(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """A 200 /apply-template response with a clean prompt (no residual
        think open-tag) must keep _sanitized_template_used True."""
        mgr, mock_session = self._launch_hard_primed(
            mock_get, mock_popen, cfg, tmp_path, monkeypatch
        )

        assert mgr._sanitized_template_used is True
        assert mgr._sanitized_template_str is not None
        apply_url = [a[0] for a, _k in mock_session.post.call_args_list]
        assert any("/apply-template" in u for u in apply_url)

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_apply_template_validation_residual_think_clears_flag(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """A rendered prompt that still carries an unbalanced think open-tag
        must clear the override flag (fallback to base suppression)."""

        def _residual_think(*args, **kwargs):
            return MockResponse({"prompt": "<|im_start|>assistant\n<think>"})

        mgr, _mock_session = self._launch_hard_primed(
            mock_get,
            mock_popen,
            cfg,
            tmp_path,
            monkeypatch,
            session_post=_residual_think,
        )

        assert mgr._sanitized_template_used is False

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_apply_template_validation_404_clears_flag(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """A non-2xx /apply-template response (e.g. 404 on older llama-server
        builds) must clear the override flag without crashing the load."""

        def _not_found(*args, **kwargs):
            return MockResponse({"error": "not found"}, status_code=404)

        mgr, _mock_session = self._launch_hard_primed(
            mock_get,
            mock_popen,
            cfg,
            tmp_path,
            monkeypatch,
            session_post=_not_found,
        )

        assert mgr._sanitized_template_used is False

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_apply_template_validation_raise_clears_flag(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """An exception from /apply-template must clear the override flag and
        never propagate (load already succeeded)."""

        def _boom(*args, **kwargs):
            raise requests.exceptions.ConnectionError("refused")

        mgr, _mock_session = self._launch_hard_primed(
            mock_get,
            mock_popen,
            cfg,
            tmp_path,
            monkeypatch,
            session_post=_boom,
        )

        assert mgr._sanitized_template_used is False

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_props_thinking_supported_reads_caps_dict(
        self, mock_get, mock_popen, cfg, tmp_path, monkeypatch
    ):
        """thinking_supported must read supports_thinking/supports_reasoning
        from the caps dict — not the mere presence of the dict."""
        model = tmp_path / "CapsModel.gguf"
        model.touch()
        cfg.set("model_path", str(model))
        fake_server = Path(cfg.get("model_path")).parent / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_get.side_effect = lambda url, **kwargs: (
            mock_get_props if "/props" in url else mock_health_resp
        )

        fake_info = GgufModelInfo(
            path=str(model),
            architecture="qwen2",
            name="CapsModel",
            chat_template="{{prompt}}",
            n_ctx_train=4096,
            reasoning_capable=False,
            file_size=1,
            mtime=1.0,
        )
        monkeypatch.setattr(
            "stet.llm.model_manager.get_gguf_info_cached", lambda p: fake_info
        )

        mock_session = MagicMock()
        mock_session.post.return_value = MockResponse(
            {"choices": [{"message": {"content": "ok"}}]}
        )
        monkeypatch.setattr(
            ModelManager, "_get_session", lambda self: mock_session
        )

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        # caps present but no support keys -> False
        mock_get_props = MagicMock()
        mock_get_props.ok = True
        mock_get_props.json.return_value = {
            "n_ctx": 4096,
            "chat_template_caps": {},
        }
        mgr = ModelManager(cfg)
        assert mgr.load_model() is True
        assert mgr.thinking_supported is False

        # supports_thinking key -> True
        mock_get_props.json.return_value = {
            "n_ctx": 4096,
            "chat_template_caps": {"supports_thinking": True},
        }
        mgr2 = ModelManager(cfg)
        assert mgr2.load_model() is True
        assert mgr2.thinking_supported is True

        # supports_reasoning key (alternative name) -> True
        mock_get_props.json.return_value = {
            "n_ctx": 4096,
            "chat_template_caps": {"supports_reasoning": True},
        }
        mgr3 = ModelManager(cfg)
        assert mgr3.load_model() is True
        assert mgr3.thinking_supported is True


class TestSentenceChunkLosslessness:
    """Regression tests for the 2026-08-11 'chunking deletes text' bug.

    Root cause: _extract_rewritten_sentence stripped ^-anchored preamble
    patterns ("The corrected version …", "Here is the corrected version …",
    "---") from the first sentence of every chunk.  When that sentence
    legitimately started with such a phrase, real content was deleted, and the
    permissive guard chain (template_transform thr=1.0) accepted the lossy
    output.  Fix: strip_meta_commentary is original-aware — a preamble pattern
    is skipped when the original text itself starts with that phrase.
    """

    # 25 filler sentences x 10 words = 250 words, which is exactly
    # template_transform's chunk_words, so the vulnerable sentence lands as
    # the FIRST sentence of chunk 1 (preamble-strip only fires at the start
    # of a chunk's model output).
    _FILLER = "The quarterly report was submitted to the board on time. " * 25
    _MORE = "The finance team will review the final numbers next week. " * 5

    def _assert_vulnerable_leads_chunk(self, text, lead):
        """Calibration guard: if this fails the filler/profile math changed and
        the regression test would pass vacuously."""
        from stet.core.text_utils import _chunk_text_by_sentences

        chunks = _chunk_text_by_sentences(text, 250)
        assert len(chunks) > 1, "filler must force a second chunk"
        assert chunks[1][0].startswith(lead), (
            f"vulnerable sentence must lead chunk 1, got {chunks[1][0][:40]!r}"
        )

    def test_verbatim_echo_is_lossless(self, manager, monkeypatch):
        """Chunking -> parallel rewrite -> join must never delete text when
        each chunk is echoed back verbatim (the model made no edits)."""
        vulnerable = "The corrected version was released on Friday. Send it out immediately."
        text = (self._FILLER + vulnerable + " " + self._MORE).rstrip()
        self._assert_vulnerable_leads_chunk(text, "The corrected version")

        def echo(chunk_text, custom_sys, idx, total, strength,
                 cancel_event=None, mode_prompt_override=None, session=None, profile=None):
            return chunk_text

        monkeypatch.setattr(manager, "_rewrite_sentence_chunk", MagicMock(side_effect=echo))
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        result, units = manager.correct_text_patch(text, strength="template_transform")
        assert result is not None
        assert units > 1
        assert result == text

    def test_preamble_phrase_preserved_through_full_pipeline(self, manager, monkeypatch):
        """The reported bug, end-to-end: the first sentence of chunk 1 starts
        with a preamble phrase.  The real extraction runs inside the per-chunk
        worker; the whole pipeline must preserve the phrase."""
        vulnerable = (
            "Here is the corrected version of the final quarterly report that the "
            "board reviewed and approved yesterday. Send it out immediately."
        )
        text = (self._FILLER + vulnerable + " " + self._MORE).rstrip()
        self._assert_vulnerable_leads_chunk(text, "Here is the corrected version")

        def echo(chunk_text, custom_sys, idx, total, strength,
                 cancel_event=None, mode_prompt_override=None, session=None, profile=None):
            # Run the REAL extraction the pipeline applies to the model's raw
            # output. Without the original-aware fix this strips the echoed
            # lead-in ("Here is the corrected version …") and the guard chain
            # accepts the lossy text; with it the phrase is preserved.
            return _extract_rewritten_sentence(chunk_text, original_text=chunk_text)

        monkeypatch.setattr(manager, "_rewrite_sentence_chunk", MagicMock(side_effect=echo))
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        result, units = manager.correct_text_patch(text, strength="template_transform")
        assert result is not None
        assert units > 1
        assert "Here is the corrected version of the final quarterly report" in result
        assert "Send it out immediately." in result

    def test_version_numbers_survive_pipeline(self, manager, monkeypatch):
        """Pin the invariant behind Gemini's (disproven) claim that chunking
        truncates version numbers: 1.2.2 must survive the full pipeline."""
        text = (
            "Stet 1.2.2 was released today. Version 3.1.4 of the SDK is out. "
            "Fixes were applied to 2.0.1 as well. The 1.2.2 build fixed the crash. "
            "Next up is 4.0.0 beta. " * 12
        ).rstrip()

        def echo(chunk_text, custom_sys, idx, total, strength,
                 cancel_event=None, mode_prompt_override=None, session=None, profile=None):
            return chunk_text

        monkeypatch.setattr(manager, "_rewrite_sentence_chunk", MagicMock(side_effect=echo))
        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc

        result, units = manager.correct_text_patch(text, strength="template_transform")
        assert result is not None
        assert units > 1
        assert result == text
        for v in ("1.2.2", "3.1.4", "2.0.1", "4.0.0"):
            assert v in result


class TestMtpLoadingAndFallback:
    """MTP CLI arguments and startup failure fallback."""

    def test_mtp_cli_includes_spec_draft_p_min(self, cfg, tmp_path, monkeypatch):
        monkeypatch.setattr("stet.llm.model_manager.WINDOWS", False)
        monkeypatch.setattr("stet.llm.model_manager.ModelManager._warmup_prompt_cache", lambda self: None)

        fake_server = tmp_path / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)
        cfg.config["model_path"] = str(model_file)
        cfg.config["mtp_enabled"] = True
        cfg.config["mtp_p_min"] = 0.85
        cfg.config["mtp_max_draft"] = 3
        cfg.config["mtp_min_draft"] = 1

        mgr = ModelManager(cfg)
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            proc = MagicMock()
            proc.poll.return_value = None
            return proc

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}

        monkeypatch.setattr("subprocess.Popen", mock_popen)
        monkeypatch.setattr(
            "requests.get",
            lambda url, **kw: mock_props_resp if "/props" in url else mock_health_resp,
        )

        assert mgr.load_model() is True
        assert "--spec-type" in captured_cmd
        assert "draft-mtp" in captured_cmd
        assert "--spec-draft-p-min" in captured_cmd
        idx = captured_cmd.index("--spec-draft-p-min")
        assert captured_cmd[idx + 1] == "0.85"
        max_idx = captured_cmd.index("--spec-draft-n-max")
        assert captured_cmd[max_idx + 1] == "3"

    def test_mtp_cli_uses_default_settings_3_tokens_ahead_at_075(self, cfg, tmp_path, monkeypatch):
        monkeypatch.setattr("stet.llm.model_manager.WINDOWS", False)
        monkeypatch.setattr("stet.llm.model_manager.ModelManager._warmup_prompt_cache", lambda self: None)

        fake_server = tmp_path / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)
        cfg.config["model_path"] = str(model_file)
        # Leave mtp_max_draft, mtp_min_draft, mtp_p_min at default
        cfg.config["mtp_enabled"] = True

        mgr = ModelManager(cfg)
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            proc = MagicMock()
            proc.poll.return_value = None
            return proc

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}

        monkeypatch.setattr("subprocess.Popen", mock_popen)
        monkeypatch.setattr(
            "requests.get",
            lambda url, **kw: mock_props_resp if "/props" in url else mock_health_resp,
        )

        assert mgr.load_model() is True
        assert "--spec-type" in captured_cmd
        assert "draft-mtp" in captured_cmd
        
        max_idx = captured_cmd.index("--spec-draft-n-max")
        assert captured_cmd[max_idx + 1] == "3"

        p_min_idx = captured_cmd.index("--spec-draft-p-min")
        assert captured_cmd[p_min_idx + 1] == "0.75"

        min_idx = captured_cmd.index("--spec-draft-n-min")
        assert captured_cmd[min_idx + 1] == "0"

    def test_mtp_startup_failure_falls_back_to_non_speculative(self, cfg, tmp_path, monkeypatch):
        monkeypatch.setattr("stet.llm.model_manager.WINDOWS", False)
        monkeypatch.setattr("stet.llm.model_manager.ModelManager._warmup_prompt_cache", lambda self: None)

        fake_server = tmp_path / "llama-server.exe"
        fake_server.touch()
        cfg.set("llama_server_path", str(fake_server))

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)
        cfg.config["model_path"] = str(model_file)
        cfg.config["mtp_enabled"] = True
        mgr = ModelManager(cfg)

        cmds = []
        attempt = [0]

        def mock_popen(cmd, *args, **kwargs):
            cmds.append(cmd)
            proc = MagicMock()
            if attempt[0] == 0:
                attempt[0] += 1
                proc.poll.return_value = 1  # exited immediately on startup
            else:
                proc.poll.return_value = None
            return proc

        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}

        monkeypatch.setattr("subprocess.Popen", mock_popen)
        monkeypatch.setattr(
            "requests.get",
            lambda url, **kw: mock_props_resp if "/props" in url else mock_health_resp,
        )

        logs = []
        monkeypatch.setattr("stet.llm.model_manager.log", lambda msg: logs.append(msg))
        monkeypatch.setattr("stet.llm.model_manager.has_nvidia", lambda: False)

        assert mgr.load_model() is True
        server_cmds = [c for c in cmds if any("llama-server" in str(x) for x in c)]
        assert len(server_cmds) == 2
        # First attempt had MTP args
        assert "--spec-draft-p-min" in server_cmds[0]
        # Fallback attempt stripped MTP args
        assert "--spec-draft-p-min" not in server_cmds[1]
        assert "--spec-type" not in server_cmds[1]
        assert any("Speculative decoding failed on startup — falling back to non-speculative mode" in log_msg for log_msg in logs)


