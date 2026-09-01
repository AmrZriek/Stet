# -*- coding: utf-8 -*-
"""Canonical user-facing vocabulary (§5b).

The user-facing actions are exactly: Correct (spelling/grammar), Rewrite
(clarity/tone), and Saved actions (templates). Chat parameters, raw prompt
editors, and JSON grammar selectors are culled from the default UI surface.
This module is the single source of truth for action labels and the culling map.
"""

from __future__ import annotations

CANONICAL_ACTIONS: tuple = ("Correct", "Rewrite", "Saved actions")

_NON_PRODUCT_MARKERS: tuple = (
    "temperature",
    "raw prompt",
    "prompt editor",
    "json grammar",
    "grammar selector",
    "chat param",
    "top_p",
    "repeat penalty",
)


def is_canonical_action(action: str) -> bool:
    return action in CANONICAL_ACTIONS


def cull_dialog_actions(actions: list) -> list:
    result: list = []
    for action in actions:
        if action in CANONICAL_ACTIONS:
            result.append(action)
            continue
        lower = action.lower()
        if any(marker in lower for marker in _NON_PRODUCT_MARKERS):
            continue
    return result