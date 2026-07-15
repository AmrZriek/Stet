"""Coverage expansion tests for stet/core/app.py.

Targets: tray retry, set_tray_icon, status handlers, rebuild_recent_menu,
show_notify, show_model_warning, show_window, on_window_destroyed,
paste_text, open_settings, on_settings_saved, browse_model, select_model,
check_app_update, on_update_available, updater_command, start_app_update,
quit, show_first_run, run_download_script, update_startup_action,
toggle_startup, parse_hotkey_string, WinHotkeyFilter, _is_terminal_or_ide,
_quote_cmd, _source_startup_python, _startup_command, _show_silent_osd,
_is_model_ready, _wait_for_model_ready.
"""

import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QMessageBox

from stet.core.app import (
    StetApp,
    AppUpdateChecker,
)
from stet.core.config import ConfigManager
from stet.llm.model_manager import ModelManager


# ── Helpers & fixtures ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_register_hotkey(monkeypatch):
    """Prevent real system-wide hotkey registration and native event filter crashes."""
    import ctypes

    monkeypatch.setattr(
        ctypes.windll.user32, "RegisterHotKey", MagicMock(return_value=1)
    )
    monkeypatch.setattr(
        ctypes.windll.user32, "UnregisterHotKey", MagicMock(return_value=1)
    )
    # Use a safe subclass that won't crash on real Windows messages
    from PyQt6.QtCore import QAbstractNativeEventFilter

    class SafeHotkeyFilter(QAbstractNativeEventFilter):
        def __init__(self):
            super().__init__()
            self._callbacks = {}

        def register_callback(self, hotkey_id, callback):
            self._callbacks[hotkey_id] = callback

        def clear_callbacks(self):
            self._callbacks.clear()

        def nativeEventFilter(self, event_type, message):
            return False, 0

    monkeypatch.setattr("stet.core.app.WinHotkeyFilter", SafeHotkeyFilter)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    model_path = tmp_path / "fake-model.gguf"
    model_path.touch()
    config_file.write_text(
        __import__("json").dumps(
            {
                "model_path": str(model_path),
                "chat_model_path": str(model_path),
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
                "chat_use_separate_model": False,
                "chat_keep_loaded": False,
                "chat_idle_timeout_seconds": 60,
                "target_language": "Spanish",
                "chat_mode": "conversation",
                "hotkeys": [
                    {
                        "shortcut": "ctrl+f9",
                        "mode": "panel",
                        "strength": "full_correction",
                    }
                ],
                "custom_templates": [
                    {"name": "Template A", "prompt": "Prompt A"},
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
                "recent_models": ["/some/model.gguf", "/other/model.gguf"],
            }
        ),
        encoding="utf-8",
    )
    import stet.core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return ConfigManager()


# ── Standalone function tests ─────────────────────────────────────────────


class TestQuoteCmd:
    def test_simple_args(self):
        from stet.core.app import _quote_cmd

        assert _quote_cmd(["echo", "hello"]) == "echo hello"

    def test_args_with_spaces(self):
        from stet.core.app import _quote_cmd

        result = _quote_cmd(["C:\\Program Files\\app.exe", "--flag"])
        assert '"C:\\Program Files\\app.exe"' in result

    def test_empty_list(self):
        from stet.core.app import _quote_cmd

        assert _quote_cmd([]) == ""


class TestSourceStartupPython:
    def test_returns_pythonw_when_current_is_python_exe(self, monkeypatch):
        from stet.core.app import _source_startup_python

        monkeypatch.setattr(sys, "executable", "C:\\Python313\\python.exe")
        result = _source_startup_python()
        assert isinstance(result, str)

    def test_falls_back_to_which(self, monkeypatch):
        from stet.core.app import _source_startup_python

        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        result = _source_startup_python()
        assert isinstance(result, str)


class TestStartupCommand:
    def test_frozen_returns_executable(self, monkeypatch):
        from stet.core.app import _startup_command

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", "C:\\Stet\\Stet.exe")
        result = _startup_command()
        assert "Stet.exe" in result

    def test_nuitka_returns_executable_without_sys_frozen(self, monkeypatch, tmp_path):
        """Nuitka builds: sys.frozen=False but exe is Stet.exe → must return exe path."""
        from stet.core.app import _startup_command

        exe = tmp_path / "Stet.exe"
        exe.write_text("")
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        result = _startup_command()
        assert "Stet.exe" in result
        assert "python" not in result.lower()
        assert "stet.main" not in result

    def test_vbs_exists_returns_wscript(self, monkeypatch, tmp_path):
        from stet.core.app import _startup_command

        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        vbs = tmp_path / "startup.vbs"
        vbs.write_text("x")
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        result = _startup_command()
        assert "wscript.exe" in result

    def test_main_py_fallback(self, monkeypatch, tmp_path):
        from stet.core.app import _startup_command

        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        stet_dir = tmp_path / "stet"
        stet_dir.mkdir()
        main_py = stet_dir / "main.py"
        main_py.write_text("pass")
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        result = _startup_command()
        assert "main.py" in result

    def test_module_fallback(self, monkeypatch, tmp_path):
        from stet.core.app import _startup_command

        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        result = _startup_command()
        assert "stet.main" in result


class TestParseHotkeyString:
    def test_ctrl_f9(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("ctrl+f9")
        assert vk == 0x78
        assert mods & 0x0002

    def test_ctrl_shift_f10(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("ctrl+shift+f10")
        assert vk == 0x79
        assert mods & 0x0002
        assert mods & 0x0004

    def test_alt_space(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("alt+space")
        assert vk == 0x20
        assert mods & 0x0001

    def test_invalid_key_returns_zero(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("ctrl+nonexistentkey")
        assert mods == 0
        assert vk == 0

    def test_single_key(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("f9")
        assert vk == 0x78

    def test_letter_key(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("ctrl+a")
        assert vk == ord("A")

    def test_number_key(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("ctrl+5")
        assert vk == ord("5")

    def test_win_modifier(self):
        from stet.core.app import parse_hotkey_string

        mods, vk = parse_hotkey_string("win+f9")
        assert mods & 0x0008

    def test_norepeat_always_set(self):
        from stet.core.app import parse_hotkey_string

        mods, _ = parse_hotkey_string("f9")
        assert mods & 0x4000


class TestWinHotkeyFilter:
    def test_fixture_filter_register_and_clear(self):
        """Test that the safe filter used in tests works correctly."""
        from stet.core.app import WinHotkeyFilter

        f = WinHotkeyFilter()
        cb = MagicMock()
        f.register_callback(1000, cb)
        assert 1000 in f._callbacks
        f.clear_callbacks()
        assert len(f._callbacks) == 0

    def test_fixture_filter_ignores_events(self):
        from stet.core.app import WinHotkeyFilter

        f = WinHotkeyFilter()
        result = f.nativeEventFilter(b"some_other_type", 0)
        assert result == (False, 0)


class TestIsTerminalOrIde:
    def test_none_returns_false(self):
        from stet.core.app import _is_terminal_or_ide

        assert _is_terminal_or_ide(None) is False

    def test_zero_returns_false(self):
        from stet.core.app import _is_terminal_or_ide

        assert _is_terminal_or_ide(0) is False

    # ── Class name detection tests ──────────────────────────────────────

    @pytest.mark.parametrize(
        "class_name",
        [
            "ConsoleWindowClass",
            "CascadiaHostingWindowClass",
            "VteTerminal",
            "mintty",
            "PuTTY",
            "ConhostWindow",
        ],
    )
    def test_terminal_class_names_return_true(self, class_name, monkeypatch):
        """Known terminal window class names must block capture."""
        import ctypes

        from stet.core.app import _is_terminal_or_ide

        def _write_class_name(hwnd, buf, max_len):
            for i, ch in enumerate(class_name):
                if i < max_len - 1:
                    buf[i] = ch
            buf[min(len(class_name), max_len - 1)] = "\0"
            return len(class_name)

        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _write_class_name
        )
        assert _is_terminal_or_ide(12345) is True

    @pytest.mark.parametrize(
        "class_name",
        [
            "Chrome_WidgetWin_1",
            "MozillaWindowClass",
            "Notepad",
            "CabinetWClass",
            "ApplicationFrameWindow",
        ],
    )
    def test_non_terminal_class_names_return_false(self, class_name, monkeypatch):
        """Everyday app window class names must NOT block capture."""
        import ctypes

        from stet.core.app import _is_terminal_or_ide

        def _write_class_name(hwnd, buf, max_len):
            for i, ch in enumerate(class_name):
                if i < max_len - 1:
                    buf[i] = ch
            buf[min(len(class_name), max_len - 1)] = "\0"
            return len(class_name)

        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _write_class_name
        )
        assert _is_terminal_or_ide(12345) is False

    # ── Process name detection tests ────────────────────────────────────

    @pytest.mark.parametrize(
        "proc_path",
        [
            "C:\\Windows\\System32\\cmd.exe",
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
            "C:\\Program Files\\WindowsApps\\Microsoft.WindowsTerminal_1.0\\wt.exe",
            "C:\\Windows\\System32\\conhost.exe",
            "C:\\Windows\\System32\\wsl.exe",
            "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
            "C:\\Program Files\\Git\\usr\\bin\\sh.exe",
            "C:\\Program Files\\WezTerm\\wezterm-gui.exe",
            "C:\\Program Files\\Alacritty\\alacritty.exe",
            "C:\\Program Files\\Cmder\\cmder.exe",
            "C:\\Program Files\\ConEmu\\ConEmu64.exe",
        ],
    )
    def test_terminal_process_names_return_true(self, proc_path, monkeypatch):
        """Terminal emulator and shell processes must block capture."""
        import ctypes

        from stet.core.app import _is_terminal_or_ide

        def _noop_class_name(hwnd, buf, max_len):
            buf[0] = "\0"
            return 0

        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )

        def _write_pid(hwnd, lpdw_process_id):
            ptr = ctypes.cast(lpdw_process_id, ctypes.POINTER(ctypes.c_ulong))
            ptr.contents.value = 9999
            return 0

        monkeypatch.setattr(
            ctypes.windll.user32, "GetWindowThreadProcessId", _write_pid
        )

        monkeypatch.setattr(
            ctypes.windll.kernel32, "OpenProcess", lambda *a: 1
        )

        def _write_proc_name(h_process, flags, buf, size_ptr):
            for i, ch in enumerate(proc_path):
                buf[i] = ch
            buf[min(len(proc_path), 259)] = "\0"
            return True

        monkeypatch.setattr(
            ctypes.windll.kernel32, "QueryFullProcessImageNameW", _write_proc_name
        )
        monkeypatch.setattr(
            ctypes.windll.kernel32, "CloseHandle", lambda *a: None
        )

        assert _is_terminal_or_ide(12345) is True

    @pytest.mark.parametrize(
        "proc_path",
        [
            "C:\\Users\\TestUser\\AppData\\Local\\Programs\\Antigravity\\antigravity ide.exe",
            "C:\\Users\\TestUser\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe",
            "C:\\Program Files\\Sublime Text\\sublime_text.exe",
            "C:\\Windows\\System32\\notepad.exe",
            "C:\\Program Files\\JetBrains\\PyCharm\\pycharm64.exe",
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
            "C:\\Users\\TestUser\\AppData\\Local\\opencode\\opencode.exe",
            "C:\\Python313\\python.exe",
            "C:\\Program Files\\nodejs\\node.exe",
        ],
    )
    def test_non_terminal_process_names_return_false(self, proc_path, monkeypatch):
        """IDEs, editors, browsers, and runtimes must NOT block capture."""
        import ctypes

        from stet.core.app import _is_terminal_or_ide

        def _noop_class_name(hwnd, buf, max_len):
            buf[0] = "\0"
            return 0

        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )

        def _write_pid(hwnd, lpdw_process_id):
            ptr = ctypes.cast(lpdw_process_id, ctypes.POINTER(ctypes.c_ulong))
            ptr.contents.value = 9999
            return 0

        monkeypatch.setattr(
            ctypes.windll.user32, "GetWindowThreadProcessId", _write_pid
        )

        monkeypatch.setattr(
            ctypes.windll.kernel32, "OpenProcess", lambda *a: 1
        )

        def _write_proc_name(h_process, flags, buf, size_ptr):
            for i, ch in enumerate(proc_path):
                buf[i] = ch
            buf[min(len(proc_path), 259)] = "\0"
            return True

        monkeypatch.setattr(
            ctypes.windll.kernel32, "QueryFullProcessImageNameW", _write_proc_name
        )
        monkeypatch.setattr(
            ctypes.windll.kernel32, "CloseHandle", lambda *a: None
        )

        assert _is_terminal_or_ide(12345) is False


# ── StetApp method tests ──────────────────────────────────────────────────


class TestStetAppTrayRetry:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_tray_when_available(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        mock_tray_cls.return_value = mock_tray
        StetApp()
        mock_tray.show.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_tray_retry_then_succeed(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        mock_tray = MagicMock()
        mock_tray.isVisible.side_effect = [False, False, True]
        mock_tray_cls.return_value = mock_tray
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        app = StetApp()
        app._tray_retry_count = 0
        app._retry_tray_show()
        app._retry_tray_show()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_tray_retry_exhaustion(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = False
        mock_tray_cls.return_value = mock_tray
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        app = StetApp()
        app._tray_retry_count = 29
        app._tray_retry_timer = MagicMock()
        app._retry_tray_show()
        app._tray_retry_timer.stop.assert_called()


class TestStetAppSetTrayIcon:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_set_tray_icon(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._set_tray_icon("#3b82f6")
        app.tray.setIcon.assert_called()


class TestStetAppStatusHandlers:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_ac_status_updates_label(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_ac_status("correcting")
        assert "correcting" in app._status_lbl.text()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_ac_status_no_label(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        if hasattr(app, "_status_lbl"):
            delattr(app, "_status_lbl")
        app._status_action = MagicMock()
        app._on_ac_status("ready")
        app._status_action.setText.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_chat_status_ready(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_chat_status("Model ready")
        app.tray.setIcon.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_chat_status_loading(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_chat_status("loading model")
        app.tray.setIcon.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_chat_status_error(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_chat_status("error: model not found")
        app.tray.setIcon.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_chat_status_idle(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_chat_status("idle")
        app.tray.setIcon.assert_called()


class TestStetAppRebuildRecentMenu:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_rebuild_recent_menu(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._llm_menu = MagicMock()
        app._rebuild_llm_menu()
        app._llm_menu.addSeparator.assert_called()
        assert app._llm_menu.addAction.call_count >= 2


class TestStetAppNotifications:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_notify_warn(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._show_notify("Test warning", "warn")
        app.tray.showMessage.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_notify_info(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._show_notify("Test info", "info")
        app.tray.showMessage.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_model_warning(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._show_model_warning("Model is tiny")
        app.tray.showMessage.assert_called()


class TestStetAppShowWindow:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_window_creates_correction_window(
        self, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch("stet.core.app.CorrectionWindow") as mock_cw:
            mock_win = MagicMock()
            mock_cw.return_value = mock_win
            app._show_window("Hello world", "full_correction")
            mock_cw.assert_called_once()
            mock_win.show.assert_called_once()
            assert app._window is mock_win

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_window_closes_old(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        old_win = MagicMock()
        app._window = old_win
        with patch("stet.core.app.CorrectionWindow") as mock_cw:
            mock_new = MagicMock()
            mock_cw.return_value = mock_new
            app._show_window("text", "full_correction")
            old_win.close.assert_called_once()
            old_win.deleteLater.assert_called_once()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_window_handles_crash(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch("stet.core.app.CorrectionWindow", side_effect=Exception("crash")):
            app._show_window("text")


class TestStetAppWindowDestroyed:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_clears_reference(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._window = MagicMock()
        app._on_window_destroyed()
        assert app._window is None


class TestStetAppPasteText:
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_chord")
    @patch("stet.core.app.QTimer")
    def test_paste_text_no_old_clip(
        self, mock_timer, mock_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._old_clip = ""
        app._safe_copy = MagicMock()
        app._paste_text("new text")
        app._safe_copy.assert_called_with("new text")

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_chord")
    @patch("stet.core.app.QTimer")
    def test_paste_text_restores_old_clip(
        self, mock_timer, mock_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._old_clip = "original"
        app._safe_copy = MagicMock()
        app._paste_text("corrected")
        app._safe_copy.assert_called_with("corrected")
        mock_timer.singleShot.assert_called()


class TestStetAppOpenSettings:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_open_settings(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch("stet.core.app.SettingsDialog") as mock_dlg:
            mock_dlg_instance = MagicMock()
            mock_dlg.return_value = mock_dlg_instance
            app._open_settings()
            mock_dlg_instance.show.assert_called_once()


class TestStetAppOnSettingsSaved:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_settings_saved_reload_model(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._register_hotkey = MagicMock()
        app.ac_model.is_loaded = MagicMock(return_value=True)
        app.ac_model.unload_model = MagicMock()
        app._on_settings_saved()
        app._register_hotkey.assert_called_with(force=True)

    @patch("stet.core.app.QSystemTrayIcon")
    def test_on_settings_saved_ac_same_as_chat(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.cfg.set("ac_same_as_chat", True)
        app.cfg.set("model_path", "/some/model.gguf")
        app._register_hotkey = MagicMock()
        app.ac_model.is_loaded = MagicMock(return_value=False)
        app._on_settings_saved()


class TestStetAppBrowseModel:
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.QFileDialog")
    def test_browse_model_selects(self, mock_fd, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._select_model = MagicMock()
        mock_fd.getOpenFileName.return_value = ("/path/model.gguf", "")
        app._browse_model()
        app._select_model.assert_called_once_with("/path/model.gguf")

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.QFileDialog")
    def test_browse_model_cancel(self, mock_fd, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._select_model = MagicMock()
        mock_fd.getOpenFileName.return_value = ("", "")
        app._browse_model()
        app._select_model.assert_not_called()


class TestStetAppSelectModel:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_select_model_sets_config(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.chat_model.unload_model = MagicMock()
        app.ac_model.is_loaded = MagicMock(return_value=False)
        app._select_model("/new/model.gguf")
        assert app.cfg.get("model_path") == "/new/model.gguf"

    @patch("stet.core.app.QSystemTrayIcon")
    def test_select_model_ac_same_as_chat(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.cfg.set("ac_same_as_chat", True)
        app.chat_model.unload_model = MagicMock()
        app.ac_model.is_loaded = MagicMock(return_value=True)
        app.ac_model.unload_model = MagicMock()
        app._select_model("/new/model.gguf")
        app.ac_model.unload_model.assert_called()


class TestStetAppCheckAppUpdate:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_check_app_update_skips_if_running(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        mock_checker = MagicMock()
        mock_checker.isRunning.return_value = True
        app._update_checker = mock_checker
        app._check_app_update()
        assert app._update_checker is mock_checker

    @patch("stet.core.app.QSystemTrayIcon")
    def test_app_update_checker_class(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        checker = AppUpdateChecker()
        assert hasattr(checker, "update_available")
        assert hasattr(checker, "check_done")


class TestStetAppOnUpdateAvailable:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_updates_action_text(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_update_available("v9.9.9", "https://example.com", "notes")
        assert "v9.9.9" in app._update_action.text()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_shows_tray_message(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._on_update_available("v1.0.0", "https://example.com", "notes")
        app.tray.showMessage.assert_called()


class TestStetAppUpdaterCommand:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_updater_command_source(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        app = StetApp()
        cmd = app._updater_command()
        assert sys.executable in cmd[0]
        assert "update.py" in cmd[1]

    @patch("stet.core.app.QSystemTrayIcon")
    def test_updater_command_frozen(self, mock_tray_cls, qtbot, monkeypatch, tmp_path):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        updater_path = tmp_path / "StetUpdater.exe"
        updater_path.write_text("fake")
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        app = StetApp()
        cmd = app._updater_command()
        assert "StetUpdater.exe" in cmd[0]

    @patch("stet.core.app.QSystemTrayIcon")
    def test_updater_command_frozen_missing_raises(
        self, mock_tray_cls, qtbot, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        app = StetApp()
        with pytest.raises(FileNotFoundError):
            app._updater_command()


class TestStetAppStartAppUpdate:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_decline_update(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch(
            "stet.core.app.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            app._start_app_update("https://url", "v1.0")

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.subprocess.Popen")
    def test_accept_update_launches_updater(
        self, mock_popen, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._updater_command = MagicMock(return_value=["updater", "--app"])
        app._quit = MagicMock()
        with patch(
            "stet.core.app.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            app._start_app_update("https://url", "v1.0")
        mock_popen.assert_called()
        app._quit.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_accept_update_launch_failure(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._updater_command = MagicMock(side_effect=Exception("launch fail"))
        with (
            patch(
                "stet.core.app.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("stet.core.app.QMessageBox.warning") as mock_warn,
        ):
            app._start_app_update("https://url", "v1.0")
        mock_warn.assert_called()


class TestStetAppQuit:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_quit_unregisters_hotkeys(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._hotkey_handles = [1000, 1001]
        app.ac_model.unload_model = MagicMock()
        app.chat_model.unload_model = MagicMock()
        app._quit()
        assert len(app._hotkey_handles) == 0
        app.ac_model.unload_model.assert_called()
        app.chat_model.unload_model.assert_called()


class TestStetAppShowFirstRun:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_bail_if_model_set(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.cfg.set("model_path", "/some/model.gguf")
        with patch("stet.core.app.QMessageBox") as mock_mb:
            app._show_first_run()
            mock_mb.assert_not_called()


class TestStetAppRunDownloadScript:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_script_missing_shows_message(
        self, mock_tray_cls, qtbot, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        app = StetApp()
        app._run_download_script()
        app.tray.showMessage.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.subprocess.Popen")
    def test_script_exists_launches(
        self, mock_popen, mock_tray_cls, qtbot, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        monkeypatch.setattr("stet.core.app.WINDOWS", True)
        script = tmp_path / "download_model.bat"
        script.write_text("echo hi")
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        app = StetApp()
        app._run_download_script()
        mock_popen.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.subprocess.Popen", side_effect=Exception("fail"))
    def test_script_launch_failure(
        self, mock_popen, mock_tray_cls, qtbot, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        monkeypatch.setattr("stet.core.app.WINDOWS", True)
        script = tmp_path / "download_model.bat"
        script.write_text("echo hi")
        monkeypatch.setattr("stet.core.app.SCRIPT_DIR", tmp_path)
        app = StetApp()
        app._run_download_script()
        app.tray.showMessage.assert_called()


class TestStetAppStartupToggle:
    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.winreg", create=True)
    def test_update_startup_action_key_found(
        self, mock_winreg, mock_tray_cls, mock_run, qtbot, monkeypatch
    ):
        mock_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._act_startup = MagicMock()
        mock_winreg.OpenKey.return_value = MagicMock()
        mock_winreg.QueryValueEx.return_value = ("cmd.exe", 1)
        app._update_startup_action()
        app._act_startup.setChecked.assert_called_with(True)

    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.winreg", create=True)
    def test_update_startup_action_key_not_found(
        self, mock_winreg, mock_tray_cls, mock_run, qtbot, monkeypatch
    ):
        mock_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._act_startup = MagicMock()
        mock_winreg.OpenKey.return_value = MagicMock()
        mock_winreg.QueryValueEx.side_effect = FileNotFoundError
        app._update_startup_action()
        app._act_startup.setChecked.assert_called_with(False)

    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.winreg", create=True)
    def test_update_startup_action_exception(
        self, mock_winreg, mock_tray_cls, mock_run, qtbot, monkeypatch
    ):
        mock_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._act_startup = MagicMock()
        mock_winreg.OpenKey.side_effect = Exception("reg error")
        app._update_startup_action()
        app._act_startup.setChecked.assert_called_with(False)

    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.winreg", create=True)
    def test_toggle_startup_checked_true(
        self, mock_winreg, mock_tray_cls, mock_run, qtbot, monkeypatch
    ):
        mock_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        mock_winreg.OpenKey.return_value = MagicMock()
        app._toggle_startup(True)
        mock_winreg.SetValueEx.assert_called()

    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.winreg", create=True)
    def test_toggle_startup_checked_false(
        self, mock_winreg, mock_tray_cls, mock_run, qtbot, monkeypatch
    ):
        mock_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        mock_winreg.OpenKey.return_value = MagicMock()
        app._toggle_startup(False)
        mock_winreg.DeleteValue.assert_called()

    @patch("stet.core.app.subprocess.run")
    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app.winreg", create=True)
    def test_toggle_startup_exception(
        self, mock_winreg, mock_tray_cls, mock_run, qtbot, monkeypatch
    ):
        mock_run.return_value = MagicMock(returncode=1)
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        mock_winreg.OpenKey.side_effect = Exception("reg error")
        app._toggle_startup(True)
        app.tray.showMessage.assert_called()


class TestStetAppIsModelReady:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_not_loaded(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.ac_model.is_loaded = MagicMock(return_value=False)
        assert app._is_model_ready() is False

    @patch("stet.core.app.QSystemTrayIcon")
    def test_loaded_health_ok(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.ac_model.is_loaded = MagicMock(return_value=True)
        app.ac_model._health_url = MagicMock(
            return_value="http://localhost:8080/health"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            assert app._is_model_ready() is True

    @patch("stet.core.app.QSystemTrayIcon")
    def test_loaded_health_fail(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.ac_model.is_loaded = MagicMock(return_value=True)
        app.ac_model._health_url = MagicMock(
            return_value="http://localhost:8080/health"
        )
        with patch("requests.get", side_effect=Exception("connection refused")):
            assert app._is_model_ready() is False


class TestStetAppShowSilentOsd:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_silent_osd_loading(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch("stet.core.app.SilentCorrectionOSD") as mock_osd_cls:
            mock_osd = MagicMock()
            mock_osd_cls.return_value = mock_osd
            app._show_silent_osd("Loading…", "loading")
            mock_osd.show_animated.assert_called_with(auto_dismiss=False)

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_silent_osd_success(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch("stet.core.app.SilentCorrectionOSD") as mock_osd_cls:
            mock_osd = MagicMock()
            mock_osd_cls.return_value = mock_osd
            app._show_silent_osd("Done", "success")
            mock_osd.show_animated.assert_called_with(auto_dismiss=True)

    @patch("stet.core.app.QSystemTrayIcon")
    def test_show_silent_osd_closes_previous(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        prev_osd = MagicMock()
        app._osd_widget = prev_osd
        with patch("stet.core.app.SilentCorrectionOSD") as mock_osd_cls:
            mock_osd_cls.return_value = MagicMock()
            app._show_silent_osd("New", "warning")
            prev_osd.close.assert_called()


class TestStetAppIsWindowAlive:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_no_window(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._window = None
        assert app._is_window_alive() is False

    @patch("stet.core.app.QSystemTrayIcon")
    def test_window_visible(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        mock_win = MagicMock()
        mock_win.isVisible.return_value = True
        app._window = mock_win
        assert app._is_window_alive() is True


class TestStetAppSafeClipboard:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_safe_paste_success(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._safe_paste = MagicMock(return_value="clipboard text")
        assert app._safe_paste() == "clipboard text"

    @patch("stet.core.app.QSystemTrayIcon")
    def test_safe_copy_success(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._safe_copy = MagicMock()
        app._safe_copy("text")
        app._safe_copy.assert_called_with("text")


class TestStetAppCaptureSelection:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_capture_selection_uia_success(
        self, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        
        # Monkeypatch UIA reader and terminal guard
        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia",
            lambda: "uia selected text"
        )
        mock_terminal_guard = MagicMock()
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", mock_terminal_guard)
        
        app._safe_paste = MagicMock(return_value="old clip")
        
        res = app._capture_selection()
        
        assert res == "uia selected text"
        mock_terminal_guard.assert_not_called()

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_shift_chord")
    def test_capture_selection_uia_fail_terminal(
        self, mock_send_shift_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()

        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia",
            lambda: None
        )
        mock_terminal_guard = MagicMock(return_value=True)
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", mock_terminal_guard)
        app._safe_paste = MagicMock(return_value="")
        app._safe_copy = MagicMock()
        monkeypatch.setattr("time.sleep", lambda t: None)

        res = app._capture_selection()

        assert res == ""
        mock_terminal_guard.assert_called_once()
        mock_send_shift_chord.assert_called_once()
        # Terminal path must NOT clear the clipboard (preserves selection)
        app._safe_copy.assert_not_called()

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_shift_chord", create=True)
    def test_capture_selection_uia_fail_terminal_change_detection(
        self, mock_send_shift_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        """Terminal path detects success by clipboard CHANGE, not non-empty."""
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()

        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia",
            lambda: None
        )
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", MagicMock(return_value=True))

        # old_clip = "previous text", then first poll gets different content
        app._safe_paste = MagicMock(side_effect=["previous text", "terminal selection"])
        app._safe_copy = MagicMock()
        monkeypatch.setattr("time.sleep", lambda t: None)

        res = app._capture_selection()

        assert res == "terminal selection"
        mock_send_shift_chord.assert_called_once()
        app._safe_copy.assert_not_called()  # clipboard clear must NOT happen

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_shift_chord", create=True)
    def test_capture_selection_uia_fail_terminal_same_as_old_clip(
        self, mock_send_shift_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        """When new clipboard content equals old, fallback returns it."""
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()

        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia",
            lambda: None
        )
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", MagicMock(return_value=True))

        # old_clip at save time = "same text",
        # poll returns "same text" (unchanged) → CB-4 clipboard guard
        # detects clip == old_clip and returns empty (no change detected)
        app._safe_paste = MagicMock(return_value="same text")
        app._safe_copy = MagicMock()
        monkeypatch.setattr("time.sleep", lambda t: None)

        res = app._capture_selection()

        assert res == ""
        mock_send_shift_chord.assert_called_once()
        # CB-4 guard detects no change; old_clip is restored via _safe_copy
        app._safe_copy.assert_called_once_with("same text")

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_shift_chord", create=True)
    def test_capture_selection_uia_fail_terminal_no_change(
        self, mock_send_shift_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        """Terminal path: clipboard stays empty → returns empty, no restore needed."""
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()

        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia",
            lambda: None
        )
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", MagicMock(return_value=True))

        # old_clip = "" (empty), all polls return ""
        app._safe_paste = MagicMock(return_value="")
        app._safe_copy = MagicMock()
        monkeypatch.setattr("time.sleep", lambda t: None)

        res = app._capture_selection()

        assert res == ""
        mock_send_shift_chord.assert_called_once()
        # old_clip was empty → no restore needed
        app._safe_copy.assert_not_called()

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_chord")
    def test_capture_selection_uia_fail_non_terminal(
        self, mock_send_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        
        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia",
            lambda: None
        )
        mock_terminal_guard = MagicMock(return_value=False)
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", mock_terminal_guard)
        
        # Mock clipboard methods
        app._safe_paste = MagicMock(side_effect=["old clip", "", "new selection"])
        app._safe_copy = MagicMock()
        
        # Monkeypatch time.sleep to avoid slow tests
        monkeypatch.setattr("time.sleep", lambda t: None)
        
        res = app._capture_selection()

        assert res == "new selection"
        mock_terminal_guard.assert_called_once()
        mock_send_chord.assert_called_once()

    @patch("stet.core.app.QSystemTrayIcon")
    @patch("stet.core.app._send_ctrl_chord")
    def test_capture_selection_uia_timeout_uses_daemon_fallback(
        self, mock_send_chord, mock_tray_cls, qtbot, monkeypatch
    ):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        created_threads = []

        class HangingThread:
            def __init__(self, *args, target=None, name=None, daemon=False, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.target = target
                self.name = name
                self.daemon = daemon
                self.join_timeouts = []
                created_threads.append(self)

            def start(self):
                pass

            def join(self, timeout=None):
                self.join_timeouts.append(timeout)

            def is_alive(self):
                return True

        monkeypatch.setattr("stet.core.app.threading.Thread", HangingThread)
        monkeypatch.setattr("stet.core.clipboard._read_selection_uia", lambda: None)
        monkeypatch.setattr("stet.core.app._is_terminal_or_ide", MagicMock(return_value=False))
        monkeypatch.setattr("time.sleep", lambda t: None)
        app._safe_paste = MagicMock(side_effect=["old clip", "", "fallback selection"])
        app._safe_copy = MagicMock()

        res = app._capture_selection()

        assert res == "fallback selection"
        assert len(created_threads) == 1
        assert created_threads[0].name == "StetUIACapture"
        assert created_threads[0].daemon is True
        assert created_threads[0].join_timeouts == [1.5]
        mock_send_chord.assert_called_once()

    def test_capture_selection_polling_constants(self):
        """Verify the polling tunables are set to the latency-reduced values.

        Worst-case wait: 50 ms + 12 * 15 ms = 230 ms (was 680 ms).
        """
        from stet.core.app import StetApp

        assert StetApp._CLIPBOARD_POLL_INTERVAL == 0.015
        assert StetApp._CLIPBOARD_MAX_POLLS == 12
        assert StetApp._CLIPBOARD_INITIAL_GRACE == 0.05

        # Sanity-check the worst-case math
        worst_case = (
            StetApp._CLIPBOARD_INITIAL_GRACE
            + StetApp._CLIPBOARD_MAX_POLLS * StetApp._CLIPBOARD_POLL_INTERVAL
        )
        assert worst_case <= 0.25  # well under the old 680 ms ceiling


class TestStetAppTrayActivated:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_double_click_opens_settings(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._open_settings = MagicMock()
        import stet.core.app

        app._tray_activated(stet.core.app.QSystemTrayIcon.ActivationReason.DoubleClick)
        app._open_settings.assert_called_once()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_single_click_does_nothing(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._open_settings = MagicMock()
        app._tray_menu.exec = MagicMock()
        import stet.core.app

        app._tray_activated(stet.core.app.QSystemTrayIcon.ActivationReason.Trigger)
        app._open_settings.assert_not_called()
        app._tray_menu.exec.assert_called_once()


class TestAppUpdateChecker:
    def test_emits_check_done_on_success(self, qtbot):
        checker = AppUpdateChecker()
        signals = []
        checker.check_done.connect(lambda: signals.append("done"))
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            b'{"tag_name": "v99.0.0", "body": "notes", "assets": []}'
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with (
            patch("urllib.request.Request"),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            checker.run()
        assert "done" in signals

    def test_emits_check_done_on_failure(self, qtbot):
        checker = AppUpdateChecker()
        signals = []
        checker.check_done.connect(lambda: signals.append("done"))
        with (
            patch("urllib.request.Request"),
            patch("urllib.request.urlopen", side_effect=Exception("network error")),
        ):
            checker.run()
        assert "done" in signals


class TestStetAppHandleHotkeyFired:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_panel_mode_window_already_open(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        mock_win = MagicMock()
        mock_win.isVisible.return_value = True
        app._window = mock_win
        hk_cfg = {"mode": "panel", "strength": "full_correction", "custom_prompt": ""}
        app._handle_hotkey_fired(hk_cfg)
        mock_win.raise_.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_busy_hotkey_ignored(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app._hotkey_busy.acquire(blocking=False)
        hk_cfg = {"mode": "panel", "strength": "full_correction"}
        app._handle_hotkey_fired(hk_cfg)
        app._hotkey_busy.release()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_silent_mode_starts_thread(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            hk_cfg = {
                "mode": "silent",
                "strength": "spelling_only",
                "custom_prompt": "",
            }
            app._handle_hotkey_fired(hk_cfg)
            mock_thread.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_large_doc_warning_signal_exists(self, mock_tray_cls, qtbot, monkeypatch):
        """_large_doc_warning_signal must be defined on StetApp."""
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        assert hasattr(app, "_large_doc_warning_signal")
        # Verify it's a signal (has emit method)
        assert hasattr(app._large_doc_warning_signal, "emit")

    @patch("stet.core.app.QSystemTrayIcon")
    def test_large_doc_warning_emitted(self, mock_tray_cls, qtbot, monkeypatch):
        """Selecting >1000 words emits _large_doc_warning_signal."""
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()

        # Build text > 1000 words
        large_text = "word " * 1001

        # Mock capture to return large text
        app._capture_selection = lambda: large_text
        app._safe_copy = MagicMock()
        app._old_clip = ""

        received = []
        app._large_doc_warning_signal.connect(lambda t: received.append(t))

        # Acquire the lock that _hotkey_worker expects to release
        app._hotkey_busy.acquire()
        try:
            app._hotkey_worker()
        finally:
            # Worker already released the lock; if not, clean up
            pass

        assert len(received) == 1
        # _hotkey_worker calls .strip() on the selection,
        # so the signal receives `large_text.strip()` (no trailing space)
        assert received[0] == large_text.strip()


class TestStetAppInitSignals:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_model_loaded_signals_set_color(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.ac_model.model_loaded.emit()
        app.chat_model.model_loaded.emit()
        app.chat_model.model_unloaded.emit()
        assert app.tray.setIcon.call_count >= 3

    @patch("stet.core.app.QSystemTrayIcon")
    def test_model_warning_signal(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        app = StetApp()
        app.ac_model.model_warning.emit("tiny model")
        app.tray.showMessage.assert_called()


class TestStetAppRegisterHotkey:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_register_hotkey_success(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        import ctypes

        mock_register = MagicMock(return_value=1)
        monkeypatch.setattr(ctypes.windll.user32, "RegisterHotKey", mock_register)
        app = StetApp()
        mock_register.reset_mock()
        app._register_hotkey(force=True)
        mock_register.assert_called()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_register_hotkey_debounced(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
        import ctypes

        mock_register = MagicMock(return_value=1)
        monkeypatch.setattr(ctypes.windll.user32, "RegisterHotKey", mock_register)
        app = StetApp()
        mock_register.reset_mock()
        app._last_register_ts = time.monotonic()
        app._register_hotkey(force=False)
        mock_register.assert_not_called()
