# -*- coding: utf-8 -*-
"""Tests for the canonical user-facing vocabulary (§5b).

User-facing actions are exactly: Correct (spelling/grammar), Rewrite
(clarity/tone), Saved actions (templates). This module defines the canonical
labels and a culling map that removes chat parameters, raw prompt editors,
and JSON grammar selectors from the default UI surface.
"""

from stet.ui.vocabulary import (
    CANONICAL_ACTIONS,
    cull_dialog_actions,
    is_canonical_action,
)


def test_canonical_actions_are_the_three_product_verbs():
    assert CANONICAL_ACTIONS == ("Correct", "Rewrite", "Saved actions")


def test_is_canonical_action_true_for_product_verbs():
    assert is_canonical_action("Correct")
    assert is_canonical_action("Rewrite")
    assert is_canonical_action("Saved actions")


def test_is_canonical_action_false_for_chat_parameters():
    assert not is_canonical_action("temperature")
    assert not is_canonical_action("raw prompt editor")
    assert not is_canonical_action("JSON grammar selector")


def test_cull_dialog_actions_removes_non_product_actions():
    actions = ["Correct", "Rewrite", "Saved actions", "temperature", "JSON grammar selector"]
    culled = cull_dialog_actions(actions)
    assert culled == ["Correct", "Rewrite", "Saved actions"]


def test_cull_dialog_actions_preserves_order():
    actions = ["Saved actions", "Rewrite", "Correct", "chat params"]
    assert cull_dialog_actions(actions) == ["Saved actions", "Rewrite", "Correct"]
