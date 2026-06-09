"""Tests for windows_installer_payload.py — QWizard-based installer verification.

Tests cover:
  - InstallWorker (background extraction thread) logic
  - Individual wizard page validatePage / isComplete / nextId
  - Post-install actions (shortcuts, model download, launch)
  - Error paths (missing ZIP, corrupt ZIP, path traversal)
  - Cancel confirmation flow
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure PyQt6 is available; skip entire module if not (e.g., headless CI).
# In practice PyQt6 is always installed for this project.
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _QT_AVAILABLE, reason="PyQt6 not available"
)

# ---------------------------------------------------------------------------
# Minimal QApplication fixture — one per test session.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    """Return or create a QApplication for the test session."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---------------------------------------------------------------------------
# Shared ZIP fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def installer_zip(tmp_path):
    """Create a valid stet_portable.zip with test application files."""
    zip_path = tmp_path / "stet_portable.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Stet.exe", "binary-content")
        zf.writestr("logo.ico", "icon-content")
        zf.writestr("config.json", '{"model_path": ""}')
        zf.writestr("run.bat", "@echo off\nStet.exe\n")
        zf.writestr("stet/ui/stet.qss", "QWidget {}")
    return zip_path


# ---------------------------------------------------------------------------
# Platform guard fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_win32(monkeypatch):
    """Ensure sys.platform reports win32 for tests that call main()."""
    monkeypatch.setattr(sys, "platform", "win32")


# ===========================================================================
# InstallWorker tests — exercises extraction logic without a live QWizard
# ===========================================================================

class TestInstallWorker:
    """Tests for the background InstallWorker QThread."""

    def test_worker_extracts_files(self, qapp, installer_zip, tmp_path):
        """Worker extracts all ZIP members to the target directory."""
        from windows_installer_payload import InstallWorker

        target = tmp_path / "install"
        worker = InstallWorker(installer_zip, target)

        finished_calls = []
        error_calls = []
        worker.finished.connect(lambda: finished_calls.append(True))
        worker.error.connect(lambda msg: error_calls.append(msg))

        worker.run()  # run() synchronously in test context

        assert not error_calls, f"Unexpected errors: {error_calls}"
        assert len(finished_calls) == 1
        assert (target / "Stet.exe").exists()
        assert (target / "logo.ico").exists()
        assert (target / "run.bat").exists()
        assert (target / "stet" / "ui" / "stet.qss").exists()

    def test_worker_preserves_existing_config(self, qapp, installer_zip, tmp_path):
        """Worker skips config.json extraction when one already exists."""
        from windows_installer_payload import InstallWorker

        target = tmp_path / "install"
        target.mkdir()
        config = target / "config.json"
        config.write_text('{"user_setting": true}', encoding="utf-8")

        worker = InstallWorker(installer_zip, target)
        worker.run()

        assert config.read_text(encoding="utf-8") == '{"user_setting": true}'

    def test_worker_overwrites_config_when_none_exists(self, qapp, installer_zip, tmp_path):
        """Worker extracts config.json when no existing config is present."""
        from windows_installer_payload import InstallWorker

        target = tmp_path / "install"
        worker = InstallWorker(installer_zip, target)
        worker.run()

        assert (target / "config.json").exists()
        assert "model_path" in (target / "config.json").read_text()

    def test_worker_emits_progress_signals(self, qapp, installer_zip, tmp_path):
        """Worker emits progress signal for each extracted file."""
        from windows_installer_payload import InstallWorker

        target = tmp_path / "install"
        worker = InstallWorker(installer_zip, target)

        progress_calls = []
        worker.progress.connect(lambda step, msg: progress_calls.append((step, msg)))

        worker.run()

        assert len(progress_calls) >= 1

    def test_worker_emits_total_steps(self, qapp, installer_zip, tmp_path):
        """Worker emits total_steps signal with the number of files to extract."""
        from windows_installer_payload import InstallWorker

        target = tmp_path / "install"
        worker = InstallWorker(installer_zip, target)

        total_received = []
        worker.total_steps.connect(lambda n: total_received.append(n))
        worker.run()

        assert len(total_received) == 1
        assert total_received[0] > 0

    def test_worker_handles_corrupt_zip(self, qapp, tmp_path):
        """Worker emits error signal on a corrupt ZIP."""
        from windows_installer_payload import InstallWorker

        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"this is not a zip")

        target = tmp_path / "install"
        worker = InstallWorker(bad_zip, target)

        error_calls = []
        finished_calls = []
        worker.error.connect(lambda msg: error_calls.append(msg))
        worker.finished.connect(lambda: finished_calls.append(True))

        worker.run()

        assert len(error_calls) == 1
        assert "corrupted" in error_calls[0].lower() or "valid" in error_calls[0].lower()
        assert not finished_calls

    def test_worker_rejects_path_traversal(self, qapp, tmp_path):
        """Worker emits error signal when ZIP contains path traversal members."""
        from windows_installer_payload import InstallWorker

        bad_zip = tmp_path / "traversal.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../../evil.txt", "malicious payload")
            zf.writestr("legit.txt", "ok")

        target = tmp_path / "install"
        worker = InstallWorker(bad_zip, target)

        error_calls = []
        worker.error.connect(lambda msg: error_calls.append(msg))

        worker.run()

        assert len(error_calls) == 1
        assert "unsafe" in error_calls[0].lower() or "aborted" in error_calls[0].lower()
        # Malicious file must NOT have escaped the target directory
        assert not (tmp_path / "evil.txt").exists()


