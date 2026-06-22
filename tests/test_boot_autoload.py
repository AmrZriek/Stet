"""Tests for StetApp boot-time model auto-load behavior.

Verifies that StetApp.__init__ correctly triggers model loading based on config,
that ac_model_path resolves/syncs correctly, and that the new deferred retry
mechanism works as designed.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import stet.core.config as config_mod
from stet.core.app import StetApp


@pytest.fixture(autouse=True)
def mock_app_dependencies(monkeypatch):
    """Stub out heavy win32/PyQt6 side effects of StetApp.__init__ to prevent crashes."""
    monkeypatch.setattr(StetApp, "_register_hotkey", lambda self: None)
    monkeypatch.setattr(StetApp, "_build_tray", lambda self: None)
    
    qapp = QApplication.instance()
    if qapp:
        monkeypatch.setattr(qapp, "installNativeEventFilter", lambda filter_obj: None)
    else:
        mock_instance = MagicMock()
        monkeypatch.setattr(QApplication, "instance", lambda: mock_instance)


class TestBootAutoLoad:
    """StetApp triggers model load at boot when a model path is configured."""

    def test_boot_starts_ac_model_load_thread_when_path_set(self, tmp_path, monkeypatch, qtbot):
        """StetApp starts a daemon thread to load ac_model when model_path is set."""
        config_file = tmp_path / "config.json"
        model_file = tmp_path / "model.gguf"
        model_file.touch()
        config_file.write_text(json.dumps({
            "model_path": str(model_file),
            "ac_model_path": str(model_file),
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)

        load_calls = []

        def track_load(*args, **kwargs):
            load_calls.append(1)
            return True

        monkeypatch.setattr("stet.llm.model_manager.ModelManager.load_model", track_load)

        app = StetApp()  # noqa: F841

        # Give the daemon thread a moment to start
        time.sleep(0.1)
        assert len(load_calls) >= 1, "Expected load_model to be called at boot"

    def test_boot_skips_load_when_no_model_path(self, tmp_path, monkeypatch, qtbot):
        """StetApp does NOT start model load when model_path is empty."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "ac_model_path": "",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)

        load_calls = []
        def track_load(*args, **kwargs):
            load_calls.append(1)
            return True
        monkeypatch.setattr("stet.llm.model_manager.ModelManager.load_model", track_load)

        app = StetApp()  # noqa: F841

        time.sleep(0.1)
        assert len(load_calls) == 0, f"Expected NO load at boot with empty path, got {len(load_calls)} calls"

    def test_boot_ac_path_synced_from_model_path_when_same_as_chat(self, tmp_path, monkeypatch):
        """Dynamic legacy migration: When ac_same_as_chat=True, chat_use_separate_model is False, and both model_path and chat_model_path are set to old model_path."""
        config_file = tmp_path / "config.json"
        model_file = tmp_path / "model.gguf"
        model_file.touch()
        config_file.write_text(json.dumps({
            "model_path": str(model_file),
            "ac_model_path": "",
            "ac_same_as_chat": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    
        cfg = config_mod.ConfigManager()
        assert cfg.get("chat_use_separate_model") is False
        assert cfg.get("model_path") == str(model_file)
        assert cfg.get("chat_model_path") == str(model_file)

    def test_boot_ac_path_independent_when_not_same_as_chat(self, tmp_path, monkeypatch):
        """Dynamic legacy migration: When ac_same_as_chat=False, chat_use_separate_model is True, model_path gets old ac_model_path, and chat_model_path gets old model_path."""
        config_file = tmp_path / "config.json"
        model_file = tmp_path / "chat_model.gguf"
        ac_file = tmp_path / "ac_model.gguf"
        model_file.touch()
        ac_file.touch()
        config_file.write_text(json.dumps({
            "model_path": str(model_file),
            "ac_model_path": str(ac_file),
            "ac_same_as_chat": False,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)

        cfg = config_mod.ConfigManager()
        assert cfg.get("chat_use_separate_model") is True
        assert cfg.get("model_path") == str(ac_file)
        assert cfg.get("chat_model_path") == str(model_file)


class TestDeferredRetry:
    """StetApp schedules and triggers deferred retry if model path is set but file not found."""

    def test_schedules_deferred_retry_when_not_found(self, tmp_path, monkeypatch, qtbot):
        """StetApp schedules QTimer.singleShot when status indicates model not found."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "nonexistent.gguf",
            "ac_model_path": "nonexistent.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)

        # Mock load_model to not actually sleep or do anything
        monkeypatch.setattr("stet.llm.model_manager.ModelManager.load_model", lambda *args, **kwargs: False)

        timer_calls = []
        def mock_single_shot(msecs, slot):
            timer_calls.append((msecs, slot))

        monkeypatch.setattr(QTimer, "singleShot", mock_single_shot)

        app = StetApp()  # noqa: F841

        # Emit the specific 'Model file not found' status to trigger the slot
        app.ac_model.status_changed.emit("Model file not found — will retry")

        assert len(timer_calls) >= 1
        deferred_calls = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
        assert len(deferred_calls) == 1
        msecs, slot = deferred_calls[0]
        assert msecs == 15_000


    def test_deferred_retry_executes_load_model_thread(self, tmp_path, monkeypatch, qtbot):
        """_deferred_model_retry spawns load_model in thread if model is not loaded."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "nonexistent.gguf",
            "ac_model_path": "nonexistent.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)

        load_calls = []
        def track_load(*args, **kwargs):
            load_calls.append(1)
            return True

        monkeypatch.setattr("stet.llm.model_manager.ModelManager.load_model", track_load)

        app = StetApp()  # noqa: F841

        assert app.ac_model.is_loaded() is False

        # Fire deferred retry directly
        app._deferred_model_retry()

        # Give it a moment to run in the thread
        time.sleep(0.1)
        assert len(load_calls) >= 1, "Expected load_model thread to be launched on deferred retry"

    def test_retry_triggers_on_load_error(self, tmp_path, monkeypatch, qtbot):
        """Retry must fire on any failure status, not just 'not found'."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "exists.gguf",
            "ac_model_path": "exists.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model",
            lambda *args, **kwargs: False,
        )

        timer_calls = []
        def mock_single_shot(msecs, slot):
            timer_calls.append((msecs, slot))
        monkeypatch.setattr(QTimer, "singleShot", mock_single_shot)

        app = StetApp()  # noqa: F841

        # "Load error" status — server crash or timeout
        app.ac_model.status_changed.emit("Load error: Server did not start within 180 s")
        assert len(timer_calls) >= 1
        deferred = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
        assert len(deferred) == 1, "Expected retry to be scheduled on 'Load error'"

    def test_retry_triggers_on_server_exited(self, tmp_path, monkeypatch, qtbot):
        """Retry must fire on 'Server exited' status."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "exists.gguf",
            "ac_model_path": "exists.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model",
            lambda *args, **kwargs: False,
        )

        timer_calls = []
        def mock_single_shot(msecs, slot):
            timer_calls.append((msecs, slot))
        monkeypatch.setattr(QTimer, "singleShot", mock_single_shot)

        app = StetApp()  # noqa: F841
        app.ac_model.status_changed.emit("Server exited immediately — see server_log.txt")
        deferred = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
        assert len(deferred) == 1, "Expected retry on 'Server exited' status"

    def test_retry_uses_exponential_backoff(self, tmp_path, monkeypatch, qtbot):
        """Second retry should have longer delay than first."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "exists.gguf",
            "ac_model_path": "exists.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model",
            lambda *args, **kwargs: False,
        )

        timer_calls = []
        def mock_single_shot(msecs, slot):
            timer_calls.append((msecs, slot))
        monkeypatch.setattr(QTimer, "singleShot", mock_single_shot)

        app = StetApp()  # noqa: F841

        # First failure → delay should be 45_000 ms
        app.ac_model.status_changed.emit("Load error: Server did not start")
        deferred1 = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
        assert len(deferred1) == 1
        msecs1 = deferred1[0][0]
        assert msecs1 == 15_000, f"First retry should be 15s, got {msecs1 // 1000}s"

        # Simulate retry executing (reset _retry_scheduled) then second failure
        app._retry_scheduled = False
        app.ac_model.status_changed.emit("Load error: Server did not start")
        deferred2 = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
        assert len(deferred2) == 2
        msecs2 = deferred2[1][0]
        assert msecs2 == 30_000, f"Second retry should be 30s (exponential), got {msecs2 // 1000}s"

    def test_retry_stops_after_max_retries(self, tmp_path, monkeypatch, qtbot):
        """After _max_retries failures, no more retries are scheduled."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "exists.gguf",
            "ac_model_path": "exists.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(
            "stet.llm.model_manager.ModelManager.load_model",
            lambda *args, **kwargs: False,
        )

        timer_calls = []
        def mock_single_shot(msecs, slot):
            timer_calls.append((msecs, slot))
        monkeypatch.setattr(QTimer, "singleShot", mock_single_shot)

        app = StetApp()  # noqa: F841
        app._max_retries = 3

        # Fire _max_retries failures
        for attempt in range(3):
            timer_calls.clear()
            app._retry_scheduled = False
            app.ac_model.status_changed.emit("Load error: Server did not start")
            deferred = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
            assert len(deferred) == 1, f"Expected retry on attempt {attempt + 1}"

        # Fourth failure should NOT schedule a retry
        timer_calls.clear()
        app._retry_scheduled = False
        app.ac_model.status_changed.emit("Load error: Server did not start")
        deferred = [(m, s) for m, s in timer_calls if s == app._deferred_model_retry]
        assert len(deferred) == 0, "Expected NO retry after max retries reached"

    def test_retry_count_resets_on_success(self, tmp_path, monkeypatch, qtbot):
        """When _deferred_model_retry finds model already loaded, counter resets."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "exists.gguf",
            "ac_model_path": "exists.gguf",
            "ac_same_as_chat": True,
            "keep_model_loaded": True,
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)

        app = StetApp()  # noqa: F841
        app._retry_count = 2

        # Simulate model_loaded signal making is_loaded return True
        # Patch is_loaded on the instance
        with patch.object(app.ac_model, "is_loaded", return_value=True):
            app._deferred_model_retry()
        assert app._retry_count == 0, "Retry count should reset on successful load"
