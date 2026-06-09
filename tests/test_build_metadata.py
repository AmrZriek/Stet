"""Tests for build.py metadata, versioning, and path detection logic."""

import sys
from pathlib import Path
from unittest.mock import patch


# Prevent build.py from triggering a re-launch or exit on import
with patch("sys.exit") as mock_exit, patch("subprocess.run") as mock_run:
    import build


def test_windows_resource_version():
    """Convert version labels to Windows resource version tuples."""
    assert build._windows_resource_version("3.2.0-test") == "3.2.0.0"
    assert build._windows_resource_version("4.5.1") == "4.5.1.0"
    assert build._windows_resource_version("1") == "1.0.0.0"
    assert build._windows_resource_version("1.2.3.4.5") == "1.2.3.4"
    assert build._windows_resource_version("v3-alpha") == "3.0.0.0"


def test_get_version_success():
    """Extract version correctly from script."""
    fake_content = 'APP_VERSION = "3.2.5"'
    with patch.object(Path, "read_text", return_value=fake_content):
        assert build._get_version() == "3.2.5"


def test_get_version_failure_fallback():
    """Fallback to current date on parsing failure."""
    with patch.object(Path, "read_text", side_effect=RuntimeError("Cannot read")):
        version = build._get_version()
        assert len(version.split(".")) == 3  # YYYY.MM.DD format


def test_find_llama_dir_from_config(tmp_path):
    """Resolve llama-server path from config.json if defined and valid."""
    fake_config = tmp_path / "config.json"
    fake_server_dir = tmp_path / "my-llama-bin"
    fake_server_dir.mkdir()

    exe_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    (fake_server_dir / exe_name).touch()

    import json

    config_data = {"llama_server_path": str(fake_server_dir / exe_name)}
    fake_config.write_text(json.dumps(config_data), encoding="utf-8")

    with patch("build.ROOT", tmp_path):
        detected = build._find_llama_dir()
        assert detected == fake_server_dir


def test_find_llama_dir_auto_detect(tmp_path):
    """Auto-detect 'llama' directory in workspace root if config is missing or blank."""
    fake_llama_dir = tmp_path / "llama-b1234-cuda"
    fake_llama_dir.mkdir()

    exe_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    (fake_llama_dir / exe_name).touch()

    with patch("build.ROOT", tmp_path):
        # Without config.json
        detected = build._find_llama_dir()
        assert detected == fake_llama_dir


def test_find_cuda_dir(tmp_path):
    """CUDA directory discovery."""
    fake_cuda_dir = tmp_path / "cuda"
    fake_cuda_dir.mkdir()

    for dll in build.CUDA_DLLS:
        (fake_cuda_dir / dll).touch()

    # Stub the paths to check inside _find_cuda_dir
    with patch("build.PLATFORM", "Windows"):
        with patch(
            "os.path.expandvars", lambda x: str(fake_cuda_dir) if "CUDA" in x else x
        ):
            # Verify it scans our mocked path successfully
            detected = build._find_cuda_dir()
            assert detected is not None


def test_nuitka_cmd_construction():
    """Verify generated Nuitka commands include metadata flags on Windows."""
    with patch("build.PLATFORM", "Windows"):
        cmd = build._nuitka_cmd("3.2.0", Path("artifacts"))
        assert "--windows-console-mode=disable" in cmd
        assert "--company-name=Stet" in cmd
        assert "--product-name=Stet" in cmd
        assert "--file-version=3.2.0.0" in cmd

    with patch("build.PLATFORM", "macOS"):
        cmd = build._nuitka_cmd("3.2.0", Path("artifacts"))
        assert f"--macos-app-icon={build.ICON_PNG}" in cmd


def test_updater_nuitka_cmd_construction():
    """Verify updater compilation commands are correct."""
    with patch("build.PLATFORM", "Windows"):
        cmd = build._updater_nuitka_cmd("3.2.0", Path("artifacts"))
        assert "--windows-console-mode=force" in cmd
        assert "--company-name=Stet" in cmd
        assert "--product-name=Stet Updater" in cmd
        assert "--file-version=3.2.0.0" in cmd


def test_uninstaller_nuitka_cmd_construction():
    """Verify uninstaller compilation commands are correct."""
    with patch("build.PLATFORM", "Windows"):
        cmd = build._uninstaller_nuitka_cmd("1.0.0", Path("artifacts"))
        assert "--onefile" in cmd
        assert "--windows-console-mode=disable" in cmd
        assert "--company-name=Stet" in cmd
        assert "--product-name=Stet Uninstaller" in cmd
        assert "--file-version=1.0.0.0" in cmd
        assert str(build.UNINSTALLER_SCRIPT) in cmd


def test_base_nuitka_cmd_shared_flags():
    """_base_nuitka_cmd includes all shared Nuitka flags."""
    with patch("build.PLATFORM", "Windows"):
        cmd = build._base_nuitka_cmd("TestApp", Path("out"))
        assert "--assume-yes-for-downloads" in cmd
        assert "--python-flag=no_warnings" in cmd
        assert "--noinclude-default-mode=error" in cmd
        assert "--remove-output" in cmd
        assert "--lto=yes" in cmd


def test_base_nuitka_cmd_pyqt6_plugin():
    """_base_nuitka_cmd inserts PyQt6 plugin when requested."""
    with patch("build.PLATFORM", "Windows"):
        cmd = build._base_nuitka_cmd("TestApp", Path("out"), pyqt6=True)
        assert "--enable-plugin=pyqt6" in cmd

        cmd_no_qt = build._base_nuitka_cmd("TestApp", Path("out"), pyqt6=False)
        assert "--enable-plugin=pyqt6" not in cmd_no_qt


def test_total_steps_includes_uninstaller():
    """_total_steps counts the uninstaller step on Windows."""
    with patch("build.PLATFORM", "Windows"), \
         patch.object(Path, "exists", return_value=True):
        builder = build.PlatformBuilder("1.0.0")
        steps = builder._total_steps()
        assert steps >= 7

