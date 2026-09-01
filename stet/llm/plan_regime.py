# -*- coding: utf-8 -*-
"""Plan-regime selector (Phase 3a / §3.5).

Selects which regeneration regime to apply:
- MONOLITHIC: the full document satisfies the closed budget (one request).
- WINDOWED: input exceeds headroom; split with 2-sentence overlap.
- PREPASS: a plan pre-pass is warranted when window count > K (=8) OR input
  > 2×headroom. Summaries are sized at ≥5% of input or 200 tokens, subject to
  the generation reservation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Regime names.
MONOLITHIC = "monolithic"
WINDOWED = "windowed"
PREPASS = "prepass"


class PlanRegime:
    MONOLITHIC = MONOLITHIC
    WINDOWED = WINDOWED
    PREPASS = PREPASS


@dataclass(frozen=True)
class PrepassDecision:
    regime: str
    window_count: int
    headroom: int


class RegimeSelector:
    """Decides the regeneration regime from input size and window count."""

    def __init__(self, headroom: int, window_k: int = 8):
        self._headroom = headroom
        self._window_k = window_k

    def select(self, document_tokens: int, headroom: int, window_count: int) -> PrepassDecision:
        if window_count > self._window_k or document_tokens > 2 * headroom:
            return PrepassDecision(PlanRegime.PREPASS, window_count, headroom)
        if document_tokens > headroom:
            return PrepassDecision(PlanRegime.WINDOWED, window_count, headroom)
        return PrepassDecision(PlanRegime.MONOLITHIC, window_count, headroom)


def summary_budget(input_tokens: int) -> int:
    """Summary size: ≥5% of input, or 200 tokens minimum."""
    return max(200, input_tokens // 20)