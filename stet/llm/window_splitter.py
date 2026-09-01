# -*- coding: utf-8 -*-
"""WindowSplitter (Phase 3a sequential windowed pass).

Splits a document into windows with a 2-sentence overlap for context.
Each window owns a disjoint character-offset interval.
"""

import re
from dataclasses import dataclass


class SplitError(Exception):
    """Raised when the document cannot be split into valid windows."""


# Split on sentence boundaries: .!? followed by space/end, plus newline breaks.
_SENTENCE_RE = re.compile(r'[^.\n]+[.!?]*[\n]*')


@dataclass(frozen=True)
class Window:
    """One window: owning interval plus the context text sent to the model."""
    start_anchor: int
    end_anchor: int
    text: str
    context_start: int
    context_end: int
    owning_text: str


class WindowSplitter:
    """Splits a document into overlapping windows over disjoint intervals."""

    def split(self, text, max_sentences=2, overlap_sentences=2):
        if not text:
            return []

        sentences = _SENTENCE_RE.findall(text)
        if not sentences:
            return [
                Window(0, len(text), text, 0, len(text), text),
            ]

        if len(sentences) <= max_sentences:
            return [
                Window(0, len(text), text, 0, len(text), text),
            ]

        windows = []
        offsets = []
        pos = 0
        for s in sentences:
            offsets.append(pos)
            pos += len(s)

        n = len(sentences)
        step = max_sentences
        for i in range(0, n, step):
            end_i = min(i + max_sentences, n)
            start_anchor = offsets[i]
            end_anchor = offsets[end_i - 1] + len(sentences[end_i - 1])
            ctx_start_i = max(0, i - overlap_sentences)
            context_start = offsets[ctx_start_i]
            context_end = end_anchor
            context_text = text[context_start:context_end]
            owning_text = text[start_anchor:end_anchor]
            windows.append(Window(
                start_anchor=start_anchor,
                end_anchor=end_anchor,
                text=context_text,
                context_start=context_start,
                context_end=context_end,
                owning_text=owning_text,
            ))
        return windows