"""DocumentProtector & Immutable Atom Table (Phase 3c).

Protects sensitive entities (URLs, file paths, emails, code blocks, inline code,
and user-defined terms) by replacing them with deterministic, in-prompt reference
placeholders ([REF1], [REF2], ...) and recording exact source character spans in
an immutable atom table for lossless restoration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from stet.core.text_utils import _INLINE_HAZARD_RE, build_user_protection_re

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_PLACEHOLDER_RE = re.compile(r"\[REF(\d+)\]")


@dataclass(frozen=True, slots=True)
class ProtectedAtom:
    """An immutable record of a protected entity and its original source location."""

    id: int
    placeholder: str
    original_text: str
    span: Tuple[int, int]
    kind: str


@dataclass(frozen=True, slots=True)
class ProtectedDocument:
    """A document whose protected entities have been replaced with placeholders."""

    original_text: str
    masked_text: str
    atoms: Tuple[ProtectedAtom, ...]
    atom_map: Dict[str, ProtectedAtom]

    @property
    def has_protected_atoms(self) -> bool:
        return len(self.atoms) > 0

    def restore(self, text: str) -> Tuple[str, bool]:
        """Restore all original atom contents back into the generated text.

        Returns (restored_text, all_atoms_preserved).
        """
        if not self.atoms:
            return text, True

        all_preserved = True
        restored = text

        # Check which atoms appear in text
        present_placeholders = set(_PLACEHOLDER_RE.findall(restored))

        for atom in self.atoms:
            str_id = str(atom.id)
            if str_id not in present_placeholders and atom.placeholder not in restored:
                all_preserved = False

            # Replace both exact [REFN] and any slightly mangled variations
            restored = restored.replace(atom.placeholder, atom.original_text)

        return restored, all_preserved


class DocumentProtector:
    """Detects, extracts, and masks protected entities into an immutable atom table."""

    @classmethod
    def protect(
        cls,
        text: str,
        user_terms: Optional[Sequence[str]] = None,
        protect_code_blocks: bool = True,
    ) -> ProtectedDocument:
        """Scan text and replace sensitive entities with [REF1], [REF2], ..."""
        if not text:
            return ProtectedDocument(
                original_text="",
                masked_text="",
                atoms=(),
                atom_map={},
            )

        spans: List[Tuple[int, int, str, str]] = []  # (start, end, original_text, kind)

        # 1. Code blocks (highest precedence)
        if protect_code_blocks:
            for match in _CODE_FENCE_RE.finditer(text):
                spans.append((match.start(), match.end(), match.group(0), "code_fence"))

            for match in _INLINE_CODE_RE.finditer(text):
                # Don't overlap with existing code fences
                start, end = match.start(), match.end()
                if not any(s <= start and end <= e for s, e, _, _ in spans):
                    spans.append((start, end, match.group(0), "inline_code"))

        # 2. URLs, emails, Windows/Unix paths
        for match in _INLINE_HAZARD_RE.finditer(text):
            start, end = match.start(), match.end()
            if not any(s <= start and end <= e for s, e, _, _ in spans):
                spans.append((start, end, match.group(0), "hazard"))

        # 3. User-protected custom terms
        if user_terms:
            user_re = build_user_protection_re(list(user_terms))
            if user_re:
                for match in user_re.finditer(text):
                    start, end = match.start(), match.end()
                    if not any(s <= start and end <= e for s, e, _, _ in spans):
                        spans.append((start, end, match.group(0), "user_term"))

        # Sort spans by starting offset and eliminate any overlapping intervals
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        non_overlapping: List[Tuple[int, int, str, str]] = []
        last_end = 0
        for start, end, orig, kind in spans:
            if start >= last_end:
                non_overlapping.append((start, end, orig, kind))
                last_end = end

        # Construct masked text and immutable atoms table
        atoms: List[ProtectedAtom] = []
        atom_map: Dict[str, ProtectedAtom] = {}
        masked_pieces: List[str] = []
        cursor = 0

        for atom_id, (start, end, orig, kind) in enumerate(non_overlapping, start=1):
            masked_pieces.append(text[cursor:start])
            placeholder = f"[REF{atom_id}]"
            atom = ProtectedAtom(
                id=atom_id,
                placeholder=placeholder,
                original_text=orig,
                span=(start, end),
                kind=kind,
            )
            atoms.append(atom)
            atom_map[placeholder] = atom
            masked_pieces.append(placeholder)
            cursor = end

        masked_pieces.append(text[cursor:])
        masked_text = "".join(masked_pieces)

        return ProtectedDocument(
            original_text=text,
            masked_text=masked_text,
            atoms=tuple(atoms),
            atom_map=atom_map,
        )
