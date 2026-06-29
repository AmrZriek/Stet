"""Tests for configuration auto-detection and download monitoring logic."""

import json
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import stet.core.config as config_mod
from stet.core.config import ConfigManager
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


class TestConfigAutoDetect:
    """Verify ConfigManager.auto_detect finds files and updates settings."""

    def test_auto_detect_none_found(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        with (
            patch("stet.llm.utils._is_valid_gguf", return_value=False),
            patch("stet.llm.utils._find_shipped_llama_server", return_value=""),
        ):
            cfg = ConfigManager()
            # Initial boot run did nothing since no files existed
            assert cfg.get("model_path") == ""
            assert cfg.get("llama_server_path") == ""

            # Run auto-detect explicitly
            assert cfg.auto_detect() is False

    def test_auto_detect_gguf_found(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        # Create a valid GGUF file (starts with GGUF and >10MB)
        model_file = tmp_path / "gemma.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * (10 * 1024 * 1024 + 1))

        with (
            patch("stet.llm.utils._is_valid_gguf", return_value=True),
            patch("stet.llm.utils._find_shipped_llama_server", return_value=""),
        ):
            # Config initialization triggers auto_detect
            cfg = ConfigManager()
            assert cfg.get("model_path") == str(model_file)
            assert cfg.get("chat_model_path") == str(model_file)

    def test_auto_detect_server_found(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        # Mock find shipped server
        server_path = tmp_path / "llama-server.exe"
        with (
            patch("stet.llm.utils._is_valid_gguf", return_value=False),
            patch("stet.llm.utils._find_shipped_llama_server", return_value=str(server_path)),
        ):
            cfg = ConfigManager()
            assert cfg.get("llama_server_path") == str(server_path)


class TestAppDownloadMonitoring:
    """Verify StetApp download polling and trigger logic."""

    def test_download_poll_completed_trigger_first_run(self, tmp_path, monkeypatch, qtbot):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        app = StetApp()
        monkeypatch.setattr(QTimer, "singleShot", lambda delay, slot: slot())
        
        # Mock running process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Process finished
        
        app._download_processes = [mock_proc]
        app._download_timer = MagicMock()

        # Mock that llama-server is now found, but no model is downloaded yet
        server_path = tmp_path / "llama-server.exe"
        first_run_called = []
        monkeypatch.setattr(app, "_show_first_run", lambda: first_run_called.append(True))

        with (
            patch("stet.core.app._find_shipped_llama_server", return_value=str(server_path)),
            patch.object(app.cfg, "auto_detect", return_value=True)
        ):
            app._check_download_processes()
            assert len(app._download_processes) == 0
            # Since model is still missing, it should show the first run welcome prompt
            # (which has been scheduled/called)
            assert app._download_timer.stop.called
            assert len(first_run_called) == 1

    def test_download_poll_completed_trigger_model_load(self, tmp_path, monkeypatch, qtbot):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        app = StetApp()
        
        # Mock running process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Process finished
        
        app._download_processes = [mock_proc]
        app._download_timer = MagicMock()

        model_file = tmp_path / "model.gguf"
        app.cfg.set("model_path", str(model_file))  # Pretend model is configured now
        
        reloaded = []
        monkeypatch.setattr(app, "_on_settings_saved", lambda: reloaded.append(True))
        app.tray = MagicMock()

        with (
            patch("stet.core.app._find_shipped_llama_server", return_value="server.exe"),
            patch.object(app.cfg, "auto_detect", return_value=True)
        ):
            app._check_download_processes()
            assert len(app._download_processes) == 0
            assert app._download_timer.stop.called
            assert len(reloaded) == 1
            app.tray.showMessage.assert_called_once()

    def test_hotkey_fired_triggers_auto_detect_and_loads(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        app = StetApp()
        
        # Mock load_model
        load_called = []
        monkeypatch.setattr(app.ac_model, "load_model", lambda: load_called.append(True))
        monkeypatch.setattr(app.ac_model, "is_loaded", lambda: False)
        
        # Mock auto_detect to return True
        app.cfg.set("model_path", "some_model.gguf")
        monkeypatch.setattr(app.cfg, "auto_detect", lambda: True)

        with patch("threading.Thread") as mock_thread:
            app._handle_hotkey_fired({"mode": "panel", "strength": "full_correction"})
            mock_thread.assert_any_call(target=app.ac_model.load_model, daemon=True)

    def test_download_scripts_use_visible_terminal(self, tmp_path, monkeypatch):
        # Force WINDOWS to True to test Windows Popen branch
        monkeypatch.setattr("stet.core.app.WINDOWS", True)
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "model_path": "",
            "chat_model_path": "",
            "llama_server_path": "",
        }))
        monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
        monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)

        # Create dummy script files to satisfy exists() checks
        (tmp_path / "download_backend.bat").write_text("")
        (tmp_path / "download_model.bat").write_text("")

        app = StetApp()
        app.tray = MagicMock()
        app._download_timer = MagicMock()

        with patch("subprocess.Popen") as mock_popen:
            app._run_download_backend_script()
            mock_popen.assert_called_once()
            cmd_args = mock_popen.call_args[0][0]
            # Should use: cmd /c start /wait "" <script_path>
            assert cmd_args[:5] == ["cmd", "/c", "start", "/wait", ""]
            assert "download_backend.bat" in str(cmd_args[5])

        with patch("subprocess.Popen") as mock_popen:
            app._run_download_script()
            mock_popen.assert_called_once()
            cmd_args = mock_popen.call_args[0][0]
            assert cmd_args[:5] == ["cmd", "/c", "start", "/wait", ""]
            assert "download_model.bat" in str(cmd_args[5])