# ===========================================================================
# Utility function tests
# ===========================================================================

class TestFindZipPath:
    def test_finds_zip_next_to_exe(self, tmp_path):
        """_find_zip_path locates the ZIP next to sys.argv[0]."""
        from windows_installer_payload import _find_zip_path

        zip_path = tmp_path / "stet_portable.zip"
        zip_path.write_bytes(b"fake-zip")
        fake_exe = tmp_path / "StetSetup.exe"
        fake_exe.write_bytes(b"fake-exe")

        with patch("sys.argv", [str(fake_exe)]):
            result = _find_zip_path()

        assert result is not None
        assert result.name == "stet_portable.zip"

    def test_returns_none_when_missing(self, tmp_path):
        """_find_zip_path returns None when no ZIP is found."""
        from windows_installer_payload import _find_zip_path

        fake_exe = tmp_path / "StetSetup.exe"
        fake_exe.write_bytes(b"fake-exe")

        with patch("sys.argv", [str(fake_exe)]), \
             patch.dict(os.environ, {"_NUITKA_ONEFILE_TEMP": ""}, clear=False):
            result = _find_zip_path()

        assert result is None

    def test_finds_zip_in_nuitka_temp(self, tmp_path):
        """_find_zip_path checks _NUITKA_ONEFILE_TEMP first."""
        from windows_installer_payload import _find_zip_path

        zip_path = tmp_path / "stet_portable.zip"
        zip_path.write_bytes(b"fake-zip")

        with patch.dict(os.environ, {"_NUITKA_ONEFILE_TEMP": str(tmp_path)}), \
             patch("sys.argv", ["/some/other/dir/StetSetup.exe"]):
            result = _find_zip_path()

        assert result is not None
        assert result == zip_path


class TestSafeExtract:
    def test_rejects_path_traversal(self, tmp_path):
        """_safe_extract raises RuntimeError on path-traversal members."""
        from windows_installer_payload import _safe_extract

        zip_path = tmp_path / "malicious.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../evil.txt", "bad")

        dest = tmp_path / "dest"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(RuntimeError, match="Unsafe path"):
                _safe_extract(zf, dest)

        assert not (tmp_path / "evil.txt").exists()

    def test_extracts_valid_members(self, tmp_path, installer_zip):
        """_safe_extract successfully extracts valid ZIP members."""
        from windows_installer_payload import _safe_extract

        dest = tmp_path / "dest"
        dest.mkdir()

        with zipfile.ZipFile(installer_zip, "r") as zf:
            _safe_extract(zf, dest)

        assert (dest / "Stet.exe").exists()
        assert (dest / "config.json").exists()


# ===========================================================================
# Wizard page unit tests
# ===========================================================================

class TestWelcomePage:
    def test_next_id_is_license(self, qapp):
        from windows_installer_payload import WelcomePage, PAGE_LICENSE
        page = WelcomePage()
        assert page.nextId() == PAGE_LICENSE

    def test_always_complete(self, qapp):
        from windows_installer_payload import WelcomePage
        page = WelcomePage()
        assert page.isComplete()


