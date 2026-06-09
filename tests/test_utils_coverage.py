"""Tests for stet.core.utils — log(), friendly_name(), _release_zip_asset()."""

from pathlib import Path
from unittest.mock import patch

import pytest

from stet.core.utils import _release_zip_asset, friendly_name, log

# ── log() ─────────────────────────────────────────────────────────────────


class TestLog:
    """Verify log writes to debug log file with timestamps."""

    def test_log_writes_to_file(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("stet.core.utils.DEBUG_LOG", log_file):
            log("Test message")
        content = log_file.read_text(encoding="utf-8")
        assert "Test message" in content

    def test_log_contains_timestamp(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("stet.core.utils.DEBUG_LOG", log_file):
            log("Timestamp check")
        content = log_file.read_text(encoding="utf-8")
        # Expect format: [YYYY-MM-DD HH:MM:SS] message
        assert "[20" in content  # year starts with 20xx

    def test_log_appends_multiple(self, tmp_path):
        log_file = tmp_path / "test.log"
        with patch("stet.core.utils.DEBUG_LOG", log_file):
            log("First")
            log("Second")
        content = log_file.read_text(encoding="utf-8")
        assert "First" in content
        assert "Second" in content

    def test_log_handles_unwritable_path(self):
        """log() must not raise on unwritable paths."""
        with patch("stet.core.utils.DEBUG_LOG", Path("/nonexistent/dir/debug.log")):
            log("Should not crash")  # no exception = pass

    def test_log_is_thread_safe(self, tmp_path):
        """Multiple threads calling log() concurrently must not corrupt the file."""
        import threading

        log_file = tmp_path / "thread.log"
        with patch("stet.core.utils.DEBUG_LOG", log_file):
            threads = [
                threading.Thread(target=log, args=(f"msg-{i}",)) for i in range(20)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        content = log_file.read_text(encoding="utf-8")
        for i in range(20):
            assert f"msg-{i}" in content


# ── friendly_name() ──────────────────────────────────────────────────────


class TestFriendlyName:
    """Parse human-readable model names from paths."""

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("qwen2.5-3b-instruct-Q4_K_M.gguf", "qwen2.5-3b-instruct Q4_K_M"),
            ("gemma-4-E2B-it-Q8_0.gguf", "gemma-4-E2B IT Q8_0"),
            ("model-F16.gguf", "model F16"),
            ("model-BF16.gguf", "model BF16"),
            ("model-Q4_K_XL.gguf", "model Q4_K_XL"),
            ("model-IQ4_NL.gguf", "model IQ4"),
            ("model-GGUF.gguf", "model"),
        ],
    )
    def test_suffix_stripping(self, path, expected):
        assert friendly_name(path) == expected

    def test_full_path(self):
        result = friendly_name(r"C:\models\qwen2.5-3b-instruct-Q4_K_M.gguf")
        assert "qwen2.5-3b-instruct" in result

    def test_empty_path(self):
        result = friendly_name("")
        assert isinstance(result, str)


# ── _release_zip_asset() ─────────────────────────────────────────────────


class TestReleaseZipAsset:
    """Find the correct platform-specific zip asset from release data."""

    def test_windows_zip_found(self):
        data = {
            "assets": [
                {"name": "stet-v1.0-linux.zip", "browser_download_url": "https://a"},
                {"name": "stet-v1.0-windows.zip", "browser_download_url": "https://b"},
            ]
        }
        with (
            patch("stet.core.utils.WINDOWS", True),
            patch("stet.core.utils.MACOS", False),
        ):
            result = _release_zip_asset(data)
        assert result["name"] == "stet-v1.0-windows.zip"

    def test_macos_zip_found(self):
        data = {
            "assets": [
                {"name": "stet-v1.0-macos.zip", "browser_download_url": "https://a"},
                {"name": "stet-v1.0-windows.zip", "browser_download_url": "https://b"},
            ]
        }
        with (
            patch("stet.core.utils.WINDOWS", False),
            patch("stet.core.utils.MACOS", True),
        ):
            result = _release_zip_asset(data)
        assert result["name"] == "stet-v1.0-macos.zip"

    def test_fallback_to_any_zip(self):
        data = {
            "assets": [
                {
                    "name": "stet-v1.0-universal.zip",
                    "browser_download_url": "https://a",
                },
            ]
        }
        with (
            patch("stet.core.utils.WINDOWS", True),
            patch("stet.core.utils.MACOS", False),
        ):
            result = _release_zip_asset(data)
        assert result["name"] == "stet-v1.0-universal.zip"

    def test_no_zip_returns_none(self):
        data = {"assets": [{"name": "stet-v1.0.tar.gz"}]}
        result = _release_zip_asset(data)
        assert result is None

    def test_empty_assets_returns_none(self):
        assert _release_zip_asset({"assets": []}) is None

    def test_no_assets_key_returns_none(self):
        assert _release_zip_asset({}) is None
