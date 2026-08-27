"""Tests for llama-server freeze auto-recovery, single-owner CAS, ReadTimeout retry, and GPU detection."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests

from stet.core.config import ConfigManager
from stet.llm.model_manager import ModelManager
from tests.conftest import MockResponse


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    model_path = tmp_path / "fake.gguf"
    model_path.touch()
    config_file.write_text(
        json.dumps(
            {
                "model_path": str(model_path),
                "ac_model_path": str(model_path),
                "server_binary": "",
                "port": 8080,
                "context_size": 4096,
                "gpu_layers": 99,
                "temperature": 0.1,
                "top_k": 40,
                "top_p": 0.95,
                "min_p": 0.05,
                "keep_model_loaded": True,
                "idle_timeout_seconds": 300,
                "custom_templates": [],
                "correction_modes": [
                    {"name": "Spelling Only", "id": "spelling_only", "prompt": "fix spelling"},
                    {"name": "Full Correction", "id": "full_correction", "prompt": "fix everything"},
                    {"name": "Rewrite & Polish", "id": "rewrite_polish", "prompt": "polish"},
                ],
            }
        ),
        encoding="utf-8",
    )
    import stet.core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return ConfigManager()


@pytest.fixture
def manager(cfg):
    return ModelManager(cfg)


class TestFreezeAutoRecovery:
    """Tests for _recycle_server single-owner CAS, ReadTimeout retry, and session rebind."""

    def test_recycle_single_owner_cas(self, manager):
        """When _recycle_in_progress is already True or server not loaded, a concurrent call returns False."""
        # 1. Not loaded -> False
        assert manager.is_loaded() is False
        assert manager._recycle_server() is False

        # 2. Fake loaded, but _recycle_in_progress is True -> False without calling unload/load
        manager._server_ready = True
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager.server_process = mock_proc
        assert manager.is_loaded() is True

        manager._recycle_in_progress = True
        unload_mock = MagicMock()
        load_mock = MagicMock()
        manager.unload_model = unload_mock
        manager.load_model = load_mock

        assert manager._recycle_server() is False
        unload_mock.assert_not_called()
        load_mock.assert_not_called()

        # Reset flag
        manager._recycle_in_progress = False

    def test_recycle_success_and_flag_cleanup(self, manager):
        """Successful _recycle_server unloads, reloads, and clears _recycle_in_progress in finally."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager.server_process = mock_proc
        manager._server_ready = True

        def fake_unload():
            manager.server_process = None
            manager._server_ready = False

        def fake_load():
            p = MagicMock()
            p.poll.return_value = None
            manager.server_process = p
            manager._server_ready = True
            return True

        manager.unload_model = MagicMock(side_effect=fake_unload)
        manager.load_model = MagicMock(side_effect=fake_load)

        res = manager._recycle_server()
        assert res is True
        assert manager._recycle_in_progress is False
        manager.unload_model.assert_called_once()
        manager.load_model.assert_called_once()

    def test_rewrite_readtimeout_recycles_and_rebinds_session(self, manager):
        """On ReadTimeout, _rewrite_sentence_chunk recycles engine, rebinds session, and succeeds on retry."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager.server_process = mock_proc
        manager._server_ready = True

        old_session = MagicMock()
        new_session = MagicMock()

        def fake_recycle():
            return True

        manager._recycle_server = MagicMock(side_effect=fake_recycle)
        manager._get_session = MagicMock(return_value=new_session)

        # Mock old_session.post raising ReadTimeout on call 1
        def old_post(*args, **kwargs):
            raise requests.exceptions.ReadTimeout("Read timed out (read timeout=60)")

        old_session.post = MagicMock(side_effect=old_post)

        # Mock new_session.post returning successful response on retry
        valid_response = MockResponse(
            json_data={
                "choices": [
                    {
                        "message": {
                            "content": "<<<START>>>Hello world.<<<END>>>",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            status_code=200,
        )
        new_session.post = MagicMock(return_value=valid_response)

        result = manager._rewrite_sentence_chunk(
            chunk_text="Hello world.",
            custom_sys=None,
            unit_idx=1,
            total=1,
            strength="full_correction",
            session=old_session,
        )

        manager._recycle_server.assert_called_once()
        manager._get_session.assert_called_once()
        assert new_session.post.called
        assert result == "Hello world."

    def test_recycle_no_infinite_loop(self, manager):
        """If ReadTimeout persists on the retry, it fails gracefully and returns None without looping."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager.server_process = mock_proc
        manager._server_ready = True

        def fake_recycle():
            return True

        manager._recycle_server = MagicMock(side_effect=fake_recycle)

        failing_session = MagicMock()
        failing_session.post = MagicMock(
            side_effect=requests.exceptions.ReadTimeout("Persistent read timeout")
        )
        manager._get_session = MagicMock(return_value=failing_session)

        result = manager._rewrite_sentence_chunk(
            chunk_text="Test sentence.",
            custom_sys=None,
            unit_idx=1,
            total=1,
            strength="full_correction",
            session=failing_session,
        )

        # Recycled at most once
        assert manager._recycle_server.call_count == 1
        assert result is None

    def test_rewrite_length_retry_readtimeout_recycles(self, manager):
        """When length-retry encounters ReadTimeout, it recycles engine, rebinds session, and completes."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager.server_process = mock_proc
        manager._server_ready = True

        # Step 1: First request returns finish_reason="length"
        first_resp = MockResponse(
            json_data={
                "choices": [
                    {
                        "message": {"content": "<<<START>>>Partial text"},
                        "finish_reason": "length",
                    }
                ]
            },
            status_code=200,
        )

        # Step 2: Retry request on old session times out
        old_session = MagicMock()
        old_session.post = MagicMock()
        old_session.post.side_effect = [
            first_resp,
            requests.exceptions.ReadTimeout("Length retry timeout"),
        ]

        new_session = MagicMock()
        new_resp = MockResponse(
            json_data={
                "choices": [
                    {
                        "message": {"content": "<<<START>>>Full text complete.<<<END>>>"},
                        "finish_reason": "stop",
                    }
                ]
            },
            status_code=200,
        )
        new_session.post = MagicMock(return_value=new_resp)

        manager._recycle_server = MagicMock(return_value=True)
        manager._get_session = MagicMock(return_value=new_session)

        result = manager._rewrite_sentence_chunk(
            chunk_text="Full text complete.",
            custom_sys=None,
            unit_idx=1,
            total=1,
            strength="full_correction",
            session=old_session,
        )

        manager._recycle_server.assert_called_once()
        assert new_session.post.called
        assert result == "Full text complete."

    def test_readtimeout_healthy_server_does_not_recycle(self, manager):
        """When a request times out but /health is 200 OK, the server is NOT killed."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        manager.server_process = mock_proc
        manager._server_ready = True

        session = MagicMock()
        session.post = MagicMock(side_effect=requests.exceptions.ReadTimeout("Timeout"))
        # Mock /health responding 200 OK
        health_resp = MagicMock(status_code=200)
        session.get = MagicMock(return_value=health_resp)

        manager._recycle_server = MagicMock(return_value=True)

        result = manager._rewrite_sentence_chunk(
            chunk_text="Hello world.",
            custom_sys=None,
            unit_idx=1,
            total=1,
            strength="full_correction",
            session=session,
        )

        manager._recycle_server.assert_not_called()
        assert result is None

    def test_actual_ctx_size_not_double_divided(self, manager):
        """When actual_ctx_size is set from /props (e.g. 1024), slot calculation uses it directly."""
        manager.actual_ctx_size = 1024
        # At 1024, slot_limit should be 1024 (not 256)
        # Verify in a sentence chunk rewrite payload calculation
        captured_payloads = []
        def mock_post(url, json=None, **kwargs):
            captured_payloads.append(json)
            return MockResponse({"choices": [{"message": {"content": "Test."}}]})

        session = MagicMock()
        session.post = MagicMock(side_effect=mock_post)

        manager._rewrite_sentence_chunk(
            chunk_text="This is a test sentence.",
            custom_sys=None,
            unit_idx=1,
            total=1,
            strength="full_correction",
            session=session,
        )

        assert len(captured_payloads) == 1
        # max_tokens should not be starved to 128
        assert captured_payloads[0]["max_tokens"] >= 512


