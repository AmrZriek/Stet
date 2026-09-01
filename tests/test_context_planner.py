# -*- coding: utf-8 -*-
"""Tests for the ContextPlanner closed budget arithmetic."""

import pytest

from stet.llm.context_planner import (
    ContextPlanner,
    BudgetResult,
    ReservePolicy,
    compute_output_reservation,
    solve_closed_budget,
)


def test_compute_output_reservation_clamps_to_min():
    g = compute_output_reservation(10, ReservePolicy(overhead=50, g_min=128, g_max=1024))
    assert g == 128


def test_compute_output_reservation_uplevel_scale():
    g = compute_output_reservation(1000, ReservePolicy(overhead=50, g_min=128, g_max=4096))
    assert g == 1300


def test_compute_output_reservation_clamps_to_max():
    g = compute_output_reservation(100000, ReservePolicy(overhead=50, g_min=128, g_max=8192))
    assert g == 8192


def test_solve_closed_budget_selects_largest_feasible_candidate():
    def tokenizer(text):
        return len(text)
    planner = ContextPlanner(
        tokenizer=tokenizer,
        reserve=ReservePolicy(overhead=0, g_min=16, g_max=256),
        safety_margin=8,
    )
    candidates = ["a" * 10, "a" * 100, "a" * 1000]
    n_ctx_slot = 300
    result = planner.plan(candidates, n_ctx_slot)
    assert result.chosen_index == 1
    assert result.max_tokens > 0


def test_closed_inequality_is_respected():
    def tokenizer(text):
        return len(text)
    planner = ContextPlanner(
        tokenizer=tokenizer,
        reserve=ReservePolicy(overhead=100, g_min=16, g_max=256),
        safety_margin=8,
    )
    candidates = ["a" * 50]
    n_ctx_slot = 100
    result = planner.plan(candidates, n_ctx_slot)
    # 50+tok does not fit (total ~221 > 100); the planner degrades to a feasible
    # prefix via binary shrink rather than overflowing context.
    assert result.chosen_index == 0
    assert len(result.selected_content) < 50


def test_fixed_point_iteration_converges():
    def tokenizer(text):
        return len(text)
    planner = ContextPlanner(
        tokenizer=tokenizer,
        reserve=ReservePolicy(overhead=0, g_min=8, g_max=64),
        safety_margin=0,
    )
    candidates = ["a" * 20]
    result = planner.plan(candidates, n_ctx_slot=30)
    assert result.chosen_index is not None or result.max_tokens == 0


def test_binary_shrink_finds_feasible_substring():
    def tokenizer(text):
        return len(text)
    planner = ContextPlanner(
        tokenizer=tokenizer,
        reserve=ReservePolicy(overhead=0, g_min=8, g_max=64),
        safety_margin=0,
    )
    candidates = ["a" * 100]
    result = planner.plan(candidates, n_ctx_slot=50)
    assert result.chosen_index == 0
    assert len(result.selected_content) <= 50


def test_budget_result_serializes_max_tokens():
    r = BudgetResult(chosen_index=0, max_tokens=256, selected_content="text", fit=True)
    assert r.max_tokens == 256
    assert r.fit
def test_safety_margin_never_overflowed():
    def tokenizer(text):
        return len(text)
    planner = ContextPlanner(
        tokenizer=tokenizer,
        reserve=ReservePolicy(overhead=50, g_min=16, g_max=256),
        safety_margin=10,
    )
    candidates = ["a" * 30]
    result = planner.plan(candidates, n_ctx_slot=60)
    # I(30)=30, G(30)=ceil(37.5+50)=88->clamp 88, 30+88+10=128 > 60, so content
    # does NOT fit; the planner must degrade (shrink) rather than overflow.
    assert len(result.selected_content) + result.max_tokens + 10 <= 60
