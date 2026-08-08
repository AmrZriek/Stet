"""Unit tests for Phase 2 diff context menu: Keep my original / Never change /
Undo all changes, and the protected-term helper.

Mirrors the fixtures in test_diff_restore.py (dummy_cfg / mock_model /
make_window) with the ConfigManager-with-tmp-path monkeypatch.

The menu is modal (`menu.exec` blocks), so the composition is exercised by
monkeypatching ``QMenu.exec`` to a no-op that records the built menu, then
asserting on ``menu.actions()`` texts.
"""

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QMenu

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
    # Construct with an EMPTY original so the _do_correction background thread
    # never spawns (non-empty original races the diff state in tests). The
    # caller seeds original/corrected explicitly after construction.
    win = CorrectionWindow(
        original="",
        ac_model=mock_model,
        chat_model=mock_model,
        cfg=dummy_cfg,
    )
    if original:
        win.original = original
    return win


def _capture_menu(monkeypatch):
    """Patch QMenu.exec to a no-op that records the built menu."""
    captured = {}

    def fake_exec(menu, *args, **kwargs):
        captured["menu"] = menu
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    return captured


def _action_texts(menu):
    return [a.text() for a in menu.actions()]


def test_menu_over_change_offers_keep_and_never(monkeypatch, qtbot, dummy_cfg, mock_model):
    """A replace/delete non-sentence change offers Keep my original and
    Never change '<term>' again, then Undo all + Copy actions."""
    orig = "This is bad text."
    corr = "This is good text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert len(win._diff_changes) > 0
    chg0 = win._diff_changes[0]
    assert chg0["tag"] == "replace"
    assert chg0["is_sentence"] is False

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: 0)

    win._show_corr_context_menu(QPoint(5, 5))

    texts = _action_texts(captured["menu"])
    assert "Keep my original" in texts
    assert f"Never change '{chg0['orig_text'].strip()}' again" in texts
    assert "Undo all changes" in texts
    assert "Copy Selected" in texts
    assert "Copy All" in texts


def test_menu_over_delete_change_offers_never(monkeypatch, qtbot, dummy_cfg, mock_model):
    """A delete change offers the Never-change item (its orig_text is the term)."""
    orig = "This is bad text."
    corr = "This is text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    tags = [c["tag"] for c in win._diff_changes]
    assert "delete" in tags
    delete_idx = tags.index("delete")
    del_chg = win._diff_changes[delete_idx]

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: delete_idx)

    win._show_corr_context_menu(QPoint(5, 5))

    texts = _action_texts(captured["menu"])
    assert "Keep my original" in texts
    assert f"Never change '{del_chg['orig_text'].strip()}' again" in texts


def test_menu_copy_only_fallback(monkeypatch, qtbot, dummy_cfg, mock_model):
    """No change under cursor (or in-flight) -> no Keep/Never actions, only
    Undo all + Copy Selected + Copy All."""
    orig = "Hello world"
    corr = "Hello beautiful world"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: None)
    win._show_corr_context_menu(QPoint(5, 5))

    texts = _action_texts(captured["menu"])
    assert "Keep my original" not in texts
    assert not any(t.startswith("Never change") for t in texts)
    assert "Undo all changes" in texts
    assert "Copy Selected" in texts
    assert "Copy All" in texts


def test_menu_in_flight_suppresses_change_actions(monkeypatch, qtbot, dummy_cfg, mock_model):
    """While a correction is in flight the menu is copy-only, even over a change."""
    orig = "This is bad text."
    corr = "This is good text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    win._correction_in_flight = True

    captured = _capture_menu(monkeypatch)
    # _change_at would resolve, but in_flight must take precedence
    monkeypatch.setattr(win, "_change_at", lambda pos: 0)
    win._show_corr_context_menu(QPoint(5, 5))

    texts = _action_texts(captured["menu"])
    assert "Keep my original" not in texts
    assert not any(t.startswith("Never change") for t in texts)


def test_menu_insert_change_offers_no_never(monkeypatch, qtbot, dummy_cfg, mock_model):
    """Insert changes get Keep my original but NO Never-change item."""
    orig = "Hello world"
    corr = "Hello beautiful world"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert len(win._diff_changes) == 1
    assert win._diff_changes[0]["tag"] == "insert"

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: 0)
    win._show_corr_context_menu(QPoint(5, 5))

    texts = _action_texts(captured["menu"])
    assert "Keep my original" in texts
    assert not any(t.startswith("Never change") for t in texts)


def test_menu_sentence_change_offers_no_never(monkeypatch, qtbot, dummy_cfg, mock_model):
    """Sentence-level replace changes get Keep my original but NO Never-change."""
    # Every word differs -> a single contiguous replace opcode spanning 5
    # tokens on each side, so is_sentence (token count > 3) is True.
    orig = "The quick brown fox jumps"
    corr = "A fast green wolf leaps"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert len(win._diff_changes) == 1
    assert win._diff_changes[0]["tag"] == "replace"
    assert win._diff_changes[0]["is_sentence"] is True

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: 0)
    win._show_corr_context_menu(QPoint(5, 5))

    texts = _action_texts(captured["menu"])
    assert "Keep my original" in texts
    assert not any(t.startswith("Never change") for t in texts)


def test_undo_all_resets_corrected(monkeypatch, qtbot, dummy_cfg, mock_model):
    """Undo all changes restores corrected == original and re-renders the diff
    (no remaining changes)."""
    orig = "One two three four five."
    corr = "1 2 3 4 5."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert win._diff_changes
    assert win.corrected != win.original

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: None)
    win._show_corr_context_menu(QPoint(5, 5))

    undo_all = next(
        a for a in captured["menu"].actions() if a.text() == "Undo all changes"
    )
    assert undo_all.isEnabled()
    undo_all.trigger()

    assert win.corrected == win.original
    assert not win._diff_changes


def test_undo_all_disabled_when_no_changes(monkeypatch, qtbot, dummy_cfg, mock_model):
    """Undo all is disabled when corrected == original (nothing to undo)."""
    orig = "Hello world"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = orig
    win._render_diff(orig)

    captured = _capture_menu(monkeypatch)
    monkeypatch.setattr(win, "_change_at", lambda pos: None)
    win._show_corr_context_menu(QPoint(5, 5))

    undo_all = next(
        a for a in captured["menu"].actions() if a.text() == "Undo all changes"
    )
    assert not undo_all.isEnabled()


def test_protect_term_appends_persists_and_restores(qtbot, dummy_cfg, mock_model):
    """_protect_term appends to protected_terms, persists to disk, and
    restores the on-screen change toward the original."""
    orig = "This is bad text."
    corr = "This is good text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert win._diff_changes[0]["orig_text"] == "bad"

    win._protect_term(0, "bad")

    assert win.cfg.get("protected_terms") == ["bad"]
    assert win.corrected == orig

    # Config was persisted to the tmp-path file on disk
    import json

    with open(config_mod.CONFIG_FILE, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert "bad" in on_disk.get("protected_terms", [])


def test_protect_term_multiword(qtbot, dummy_cfg, mock_model):
    """Multi-word protected terms are supported (build_user_protection_re
    handles phrases)."""
    orig = "I seen the movie yesterday."
    corr = "I saw the movie yesterday."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    win._render_diff(corr)
    assert win._diff_changes
    chg0 = win._diff_changes[0]
    term = chg0["orig_text"].strip()

    win._protect_term(0, term)

    assert win.cfg.get("protected_terms") == [term]
    assert win.corrected == orig
