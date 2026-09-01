# -*- coding: utf-8 -*-
"""DocumentProtector (Phase 3c).

Parses protected entities (URLs, emails, absolute/relative paths, user terms)
into an immutable atom table, keeps in-prompt placeholders (``[REFN]``), and
records exact source offsets so the reassembler can splice by character offset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from stet.core.text_utils import _INLINE_HAZARD_RE


@dataclass(frozen=True)
class AtomEntry:
    """A protected atom with its exact source offsets and placeholder ref."""
    original: str
    start: int
    end: int
    ref: str


@dataclass(frozen=True)
class AtomTable:
    """Immutable collection of atoms extracted from one document, plus the source."""
    source: str
    atoms: tuple[AtomEntry, ...]

    def replace_with_placeholders(self) -> str:
        """Return the source text with each atom replaced by its ``[REFN]`` placeholder."""
        result = self.source
        # Splice from the last atom to the first so earlier offsets stay valid.
        for atom in sorted(self.atoms, key=lambda a: a.start, reverse=True):
            result = result[: atom.start] + atom.ref + result[atom.end :]
        return result

    def validate_survival(self, output: str) -> list[str]:
        """Return refs that are missing from ``output`` (dropped atoms)."""
        return [a.ref for a in self.atoms if a.ref not in output]


def extract_atoms_from_text(text: str) -> AtomTable:
    """Extract protected atoms (URLs, emails, paths) with exact offsets."""
    atoms: list[AtomEntry] = []
    for m in _INLINE_HAZARD_RE.finditer(text):
        if m.start() == m.end():
            continue
        ref = f"[REF{len(atoms) + 1}]"
        atoms.append(AtomEntry(original=m.group(0), start=m.start(), end=m.end(), ref=ref))
    return AtomTable(source=text, atoms=tuple(atoms))


def restore_atoms(masked: str, table: AtomTable) -> str:
    """Replace ``[REFN]`` placeholders in ``masked`` with the original atom text."""
    result = masked
    for atom in table.atoms:
        result = result.replace(atom.ref, atom.original)
    return result


def validate_atom_survival(output: str, table: AtomTable) -> list[str]:
    """Return a list of atom refs that were dropped (not survived) in ``output``."""
    return table.validate_survival(output)


@dataclass(frozen=True)
class PlaceholderStyle:
    """The placeholder convention the protector uses."""
    style: str = "refn"