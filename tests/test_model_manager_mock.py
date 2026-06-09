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
