"""Tests for audit-driven architecture enhancements and hardening."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

import stet.core.config as config_mod
from stet.core.config import ConfigManager
from stet.core.text_utils import _INLINE_SENTINEL_CAPTURE_RE, _INLINE_SENTINEL_RE
from stet.llm.model_manager import ModelManager, _resolve_mode_index


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)
    return cfg_file


def test_sentinel_regex_unification():
    """Verify _INLINE_SENTINEL_RE and _INLINE_SENTINEL_CAPTURE_RE behavior."""
    sample = "Hello __STET_PROTECTED_1__ and __STET_PROTECTED_42__ world"
    matches = _INLINE_SENTINEL_RE.findall(sample)
    assert matches == ["__STET_PROTECTED_1__", "__STET_PROTECTED_42__"]

    cap_matches = [int(m.group(1)) for m in _INLINE_SENTINEL_CAPTURE_RE.finditer(sample)]
    assert cap_matches == [1, 42]


def test_resolve_mode_index_custom_and_renamed():
    """Test resolution of mode index across built-in, renamed, and custom modes."""
    modes = [
        {"name": "Fix Grammar", "id": "spelling_only", "prompt": "prompt 0"},
        {"name": "Smart Fix", "id": "full_correction", "prompt": "prompt 1"},
        {"name": "Polish & Flow", "id": "rewrite_polish", "prompt": "prompt 2"},
        {"name": "Technical Tone", "prompt": "prompt 3"},
        {"name": "Academic Tone", "prompt": "prompt 4"},
    ]

    # Built-in strength keys
    assert _resolve_mode_index("spelling_only", modes) == 0
    assert _resolve_mode_index("full_correction", modes) == 1
    assert _resolve_mode_index("rewrite_polish", modes) == 2

    # Renamed / custom mode names
    assert _resolve_mode_index("Smart Fix", modes) == 1
    assert _resolve_mode_index("Technical Tone", modes) == 3
    assert _resolve_mode_index("Academic Tone", modes) == 4

    # Unknown mode fallback
    assert _resolve_mode_index("NonExistent", modes) == 1


def test_warmup_prompt_cache_multi_mode(mock_config):
    """Verify multi-mode KV warmup sends requests for all distinct configured mode prompts."""
    cfg = ConfigManager()
    cfg.set("hotkeys", [
        {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
        {"shortcut": "f10", "mode": "silent", "strength": "spelling_only"},
        {"shortcut": "shift+f9", "mode": "panel", "strength": "rewrite_polish"},
    ])
    cfg.set("parallel", 4)

    manager = ModelManager(cfg)

    with patch("requests.Session.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_post.return_value = mock_resp

        manager._warmup_prompt_cache()

        # Should warm all 4 parallel slots with distinct mode strengths distributed
        assert mock_post.call_count == 4


def test_config_migration_context_size_auto(mock_config):
    """Verify ConfigManager migrates context_size_auto and chat_context_size_auto."""
    # Write config without context_size_auto keys
    mock_config.write_text('{"context_size": 12800, "chat_context_size": 4096}', encoding="utf-8")

    cfg = ConfigManager()
    assert cfg.get("context_size_auto") is True
    assert cfg.get("chat_context_size_auto") is False


class TestAuditEnhancements:
    """Tests requiring unblocked load_model execution."""

    def test_load_model_context_size_auto(self, mock_config, tmp_path):
        """Verify context size auto-derivation honors context_size_auto flag."""
        cfg = ConfigManager()
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)

        cfg.set("model_path", str(model_file))
        cfg.set("context_size_auto", True)
        cfg.set("context_size", 12800)

        manager = ModelManager(cfg)

        mock_info = MagicMock()
        mock_info.n_ctx_train = 32768

        with (
            patch("stet.llm.model_manager.get_gguf_info_cached", return_value=mock_info),
            patch("stet.llm.model_manager._find_shipped_llama_server", return_value="llama-server.exe"),
            patch("stet.llm.model_manager.subprocess.Popen") as mock_popen,
            patch("stet.llm.model_manager.requests.get") as mock_get,
            patch("stet.llm.model_manager.requests.Session.get") as mock_sess_get,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            mock_health = MagicMock()
            mock_health.status_code = 200
            mock_health.json.return_value = {"status": "ok"}
            mock_get.return_value = mock_health
            mock_sess_get.return_value = mock_health

            with patch.object(manager, "_warmup_prompt_cache"):
                success = manager.load_model()
                assert success is True

            server_call = next(c for c in mock_popen.call_args_list if "--model" in c[0][0])
            call_args = server_call[0][0]
            ctx_idx = call_args.index("--ctx-size")
            assert call_args[ctx_idx + 1] == "12800"

    def test_load_model_context_size_manual(self, mock_config, tmp_path):
        """Verify explicit context_size is preserved when context_size_auto is False."""
        cfg = ConfigManager()
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)

        cfg.set("model_path", str(model_file))
        cfg.set("context_size_auto", False)
        cfg.set("context_size", 4096)

        manager = ModelManager(cfg)

        mock_info = MagicMock()
        mock_info.n_ctx_train = 32768

        with (
            patch("stet.llm.model_manager.get_gguf_info_cached", return_value=mock_info),
            patch("stet.llm.model_manager._find_shipped_llama_server", return_value="llama-server.exe"),
            patch("stet.llm.model_manager.subprocess.Popen") as mock_popen,
            patch("stet.llm.model_manager.requests.get") as mock_get,
            patch("stet.llm.model_manager.requests.Session.get") as mock_sess_get,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            mock_health = MagicMock()
            mock_health.status_code = 200
            mock_health.json.return_value = {"status": "ok"}
            mock_get.return_value = mock_health
            mock_sess_get.return_value = mock_health

            with patch.object(manager, "_warmup_prompt_cache"):
                success = manager.load_model()
                assert success is True

            server_call = next(c for c in mock_popen.call_args_list if "--model" in c[0][0])
            call_args = server_call[0][0]
            ctx_idx = call_args.index("--ctx-size")
            assert call_args[ctx_idx + 1] == "4096"

    def test_load_model_flash_attn_fallback(self, mock_config, tmp_path):
        """Verify flash-attention failure retries with disable_flash_attn=True."""
        cfg = ConfigManager()
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)

        cfg.set("model_path", str(model_file))
        cfg.set("flash_attn", True)
        cfg.set("mtp_enabled", False)

        manager = ModelManager(cfg)

        attempts = []

        def mock_popen_impl(cmd, **kwargs):
            flash_val = cmd[cmd.index("--flash-attn") + 1]
            attempts.append(flash_val)
            if flash_val == "on":
                raise RuntimeError("Flash attention kernel not supported")
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            return mock_proc

        with (
            patch("stet.llm.model_manager.get_gguf_info_cached", return_value=None),
            patch("stet.llm.model_manager._find_shipped_llama_server", return_value="llama-server.exe"),
            patch("stet.llm.model_manager.subprocess.Popen", side_effect=mock_popen_impl),
            patch("stet.llm.model_manager.requests.get") as mock_get,
            patch("stet.llm.model_manager.requests.Session.get") as mock_sess_get,
        ):
            mock_health = MagicMock()
            mock_health.status_code = 200
            mock_health.json.return_value = {"status": "ok"}
            mock_get.return_value = mock_health
            mock_sess_get.return_value = mock_health

            with patch.object(manager, "_warmup_prompt_cache"):
                success = manager.load_model()
                assert success is True
                assert attempts == ["on", "off"]

    def test_vram_precheck_warning_logging(self, mock_config, tmp_path):
        """Verify VRAM pre-check logs warning when estimated need exceeds free VRAM."""
        cfg = ConfigManager()
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 2000)

        cfg.set("model_path", str(model_file))
        manager = ModelManager(cfg)

        logs = []
        with (
            patch("stet.llm.model_manager.has_nvidia", return_value=True),
            patch("stet.llm.model_manager.query_free_vram_mb", return_value=2048),
            patch("stet.llm.model_manager.estimate_model_vram_mb", return_value=8192),
            patch("stet.llm.model_manager.log", side_effect=lambda msg: logs.append(msg)),
            patch("stet.llm.model_manager.get_gguf_info_cached", return_value=None),
            patch("stet.llm.model_manager._find_shipped_llama_server", return_value="llama-server.exe"),
            patch("stet.llm.model_manager.subprocess.Popen") as mock_popen,
            patch("stet.llm.model_manager.requests.get") as mock_get,
            patch("stet.llm.model_manager.requests.Session.get") as mock_sess_get,
        ):
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            mock_health = MagicMock()
            mock_health.status_code = 200
            mock_health.json.return_value = {"status": "ok"}
            mock_get.return_value = mock_health
            mock_sess_get.return_value = mock_health

            with patch.object(manager, "_warmup_prompt_cache"):
                manager.load_model()

            assert any("VRAM pre-check: free=2048 MB, estimated_required=8192 MB" in l for l in logs)
            assert any("WARNING: Model estimated memory (8192 MB) exceeds available free VRAM" in l for l in logs)