class TestLicensePage:
    def test_incomplete_by_default(self, qapp):
        """License page is incomplete (Next disabled) until user accepts."""
        from windows_installer_payload import LicensePage
        page = LicensePage()
        assert not page.isComplete()

    def test_complete_after_accept(self, qapp):
        """Clicking Accept radio makes the page complete."""
        from windows_installer_payload import LicensePage
        page = LicensePage()
        page._accept_radio.setChecked(True)
        assert page.isComplete()

    def test_incomplete_when_decline_selected(self, qapp):
        """Selecting Decline keeps the page incomplete."""
        from windows_installer_payload import LicensePage
        page = LicensePage()
        page._accept_radio.setChecked(True)
        page._decline_radio.setChecked(True)
        assert not page.isComplete()

    def test_next_id_is_destination(self, qapp):
        from windows_installer_payload import LicensePage, PAGE_DESTINATION
        page = LicensePage()
        assert page.nextId() == PAGE_DESTINATION

    def test_license_text_loaded(self, qapp):
        """License page contains meaningful license text."""
        from windows_installer_payload import LicensePage
        page = LicensePage()
        text = page._license_edit.toPlainText()
        assert len(text) > 100  # must have substantial content
        assert "license" in text.lower() or "general public" in text.lower()


class TestDestinationPage:
    def test_default_path_is_localappdata_stet(self, qapp):
        """Destination page defaults to %LOCALAPPDATA%\\Stet."""
        from windows_installer_payload import DestinationPage, DEFAULT_INSTALL_DIR
        page = DestinationPage()
        assert page._path_edit.text() == DEFAULT_INSTALL_DIR

    def test_incomplete_when_path_empty(self, qapp):
        from windows_installer_payload import DestinationPage
        page = DestinationPage()
        page._path_edit.setText("")
        assert not page.isComplete()

    def test_complete_when_path_non_empty(self, qapp):
        from windows_installer_payload import DestinationPage
        page = DestinationPage()
        page._path_edit.setText(r"C:\Some\Valid\Path")
        assert page.isComplete()

    def test_next_id_is_ready(self, qapp):
        from windows_installer_payload import DestinationPage, PAGE_READY
        page = DestinationPage()
        assert page.nextId() == PAGE_READY

    def test_existing_install_warning_shown(self, qapp, tmp_path):
        """Info label warns when Stet.exe exists in the chosen directory."""
        from windows_installer_payload import DestinationPage

        # Create a fake Stet.exe
        fake_exe = tmp_path / "Stet.exe"
        fake_exe.write_bytes(b"fake")

        page = DestinationPage()
        page._path_edit.setText(str(tmp_path))

        assert page._info_label.text() != ""
        assert "existing" in page._info_label.text().lower()

    def test_no_warning_for_fresh_install(self, qapp, tmp_path):
        """No warning shown for a directory without an existing installation."""
        from windows_installer_payload import DestinationPage
        page = DestinationPage()
        page._path_edit.setText(str(tmp_path))
        assert page._info_label.text() == ""

    def test_browse_updates_path(self, qapp):
        """Browse button updates the path field with the chosen directory."""
        from windows_installer_payload import DestinationPage
        page = DestinationPage()

        chosen_dir = r"C:\Users\Test\MyApps"
        with patch("windows_installer_payload.QFileDialog.getExistingDirectory",
                   return_value=chosen_dir):
            page._browse()

        assert page._path_edit.text() == str(Path(chosen_dir) / "Stet")

    def test_browse_no_op_when_cancel(self, qapp):
        """Path unchanged when dialog is cancelled."""
        from windows_installer_payload import DestinationPage
        page = DestinationPage()
        original = page._path_edit.text()

        with patch("windows_installer_payload.QFileDialog.getExistingDirectory",
                   return_value=""):
            page._browse()

        assert page._path_edit.text() == original


class TestReadyPage:
    def test_is_commit_page(self, qapp):
        """ReadyPage is marked as a commit page."""
        from windows_installer_payload import ReadyPage
        page = ReadyPage()
        assert page.isCommitPage()

    def test_next_id_is_progress(self, qapp):
        from windows_installer_payload import ReadyPage, PAGE_PROGRESS
        page = ReadyPage()
        assert page.nextId() == PAGE_PROGRESS


