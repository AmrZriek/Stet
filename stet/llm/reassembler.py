# -*- coding: utf-8 -*-
"""Reassembler (Phase 3d): offset-based splicing of windowed corrections.

Each window owns a disjoint character-offset interval [start_anchor, end_anchor).
Overlap between windows is context only; the reassembler splices each window's
output into its OWNING interval and discards anything beyond it. It rejects
missing/duplicated seams and a GENERATION_TRUNCATED window, and computes
changed strictly after post-fixes.
"""

from __future__ import annotations

from dataclasses import dataclass


class ReassemblyError(Exception):
    """Raised when the window inputs violate seam/offset invariants."""


@dataclass(frozen=True)
class WindowResult:
    """One window's correction attributed to its owning interval."""

    start: int
    end: int
    text: str
    truncated: bool


@dataclass(frozen=True)
class ReassemblyResult:
    """The fully-reassembled document plus its changed flag."""

    text: str
    changed: bool


def compute_changed(original: str, corrected: str) -> bool:
    """Whether the corrected text differs from the original (strict post-fix)."""
    return original != corrected


class Reassembler:
    """Splices window results by character offset into one contiguous document."""

    def __init__(self, windows: list[WindowResult], original: str = ""):
        self._windows = windows
        self._original = original

    def run(self) -> ReassemblyResult:
        # 1. Validate each window's interval is well-formed.
        for w in self._windows:
            if w.start < 0 or w.end < 0 or w.end < w.start:
                raise ReassemblyError(f"invalid interval [{w.start},{w.end})")
            if w.truncated:
                raise ReassemblyError(
                    "GENERATION_TRUNCATED window cannot be reassembled"
                )

        # 2. Sort by start and enforce strictly disjoint, fully-contiguous seams.
        ordered = sorted(self._windows, key=lambda w: (w.start, w.end))
        cursor = ordered[0].start if ordered else 0
        for w in ordered:
            if w.start != cursor:
                raise ReassemblyError(
                    f"missing seam at offset {cursor} (window starts at {w.start})"
                )
            cursor = w.end

        # 3. Splice into a character buffer of the total span.
        total_len = cursor  # end of the last window
        # The owning intervals must tile [0, total_len) with no overlaps.
        buf = [" "] * total_len
        written = [False] * total_len  # track ownership to detect duplication
        for w in ordered:
            for i in range(w.start, w.end):
                if written[i]:
                    raise ReassemblyError("duplicate seam ownership")
                written[i] = True
                # The owning interval takes exactly the corresponding portion.
                idx = i - w.start
                if idx >= len(w.text):
                    raise ReassemblyError("window text shorter than its interval")
                buf[i] = w.text[idx]

        # 4. Any unwritten position is a gap (should not happen after contiguity).
        if not all(written):
            raise ReassemblyError("unowned span in reassembled document")

        text = "".join(buf)
        return ReassemblyResult(text=text, changed=compute_changed(self._original, text))