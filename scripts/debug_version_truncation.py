"""Test Gemini's claim: does the pipeline truncate version numbers (1.2.2 -> 1.2.)?

Probes chunking + dedup + full correct_text_patch (verbatim-echo mock) on
version-number-heavy text across all correction strengths.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stet.llm.model_manager import ModelManager
from tests.conftest import MockResponse


def _build_mgr():
    cfg = MagicMock()
    def get(key, default=None):
        d = {
            "model_path": "C:/models/fake.gguf",
            "ac_model_path": "C:/models/fake.gguf",
            "server_host": "127.0.0.1",
            "server_port": 8080,
            "context_size": 12800,
            "parallel": 4,
            "temperature": 0.0,
            "top_k": 1,
            "top_p": 0.95,
            "min_p": 0.0,
            "seed": -1,
            "typical_p": 1.0,
            "tfs_z": 1.0,
            "mirostat": 0,
            "mirostat_tau": 5.0,
            "mirostat_eta": 0.1,
            "repeat_penalty": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "cache_prompt": True,
            "streaming_strength": "full_correction",
            "correction_modes": [],
        }
        return d.get(key, default)
    cfg.get = get
    cfg.set = MagicMock()
    return ModelManager(cfg)


def _echo_session():
    session = MagicMock()
    def post(url, json=None, timeout=60):
        messages = json["messages"]
        content = messages[-1]["content"]
        chunk = content.split("CONTENT_BEGIN\n", 1)[1].rsplit("\nCONTENT_END", 1)[0]
        return MockResponse({"choices": [{"message": {"content": chunk}, "finish_reason": "stop"}]})
    session.post.side_effect = post
    return session


SCENARIOS = {
    "gemini example": "This will be 1.2.2 Also, there's a tiny commit for the release.",
    "release notes (repeats)": (
        "Stet 1.2.2 was released today. Version 3.1.4 of the SDK is out. "
        "Fixes were applied to 2.0.1 as well. The 1.2.2 build fixed the crash. "
        "Next up is 4.0.0 beta."
    ),
    "trailing periods after numbers": (
        "In 2026. Next sentence starts fresh. The total is 42. Let us move on. "
        "Pi is 3.14159. That is an approximation."
    ),
}


def main():
    for label, text in SCENARIOS.items():
        print(f"=== {label} ===")
        for strength in ("spelling_only", "full_correction", "rewrite_polish", "template_transform"):
            mgr = _build_mgr()
            with patch.object(mgr, "is_loaded", return_value=True), \
                 patch.object(mgr, "_get_session", return_value=_echo_session()), \
                 patch.object(mgr, "mark_used", lambda: None), \
                 patch.object(mgr, "status_changed", MagicMock()), \
                 patch.object(mgr, "label", f"test-{strength}"):
                res = mgr.correct_text_patch(text, strength=strength)
            out = res.text
            changed = out != text
            trunc = "1.2." in out and "1.2.2" not in out  # trailing digit eaten
            digits_ok = all(v in out for v in ("1.2.2", "3.1.4", "2.0.1", "4.0.0", "3.14159", "2026"))
            print(f"  {strength}: changed={changed} version_truncated={trunc} "
                  f"all_numbers_kept={digits_ok} reason={res.reason!r}")


if __name__ == "__main__":
    main()
