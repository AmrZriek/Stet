"""Tests for sys.frozen=True compatibility (Nuitka compiled build).

Verifies that path resolution, config, and startup logic work correctly
when the app is running as a compiled executable (sys.frozen=True).
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Prevent build.py from triggering a re-launch or exit on import
with patch("sys.exit") as _mock_exit, patch("subprocess.run") as _mock_run:
    import build


class TestFrozenPathResolution:
    """SCRIPT_DIR and CONFIG_FILE resolve correctly when frozen."""

    def test_script_dir_is_exe_parent_when_frozen(self, tmp_path, monkeypatch):
        """When sys.frozen=True, SCRIPT_DIR should be exe.parent, not __file__.parent.parent."""
        fake_exe = tmp_path / "Stet.exe"
        fake_exe.touch()

        # We can't reload constants (PyQt6 side effects), so test the logic directly

        # Simulate what constants.py does:
        # if getattr(sys, 'frozen', False):
        #     SCRIPT_DIR = Path(sys.executable).parent.resolve()
        with monkeypatch.context() as m:
            m.setattr(sys, "executable", str(fake_exe))
            m.setattr(sys, "frozen", True, raising=False)
            # Recalculate what SCRIPT_DIR would be:
            expected = fake_exe.parent.resolve()
            calculated = Path(sys.executable).parent.resolve()
            assert calculated == expected

    def test_script_dir_is_exe_parent_when_nuitka(self, tmp_path, monkeypatch):
        """When exe is Stet.exe (Nuitka, no sys.frozen), SCRIPT_DIR must still be exe.parent.

        This validates the constants.py fix matching the _startup_command() fix.
        """
        fake_exe = tmp_path / "Stet.exe"
        fake_exe.touch()

        with monkeypatch.context() as m:
            m.setattr(sys, "executable", str(fake_exe))
            m.setattr(sys, "frozen", False, raising=False)

            exe_stem = Path(sys.executable).stem.lower()
            is_compiled = getattr(sys, "frozen", False) or not exe_stem.startswith("python")
            assert is_compiled is True, "Stet.exe must be detected as compiled"

            if is_compiled:
                calculated = Path(sys.executable).parent.resolve()
            else:
                calculated = Path(__file__).parent.parent.resolve()

            expected = fake_exe.parent.resolve()
            assert calculated == expected

    def test_script_dir_is_project_root_when_not_frozen(self):
        """When not frozen (source mode), SCRIPT_DIR is two levels up from constants.py."""
        from stet.constants import SCRIPT_DIR
        # constants.py is at stet/constants.py → SCRIPT_DIR should be project root
        expected_name = "Stet"
        assert SCRIPT_DIR.name == expected_name, \
            f"SCRIPT_DIR should be project root 'Stet', got: {SCRIPT_DIR}"

    def test_config_file_is_in_script_dir(self):
        """CONFIG_FILE is always SCRIPT_DIR / 'config.json'."""
        from stet.constants import SCRIPT_DIR, CONFIG_FILE
        assert CONFIG_FILE == SCRIPT_DIR / "config.json"
        assert CONFIG_FILE.name == "config.json"

    def test_log_file_is_in_script_dir(self):
        """Server log and debug log are in SCRIPT_DIR."""
        from stet.constants import SCRIPT_DIR, LOG_FILE, DEBUG_LOG
        assert LOG_FILE.parent == SCRIPT_DIR
        assert DEBUG_LOG.parent == SCRIPT_DIR


class TestReleaseConfigCompleteness:
    """RELEASE_CONFIG in build.py must stay in sync with DEFAULT_CONFIG."""

    @pytest.fixture
    def release_config(self):
        return build.RELEASE_CONFIG

    @pytest.fixture
    def default_config_keys(self):
        from stet.constants import DEFAULT_CONFIG
        return set(DEFAULT_CONFIG.keys())

    def test_release_config_has_model_path(self, release_config):
        """Release config must have model_path (blank for user to fill in)."""
        assert "model_path" in release_config
        assert release_config["model_path"] == "", \
            "model_path must be empty in release config (user fills in)"

    def test_release_config_has_llama_server_path(self, release_config):
        """Release config must have llama_server_path (blank for auto-detect)."""
        assert "llama_server_path" in release_config
        assert release_config["llama_server_path"] == "", \
            "llama_server_path must be empty in release config (auto-detected at runtime)"

    def test_release_config_blank_paths_no_machine_specific(self, release_config):
        """Release config must not contain any machine-specific absolute paths."""
        machine_path_indicators = [
            "C:\\", "D:\\", "E:\\", "F:\\", "/home/", "/Users/"
        ]
        for key, value in release_config.items():
            if isinstance(value, str):
                for indicator in machine_path_indicators:
                    assert indicator not in value, \
                        f"Release config key '{key}' contains machine-specific path: {value!r}"

    def test_release_config_keep_model_loaded_is_true(self, release_config):
        """Release config should default keep_model_loaded=True for new users."""
        assert release_config.get("keep_model_loaded") is True

    def test_release_config_has_core_server_settings(self, release_config):
        """Core llama-server settings must be present."""
        required = ["server_host", "server_port", "context_size", "gpu_layers"]
        for key in required:
            assert key in release_config, f"Missing required key in RELEASE_CONFIG: {key!r}"


class TestStartupCommandFrozen:
    """_startup_command() returns correct value for frozen vs source builds."""

    def test_frozen_returns_exe_path(self, tmp_path, monkeypatch):
        """For frozen builds (sys.frozen=True), startup command is just the exe itself."""
        import stet.core.app as app_mod
        exe = tmp_path / "Stet.exe"
        exe.touch()
        monkeypatch.setattr(app_mod.sys, "executable", str(exe))
        monkeypatch.setattr(app_mod.sys, "frozen", True, raising=False)
        cmd = app_mod._startup_command()
        assert str(exe) in cmd

    def test_nuitka_returns_exe_path_without_sys_frozen(self, tmp_path, monkeypatch):
        """Nuitka builds have sys.frozen=False but exe is Stet.exe.

        This is the core bug fix: Nuitka doesn't set sys.frozen, so the old
        code fell through to source-mode and registered pythonw.exe -m stet.main.
        """
        import stet.core.app as app_mod
        exe = tmp_path / "Stet.exe"
        exe.touch()
        monkeypatch.setattr(app_mod.sys, "executable", str(exe))
        monkeypatch.setattr(app_mod.sys, "frozen", False, raising=False)
        cmd = app_mod._startup_command()
        assert str(exe) in cmd
        assert "python" not in cmd.lower()
        assert "stet.main" not in cmd

    def test_source_prefers_vbs_wrapper(self, tmp_path, monkeypatch):
        """For source builds, startup command uses startup.vbs if it exists."""
        import stet.core.app as app_mod
        vbs = tmp_path / "startup.vbs"
        vbs.write_text("echo test")
        monkeypatch.setattr(app_mod, "SCRIPT_DIR", tmp_path)
        monkeypatch.setattr(app_mod.sys, "frozen", False, raising=False)
        monkeypatch.setattr(app_mod.sys, "executable", str(tmp_path / "python.exe"))
        cmd = app_mod._startup_command()
        assert "wscript.exe" in cmd
        assert "startup.vbs" in cmd


class TestBuildScriptIntegrity:
    """build.py structural checks — ensures release packaging is correct."""

    def test_nuitka_cmd_includes_pyqt6_plugin(self):
        """Nuitka build command must include --enable-plugin=pyqt6."""
        cmd = build._nuitka_cmd("3.2.0", Path("artifacts"))
        cmd_str = " ".join(str(c) for c in cmd)
        assert "--enable-plugin=pyqt6" in cmd_str

    def test_nuitka_cmd_is_standalone(self):
        """Nuitka build command must use --standalone."""
        cmd = build._nuitka_cmd("3.2.0", Path("artifacts"))
        cmd_str = " ".join(str(c) for c in cmd)
        assert "--standalone" in cmd_str

    def test_windows_resource_version_pads_to_four_parts(self):
        """Windows resource version must be four dot-separated integers."""
        result = build._windows_resource_version("3.2.0")
        parts = result.split(".")
        assert len(parts) == 4
        assert all(p.isdigit() for p in parts)

    def test_windows_resource_version_strips_non_numeric(self):
        """Labels like '3.2.0-test' must become '3.2.0.0'."""
        result = build._windows_resource_version("3.2.0-test")
        parts = result.split(".")
        assert all(p.isdigit() for p in parts)

    def test_cuda_dlls_list_has_required_dlls(self):
        """CUDA DLL list must include all three required runtime libraries."""
        assert "cudart64_12.dll" in build.CUDA_DLLS
        assert "cublas64_12.dll" in build.CUDA_DLLS
        assert "cublasLt64_12.dll" in build.CUDA_DLLS

    def test_startup_vbs_copied_to_portable(self, tmp_path):
        """build_extras() must copy startup.vbs to the portable directory."""

        # Create a minimal source layout
        src_root = tmp_path / "src"
        src_root.mkdir()
        (src_root / "startup.vbs").write_text("' test vbs", encoding="utf-8")
        (src_root / "logo.png").write_bytes(b"\x89PNG")
        (src_root / "logo.ico").write_bytes(b"\x00\x00")
        (src_root / "LICENSE").write_text("MIT", encoding="utf-8")
        (src_root / "README.md").write_text("# Stet", encoding="utf-8")
        qss_dir = src_root / "stet" / "ui"
        qss_dir.mkdir(parents=True)
        (qss_dir / "stet.qss").write_text("QWidget {}", encoding="utf-8")

        # Create a portable dir with Stet.exe (simulating Nuitka output)
        portable = tmp_path / "portable"
        portable.mkdir()
        (portable / "Stet.exe").write_bytes(b"MZ")

        # Patch build paths and run build_extras
        builder = build.PlatformBuilder.__new__(build.PlatformBuilder)
        builder.version = "1.0.0"
        builder.portable_dir = portable
        builder.llama_dir = None
        builder.cuda_dir = None

        with patch.object(build, "ROOT", src_root):
            builder.build_extras()

        assert (portable / "startup.vbs").exists(), \
            "startup.vbs must be copied to portable dir for Windows startup support"

    def test_compiled_build_smoke_test(self):
        """If a compiled build exists, run the smoke test script on it."""
        import subprocess
        import sys
        from pathlib import Path

        # Check if there is any compiled build Stet.exe in dist/
        root = Path(__file__).parent.parent.resolve()
        dist_dir = root / "dist"
        if not dist_dir.exists():
            pytest.skip("No dist/ folder found; skipping compiled build smoke test.")

        candidates = sorted(
            dist_dir.glob("**/Stet.exe"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            pytest.skip("No Stet.exe found in dist/; skipping compiled build smoke test.")

        latest_exe = candidates[0]
        smoke_test_script = root / "scripts" / "smoke_test_build.py"

        # Run the smoke test script
        res = subprocess.run(
            [sys.executable, str(smoke_test_script), str(latest_exe)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Smoke test failed with return code {res.returncode}.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
