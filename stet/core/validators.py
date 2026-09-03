"""Unit and Document Output Validators (Phase 3d).

Separates unit-level validation (UnitValidator: per-window hallucination,
divergence ratio, prompt injection / unrelated output, placeholder preservation,
and refusal guards) from document-level validation (DocumentValidator: whole
document structure and atom integrity).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import List, Optional, Set

from stet.core.engine_types import GuardSet

_PLACEHOLDER_RE = re.compile(r"\[REF\d+\]")
_REFUSAL_PATTERNS = [
    re.compile(r"^(?:i am sorry|i'm sorry|as an ai|as a language model|i cannot fulfill|i cannot assist)", re.IGNORECASE),
    re.compile(r"^(?:here is the corrected text|here's the corrected|below is the corrected):?", re.IGNORECASE),
]


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Outcome of a validation check."""

    valid: bool
    cleaned_text: str
    reason: str = ""


class UnitValidator:
    """Validates individual window/chunk LLM outputs against safety guards."""

    @classmethod
    def validate(
        cls,
        input_text: str,
        output_text: str,
        guard_set: Optional[GuardSet] = None,
        mode_index: int = 0,
    ) -> ValidationOutcome:
        """Validate a single generation unit."""
        if guard_set is None:
            guard_set = GuardSet()

        cleaned = output_text.strip()
        if not cleaned and input_text.strip():
            return ValidationOutcome(valid=False, cleaned_text=input_text, reason="Output is empty")

        # 1. Refusal / Preamble detection
        for pattern in _REFUSAL_PATTERNS:
            if pattern.search(cleaned):
                return ValidationOutcome(
                    valid=False, cleaned_text=input_text, reason="Model refusal or preamble detected"
                )

        # 2. Reference marker preservation ([REF1], [REF2], ...)
        if guard_set.preserve_reference_markers:
            in_list: List[str] = _PLACEHOLDER_RE.findall(input_text)
            out_list: List[str] = _PLACEHOLDER_RE.findall(cleaned)
            in_placeholders: Set[str] = set(in_list)
            out_placeholders: Set[str] = set(out_list)
            missing = in_placeholders - out_placeholders
            if missing:
                return ValidationOutcome(
                    valid=False,
                    cleaned_text=input_text,
                    reason=f"Protected reference markers missing: {sorted(missing)}",
                )
            extra = out_placeholders - in_placeholders
            if extra:
                return ValidationOutcome(
                    valid=False,
                    cleaned_text=input_text,
                    reason=f"Hallucinated reference markers detected: {sorted(extra)}",
                )
            for ph in in_placeholders:
                if out_list.count(ph) > in_list.count(ph):
                    return ValidationOutcome(
                        valid=False,
                        cleaned_text=input_text,
                        reason=f"Protected reference marker duplicated: {ph}",
                    )
        # 3. Code fence preservation (```)
        if guard_set.preserve_code_blocks:
            in_fences = input_text.count("```")
            out_fences = cleaned.count("```")
            if in_fences > 0 and out_fences < in_fences:
                return ValidationOutcome(
                    valid=False,
                    cleaned_text=input_text,
                    reason="Code blocks/fences truncated or missing",
                )

        # 4. Length divergence ratio
        in_words = max(1, len(input_text.split()))
        out_words = len(cleaned.split())
        ratio = out_words / in_words

        # For spelling/grammar (mode 0, 1), divergence bounds are tighter
        min_ratio = 0.50 if mode_index >= 2 else 0.65
        max_ratio = 2.50 if mode_index >= 2 else 1.60

        if ratio < min_ratio or ratio > max_ratio:
            return ValidationOutcome(
                valid=False,
                cleaned_text=input_text,
                reason=f"Word count ratio {ratio:.2f} outside bounds [{min_ratio}, {max_ratio}]",
            )

        # 5. Output unrelated to input (prompt injection / hallucination guard)
        if guard_set.output_unrelated_guard and mode_index < 2 and in_words >= 5:
            matcher = difflib.SequenceMatcher(None, input_text.lower(), cleaned.lower())
            similarity = matcher.ratio()
            if similarity < 0.35:
                return ValidationOutcome(
                    valid=False,
                    cleaned_text=input_text,
                    reason=f"Output similarity {similarity:.2f} indicates unrelated text or hallucination",
                )

        return ValidationOutcome(valid=True, cleaned_text=cleaned)


class DocumentValidator:
    """Validates the fully reassembled document."""

    @classmethod
    def validate(
        cls,
        original_doc: str,
        assembled_doc: str,
        guard_set: Optional[GuardSet] = None,
    ) -> ValidationOutcome:
        """Validate whole document integrity."""
        if guard_set is None:
            guard_set = GuardSet()

        if not assembled_doc and original_doc:
            return ValidationOutcome(valid=False, cleaned_text=original_doc, reason="Assembled document is empty")

        # Post-restore there should be no [REFn] left unless the user typed
        # it literally in the original. Any other leftover is a hallucinated
        # or un-restored marker leaking to the user.
        if guard_set.preserve_reference_markers:
            orig_literals = set(_PLACEHOLDER_RE.findall(original_doc))
            assem_placeholders = _PLACEHOLDER_RE.findall(assembled_doc)
            assem_set = set(assem_placeholders)
            extra = assem_set - orig_literals
            if extra:
                return ValidationOutcome(
                    valid=False,
                    cleaned_text=original_doc,
                    reason=f"Document has hallucinated markers: {sorted(extra)}",
                )
            for ph in assem_set & orig_literals:
                if assem_placeholders.count(ph) > original_doc.count(ph):
                    return ValidationOutcome(
                        valid=False,
                        cleaned_text=original_doc,
                        reason=f"Document has duplicated marker: {ph}",
                    )

        return ValidationOutcome(valid=True, cleaned_text=assembled_doc)
