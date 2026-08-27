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
    _find_mtp_draft_model,
    _find_shipped_llama_server,
    _is_valid_gguf,
    _model_size_billions,
    _supports_mtp,
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


# ── _is_valid_gguf ────────────────────────────────────────────────────────


class TestIsValidGguf:
    """Verify GGUF file integrity validation."""

    def test_valid_gguf(self, tmp_path):
        f = tmp_path / "valid.gguf"
        # Write magic bytes and pad to >10MB (10MB + 1 byte)
        f.write_bytes(b"GGUF" + b"\x00" * (10 * 1024 * 1024))
        assert _is_valid_gguf(f) is True

    def test_invalid_magic(self, tmp_path):
        f = tmp_path / "bad_magic.gguf"
        f.write_bytes(b"FUGG" + b"\x00" * (10 * 1024 * 1024))
        assert _is_valid_gguf(f) is False

    def test_too_small(self, tmp_path):
        f = tmp_path / "small.gguf"
        f.write_bytes(b"GGUF" + b"\x00" * 1000)
        assert _is_valid_gguf(f) is False

    def test_missing_file(self, tmp_path):
        assert _is_valid_gguf(tmp_path / "missing.gguf") is False

    def test_is_directory(self, tmp_path):
        assert _is_valid_gguf(tmp_path) is False


# ── _find_mtp_draft_model & _supports_mtp ─────────────────────────────────


class TestFindMtpDraftModel:
    """Test sibling companion draft model detection."""

    def test_finds_mtp_prefix(self, tmp_path):
        base = tmp_path / "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
        draft = tmp_path / "mtp-gemma-4-E2B-it.gguf"
        base.write_bytes(b"GGUF" + b"\x00" * 2000)
        draft.write_bytes(b"GGUF" + b"\x00" * 2000)

        found = _find_mtp_draft_model(str(base))
        assert found == str(draft)

    def test_finds_assistant_suffix(self, tmp_path):
        base = tmp_path / "model.gguf"
        draft = tmp_path / "model-assistant.gguf"
        base.write_bytes(b"GGUF" + b"\x00" * 2000)
        draft.write_bytes(b"GGUF" + b"\x00" * 2000)

        found = _find_mtp_draft_model(str(base))
        assert found == str(draft)

    def test_no_sibling_draft(self, tmp_path):
        base = tmp_path / "model.gguf"
        other = tmp_path / "unrelated.txt"
        base.write_bytes(b"GGUF" + b"\x00" * 2000)
        other.write_text("hello")

        assert _find_mtp_draft_model(str(base)) is None

    def test_nonexistent_base(self):
        assert _find_mtp_draft_model("/nonexistent/model.gguf") is None


class TestSupportsMtp:
    """Test MTP support detection."""

    def test_sibling_draft_enables_mtp(self, tmp_path):
        base = tmp_path / "gemma-base.gguf"
        draft = tmp_path / "mtp-gemma-draft.gguf"
        base.write_bytes(b"GGUF" + b"\x00" * 2000)
        draft.write_bytes(b"GGUF" + b"\x00" * 2000)

        assert _supports_mtp(str(base)) is True

    def test_filename_mtp(self, tmp_path):
        model = tmp_path / "qwen-4b-mtp-q4_k_m.gguf"
        model.write_bytes(b"GGUF" + b"\x00" * 2000)
        assert _supports_mtp(str(model)) is True

    def test_content_nextn(self, tmp_path):
        model = tmp_path / "model.gguf"
        model.write_bytes(b"GGUF" + b"some header nextn_predict_layers bytes" + b"\x00" * 2000)
        assert _supports_mtp(str(model)) is True

    def test_plain_model_no_mtp(self, tmp_path):
        model = tmp_path / "plain_model.gguf"
        model.write_bytes(b"GGUF" + b"\x00" * 2000)
        assert _supports_mtp(str(model)) is False


class TestVramHelpers:
    """Test VRAM query and estimation functions."""

    def test_query_free_vram_mb_nvidia_smi(self):
        from stet.llm.utils import query_free_vram_mb

        with patch("subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "8192\n4096\n"
            mock_run.return_value = mock_res

            val = query_free_vram_mb()
            assert val == 8192

    def test_query_free_vram_mb_failure(self):
        from stet.llm.utils import query_free_vram_mb

        with patch("subprocess.run", side_effect=Exception("no nvidia-smi")):
            assert query_free_vram_mb() is None

    def test_estimate_model_vram_mb(self, tmp_path):
        from stet.llm.utils import estimate_model_vram_mb

        # Missing file
        assert estimate_model_vram_mb(str(tmp_path / "missing.gguf")) is None
        assert estimate_model_vram_mb("") is None

        # Existing file with 100 MB dummy size
        dummy = tmp_path / "dummy.gguf"
        dummy.write_bytes(b"0" * (100 * 1024 * 1024))
        est = estimate_model_vram_mb(str(dummy), ctx_size=4096)
        assert est is not None
        assert est > 100  # Should include weights + overhead

