"""Tests for stet.ui.main_window — CorrectionWindow static helpers, strength
normalization, and UI-independent utility methods."""

import pytest

from stet.ui.main_window import CorrectionWindow


class TestStrengthNormalization:
    """CorrectionWindow._normalize_strength maps legacy values to new ones."""

    @pytest.mark.parametrize(
        "input_val, expected",
        [
            ("spelling_only", "spelling_only"),
            ("full_correction", "full_correction"),
            ("rewrite_polish", "rewrite_polish"),
            ("conservative", "spelling_only"),
            ("aggressive", "rewrite_polish"),
            ("smart_fix", "full_correction"),
            (None, "full_correction"),
            ("unknown", "unknown"),
        ],
    )
    def test_normalize(self, input_val, expected):
        assert CorrectionWindow._normalize_strength(input_val) == expected


class TestStrengthFromLabel:
    """CorrectionWindow._strength_from_label maps display labels to config values."""

    @pytest.mark.parametrize(
        "label, expected",
        [
            ("Spelling Only", "spelling_only"),
            ("Conservative Spelling", "spelling_only"),
            ("Full Correction", "full_correction"),
            ("Smart Fix", "full_correction"),
            ("Rewrite & Polish", "rewrite_polish"),
            ("Aggressive Rewrite", "rewrite_polish"),
        ],
    )
    def test_label_mapping(self, label, expected):
        assert CorrectionWindow._strength_from_label(label) == expected


class TestStrengthIndex:
    """CorrectionWindow._strength_index maps strength to combo box index."""

    @pytest.mark.parametrize(
        "strength, index",
        [
            ("spelling_only", 0),
            ("conservative", 0),
            ("full_correction", 1),
            ("smart_fix", 1),
            ("rewrite_polish", 2),
            ("aggressive", 2),
        ],
    )
    def test_index(self, strength, index):
        assert CorrectionWindow._strength_index(strength) == index
