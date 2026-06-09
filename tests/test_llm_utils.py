"""Tests for stet.llm.utils — model size parsing, server discovery, GPU detection."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stet.llm.utils import (
    _COMPILED_PREAMBLES,
    _COMPILED_THINKING_PATTERNS,
    _COMPILED_UNCLOSED_PATTERNS,
    _MIN_RELIABLE_MODEL_B,
    _find_shipped_llama_server,
    _model_size_billions,
    has_nvidia,
)

# ── _model_size_billions ──────────────────────────────────────────────────


class TestModelSizeBillions:
    """Parse parameter counts from GGUF filenames."""

    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("qwen2.5-3b-instruct-q4_k_m.gguf", 3.0),
            ("gemma-4-E2B-it-UD-Q4_K_XL.gguf", 2.0),
            ("gemma3-270m-grammar-q8_0.gguf", 0.27),
            ("Llama-3.2-1B-Instruct-Q4_K_M.gguf", 1.0),
            ("phi-mini-3.8b-Q4.gguf", 3.8),
            ("some-model-7b.gguf", 7.0),
            ("tiny-500m-q4.gguf", 0.5),
        ],
    )
    def test_valid_sizes(self, filename, expected):
        assert _model_size_billions(filename) == expected

    def test_empty_path(self):
        assert _model_size_billions("") is None

    def test_none_path(self):
        assert _model_size_billions(None) is None

    def test_no_size_marker(self):
        assert _model_size_billions("random-model-q4.gguf") is None

    def test_full_path(self):
        result = _model_size_billions(r"C:\models\qwen2.5-3b-instruct.gguf")
        assert result == 3.0

    def test_min_reliable_constant(self):
        assert _MIN_RELIABLE_MODEL_B == 1.0


# ── _find_shipped_llama_server ────────────────────────────────────────────


class TestFindShippedLlamaServer:
    """Locate llama-server binary shipped alongside the app."""

    def test_legacy_path_found(self, tmp_path):
        """Legacy llama_cpp/ folder contains the server binary."""
        legacy = tmp_path / "llama_cpp" / "llama-server.exe"
        legacy.parent.mkdir()
        legacy.touch()

        with (
            patch("stet.llm.utils.LLAMA_CPP_DIR", tmp_path / "llama_cpp"),
            patch("stet.llm.utils.SERVER_EXE", "llama-server.exe"),
            patch("stet.llm.utils.SCRIPT_DIR", tmp_path),
        ):
            result = _find_shipped_llama_server()
            assert result == str(legacy)

    def test_sibling_folder_found(self, tmp_path):
        """Sibling llama-* folder with server binary."""
        sibling = tmp_path / "llama-b9119-bin-win-cuda-12.4-x64"
        sibling.mkdir()
        server = sibling / "llama-server.exe"
        server.touch()

        with (
            patch("stet.llm.utils.LLAMA_CPP_DIR", tmp_path / "llama_cpp"),
            patch("stet.llm.utils.SERVER_EXE", "llama-server.exe"),
            patch("stet.llm.utils.SCRIPT_DIR", tmp_path),
        ):
            result = _find_shipped_llama_server()
            assert result == str(server)

    def test_no_server_found(self, tmp_path):
        """No matching directory exists."""
        with (
            patch("stet.llm.utils.LLAMA_CPP_DIR", tmp_path / "llama_cpp"),
            patch("stet.llm.utils.SERVER_EXE", "llama-server.exe"),
            patch("stet.llm.utils.SCRIPT_DIR", tmp_path),
        ):
            result = _find_shipped_llama_server()
            assert result == ""

    def test_iterdir_exception(self, tmp_path):
        """Gracefully handles OSError during directory scan."""
        with (
            patch("stet.llm.utils.LLAMA_CPP_DIR", tmp_path / "llama_cpp"),
            patch("stet.llm.utils.SERVER_EXE", "llama-server.exe"),
            patch("stet.llm.utils.SCRIPT_DIR", tmp_path),
        ):
            with patch.object(Path, "iterdir", side_effect=PermissionError):
                result = _find_shipped_llama_server()
                assert result == ""


# ── has_nvidia ────────────────────────────────────────────────────────────


class TestHasNvidia:
    """GPU detection via nvidia-smi."""

    def test_gpu_present(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "NVIDIA GeForce RTX 4090\n"

        with patch("stet.llm.utils.subprocess.run", return_value=mock_result):
            assert has_nvidia() is True

    def test_no_gpu(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("stet.llm.utils.subprocess.run", return_value=mock_result):
            assert has_nvidia() is False

    def test_empty_stdout(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   "

        with patch("stet.llm.utils.subprocess.run", return_value=mock_result):
            assert has_nvidia() is False

    def test_nvidia_smi_not_found(self):
        with patch("stet.llm.utils.subprocess.run", side_effect=FileNotFoundError):
            assert has_nvidia() is False

    def test_timeout(self):
        with patch(
            "stet.llm.utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("nvidia-smi", 5),
        ):
            assert has_nvidia() is False


# ── Compiled regex patterns ──────────────────────────────────────────────


class TestCompiledPatterns:
    """Verify thinking/preamble regex patterns compile and match."""

    def test_thinking_patterns_strip_blocks(self):
        text = "Hello <think>internal</think> world"
        result = text
        for p in _COMPILED_THINKING_PATTERNS:
            result = p.sub("", result)
        assert "internal" not in result
        assert "Hello" in result

    def test_unclosed_thinking_stripped(self):
        text = "Hello <think>dangling text without close"
        result = text
        for p in _COMPILED_UNCLOSED_PATTERNS:
            result = p.sub("", result)
        assert "dangling" not in result

    def test_preamble_patterns_strip_prefix(self):
        text = "Here is the corrected text:\nActual content here."
        result = text
        for p in _COMPILED_PREAMBLES:
            result = p.sub("", result)
        assert "Actual content here" in result

    def test_preamble_patterns_count(self):
        assert len(_COMPILED_PREAMBLES) >= 10