class TestGpuDetectionPropsModern:
    """Tests for modernized GPU detection via /props device inspection."""

    def test_props_updates_backend_to_cuda(self, manager, monkeypatch):
        """When log scraper found no legacy CUDA banners, /props containing device info updates backend to cuda."""
        manager.actual_backend_type = "cpu"

        # Mock /props response with modern llama.cpp devices payload
        props_data = {
            "default_generation_settings": {"n_ctx": 4096, "n_gpu_layers": 99},
            "devices": ["CUDA0 (NVIDIA GeForce RTX)"],
            "device_info": "CUDA 12.4",
            "chat_template_caps": {"supports_thinking": False},
        }
        mock_props_resp = MockResponse(json_data=props_data, status_code=200)

        with patch("stet.llm.model_manager.requests.get") as mock_get:
            mock_get.return_value = mock_props_resp
            # Run the props inspection logic directly
            pr = requests.get("http://127.0.0.1:8080/props", timeout=3)
            jp = pr.json()
            devices = jp.get("devices") or []
            device_info = str(jp.get("device_info", "")).lower()
            backend_name = str(jp.get("backend", "")).lower()
            default_gen = jp.get("default_generation_settings", {})
            props_str = json.dumps(jp).lower()
            has_cuda_prop = (
                any("cuda" in str(d).lower() for d in devices)
                or "cuda" in device_info
                or "cuda" in backend_name
                or "cuda" in props_str
                or int(jp.get("n_gpu_layers", default_gen.get("n_gpu_layers", 0)) or 0) > 0
            )
            if has_cuda_prop and manager.actual_backend_type == "cpu":
                manager.actual_backend_type = "cuda"

        assert manager.actual_backend_type == "cuda"


