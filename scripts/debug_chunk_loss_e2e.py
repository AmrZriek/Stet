"""End-to-end repro: sentence chunking deletes text via preamble-stripping.

Reproduces: a chunk whose first sentence legitimately starts with a
preamble-like phrase (e.g. "The corrected version ...", "Here is the
corrected version ...") has that phrase deleted from the final output
because _extract_rewritten_sentence -> strip_meta_commentary treats the
real sentence as model commentary.

Run: python scripts/debug_chunk_loss_e2e.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stet.llm.model_manager import ModelManager
from stet.core.text_utils import _chunk_text_by_sentences
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
    mgr = ModelManager(cfg)
    return mgr


def _echo_session():
    """Session whose .post() echoes the CONTENT_BEGIN...CONTENT_END chunk back verbatim."""
    session = MagicMock()
    def post(url, json=None, timeout=60):
        messages = json["messages"]
        # chunk lives in the last user message's CONTENT_BEGIN block
        content = messages[-1]["content"]
        chunk = content.split("CONTENT_BEGIN\n", 1)[1].rsplit("\nCONTENT_END", 1)[0]
        return MockResponse({"choices": [{"message": {"content": chunk}, "finish_reason": "stop"}]})
    session.post.side_effect = post
    return session


def main():
    # Build a long document (> chunk_words=250) so it chunks.
    # 25 filler sentences pack chunk 0; the vulnerable sentence is the FIRST
    # sentence of chunk 1, so its leading phrase is at the start of that
    # chunk's model output -> subject to strip_meta_commentary.
    filler = ("The quarterly report was submitted to the board on time. " * 25)
    more = ("The finance team will review the final numbers next week. " * 5)

    scenarios = {
        "short phrase (6 words)": "The corrected version was released on Friday. Send it out immediately.",
        "long phrase (15 words)": (
            "Here is the corrected version of the final quarterly report that the board "
            "reviewed and approved yesterday. Send it out immediately."
        ),
    }

    for label, vulnerable in scenarios.items():
        text = filler + vulnerable + " " + more
        chunked = _chunk_text_by_sentences(text, 250)
        vulnerable_starts_chunk = any(c.startswith(vulnerable[:25]) for c, _ in chunked[1:])
        print(f"\n=== {label} (vulnerable-starts-chunk={vulnerable_starts_chunk}, "
              f"total words={len(text.split())}) ===")
        for strength in ("spelling_only", "full_correction", "rewrite_polish", "template_transform"):
            mgr = _build_mgr()
            session = _echo_session()
            with patch.object(mgr, "is_loaded", return_value=True), \
                 patch.object(mgr, "_get_session", return_value=session), \
                 patch.object(mgr, "mark_used", lambda: None), \
                 patch.object(mgr, "status_changed", MagicMock()), \
                 patch.object(mgr, "label", f"test-{strength}"):
                res = mgr.correct_text_patch(text, strength=strength)

            out = res.text
            first_sentence = vulnerable.split(".")[0]
            lost = first_sentence not in out
            print(f"  {strength}: outcome={res.outcome.value} units={res.units_processed} "
                  f"corrected={res.units_corrected} LOST_LEADING_PHRASE={lost}")
            if res.reason:
                print(f"      reason: {res.reason!r}")


if __name__ == "__main__":
    main()
