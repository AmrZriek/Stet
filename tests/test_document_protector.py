# -*- coding: utf-8 -*-
"""Tests for the DocumentProtector (§3c).

Parse protected entities into an immutable lookup table, keep in-prompt
placeholders (`[REFN]`), and record exact source offsets for offset-based
splicing by the reassembler.
"""



from stet.llm.document_protector import (
    extract_atoms_from_text,
    restore_atoms,
    validate_atom_survival,
)


def test_atom_table_is_immutable_and_records_offsets():
    t = extract_atoms_from_text("Visit https://example.com now")
    assert len(t.atoms) == 1
    atom = t.atoms[0]
    assert atom.original == "https://example.com"
    # Exact source offset of the matched atom.
    assert atom.start == 6
    assert atom.end == 25
    assert atom.ref == "[REF1]"


def test_placeholder_style_uses_refn():
    t = extract_atoms_from_text("mail me at a@b.com ok")
    assert t.atoms
    for atom in t.atoms:
        assert atom.ref.startswith("[REF")
        assert atom.ref.endswith("]")


def test_replace_returns_placeholder_text():
    t = extract_atoms_from_text("see C:\\Users\\me\\file.txt")
    masked = t.replace_with_placeholders()
    assert "[REF1]" in masked
    assert "C:\\Users\\me\\file.txt" not in masked


def test_restore_atoms_reverts_placeholders():
    text = "call 555-1234 or email a@b.com please"
    t = extract_atoms_from_text(text)
    masked = t.replace_with_placeholders()
    restored = restore_atoms(masked, t)
    assert restored == text


def test_validate_atom_survival_detects_dropped_atom():
    t = extract_atoms_from_text("keep https://a.com and https://b.com")
    masked = t.replace_with_placeholders()
    # A losing output that dropped [REF2] is a violation.
    dropped = masked.replace("[REF2]", "")
    violations = validate_atom_survival(dropped, t)
    assert len(violations) == 1


def test_validate_atom_survival_passes_when_all_present():
    t = extract_atoms_from_text("a https://x.com b https://y.com")
    masked = t.replace_with_placeholders()
    assert validate_atom_survival(masked, t) == []


def test_atom_offsets_are_disjoint_and_sorted():
    t = extract_atoms_from_text("path C:\\a\\b email c@d.com tail 12/34")
    offsets = [(a.start, a.end) for a in t.atoms]
    assert offsets == sorted(offsets)
    for (s1, e1), (s2, e2) in zip(offsets, offsets[1:]):
        assert e1 <= s2


def test_empty_input_has_no_atoms():
    t = extract_atoms_from_text("plain text without hazards")
    assert t.atoms == ()
    assert t.replace_with_placeholders() == "plain text without hazards"