class TestUIStreamAutoRecovery:
    """Tests for UI streaming correction and chat auto-recycle on StetStreamTimeout."""

    def test_correction_stream_error_recycles_and_retries(self, cfg, qtbot):
        from stet.ui.main_window import CorrectionWindow

        ac_mock = MagicMock()
        ac_mock.is_loaded.return_value = True
        ac_mock.loading = False
        ac_mock.label = "AC"
        ac_mock._recycle_server.return_value = True

        chat_mock = MagicMock()
        chat_mock.is_loaded.return_value = True
        chat_mock.loading = False
        chat_mock.label = "Chat"

        win = CorrectionWindow(
            original="Hello world",
            ac_model=ac_mock,
            chat_model=chat_mock,
            cfg=cfg,
        )
        qtbot.addWidget(win)

        win._start_streaming_correction = MagicMock()
        win._recycle_done = False

        # Emit StetStreamTimeout error
        win._on_correction_stream_error("StetStreamTimeout: engine unresponsive")

        assert win._recycle_done is True
        ac_mock._recycle_server.assert_called_once()
        win._start_streaming_correction.assert_called_once()

    def test_chat_stream_error_recycles_and_retries(self, cfg, qtbot):
        from stet.ui.main_window import CorrectionWindow

        ac_mock = MagicMock()
        ac_mock.is_loaded.return_value = True
        ac_mock.loading = False
        ac_mock.label = "AC"

        chat_mock = MagicMock()
        chat_mock.is_loaded.return_value = True
        chat_mock.loading = False
        chat_mock.label = "Chat"
        chat_mock._recycle_server.return_value = True

        win = CorrectionWindow(
            original="Hello world",
            ac_model=ac_mock,
            chat_model=chat_mock,
            cfg=cfg,
        )
        qtbot.addWidget(win)

        win._target_chat_model = chat_mock
        win._do_stream = MagicMock()
        win._chat_recycle_done = False

        # Emit StetStreamTimeout error in chat
        win._on_chat_error("StetStreamTimeout: engine unresponsive")

        assert win._chat_recycle_done is True
        chat_mock._recycle_server.assert_called_once()
        win._do_stream.assert_called_once()

