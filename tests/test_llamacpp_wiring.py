"""Settings-GUI -> llama.cpp wiring guard.

Every sampling knob the settings UI exposes must actually reach the
bundled llama-server (b10639), either as a CLI default or as a per-request
JSON field. This broke silently before: TFS-Z was removed upstream and the
server ignored ``tfs_z`` without error, so the GUI pretended to work.

Run this after every llama.cpp bump. If upstream renames a flag/field,
add the new name here and fix model_manager + settings_pages together.
"""

from unittest.mock import MagicMock, patch

from stet.constants import DEFAULT_CONFIG
from stet.llm.model_manager import ModelManager


def _cfg(extra=None):
    data = dict(DEFAULT_CONFIG)
    data.update(extra or {})
    cfg = MagicMock()
    cfg.get = lambda key, default=None: data.get(key, default)
    cfg.set = MagicMock()
    return cfg


def _launch_cmd(cfg, tmp_path, monkeypatch):
    """Run the real load_model CLI builder with subprocess/network stubbed."""
    import stet.llm.model_manager as mm

    model = tmp_path / "m.gguf"
    model.touch()
    server = tmp_path / "llama-server.exe"
    server.touch()
    cfg.get = (lambda _g=cfg.get: (lambda k, d=None: str(server) if k == "llama_server_path" else (str(model) if k in ("model_path", "ac_model_path") else _g(k, d))))()
    monkeypatch.setattr(mm, "get_gguf_info_cached", lambda p: None)

    with patch("subprocess.Popen") as mock_popen, patch("requests.get") as mock_get:
        health = MagicMock(status_code=200)
        props = MagicMock(ok=True)
        props.json.return_value = {"n_ctx": 4096}
        mock_get.side_effect = lambda url, **kw: props if "/props" in url else health
        proc = MagicMock()
        proc.poll.return_value = None
        mock_popen.return_value = proc
        mgr = ModelManager(cfg)
        assert mgr.load_model() is True
        # load_model may shell out first (e.g. --list-devices probe);
        # the server launch is the call carrying --model.
        launches = [
            c[0][0] for c in mock_popen.call_args_list if "--model" in c[0][0]
        ]
        assert launches, f"no server launch among Popen calls: {mock_popen.call_args_list}"
        return launches[-1]


class TestServerLaunchCommandWiring:
    """Extend TestServerLaunchCommand: penalty + sampling CLI coverage."""
    def test_penalty_flags_on_cli(self, tmp_path, monkeypatch):
        cfg = _cfg({"frequency_penalty": 0.5, "presence_penalty": 0.3})
        cmd = _launch_cmd(cfg, tmp_path, monkeypatch)
        assert cmd[cmd.index("--frequency-penalty") + 1] == "0.5"
        assert cmd[cmd.index("--presence-penalty") + 1] == "0.3"

    def test_core_sampling_defaults_on_cli(self, tmp_path, monkeypatch):
        cmd = _launch_cmd(_cfg(), tmp_path, monkeypatch)
        for flag in ("--temp", "--top-k", "--top-p", "--min-p", "--repeat-penalty"):
            assert flag in cmd, f"{flag} missing from server CLI"
        for flag in ("--ctx-size", "--n-gpu-layers", "--threads", "--batch-size",
                     "--ubatch-size", "--flash-attn", "--parallel"):
            assert flag in cmd, f"{flag} missing from server CLI"


class TestPayloadWiring:
    def _mgr(self):
        return ModelManager(_cfg())

    def test_stream_worker_has_no_tfs_z(self):
        mgr = self._mgr()
        worker = mgr.make_stream_worker([{"role": "user", "content": "hi"}], max_tokens=8)
        assert "tfs_z" not in worker.payload
        for field in ("temperature", "top_k", "top_p", "min_p", "typical_p",
                      "mirostat", "mirostat_tau", "mirostat_eta",
                      "repeat_penalty", "frequency_penalty", "presence_penalty",
                      "seed", "cache_prompt"):
            assert field in worker.payload, f"{field} missing from chat payload"

    def test_engine_payload_parity_no_tfs_z(self):
        mgr = self._mgr()
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class FakeSession:
            def post(self, url, json=None, timeout=None):
                captured.update(json)
                return FakeResp()

        mgr._get_session = lambda: FakeSession()  # noqa: SLF001
        # CorrectionEngineImpl stores provider as _inference_fn or inference_provider
        real_engine = mgr.get_correction_engine()
        fn = getattr(real_engine, "_inference_fn", None) or getattr(
            real_engine, "inference_provider", None
        )
        assert fn is not None
        fn([{"role": "user", "content": "hi"}], 8)
        assert "tfs_z" not in captured
        for field in ("seed", "typical_p", "mirostat", "mirostat_tau",
                      "mirostat_eta", "repeat_penalty", "frequency_penalty",
                      "presence_penalty"):
            assert field in captured, f"{field} missing from engine payload"
