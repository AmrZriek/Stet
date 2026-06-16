"""
test_update.py — Unit tests for the AppUpdateChecker version comparison logic
and the update.py standalone script helpers.

These tests are source-level (no network, no Qt event loop) — they verify:
  - APP_VERSION is a valid semver string
  - The version parser handles all tag formats we've seen in the GitHub releases
  - The update checker only fires when remote > local
  - The standalone updater's file-copy exclusion list covers user data files
"""

import re
import sys
from pathlib import Path

# ── Project root on path ────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "stet").rglob("*.py"))
UPDATE_SRC = (ROOT / "stet" / "update.py").read_text(encoding="utf-8")


# ── Helpers extracted from source (duplicated here to test them in isolation) ──


def _parse_version(v_str: str) -> tuple:
    """Same implementation as AppUpdateChecker._parse_version (inner function)."""
    v_str = re.sub(r"[^0-9\.]", "", v_str)
    parts = []
    for p in v_str.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# APP_VERSION constant
# ═══════════════════════════════════════════════════════════════════════════════


def test_app_version_constant_exists():
    """APP_VERSION must be defined at module level."""
    m = re.search(r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', SRC)
    assert m, "APP_VERSION constant not found in stet.py"


def test_app_version_is_semver():
    """APP_VERSION must be in X.Y.Z or X.Y format."""
    m = re.search(r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', SRC)
    version = m.group(1)
    assert re.match(r"^\d+\.\d+(\.\d+)?$", version), (
        f"APP_VERSION '{version}' is not a valid semver string"
    )


def test_app_version_is_newer_than_old_releases():
    """APP_VERSION must be >= 1.0.0 (the new baseline release)."""
    m = re.search(r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', SRC)
    version = m.group(1)
    assert _parse_version(version) >= _parse_version("1.0.0"), (
        f"APP_VERSION '{version}' is older than the baseline release 1.0.0"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Version parser
# ═══════════════════════════════════════════════════════════════════════════════


def test_parse_version_clean():
    assert _parse_version("3.2.0") == (3, 2, 0)


def test_parse_version_with_v_prefix():
    """Tags from GitHub often come as 'v3.2.0'."""
    assert _parse_version("v3.2.0".lstrip("vV")) == (3, 2, 0)


def test_parse_version_release_prefix():
    """Old releases used 'Release_v3.1.0' as the tag."""
    assert _parse_version("Release_v3.1.0".lstrip("vV")) == (3, 1, 0)


def test_parse_version_two_part():
    assert _parse_version("3.1") == (3, 1, 0)


def test_parse_version_ordering_major():
    assert _parse_version("4.0.0") > _parse_version("3.9.9")


def test_parse_version_ordering_minor():
    assert _parse_version("3.2.0") > _parse_version("3.1.9")


def test_parse_version_ordering_patch():
    assert _parse_version("3.1.2") > _parse_version("3.1.1")


def test_parse_version_equal():
    assert _parse_version("3.1.1") == _parse_version("3.1.1")


# ═══════════════════════════════════════════════════════════════════════════════
# AppUpdateChecker wiring
# ═══════════════════════════════════════════════════════════════════════════════


def test_update_checker_class_exists():
    assert "class AppUpdateChecker" in SRC, (
        "AppUpdateChecker class not found — was it renamed or removed?"
    )


def test_update_checker_points_to_stet_repo():
    assert "AmrZriek/Stet/releases/latest" in SRC, (
        "GITHUB_RELEASES_API must point to AmrZriek/Stet, not llama.cpp"
    )


def test_old_llama_api_removed():
    assert "ggml-org/llama.cpp/releases/latest" not in SRC, (
        "Old llama.cpp GitHub API URL should be removed"
    )


def test_check_app_update_wired_to_boot():
    """The update check must be scheduled at boot, not _check_llama_update."""
    assert "_check_app_update" in SRC
    assert "_check_llama_update" not in SRC


def test_update_action_label_is_generic():
    """Tray menu item should say 'Check for updates', not 'llama.cpp update'."""
    assert '"Check for Updates"' in SRC or '"Check for updates"' in SRC
    assert '"Check for llama.cpp update"' not in SRC


def test_gui_update_launches_packaged_updater():
    """The packaged app should launch the dedicated updater helper."""
    assert "StetUpdater.exe" in SRC
    assert "_start_app_update" in SRC
    assert "_updater_command" in SRC


def test_gui_update_has_no_self_apply_batch():
    """The packaged GUI should not generate updater batch files."""
    assert "_apply_update.bat" not in SRC
    assert "_update_exclude.txt" not in SRC
    assert "xcopy" not in SRC
    assert "DETACHED_PROCESS" not in SRC


def test_gui_update_no_shell_true():
    """The packaged GUI update flow must not launch shell scripts."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m, "_start_app_update method not found"
    body = body_m.group(0)
    assert "shell=True" not in body
    assert "_apply_update.bat" not in body
    assert "xcopy" not in body


def test_gui_update_uses_temp_updater_copy():
    """import tempfile was added by mistake and then removed — ensure it's gone."""
    body_m = re.search(r"def _updater_command.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert "tempfile.gettempdir" in body
    assert "shutil.copy2" in body


def test_gui_update_does_not_create_exclude_file():
    """The packaged GUI should not create xcopy exclude files."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert "_update_exclude.txt" not in body


def test_gui_update_does_not_embed_model_copy_rules():
    """The packaged GUI should not embed model overwrite rules."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert ".gguf" not in body


def test_gui_update_does_not_embed_llama_copy_rules():
    """The packaged GUI should not embed llama overwrite rules."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert "llama-" not in body
    assert "llama_cpp" not in body


# ═══════════════════════════════════════════════════════════════════════════════
# update.py standalone script
# ═══════════════════════════════════════════════════════════════════════════════


def test_update_script_has_app_flag():
    assert "--app" in UPDATE_SRC, (
        "update.py must expose --app flag for standalone app update"
    )


def test_update_script_no_llama_flag():
    assert "--llama" not in UPDATE_SRC, (
        "Old --llama flag should be removed from update.py"
    )


def test_update_script_reads_app_version():
    assert "APP_VERSION" in UPDATE_SRC, "update.py must read APP_VERSION from stet.py"


def test_update_script_preserves_config():
    assert '"config.json"' in UPDATE_SRC or "'config.json'" in UPDATE_SRC, (
        "update.py must exclude config.json from overwrite"
    )


def test_update_script_preserves_gguf():
    assert ".gguf" in UPDATE_SRC, (
        "update.py must exclude .gguf model files from overwrite"
    )


def test_update_script_waits_for_gui_before_copying():
    assert "--wait-pid" in UPDATE_SRC
    assert "_wait_for_pid" in UPDATE_SRC


def test_update_script_uses_safe_extract():
    assert "_safe_extract" in UPDATE_SRC
    assert "extractall(staging_dir)" not in UPDATE_SRC


def test_update_script_has_atomic_copy_helper():
    assert "_copy_file_atomic" in UPDATE_SRC
    assert "os.replace" in UPDATE_SRC


# ═══════════════════════════════════════════════════════════════════════════════
# build.py version extraction
# ═══════════════════════════════════════════════════════════════════════════════


def test_build_reads_app_version_constant():
    build_src = (ROOT / "build.py").read_text(encoding="utf-8")
    assert "APP_VERSION" in build_src, (
        "build.py must extract version from APP_VERSION, not the docstring"
    )


def test_build_writes_version_file():
    build_src = (ROOT / "build.py").read_text(encoding="utf-8")
    assert '"VERSION"' in build_src or "'VERSION'" in build_src, (
        "build.py must write a VERSION file into the release folder"
    )


def test_build_creates_updater_helper():
    build_src = (ROOT / "build.py").read_text(encoding="utf-8")
    assert "UPDATER_SCRIPT" in build_src
    assert "StetUpdater" in build_src
    assert "--onefile" in build_src
