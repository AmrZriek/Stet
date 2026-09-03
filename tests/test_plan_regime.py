# -*- coding: utf-8 -*-
"""Tests for the PlanPrePass regime selector (§3a / §3.5).

The Plan Pre-Pass is a THIRD regime applied only when window count > K (=8) or
input > 2×headroom. Summaries are sized at ≥5% of input or 200 tokens, subject
to the generation reservation. This module decides which regeneration regime to
use and the pre-pass summary budget.
"""


from stet.llm.plan_regime import (
    PlanRegime,
    RegimeSelector,
    summary_budget,
)


def test_monolithic_when_document_fits():
    selector = RegimeSelector(headroom=1000, window_k=8)
    decision = selector.select(document_tokens=500, headroom=1000, window_count=1)
    assert decision.regime == PlanRegime.MONOLITHIC


def test_windowed_when_document_exceeds_headroom():
    selector = RegimeSelector(headroom=1000, window_k=8)
    decision = selector.select(document_tokens=2000, headroom=1000, window_count=3)
    # 2000 > 1000 -> windowed. window_count 3 <= K 8, so no pre-pass.
    assert decision.regime == PlanRegime.WINDOWED


def test_prepass_when_window_count_exceeds_k():
    selector = RegimeSelector(headroom=1000, window_k=8)
    decision = selector.select(document_tokens=5000, headroom=1000, window_count=12)
    # window_count 12 > K 8 -> pre-pass.
    assert decision.regime == PlanRegime.PREPASS


def test_prepass_when_input_exceeds_twice_headroom():
    selector = RegimeSelector(headroom=1000, window_k=8)
    decision = selector.select(document_tokens=2500, headroom=1000, window_count=2)
    # 2500 > 2*1000=2000 -> pre-pass.
    assert decision.regime == PlanRegime.PREPASS


def test_windowed_when_input_between_headroom_and_2x():
    selector = RegimeSelector(headroom=1000, window_k=8)
    decision = selector.select(document_tokens=1500, headroom=1000, window_count=2)
    # 1000 < 1500 <= 2000 -> windowed, not pre-pass.
    assert decision.regime == PlanRegime.WINDOWED


def test_summary_budget_is_percent_of_input():
    # ≥5% of input, or 200 tokens minimum.
    assert summary_budget(input_tokens=10000) == 500


def test_summary_budget_respects_200_token_min():
    assert summary_budget(input_tokens=2000) == 200
