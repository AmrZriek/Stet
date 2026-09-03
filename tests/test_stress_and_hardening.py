"""Targeted stress, penetration, and edge-case hardening regression tests."""

from pathlib import Path

from stet.core.document_protector import DocumentProtector
from stet.core.validators import UnitValidator, GuardSet
from stet.core.reassembler import OffsetReassembler
from stet.core.context_planner import ContextPlanner


def test_document_protector_cascading_placeholder_safety():
    """Verify that an atom containing another placeholder does not trigger cascading replacement."""
    # Text with a URL that happens to contain a query parameter with "[REF2]"
    text = "Visit https://example.com/item?q=[REF2] and then check C:\\Program Files\\App."
    doc = DocumentProtector.protect(text)
    assert len(doc.atoms) >= 2

    # In model output, both placeholders appear
    output_with_placeholders = doc.masked_text
    restored, all_ok = doc.restore(output_with_placeholders)

    # Restored text must match original exactly without cascading or corrupting
    assert all_ok is True
    assert restored == text


def test_unit_validator_rejects_hallucinated_placeholders():
    """Verify that UnitValidator rejects markers invented by the model."""
    inp = "Correct this: [REF1] is working."
    out_hallucinated = "Correct this: [REF1] is working and also [REF99]."
    outcome = UnitValidator.validate(inp, out_hallucinated, GuardSet(preserve_reference_markers=True))
    assert outcome.valid is False
    assert "Hallucinated reference markers" in outcome.reason


def test_unit_validator_rejects_duplicated_placeholders():
    """Verify that UnitValidator rejects duplicated placeholders."""
    inp = "This is [REF1]."
    out_duplicated = "This is [REF1] and [REF1]."
    outcome = UnitValidator.validate(inp, out_duplicated, GuardSet(preserve_reference_markers=True))
    assert outcome.valid is False
    assert "duplicated" in outcome.reason


def test_context_planner_splits_lowercase_and_cjk_sentences():
    """Verify that sentences starting with lowercase letters or CJK are properly segmented."""
    text = "first sentence. second sentence. third sentence."
    sentences, spans = ContextPlanner._split_sentences(text)
    assert len(sentences) == 3
    cjk_text = "这是第一句话。这是第二句话！这是第三句话？"
    cjk_sentences, cjk_spans = ContextPlanner._split_sentences(cjk_text)
    assert len(cjk_sentences) == 3


def test_reassembler_preserves_paragraph_breaks():
    """Verify that OffsetReassembler preserves paragraph newlines between windows."""
    doc_text = "Paragraph one with some text.\n\nParagraph two with some text."
    doc = DocumentProtector.protect(doc_text)
    plan = ContextPlanner.plan(doc.masked_text)
    # If monolithic, directly restores
    reassembled, ok = OffsetReassembler.reassemble(plan, [doc.masked_text], doc)
    assert ok is True
    assert "\n\n" in reassembled


def test_config_backup_on_corruption(tmp_path: Path, monkeypatch):
    from stet.core import config as cfg_module
    corrupt_file = tmp_path / "config.json"
    corrupt_file.write_text("{\"broken\": [1, 2,", encoding="utf-8")
    monkeypatch.setattr(cfg_module, "CONFIG_FILE", corrupt_file)
    cfg = cfg_module.ConfigManager()
    assert cfg is not None
    backup_file = tmp_path / "config.json.corrupt.bak"
    assert backup_file.exists()
    assert backup_file.read_text(encoding="utf-8") == "{\"broken\": [1, 2,"
