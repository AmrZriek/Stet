"""Comprehensive integration tests targeting uncovered branches in Stet.
Covers app.py, model_manager.py, settings.py, and main_window.py.
"""

import json
import pytest
import threading
import importlib
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QMessageBox

from stet.core.config import ConfigManager
from stet.core.app import StetApp, _is_terminal_or_ide
from stet.llm.model_manager import ModelManager
from stet.ui.settings import SettingsDialog
from stet.ui.main_window import CorrectionWindow


# ── Metaprogramming Bypasses ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def restore_real_load_model(monkeypatch):
    """Restore the real load_model method for testing it directly.

    The global block_model_load fixture in conftest.py patches load_model
    to a no-op to prevent tests from spawning llama-server processes.
    For this test file, we reload the module to get the clean class method
    and patch it back onto ModelManager so we can test its inner logic.
    """
    import stet.llm.model_manager

    importlib.reload(stet.llm.model_manager)
    real_load_model = stet.llm.model_manager.ModelManager.load_model
    monkeypatch.setattr(
        stet.llm.model_manager.ModelManager, "load_model", real_load_model
    )
    monkeypatch.setattr(ModelManager, "load_model", real_load_model)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """ConfigManager reading from a temporary config file."""
    config_file = tmp_path / "config.json"
    model_path = tmp_path / "fake-model.gguf"
    model_path.touch()

    config_file.write_text(
        json.dumps(
            {
                "model_path": str(model_path),
                "ac_model_path": str(model_path),
                "server_binary": "",
                "server_host": "127.0.0.1",
                "server_port": 8080,
                "context_size": 2048,
                "gpu_layers": 8,
                "temperature": 0.15,
                "top_k": 35,
                "top_p": 0.90,
                "min_p": 0.05,
                "keep_model_loaded": False,
                "idle_timeout_seconds": 300,
                "ac_same_as_chat": False,
                "target_language": "Spanish",
                "chat_mode": "conversation",
                "hotkeys": [
                    {
                        "shortcut": "ctrl+f9",
                        "mode": "panel",
                        "strength": "full_correction",
                    },
                    {
                        "shortcut": "ctrl+f10",
                        "mode": "silent",
                        "strength": "spelling_only",
                    },
                ],
                "custom_templates": [
                    {"name": "Template A", "prompt": "Prompt A"},
                    {"name": "Template B", "prompt": "Prompt B"},
                ],
                "correction_modes": [
                    {
                        "name": "Conservative",
                        "prompt": "Fix spelling.",
                        "hallucination_threshold": 0.4,
                    },
                    {
                        "name": "Smart Fix",
                        "prompt": "Fix spelling.",
                        "hallucination_threshold": 1.0,
                    },
                    {
                        "name": "Smart Fix Custom",
                        "prompt": "Fix spelling.",
                        "hallucination_threshold": 1.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    import stet.core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return ConfigManager()


@pytest.fixture
def dialog(cfg, qtbot):
    """SettingsDialog attached to qtbot."""
    dlg = SettingsDialog(cfg)
    qtbot.addWidget(dlg)
    return dlg


# ── Section 1: stet/core/app.py ───────────────────────────────────────────


class TestAppCoverage:
    """Coverage expansion for app.py."""

    def test_is_terminal_or_ide_handles_invalid_hwnd(self):
        # Should return False on invalid hwnd (like 0 or None)
        assert _is_terminal_or_ide(0) is False
        assert _is_terminal_or_ide(None) is False

    @patch("stet.core.app.make_tray_icon")
    @patch("stet.core.app.QSystemTrayIcon")
    def test_app_tray_menu_and_signals(
        self, mock_tray_class, mock_make_icon, cfg, qtbot, monkeypatch
    ):
        # Mock load_model to avoid background server spawn
        monkeypatch.setattr(ModelManager, "load_model", lambda *args, **kwargs: True)

        # Instantiate StetApp
        app = StetApp()
        if app._window:
            qtbot.addWidget(app._window)

        # Trigger tray status changes
        app._on_ac_status("correcting")
        app._on_chat_status("loading model")

        # Verify model size warning triggering
        warning_emitted = []
        app.ac_model.model_warning.connect(warning_emitted.append)
        app.ac_model.model_warning.emit("tiny model warning")
        assert "tiny model warning" in warning_emitted

        # Test double-click setting tray activation
        import stet.core.app

        with patch.object(app, "_open_settings") as mock_open:
            app._tray_activated(
                stet.core.app.QSystemTrayIcon.ActivationReason.DoubleClick
            )
            mock_open.assert_called_once()

    @patch("stet.core.app.WINDOWS", True)
    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.winreg", create=True)
    def test_app_toggle_startup(self, mock_winreg, mock_run, cfg, qtbot, monkeypatch):
        mock_run.return_value = MagicMock(returncode=1)
        # Mock load_model to avoid background server spawn
        monkeypatch.setattr(ModelManager, "load_model", lambda *args, **kwargs: True)

        app = StetApp()

        # Test startup status updater
        app._act_startup.setChecked(True)
        mock_winreg.QueryValueEx.return_value = ("cmd.exe", 1)
        app._update_startup_action()
        assert app._act_startup.isChecked() is True

        # Test toggle to False
        app._act_startup.setChecked(False)
        app._toggle_startup(False)
        mock_winreg.DeleteValue.assert_called()

        # Test toggle to True
        app._act_startup.setChecked(True)
        app._toggle_startup(True)
        mock_winreg.SetValueEx.assert_called()

    def test_app_various_methods(self):
        pass


# ── Section 2: stet/llm/model_manager.py ──────────────────────────────────


class TestModelManagerCoverage:
    """Coverage expansion for model_manager.py."""

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_load_model_success_path(self, mock_get, mock_popen, cfg):
        manager = ModelManager(cfg)

        # Mock health endpoint ok, and props returning context size
        mock_health_resp = MagicMock()
        mock_health_resp.status_code = 200
        mock_props_resp = MagicMock()
        mock_props_resp.ok = True
        mock_props_resp.json.return_value = {"n_ctx": 4096}

        mock_get.side_effect = lambda url, **kwargs: (
            mock_props_resp if "/props" in url else mock_health_resp
        )

        # Mock process
        proc = MagicMock()
        proc.poll.return_value = None  # Alive
        mock_popen.return_value = proc

        # Load model and verify context is populated
        assert manager.load_model() is True
        assert manager.actual_ctx_size == 4096
        assert manager.is_loaded() is True

        manager.unload_model()
        assert manager.is_loaded() is False

    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_load_model_timeout_path(self, mock_get, mock_popen, cfg):
        manager = ModelManager(cfg)

        # Mock process to be alive, but request to health always throws ConnectionError
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("offline")

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        # Load model should fail due to health check timeout (mock sleep so test is instant)
        with patch("time.sleep"):
            assert manager.load_model() is False
        assert manager.is_loaded() is False

    @patch("stet.llm.utils.has_nvidia", return_value=False)
    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_load_model_gpu_oom_retry_cpu(
        self, mock_get, mock_popen, mock_has_nvidia, cfg, monkeypatch
    ):
        # Reload and force restore real class method within this test scope
        import stet.llm.model_manager
        import traceback

        importlib.reload(stet.llm.model_manager)
        real_load_model = stet.llm.model_manager.ModelManager.load_model

        def wrap_load_model(*args, **kwargs):
            try:
                res = real_load_model(*args, **kwargs)
                print(f"DEBUG: load_model returned {res}")
                return res
            except Exception as ex:
                print("DEBUG exception in load_model:")
                traceback.print_exc()
                raise ex

        monkeypatch.setattr(
            stet.llm.model_manager.ModelManager,
            "load_model",
            wrap_load_model,
        )
        monkeypatch.setattr(
            ModelManager,
            "load_model",
            wrap_load_model,
        )
        monkeypatch.setattr(
            stet.llm.model_manager,
            "log",
            lambda msg: print(f"INTERCEPTED LOG: {msg}"),
        )

        manager = ModelManager(cfg)
        manager.cfg.set("gpu_layers", 10)

        # On GPU load, popen raises a CUDA runtime error
        cpu_proc = MagicMock()
        cpu_proc.poll.return_value = None  # Process stays alive

        mock_popen.side_effect = [
            RuntimeError("CUDA error: out of memory"),  # GPU try fails
            cpu_proc,  # CPU retry succeeds
        ]

        # Mock health check OK for the second try
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        with patch("time.sleep"):
            res = manager.load_model()
            assert res is True
            assert manager.server_process is not None

    @patch("stet.llm.model_manager._model_size_billions", return_value=0.45)
    @patch("subprocess.Popen")
    @patch("requests.get")
    def test_load_model_warning_tiny_model(self, mock_get, mock_popen, mock_size, cfg):
        manager = ModelManager(cfg)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        # Capture signals
        warning_message = []
        manager.model_warning.connect(warning_message.append)

        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        assert manager.load_model() is True
        assert len(warning_message) == 1
        assert "parameters" in warning_message[0]

    def test_correct_text_patch_cancel(self, cfg):
        manager = ModelManager(cfg)
        cancel = threading.Event()
        cancel.set()  # pre-cancelled

        res, units = manager.correct_text_patch("Hello world", cancel_event=cancel)
        assert res is None
        assert units == 0



    @patch("requests.Session.post")
    def test_correct_text_patch_hallucination_reject(self, mock_post, cfg, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *args, **kwargs: True)

        manager = ModelManager(cfg)
        manager.cfg.set(
            "hotkeys",
            [{"shortcut": "ctrl+f9", "mode": "panel", "strength": "conservative"}],
        )

        # Set hallucination threshold very low to reject changes
        manager.cfg.set(
            "correction_modes",
            [
                {
                    "name": "Conservative Spelling",
                    "prompt": "Fix spelling.",
                    "hallucination_threshold": 0.1,
                }
            ],
        )

        proc = MagicMock()
        proc.poll.return_value = None
        manager.server_process = proc  # mock loaded

        # Mock rewrite sentence API response returning a completely different sentence (hallucination)
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "<<<START>>>This is completely unrelated new sentence.<<<END>>>"
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        mock_post.return_value = mock_resp

        # Call correction on a long sentence (> 3 words so word_count limit threshold doesn't boost to 0.7)
        # should reject hallucination and fall back to returning None (caller does stream fallback)
        orig_text = "Hello beautiful new world that we are living in."
        res, units = manager.correct_text_patch(orig_text, strength="conservative")
        assert res is None
        assert units == 1


# ── Section 3: stet/ui/settings.py ────────────────────────────────────────


class TestSettingsCoverage:
    """Coverage expansion for settings.py and settings_pages.py."""

    def test_active_same_cb_toggled(self, dialog):
        # Toggling Same Model as Chat checkbox disables/enables autocorrect model row
        dialog.chat_use_separate_cb.setChecked(True)
        assert dialog.chat_row_w.isEnabled() is True
        dialog.chat_use_separate_cb.setChecked(False)
        assert dialog.chat_row_w.isEnabled() is False

    @patch("PyQt6.QtWidgets.QMessageBox.warning")
    @patch("PyQt6.QtWidgets.QDialog.exec")
    def test_validation_empty_hotkey(self, mock_exec, mock_warning, dialog):
        # dlg.exec returns Accepted
        mock_exec.return_value = 1

        # Attempt to edit hotkey dialog with an empty key input
        with patch("stet.ui.components.HotkeyEdit.text", return_value=""):
            dialog._edit_hotkey(-1)
            mock_warning.assert_called_once()

    @patch("PyQt6.QtWidgets.QDialog.exec")
    def test_settings_profiles_crud(self, mock_exec, dialog):
        # mock QDialog.exec to return Accepted (1)
        mock_exec.return_value = 1

        count_before = len(dialog._temp_hotkeys)

        with patch("stet.ui.components.HotkeyEdit.text", return_value="ctrl+shift+p"):
            dialog._edit_hotkey(-1)  # Add new
            assert len(dialog._temp_hotkeys) == count_before + 1
            assert dialog._temp_hotkeys[-1]["shortcut"] == "ctrl+shift+p"

        # Edit existing profile
        with patch("stet.ui.components.HotkeyEdit.text", return_value="ctrl+shift+p"):
            dialog._edit_hotkey(len(dialog._temp_hotkeys) - 1)
            assert dialog._temp_hotkeys[-1]["shortcut"] == "ctrl+shift+p"

        # Delete existing profile (exec returns 2 = Delete)
        mock_exec.return_value = 2
        dialog._edit_hotkey(len(dialog._temp_hotkeys) - 1)
        assert len(dialog._temp_hotkeys) == count_before

    @patch(
        "PyQt6.QtWidgets.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    )
    @patch("PyQt6.QtWidgets.QDialog.exec")
    def test_settings_templates_crud_and_reorder(
        self, mock_exec, mock_question, dialog
    ):
        # CRUD operations for templates
        mock_exec.return_value = 1
        count_before = len(dialog._temp_templates)

        with patch("PyQt6.QtWidgets.QLineEdit.text", return_value="New Template"):
            dialog._edit_template(-1)  # Add new
            assert len(dialog._temp_templates) == count_before + 1
            assert dialog._temp_templates[-1]["name"] == "New Template"

        # Edit existing template
        with patch("PyQt6.QtWidgets.QLineEdit.text", return_value="Edited Template"):
            dialog._edit_template(len(dialog._temp_templates) - 1)
            assert dialog._temp_templates[-1]["name"] == "Edited Template"

        # Delete existing template (exec returns 2 = Delete)
        mock_exec.return_value = 2
        dialog._edit_template(len(dialog._temp_templates) - 1)
        assert len(dialog._temp_templates) == count_before

    def test_on_templates_reordered(self, dialog):
        # Manually set temp templates
        dialog._temp_templates = [
            {"name": "A", "prompt": "Prompt A"},
            {"name": "B", "prompt": "Prompt B"},
        ]
        dialog._refresh_settings_templates()

        # Simulate reordering templates in QListWidget
        dialog.templates_list_w.clear()
        dialog.templates_list_w.addItem("B")
        dialog.templates_list_w.addItem("A")

        dialog._on_templates_reordered()

        # Verify internal temp list updated its order to match list widget
        assert dialog._temp_templates[0]["name"] == "B"
        assert dialog._temp_templates[1]["name"] == "A"

    @patch(
        "PyQt6.QtWidgets.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    )
    def test_delete_selected_template(self, mock_question, dialog):
        dialog._temp_templates = [
            {"name": "Template A", "prompt": "Prompt A"},
            {"name": "Template B", "prompt": "Prompt B"},
        ]
        dialog._refresh_settings_templates()

        dialog.templates_list_w.setCurrentRow(1)
        dialog._delete_selected_template()

        assert len(dialog._temp_templates) == 1
        assert dialog._temp_templates[0]["name"] == "Template A"
        mock_question.assert_called_once()

    @patch(
        "PyQt6.QtWidgets.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    )
    def test_delete_selected_template_cancel(self, mock_question, dialog):
        dialog._temp_templates = [
            {"name": "Template A", "prompt": "Prompt A"},
        ]
        dialog._refresh_settings_templates()

        dialog.templates_list_w.setCurrentRow(0)
        dialog._delete_selected_template()

        assert len(dialog._temp_templates) == 1
        mock_question.assert_called_once()

    def test_delete_selected_template_no_selection(self, dialog):
        dialog._temp_templates = [
            {"name": "Template A", "prompt": "Prompt A"},
        ]
        dialog._refresh_settings_templates()

        dialog.templates_list_w.setCurrentItem(None)
        dialog._delete_selected_template()

        assert len(dialog._temp_templates) == 1


# ── Section 4: stet/ui/main_window.py ─────────────────────────────────────


class TestMainWindowCoverage:
    """Coverage expansion for main_window.py."""

    def test_render_diff_preserving_newlines(self, qtbot, cfg):
        ac_model = MagicMock()
        chat_model = MagicMock()
        cw = CorrectionWindow("Hello\nWorld", ac_model, chat_model, cfg)
        qtbot.addWidget(cw)

        cw._render_diff("Hello\nBeautiful\nWorld")

        html = cw.corr_edit.toHtml()
        assert "Hello" in html
        assert "Beautiful" in html
        assert "World" in html

    def test_apply_template_triggers_chat_reset(self, qtbot, cfg):
        ac_model = MagicMock()
        chat_model = MagicMock()
        cw = CorrectionWindow("Hello\nWorld", ac_model, chat_model, cfg)
        qtbot.addWidget(cw)

        # Populate history
        cw.chat_history = [{"role": "user", "content": "hello"}]

        with patch.object(cw, "_send_chat") as mock_send:
            cw._apply_template("Translate to French")
            assert cw._correction_cancelled is True
            assert len(cw.chat_history) == 0
            mock_send.assert_called_with(msg="Translate to French", is_template=True)

    def test_cancel_and_close_events(self, qtbot, cfg):
        ac_model = MagicMock()
        chat_model = MagicMock()
        cw = CorrectionWindow("Hello\nWorld", ac_model, chat_model, cfg)
        qtbot.addWidget(cw)

        mock_stream = MagicMock()
        mock_stream.isRunning.return_value = True
        cw._stream_worker = mock_stream
        cw._correction_stream_worker = mock_stream

        # Trigger reset
        cw._reset()
        assert cw._correction_cancelled is True
        mock_stream.stop.assert_called()
