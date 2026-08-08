"""Unit tests for Phase 1 diff state, anchor generation, and change restoration engine."""

from unittest.mock import MagicMock
import pytest

from stet.core.config import ConfigManager
import stet.core.config as config_mod
from stet.llm.model_manager import ModelManager
from stet.ui.main_window import CorrectionWindow


@pytest.fixture
def dummy_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    cm.config["protected_terms"] = []
    return cm


@pytest.fixture
def mock_model():
    mm = MagicMock(spec=ModelManager)
    mm.is_loaded.return_value = True
    return mm


def make_window(original: str, dummy_cfg, mock_model) -> CorrectionWindow:
    win = CorrectionWindow(
        original=original,
        ac_model=mock_model,
        chat_model=mock_model,
        cfg=dummy_cfg,
    )
    return win


def test_diff_state_classification(qtbot, dummy_cfg, mock_model):
    orig = "The quick brown fox jumps over the lazy dog."
    corr = "The fast green fox leaps across the lazy hound."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._render_diff(corr)

    assert hasattr(win, "_diff_changes")
    changes = win._diff_changes
    assert len(changes) > 0

    # Verify attributes of recorded change units
    for chg in changes:
        assert "idx" in chg
        assert "tag" in chg
        assert "i1" in chg and "i2" in chg
        assert "j1" in chg and "j2" in chg
        assert "orig_text" in chg
        assert "corr_text" in chg
        assert "is_sentence" in chg
        assert "orig_slice" in chg
        assert "corr_slice" in chg
        assert chg["tag"] in ("delete", "insert", "replace")


def test_anchor_href_emission(qtbot, dummy_cfg, mock_model):
    orig = "Hello world"
    corr = "Hello beautiful world"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    html = win._diff_html(corr)

    assert '<a href="#chg0">' in html
    assert 'beautiful' in html


def test_single_restore_splice(qtbot, dummy_cfg, mock_model):
    orig = "This is bad text."
    corr = "This is good text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert win.corrected == corr

    win._restore_change(0)
    assert win.corrected == orig


def test_multiple_restore_roundtrip(qtbot, dummy_cfg, mock_model):
    orig = "One two three four five."
    corr = "1 2 3 4 5."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)

    # Sequentially restore changes from index 0 until no changes remain
    iterations = 0
    while win._diff_changes and iterations < 10:
        win._restore_change(0)
        iterations += 1

    assert win.corrected == orig


def test_newline_preservation_and_no_orphan_spaces(qtbot, dummy_cfg, mock_model):
    orig = "First line.\nSecond line.\nThird line."
    corr = "1st line.\nSecond paragraph.\n3rd line."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)

    # Restore all changes sequentially
    iterations = 0
    while win._diff_changes and iterations < 10:
        win._restore_change(0)
        iterations += 1

    assert win.corrected == orig
    assert "\n" in win.corrected
    assert "  " not in win.corrected
    assert " \n" not in win.corrected
    assert "\n " not in win.corrected


def test_punctuation_exact_restores(qtbot, dummy_cfg, mock_model):

    cases = [
        ("Hello, world", "Hello world"),
        ("It is bad.", "It's bad."),
        ("Value is 00.", "Value is 100 dollars."),
        ("Hello world", "Hello beautiful world"),
        ("This is bad text.", "This is text."),
        ("Hello world", "Hello world today"),
        ("Hello world today", "Hello world"),
    ]
    for orig, corr in cases:
        win = make_window(orig, dummy_cfg, mock_model)
        qtbot.addWidget(win)
        win.corrected = corr
        win._render_diff(corr)

        iterations = 0
        while win._diff_changes and iterations < 10:
            win._restore_change(0)
            iterations += 1

        assert win.corrected == orig, f"Failed exact restore for orig={orig!r}, corr={corr!r}, got={win.corrected!r}"


def test_cross_render_reindex_via_change_at(qtbot, dummy_cfg, mock_model):

    orig = "One red two blue four."
    corr = "1 red 2 blue 4."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)

    # Initial state should have 3 distinct change units: "1", "2", "4"
    assert len(win._diff_changes) == 3

    # Restore change index 0 ("1" -> "One")
    win._restore_change(0)
    assert win.corrected == "One red 2 blue 4."
    # After re-render, remaining changes are re-indexed: now index 0 is "2" -> "two"
    assert len(win._diff_changes) == 2

    # Verify that change index 0 on updated diff refers to "2" -> "two"
    assert win._diff_changes[0]["orig_text"] == "two"
    assert win._diff_changes[0]["corr_text"] == "2"

    # Restore index 0 on updated diff
    win._restore_change(0)
    assert win.corrected == "One red two blue 4."


def test_correction_in_flight_flag_transitions(qtbot, dummy_cfg, mock_model):
    win = make_window("", dummy_cfg, mock_model)
    qtbot.addWidget(win)

    assert win._correction_in_flight is False

    win._correction_in_flight = True
    win._on_correction_ready("Test corrected", "Patch")
    assert win._correction_in_flight is False

    win._correction_in_flight = True
    win._on_correction_failed()
    assert win._correction_in_flight is False

    win._correction_in_flight = True
    win._on_correction_failed_with_msg("Error")
    assert win._correction_in_flight is False

    win._correction_in_flight = True
    win._reset()
    assert win._correction_in_flight is False


def test_corr_edit_open_external_links(qtbot, dummy_cfg, mock_model):
    win = make_window("Test original", dummy_cfg, mock_model)
    qtbot.addWidget(win)
    assert hasattr(win, "corr_edit")
    assert win.corr_edit.isReadOnly() is True