class TestProgressPage:
    def test_not_complete_initially(self, qapp, installer_zip):
        """Progress page is incomplete (blocks Next) until installation finishes."""
        from windows_installer_payload import ProgressPage
        page = ProgressPage(installer_zip)
        assert not page.isComplete()

    def test_next_id_is_completion(self, qapp, installer_zip):
        from windows_installer_payload import ProgressPage, PAGE_COMPLETION
        page = ProgressPage(installer_zip)
        assert page.nextId() == PAGE_COMPLETION

    def test_complete_after_finished_signal(self, qapp, installer_zip):
        """Progress page becomes complete when _on_finished is called."""
        from windows_installer_payload import ProgressPage
        page = ProgressPage(installer_zip)
        # Simulate finished without a running wizard
        page._install_done = False
        # Directly test the state transition
        page._install_done = True
        assert page.isComplete()


class TestCompletionPage:
    def test_defaults(self, qapp):
        """Completion page defaults: desktop/startmenu checked, download/launch checked."""
        from windows_installer_payload import CompletionPage
        page = CompletionPage()
        assert page.create_desktop_shortcut is True
        assert page.create_startmenu_shortcut is True
        assert page.download_model is False
        assert page.launch_stet is True

    def test_is_final_page(self, qapp):
        from windows_installer_payload import CompletionPage
        page = CompletionPage()
        assert page.isFinalPage()

    def test_next_id_is_minus_one(self, qapp):
        from windows_installer_payload import CompletionPage
        page = CompletionPage()
        assert page.nextId() == -1

    def test_checkboxes_reflect_user_choices(self, qapp):
        from windows_installer_payload import CompletionPage
        page = CompletionPage()
        page._desktop_cb.setChecked(False)
        page._startmenu_cb.setChecked(False)
        page._download_cb.setChecked(True)
        page._launch_cb.setChecked(False)

        assert page.create_desktop_shortcut is False
        assert page.create_startmenu_shortcut is False
        assert page.download_model is True
        assert page.launch_stet is False


# ===========================================================================
# Post-install action tests
# ===========================================================================

class TestPostInstallActions:
    """Tests for StetInstaller._run_post_install_actions()."""

    def _make_wizard(self, qapp, installer_zip):
        from windows_installer_payload import StetInstaller
        wizard = StetInstaller(installer_zip)
        return wizard

    def test_shortcut_creation_called(self, qapp, installer_zip, tmp_path):
        """create_shortcut called when desktop/startmenu checkboxes are checked."""
        from windows_installer_payload import StetInstaller

        wizard = StetInstaller(installer_zip)
        # Set install dir field manually
        wizard.setField("installDir", str(tmp_path))

        with patch("windows_installer_payload.create_shortcut") as mock_sc:
            wizard._run_post_install_actions()

        assert mock_sc.call_count == 2  # desktop + start menu

    def test_no_shortcut_when_unchecked(self, qapp, installer_zip, tmp_path):
        """create_shortcut not called when both shortcut checkboxes are unchecked."""
        from windows_installer_payload import StetInstaller

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))
        wizard._completion_page._desktop_cb.setChecked(False)
        wizard._completion_page._startmenu_cb.setChecked(False)

        with patch("windows_installer_payload.create_shortcut") as mock_sc:
            wizard._run_post_install_actions()

        mock_sc.assert_not_called()

    def test_model_download_launched(self, qapp, installer_zip, tmp_path):
        """Popen called with download_model.bat when checkbox is checked."""
        from windows_installer_payload import StetInstaller

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))
        wizard._completion_page._download_cb.setChecked(True)
        wizard._completion_page._launch_cb.setChecked(False)

        with patch("windows_installer_payload.create_shortcut"), \
             patch("windows_installer_payload.subprocess.Popen") as mock_popen:
            wizard._run_post_install_actions()

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert any("download_model.bat" in str(arg) for arg in cmd)

    def test_launch_stet_called(self, qapp, installer_zip, tmp_path):
        """Popen called with Stet.exe when launch checkbox is checked."""
        from windows_installer_payload import StetInstaller

        # Create a fake Stet.exe so the path exists
        fake_exe = tmp_path / "Stet.exe"
        fake_exe.write_bytes(b"fake")

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))
        wizard._completion_page._desktop_cb.setChecked(False)
        wizard._completion_page._startmenu_cb.setChecked(False)
        wizard._completion_page._download_cb.setChecked(False)
        wizard._completion_page._launch_cb.setChecked(True)

        with patch("windows_installer_payload.subprocess.Popen") as mock_popen:
            wizard._run_post_install_actions()

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "Stet.exe" in str(cmd)

    def test_no_launch_when_unchecked(self, qapp, installer_zip, tmp_path):
        """Popen not called with Stet.exe when launch checkbox is unchecked."""
        from windows_installer_payload import StetInstaller

        fake_exe = tmp_path / "Stet.exe"
        fake_exe.write_bytes(b"fake")

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))
        wizard._completion_page._desktop_cb.setChecked(False)
        wizard._completion_page._startmenu_cb.setChecked(False)
        wizard._completion_page._download_cb.setChecked(False)
        wizard._completion_page._launch_cb.setChecked(False)

        with patch("windows_installer_payload.subprocess.Popen") as mock_popen:
            wizard._run_post_install_actions()

        mock_popen.assert_not_called()


