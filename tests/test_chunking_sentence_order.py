"""Sentence-order integrity: abbreviation chunking + hunk-guard hardening.

Regression coverage for the 2026-08-18 deep-investigation dossier:

* `apply_hunk_guard` accepted unbounded `replace` opcodes in full_correction
  mode, letting a 1-word typo balloon into a 10-word hallucinated clause.
* A multi-word delete reversion followed by an adjacent corrected insert
  emitted the original phrase AND the model's replacement (Frankenstein
  duplication: "please investigatre. Please investigate.").
* `_chunk_text_by_sentences` split sentences at abbreviations, middle
  initials, and decimals, sending orphaned fragments to LLM slots.
"""

from stet.core.text_utils import (
    _chunk_text_by_sentences,
    apply_hunk_guard,
)


# ── 1. Abbreviation preservation in chunking ─────────────────────────────────


def test_abbreviation_preservation_in_chunking():
    """Dr. / e.g. / middle initials / decimals must not split across chunks."""
    # Chained abbreviations: Dr. → e.g. → rest of sentence.
    text = "We should check Dr. Smith and e.g. the report before deciding. Next sentence."
    chunks = _chunk_text_by_sentences(text, 60)
    assert "".join(chunk + sep for chunk, sep in chunks) == text
    assert len(chunks) == 1
    assert chunks[0][0] == text

    # Middle initial.
    text2 = "John F. Kennedy was president. So was Lincoln."
    chunks2 = _chunk_text_by_sentences(text2, 60)
    assert "".join(chunk + sep for chunk, sep in chunks2) == text2
    assert len(chunks2) == 1

    # Decimal with a space: "version 2. 0" must stay whole.
    text3 = "The answer is version 2. 0 or higher. Next."
    chunks3 = _chunk_text_by_sentences(text3, 60)
    assert "".join(chunk + sep for chunk, sep in chunks3) == text3
    assert len(chunks3) == 1

    # Version token without space after the dot never split anyway.
    text4 = "Upgrade v2.5 now. The end."
    chunks4 = _chunk_text_by_sentences(text4, 60)
    assert "".join(chunk + sep for chunk, sep in chunks4) == text4
    assert len(chunks4) == 1


def test_abbreviation_merge_never_crosses_paragraph_break():
    """Abbreviation merging stops at blank-line boundaries."""
    text = "Dr. Smith.\n\nNext paragraph."
    chunks = _chunk_text_by_sentences(text, 60)
    assert "".join(chunk + sep for chunk, sep in chunks) == text
    assert chunks == [("Dr. Smith.", "\n\n"), ("Next paragraph.", "")]


# ── 2. Ballooning-replace bound ──────────────────────────────────────────────


def test_hunk_guard_rejects_ballooning_replace():
    """A 1-word typo expanding into a long invented clause is reverted."""
    orig = "it randomly moved the last sentyenc"
    corr = "it randomly moved the last sentence and repeated it in the middle of the paragraph"
    out = apply_hunk_guard(orig, corr, 1)
    assert "repeated it in the middle" not in out
    assert "sentyenc" in out


def test_hunk_guard_accepts_bounded_expansions():
    """Legit 1→2 word expansions and punctuation/case replaces still pass."""
    # "hteres" → "there is" (1 → 2 words) is a normal correction.
    assert apply_hunk_guard("hteres", "there is", 1) == "there is"
    assert apply_hunk_guard("itrs", "it is", 1) == "it is"
    # Case-only and punctuation-only replaces are untouched by the bound.
    assert apply_hunk_guard("now", "Now", 1) == "Now"
    assert apply_hunk_guard("chunking, it", "chunking; it", 1) == "chunking; it"
    # Exactly +2 words is still within the bound.
    assert apply_hunk_guard("wont", "will not", 1) == "will not"


# ── 3. No duplicated original + corrected text ───────────────────────────────


def test_hunk_guard_no_duplicate_sentence_splicing():
    """A reverted multi-word delete must not be followed by the model's
    re-added phrase (delete → equal → insert adjacency)."""
    orig = "please investigatre the end"
    corr = "the end Please"
    out = apply_hunk_guard(orig, corr, 1)
    # The reverted clause stays; the corrected duplicate is suppressed.
    assert out == "please investigatre the end"
    assert out.count("please") == 1


def test_hunk_guard_suppression_is_overlap_based():
    """An unrelated single-word insert after a reverted delete still passes."""
    orig = "please investigatre the end"
    corr = "the end now"
    out = apply_hunk_guard(orig, corr, 1)
    assert out == "please investigatre the end now"


# ── 4. End-to-end sentence integrity through correct_text_patch ──────────────


def test_end_to_end_full_correction_sentence_integrity(monkeypatch, tmp_path):
    """The real user case: hallucinated clause insertion + clause duplication
    are both blocked while valid typo fixes flow through."""
    import stet.core.config as config_mod
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    manager = ModelManager(cm)
    monkeypatch.setattr(manager, "is_loaded", lambda: True)

    # Replay the model's raw output from the forensic log: typos corrected,
    # plus a hallucinated clause inserted via a ballooning replace.
    monkeypatch.setattr(
        manager,
        "_rewrite_sentence_chunk",
        lambda chunk_text, *a, **k:
            "Now there is an issue with how it is chunking; it randomly moved "
            "the last sentence and repeated it in the middle of the paragraph. "
            "Please investigate. Load superpowers",
    )

    text = (
        "now hteres an issue with hows itrs chunking, it randomly moved "
        "the last sentyenc, please investigatre. Load superpowers"
    )
    res, units = manager.correct_text_patch(text, strength="full_correction")

    assert res is not None
    assert isinstance(res, str)
    assert units == 1
    # Hallucinated clause and its corrected duplicate are gone.
    assert "repeated it in the middle" not in res
    assert "Please investigate" not in res
    # Valid fixes and sentence order survive.
    assert "there is" in res
    assert "sentence" in res
    assert "Load superpowers" in res
    assert res.index("chunking") < res.index("Load superpowers")


def test_end_to_end_multi_chunk_order_preservation(monkeypatch, tmp_path):
    """Two paragraphs corrected in parallel keep their order and never gain
    hallucinated clauses."""
    import stet.core.config as config_mod
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    manager = ModelManager(cm)
    monkeypatch.setattr(manager, "is_loaded", lambda: True)

    def mock_rewrite(chunk_text, *a, **k):
        if "sentyenc" in chunk_text:
            # Ballooning replace attempt: 1 typo word → invented clause.
            return chunk_text.replace(
                "sentyenc",
                "sentence and repeated it in the middle of the paragraph",
            )
        if "investigatre" in chunk_text:
            return chunk_text.replace("investigatre", "investigate")
        return chunk_text

    monkeypatch.setattr(manager, "_rewrite_sentence_chunk", mock_rewrite)

    text = (
        "now hteres an issue with hows itrs chunking, it randomly moved "
        "the last sentyenc.\n\nplease investigatre. Load superpowers"
    )
    res, units = manager.correct_text_patch(text, strength="full_correction")

    assert res is not None
    assert isinstance(res, str)
    assert units == 2
    assert "repeated it in the middle" not in res
    # Paragraph order is preserved; the valid fix in chunk 2 lands.
    assert "investigate." in res
    assert "Load superpowers" in res
    assert res.index("chunking") < res.index("please investigate")
    assert res.index("please investigate") < res.index("Load superpowers")
