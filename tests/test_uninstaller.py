"""Tests for uninstall.py — Stet uninstaller logic.

Tests cover:
  - Registry reading for install location
  - File deletion (preserving config/models by default)
  - Cleanup batch file generation
  - Silent and purge modes
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def install_dir(tmp_path):
    fake_dir = tmp_path / "Stet"
    fake_dir.mkdir()
    (fake_dir / "Stet.exe").write_bytes(b"fake-exe")
    (fake_dir / "StetUninstall.exe").write_bytes(b"fake-uninstaller")
    (fake_dir / "config.json").write_text('{"setting": true}', encoding="utf-8")
    (fake_dir / "model.gguf").write_bytes(b"fake-model")
    (fake_dir / "run.bat").write_text("@echo off", encoding="utf-8")
    (fake_dir / "stet").mkdir()
    (fake_dir / "stet" / "main.py").write_text("pass", encoding="utf-8")
    return fake_dir


class TestReadInstallDir:
    def test_reads_from_registry(self):
        mock_key = MagicMock()
        with patch("uninstall.winreg") as mock_winreg:
            mock_winreg.OpenKey.return_value = mock_key
            mock_winreg.QueryValueEx.return_value = (r"C:\Users\Test\AppData\Local\Stet", 1)
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.KEY_READ = 0x20019

            import uninstall
            result = uninstall._read_install_dir()

        assert result == r"C:\Users\Test\AppData\Local\Stet"

    def test_returns_none_when_missing(self):
        with patch("uninstall.winreg") as mock_winreg:
            mock_winreg.OpenKey.side_effect = OSError("Not found")
            mock_winreg.HKEY_CURRENT_USER = 0x80000001
            mock_winreg.KEY_READ = 0x20019

            import uninstall
            result = uninstall._read_install_dir()

        assert result is None


class TestRemoveAppFiles:
    def test_preserves_config_and_models_by_default(self, install_dir):
        import uninstall
        uninstall._remove_app_files(install_dir, purge=False)

        assert (install_dir / "config.json").exists()
        assert (install_dir / "model.gguf").exists()
        assert (install_dir / "StetUninstall.exe").exists()
        assert not (install_dir / "Stet.exe").exists()
        assert not (install_dir / "run.bat").exists()
        assert not (install_dir / "stet").exists()

    def test_purge_deletes_everything(self, install_dir):
        import uninstall
        uninstall._remove_app_files(install_dir, purge=True)

        assert not (install_dir / "config.json").exists()
        assert not (install_dir / "model.gguf").exists()
        assert not (install_dir / "Stet.exe").exists()
        assert (install_dir / "StetUninstall.exe").exists()


class TestCleanupBat:
    def test_creates_bat_with_correct_content(self, install_dir):
        import uninstall
        bat_path = uninstall._create_cleanup_bat(install_dir, purge=False)

        content = bat_path.read_text(encoding="utf-8")
        assert "timeout /t 2" in content
        assert "StetUninstall.exe" in content
        assert "Stet.lnk" in content
        assert "reg delete" in content
        assert str(bat_path) in content

    def test_purge_bat_uses_recursive_rmdir(self, install_dir):
        import uninstall
        bat_path = uninstall._create_cleanup_bat(install_dir, purge=True)

        content = bat_path.read_text(encoding="utf-8")
        assert "/q /s" in content

    def test_non_purge_bat_uses_non_recursive_rmdir(self, install_dir):
        import uninstall
        bat_path = uninstall._create_cleanup_bat(install_dir, purge=False)

        content = bat_path.read_text(encoding="utf-8")
        assert "/q /s" not in content


class TestMainEntryPoint:
    def test_not_found_shows_error(self):
        with patch("uninstall._read_install_dir", return_value=None), \
             patch("uninstall._message_box", return_value=6) as mock_mb, \
             patch("sys.argv", ["StetUninstall.exe"]):
            import uninstall
            with pytest.raises(SystemExit):
                uninstall.main()
            mock_mb.assert_called_once()

    def test_silent_mode_skips_dialog(self):
        with patch("uninstall._read_install_dir", return_value=None), \
             patch("uninstall._message_box") as mock_mb, \
             patch("sys.argv", ["StetUninstall.exe", "--silent"]):
            import uninstall
            with pytest.raises(SystemExit):
                uninstall.main()
            mock_mb.assert_not_called()

    def test_user_cancel_aborts(self, install_dir):
        with patch("uninstall._read_install_dir", return_value=str(install_dir)), \
             patch("uninstall._message_box", return_value=7), \
             patch("uninstall._kill_stet_processes") as mock_kill, \
             patch("sys.argv", ["StetUninstall.exe"]):
            import uninstall
            with pytest.raises(SystemExit):
                uninstall.main()
            mock_kill.assert_not_called()

    def test_full_uninstall_flow(self, install_dir):
        with patch("uninstall._read_install_dir", return_value=str(install_dir)), \
             patch("uninstall._message_box", return_value=6), \
             patch("uninstall._kill_stet_processes") as mock_kill, \
             patch("uninstall._spawn_cleanup") as mock_spawn, \
             patch("sys.argv", ["StetUninstall.exe"]):
            import uninstall
            uninstall.main()

            mock_kill.assert_called_once()
            mock_spawn.assert_called_once()
            assert (install_dir / "config.json").exists()
            assert (install_dir / "model.gguf").exists()
