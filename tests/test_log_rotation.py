"""Tests for log rotation and tray diagnostic actions."""

from unittest.mock import MagicMock, patch


from stet.core import utils as log_utils
from stet.core.app import StetApp


class TestLogRotation:
    def test_log_creates_file_if_missing(self, tmp_path):
        """log() creates the log file when it doesn't exist."""
        log_path = tmp_path / "test.log"
        with patch.object(log_utils, "DEBUG_LOG", log_path):
            log_utils.log("test message")
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "test message" in content

    def test_log_rotates_when_over_limit(self, tmp_path):
        """log() rotates to .1 when file exceeds 2 MB."""
        log_path = tmp_path / "test.log"
        backup_path = tmp_path / "test.log.1"

        with patch.object(log_utils, "DEBUG_LOG", log_path):
            big_msg = "X" * 5000
            for _ in range(450):
                log_utils.log(big_msg)

        assert log_path.exists()
        assert backup_path.exists()

        live_size = log_path.stat().st_size
        backup_size = backup_path.stat().st_size
        assert live_size < 2 * 1024 * 1024, f"live log too large: {live_size}"
        assert backup_size > 2 * 1024 * 1024, f"backup too small: {backup_size}"

    def test_log_rotation_handles_missing_file(self, tmp_path):
        """log() handles a race where the file disappears between checks."""
        log_path = tmp_path / "nonexistent.log"
        with patch.object(log_utils, "DEBUG_LOG", log_path):
            log_utils.log("hello")
        assert log_path.exists()
        assert "hello" in log_path.read_text(encoding="utf-8")

    def test_log_rotation_replaces_existing_backup(self, tmp_path):
        """When a .1 backup already exists, it's replaced on rotation."""
        log_path = tmp_path / "test.log"
        backup_path = tmp_path / "test.log.1"
        backup_path.write_text("old backup", encoding="utf-8")

        with patch.object(log_utils, "DEBUG_LOG", log_path):
            big_msg = "X" * 5000
            for _ in range(450):
                log_utils.log(big_msg)

        assert backup_path.exists()
        assert "old backup" not in backup_path.read_text(encoding="utf-8")


class TestTrayDiagnosticActions:
    @patch("stet.core.app.QSystemTrayIcon")
    def test_tray_has_open_log_folder_action(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr("stet.core.app.ModelManager.load_model", lambda *a, **k: None)
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        mock_tray_cls.return_value = mock_tray
        app = StetApp()
        action_texts = []
        for a in app._tray_menu.actions():
            action_texts.append(a.text())
        assert "Open Log Folder" in action_texts

    @patch("stet.core.app.QSystemTrayIcon")
    def test_tray_has_copy_debug_info_action(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr("stet.core.app.ModelManager.load_model", lambda *a, **k: None)
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        mock_tray_cls.return_value = mock_tray
        app = StetApp()
        action_texts = []
        for a in app._tray_menu.actions():
            action_texts.append(a.text())
        assert "Copy Debug Info" in action_texts

    @patch("stet.core.app.QSystemTrayIcon")
    def test_open_log_folder_opens_url(self, mock_tray_cls, qtbot, monkeypatch):
        monkeypatch.setattr("stet.core.app.ModelManager.load_model", lambda *a, **k: None)
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        mock_tray_cls.return_value = mock_tray
        app = StetApp()
        with patch("PyQt6.QtGui.QDesktopServices.openUrl") as mock_open:
            app._open_log_folder()
            mock_open.assert_called_once()

    @patch("stet.core.app.QSystemTrayIcon")
    def test_copy_debug_info_does_not_raise(self, mock_tray_cls, qtbot, monkeypatch, tmp_path):
        monkeypatch.setattr("stet.core.app.ModelManager.load_model", lambda *a, **k: None)
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        mock_tray_cls.isSystemTrayAvailable.return_value = True
        mock_tray_cls.return_value = mock_tray
        log_file = tmp_path / "app_debug.log"
        log_file.write_text("line 1\nline 2\n", encoding="utf-8")
        with patch.object(log_utils, "DEBUG_LOG", log_file):
            app = StetApp()
            app._copy_debug_info()
        # Verify no exception raised
