# -*- coding: utf-8 -*-
"""ContextPlanner (Phase 3a): closed budget arithmetic."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ReservePolicy:
    overhead: float = 50.0
    g_min: int = 128
    g_max: int = 4096


def compute_output_reservation(i: int, reserve: ReservePolicy) -> int:
    raw = math.ceil(1.25 * i + reserve.overhead)
    return max(reserve.g_min, min(reserve.g_max, raw))


def solve_closed_budget(n_ctx_slot: int, reserve: ReservePolicy, safety_margin: int) -> int:
    """Return the largest content budget i satisfying i + G(i) + S <= n_ctx_slot.

    The predicate i -> [i + G(i) + S <= n_ctx] is monotone (larger content never
    reduces total, since G is non-decreasing... actually G may clamped-flat, but
    i+G(i) is non-decreasing in i because G(i) >= g_min and 1.25 slope, and even
    at the clamp the 1.0 slope of i dominates). So binary-search the maximum.
    Returns 0 if nothing fits.
    """
    if n_ctx_slot <= safety_margin:
        return 0

    def predicate(i: int) -> bool:
        return i + compute_output_reservation(i, reserve) + safety_margin <= n_ctx_slot

    lo, hi = 0, max(0, n_ctx_slot - safety_margin)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if predicate(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


@dataclass(frozen=True)
class BudgetResult:
    chosen_index: object
    max_tokens: int
    selected_content: str
    fit: bool


class ContextPlanner:
    def __init__(self, tokenizer, reserve, safety_margin):
        self._tokenizer = tokenizer
        self._reserve = reserve
        self._safety = safety_margin

    def _compiled_token_count(self, content):
        return self._tokenizer(content)

    def plan(self, candidates, n_ctx_slot):
        best_index = None
        best_content = ""
        best_tokens = 0
        # solve_closed_budget is loop-invariant: compute once per slot budget
        feasible_budget = solve_closed_budget(n_ctx_slot, self._reserve, self._safety)
        for idx, candidate in enumerate(candidates):
            i = self._compiled_token_count(candidate)
            if i <= feasible_budget:
                best_index = idx
                best_content = candidate
                best_tokens = i
        if best_index is None:
            if candidates:
                shrunk = self._binary_shrink(candidates[0], feasible_budget)
                if shrunk is not None:
                    return BudgetResult(0, self._reserved(shrunk, n_ctx_slot), shrunk, True)
            return BudgetResult(None, 0, "", False)
        max_tokens = compute_output_reservation(best_tokens, self._reserve)
        return BudgetResult(best_index, max_tokens, best_content, True)

    def _reserved(self, content, n_ctx_slot):
        return compute_output_reservation(self._tokenizer(content), self._reserve)

    def _binary_shrink(self, content, feasible_budget):
        lo, hi = 0, len(content)
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            prefix = content[:mid]
            if self._compiled_token_count(prefix) <= feasible_budget:
                best = prefix
                lo = mid + 1
            else:
                hi = mid - 1
        return best