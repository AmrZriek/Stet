# -*- coding: utf-8 -*-
"""Validator separation (Phase 3d): UnitValidator vs DocumentValidator.

- UnitValidator checks a single segment (length ratio, atom preservation,
  output-unrelated-to-input).
- DocumentValidator runs per-paragraph sub-validation with chunked-retry
  fallback, and guards against adversarial input like "SYSTEM:" prompt
  injection via an output-unrelated-to-input test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class ValidationError(Exception):
    """Raised on unrecoverable validation failure."""


_PROMPT_INJECTION_RE = re.compile(
    r"(?im)\b(?:system|assistant|user)\s*:\s*|"
    r"ignore (?:all )?(?:previous|prior|above) (?:instructions|rules)|"
    r"disregard (?:all )?(?:previous|prior|above)|"
    r"you are now (?:a |an )?(?:sophisticated|a|an)|"
    r"<<\s*\|\s*(?:system|user|assistant)\s*\|\s*>>"
)


@dataclass(frozen=True)
class InjectionResult:
    is_injected: bool
    reason: str


@dataclass(frozen=True)
class UnitResult:
    ok: bool
    reason: str = ""


def detect_prompt_injection(text: str) -> InjectionResult:
    """Flag text that reads like a system/instruction override."""
    if not text:
        return InjectionResult(False, "")
    m = _PROMPT_INJECTION_RE.search(text)
    if m:
        return InjectionResult(True, f"prompt-injection pattern: {m.group(0)!r}")
    return InjectionResult(False, "")


def _token_set(text: str) -> set[str]:
    """Low-overhead token proxy: lowercase word set."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class UnitValidator:
    """Checks one output segment against its input segment."""

    def __init__(self, max_len_ratio: float = 3.0, min_overlap_ratio: float = 0.15):
        self._max_len_ratio = max_len_ratio
        self._min_overlap_ratio = min_overlap_ratio

    def check(self, input_text: str, output: str) -> UnitResult:
        if detect_prompt_injection(output).is_injected:
            return UnitResult(False, "output contains prompt-injection pattern")
        in_tokens = _token_set(input_text)
        out_tokens = _token_set(output)
        if not input_text:
            return UnitResult(True, "")
        # Length divergence: output must not balloon/implode relative to input.
        if len(output) > self._max_len_ratio * max(1, len(input_text)):
            return UnitResult(False, "output length diverges from input")
        # Output-unrelated-to-input: output must share enough content atoms.
        if in_tokens and out_tokens:
            overlap = len(in_tokens & out_tokens) / len(in_tokens)
            if overlap < self._min_overlap_ratio:
                return UnitResult(False, "output unrelated to input")
        # Atom-preservation: any unresolved [REFN] placeholder in the output
        # means a protected atom was NOT restored to its original text.
        unresolved = re.findall(r"\[REF\d+\]", output)
        if unresolved:
            return UnitResult(False, f"unresolved atom placeholder: {unresolved[0]}")
        return UnitResult(True, "")


@dataclass(frozen=True)
class DocumentResult:
    is_valid: bool
    reason: str = ""


class DocumentValidator:
    """Validates a whole document via per-paragraph sub-validation."""

    def __init__(self, unit: UnitValidator | None = None):
        self._unit = unit or UnitValidator()

    def build_retry_plan(self, paragraphs: list[str], output: str) -> RetryPlan:
        """Identify paragraphs missing from the output (whitespace-normalized).

        Returns a RetryPlan naming the indices to re-attempt, so the engine can
        do chunked-retry fallback rather than rejecting the document outright.
        """
        missing: list[int] = []
        for i, para in enumerate(paragraphs):
            para_norm = " ".join(para.split())
            if not para_norm:
                continue
            if para_norm not in " ".join(output.split()):
                missing.append(i)
        return RetryPlan(missing_paragraph_indices=tuple(missing))

    def validate(self, paragraphs: list[str], output: str) -> DocumentResult:
        # Per-paragraph sub-validation: each input paragraph should be
        # represented in the output. A missing paragraph triggers chunked-retry.
        plan = self.build_retry_plan(paragraphs, output)
        if plan.retryable:
            return DocumentResult(False, f"missing paragraphs: {plan.missing_paragraph_indices}")
        # Adversarial-output guard: prompt injection in the output is always fatal.
        if detect_prompt_injection(output).is_injected:
            return DocumentResult(False, "adversarial output")
        # Whole-document: output must not be a fragment of the input.
        if output and len(output) < min(len(p) for p in paragraphs):
            return DocumentResult(False, "output shorter than every input paragraph")
        return DocumentResult(True, "")


@dataclass(frozen=True)
class RetryPlan:
    """Which paragraphs a chunked-retry should re-attempt."""
    missing_paragraph_indices: tuple[int, ...]

    @property
    def retryable(self) -> bool:
        return len(self.missing_paragraph_indices) > 0


def validate_unit(input_text: str, output: str) -> UnitResult:
    """Convenience wrapper for a single unit check."""
    return UnitValidator().check(input_text, output)