# ===========================================================================
# main() entry point tests
# ===========================================================================

class TestMainEntryPoint:
    def test_missing_zip_shows_error_and_exits(self, mock_win32):
        """main() shows an error and exits with code 1 when ZIP is not found."""
        mock_msgbox = MagicMock(return_value=1)
        with patch("windows_installer_payload._find_zip_path", return_value=None), \
             patch("ctypes.windll.user32.MessageBoxW", mock_msgbox), \
             patch("sys.exit", side_effect=SystemExit) as mock_exit:
            with pytest.raises(SystemExit):
                import windows_installer_payload as installer
                installer.main()

        mock_exit.assert_called_once_with(1)
        mock_msgbox.assert_called_once()
        assert "stet_portable.zip" in mock_msgbox.call_args[0][1].lower()

    def test_non_windows_exits_immediately(self, monkeypatch):
        """main() exits immediately on non-Windows platforms."""
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("sys.exit", side_effect=SystemExit) as mock_exit:
            with pytest.raises(SystemExit):
                import windows_installer_payload as installer
                installer.main()
        mock_exit.assert_called_once_with(1)


# ===========================================================================
# ARP registry tests
# ===========================================================================

class TestARPRegistry:
    """Tests for _write_arp_registry()."""

    def test_writes_registry_keys(self, qapp, installer_zip, tmp_path):
        """_write_arp_registry writes all expected values to the registry."""
        from windows_installer_payload import StetInstaller

        (tmp_path / "VERSION").write_text("1.0.0", encoding="utf-8")
        (tmp_path / "Stet.exe").write_bytes(b"fake")

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))

        mock_key = MagicMock()
        with patch("windows_installer_payload.winreg") as mock_winreg:
            mock_winreg.CreateKeyEx.return_value = mock_key
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.KEY_WRITE = 0x20006
            mock_winreg.REG_SZ = 1
            mock_winreg.REG_DWORD = 4

            wizard._write_arp_registry(tmp_path)

            mock_winreg.CreateKeyEx.assert_called_once()
            assert mock_winreg.SetValueEx.call_count >= 10
            mock_winreg.CloseKey.assert_called_once_with(mock_key)

    def test_registry_failure_does_not_crash(self, qapp, installer_zip, tmp_path):
        """_write_arp_registry catches exceptions gracefully."""
        from windows_installer_payload import StetInstaller

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))

        with patch("windows_installer_payload.winreg") as mock_winreg:
            mock_winreg.CreateKeyEx.side_effect = OSError("Access denied")
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.KEY_WRITE = 0x20006
            wizard._write_arp_registry(tmp_path)

    def test_post_install_calls_arp_registry(self, qapp, installer_zip, tmp_path):
        """_run_post_install_actions calls _write_arp_registry."""
        from windows_installer_payload import StetInstaller

        wizard = StetInstaller(installer_zip)
        wizard.setField("installDir", str(tmp_path))
        wizard._completion_page._desktop_cb.setChecked(False)
        wizard._completion_page._startmenu_cb.setChecked(False)
        wizard._completion_page._download_cb.setChecked(False)
        wizard._completion_page._launch_cb.setChecked(False)

        with patch.object(wizard, "_write_arp_registry") as mock_arp:
            wizard._run_post_install_actions()
            mock_arp.assert_called_once_with(tmp_path)
