"""Tests for stet.llm.model_manager -- subprocess management, Job Object, health checks."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stet.core.config import ConfigManager
from stet.llm.model_manager import (
    _STRENGTH_TO_MODE_INDEX,
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
            with patch("ctypes.windll.kernel32", mock_kernel32):
                proc = MagicMock()
                proc._handle = 1234
                _create_job_object_for_subprocess(proc)
                # Should not crash even when Job Object creation fails


# -- Strength routing ---------------------------------------------------------


class TestStrengthModeIndex:
    """_STRENGTH_TO_MODE_INDEX maps all variants correctly."""

    def test_conservative_variants(self):
        assert _STRENGTH_TO_MODE_INDEX["conservative"] == 0
        assert _STRENGTH_TO_MODE_INDEX["spelling_only"] == 0

    def test_smart_fix_variants(self):
        assert _STRENGTH_TO_MODE_INDEX["smart_fix"] == 1
        assert _STRENGTH_TO_MODE_INDEX["full_correction"] == 1

    def test_aggressive_variants(self):
        assert _STRENGTH_TO_MODE_INDEX["aggressive"] == 2
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

        def fake_post(url, *args, **kwargs):
            captured_calls.append({
                "url": url,
                "json": kwargs.get("json"),
                "timeout": kwargs.get("timeout"),
            })
            return MockResponse({"choices": [{"message": {"content": ""}}]})

        monkeypatch.setattr("requests.post", fake_post)
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
            # User message should contain the START/END markers
            user_msg = payload["messages"][1]
            assert user_msg["role"] == "user"
            assert "<<<START>>>" in user_msg["content"]
            assert payload["messages"][2]["role"] == "assistant"
            assert payload["messages"][2]["content"] == "<<<START>>>\n"

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

        monkeypatch.setattr("requests.post", fake_post)
        manager._warmup_prompt_cache()

        assert len(captured_calls) == 1
        messages = captured_calls[0]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert "Fix spelling, grammar, punctuation, and capitalization" in messages[0]["content"]
        assert "<<<START>>>\nwarmup\n<<<END>>>" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "<<<START>>>\n"

    def test_swallows_exceptions(self, manager, monkeypatch):
        """A failed warmup must not raise — load must still complete."""
        def fake_post(url, *args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("requests.post", fake_post)
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
            cancel_event=None, mode_prompt_override=None, session=None: (
                "Hello world this is test."
            )
        )

        # Pre-warm so a session exists; capture it
        pre = manager._get_session()

        result, units = manager.correct_text_patch(
            "hello world this is test", strength="smart_fix"
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
            cancel_event=None, mode_prompt_override=None, session=None: (
                "Hello world this is test."
            )
        )

        s1 = manager._get_session()
        mgr_id_after_first = id(manager._session)

        manager.correct_text_patch("hello world this is test", strength="smart_fix")
        assert id(manager._session) == mgr_id_after_first

        manager.correct_text_patch("another sentence to fix", strength="smart_fix")
        assert id(manager._session) == mgr_id_after_first

        manager.correct_text_patch("yet another chunk here", strength="smart_fix")
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

        manager._rewrite_sentence_chunk("hello world", None, 1, 1, "smart_fix")

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
        res = manager._rewrite_sentence_chunk("Hello world.", None, 1, 1, "smart_fix")
        assert res == "Hello world."

        # Exclamation dropped -> restored
        res = manager._rewrite_sentence_chunk("Hello world!", None, 1, 1, "smart_fix")
        assert res == "Hello world!"

        # Question mark dropped -> restored
        res = manager._rewrite_sentence_chunk("Hello world?", None, 1, 1, "smart_fix")
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
        monkeypatch.setattr(mm, "_extract_rewritten_sentence", lambda raw: "Hello world ")

        res = manager._rewrite_sentence_chunk("Hello world. ", None, 1, 1, "smart_fix")
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

        res = manager._rewrite_sentence_chunk("Hello world.", None, 1, 1, "smart_fix")
        assert res == "Hello world."