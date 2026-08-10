"""Unit tests for Phase 4: clean view toggle, rewrite mode deletions, edit change dialog, and context menu."""

from unittest.mock import MagicMock
import pytest

from stet.core.config import ConfigManager
import stet.core.config as config_mod
from stet.llm.model_manager import ModelManager
from stet.ui.main_window import CorrectionWindow, EditChangeDialog
from PyQt6.QtWidgets import QApplication


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
        original="",
        ac_model=mock_model,
        chat_model=mock_model,
        cfg=dummy_cfg,
    )
    if original:
        win.original = original
    return win


def test_clean_view_contains_anchors_and_visible_deleted_text(qtbot, dummy_cfg, mock_model):
    orig = "The quick brown fox jumps over the lazy dog."
    corr = "A fast green wolf leaps over the lazy dog."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win.corrected = corr
    html_clean = win._final_result_html(win.corrected)

    assert '#chg0' in html_clean
    assert '#f87171' in html_clean or 'line-through' in html_clean
    assert 'The' in html_clean
    assert 'quick' in html_clean
    assert 'A' in html_clean
    assert 'fast' in html_clean


def test_toggle_flips_view_mode_and_persists_across_restore(qtbot, dummy_cfg, mock_model):
    orig = "This is bad text."
    corr = "This is good text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._render_diff(corr)
    assert win._clean_view is False

    win._toggle_clean_view()
    assert win._clean_view is True

    win._restore_change(0)
    assert win._clean_view is True
    assert win.corrected == orig


def test_clean_view_button_wired_and_reflects_state(qtbot, dummy_cfg, mock_model):
    """The 'Clean view' toggle must be reachable from the header (viewModeBtn)
    and its checked state must track _clean_view."""
    win = make_window("", dummy_cfg, mock_model)
    qtbot.addWidget(win)

    btn = win.view_mode_btn
    assert btn.objectName() == "viewModeBtn"
    assert btn.isCheckable() is True

    # Buttons are disabled until a correction completes (same as edit_text_btn);
    # this window was built with no original, so simulate the done state.
    btn.setEnabled(True)

    # Clicking the button flips the view and checks the button.
    win._render_diff("This is good text.")
    btn.click()
    assert win._clean_view is True
    assert btn.isChecked() is True

    btn.click()
    assert win._clean_view is False
    assert btn.isChecked() is False

    # Programmatic toggles keep the button state in sync.
    win._toggle_clean_view()
    assert btn.isChecked() is True


def test_popover_save_splices_typed_text(monkeypatch, qtbot, dummy_cfg, mock_model):
    orig = "Hello world"
    corr = "Hello beautiful world"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)
    win._render_diff(corr)

    typed_value = "wonderful"

    def mock_exec(self):
        self.result_text = typed_value
        return EditChangeDialog.DialogCode.Accepted

    monkeypatch.setattr(EditChangeDialog, "exec", mock_exec)

    # Test single word splice
    typed_value = "wonderful"
    win._edit_change(0)
    assert win.corrected == "Hello wonderful world"

    # Test multi-word splice
    win._render_diff("Hello beautiful world")
    typed_value = "very beautiful"
    win._edit_change(0)
    assert win.corrected == "Hello very beautiful world"

    # Test multi-line splice
    win._render_diff("Hello beautiful world")
    typed_value = "beautiful\nnew"
    win._edit_change(0)
    assert win.corrected == "Hello beautiful\nnew world"


def test_empty_popover_input_rejected(qtbot):
    dlg = EditChangeDialog("some text")
    qtbot.addWidget(dlg)
    dlg.show()

    dlg.edit.setPlainText("   ")
    dlg._on_save()

    assert dlg.result_text is None
    assert dlg.status_lbl.isHidden() is False
    assert "cannot be empty" in dlg.status_lbl.text()


def test_edit_this_fix_in_context_menu(monkeypatch, qtbot, dummy_cfg, mock_model):
    orig = "This is bad text."
    corr = "This is good text."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)
    win._render_diff(corr)

    pos = win.corr_edit.viewport().rect().center()
    monkeypatch.setattr(win, "_change_at", lambda p: 0)

    menus_shown = []
    from PyQt6.QtWidgets import QMenu
    orig_exec = QMenu.exec

    def mock_exec(self, p):
        menus_shown.append(self)
        return None

    QMenu.exec = mock_exec
    try:
        win._show_corr_context_menu(pos)
        assert len(menus_shown) == 1
        menu = menus_shown[0]
        action_texts = [a.text() for a in menu.actions()]
        assert "Edit this fix" in action_texts
        assert "Keep my original" in action_texts
        assert "Undo all changes" in action_texts
    finally:
        QMenu.exec = orig_exec


def test_no_crash_in_chat_mode_after_edit(monkeypatch, qtbot, dummy_cfg, mock_model):
    orig = "Hello world"
    corr = "Hello beautiful world"
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._is_chat_mode = True
    mock_chat = MagicMock()
    win._render_chat_transcript = mock_chat

    win._render_diff(corr)

    def mock_exec(self):
        self.result_text = "gorgeous"
        return EditChangeDialog.DialogCode.Accepted

    monkeypatch.setattr(EditChangeDialog, "exec", mock_exec)

    win._edit_change(0)
    assert win.corrected == "Hello gorgeous world"
    assert mock_chat.called


def test_green_blue_shared_letter_classification(qtbot, dummy_cfg, mock_model):
    win = make_window("", dummy_cfg, mock_model)
    qtbot.addWidget(win)

    # 1. 1:1 Replace sharing letters ('teh' -> 'the') -> GREEN (#4ade80)
    win.original = "This is teh test."
    html_typo = win._diff_html("This is the test.")
    assert "color:#4ade80;" in html_typo

    # 2. 1:1 Replace with no shared letters ('is' -> 'are') -> BLUE (#60a5fa)
    win.original = "These is test."
    html_grammar = win._diff_html("These are test.")
    assert "color:#60a5fa;" in html_grammar

    # 3. 1:1 Replace with no shared letters ('cat' -> 'dog') -> BLUE (#60a5fa)
    win.original = "The cat slept."
    html_noreplace = win._diff_html("The dog slept.")
    assert "color:#60a5fa;" in html_noreplace

    # 4. Token split ('Iam' -> 'I am') -> BLUE (#60a5fa)
    win.original = "Iam happy."
    html_split = win._diff_html("I am happy.")
    assert "color:#60a5fa;" in html_split


def test_whole_document_edit_mode_toggle_done_reset_chat(qtbot, dummy_cfg, mock_model):
    orig = "Hello world"
    corr = "Hello beautiful world"
    # No qtbot.addWidget: _accept() owns the close -> deferred emit ->
    # deleteLater lifecycle; registering would make pytest-qt close/delete
    # the window again at teardown and race the deferred deletion.
    win = CorrectionWindow(
        original="",
        ac_model=mock_model,
        chat_model=mock_model,
        cfg=dummy_cfg,
    )
    win.original = orig
    win._render_diff(corr)

    assert win.edit_text_btn.text() == "Edit text"
    assert win.corr_edit.isReadOnly() is True

    # Enter Edit Mode
    win._toggle_edit_text_mode()
    assert win._edit_text_mode is True
    assert win.edit_text_btn.text() == "Done"
    assert win.edit_text_btn.isChecked() is True
    assert win.corr_edit.isReadOnly() is False
    assert win.corr_edit.toPlainText() == corr

    # Edit Plain Text directly in corr_edit
    win.corr_edit.setPlainText("Hello gorgeous world\nSecond line")

    # Press Done
    win._toggle_edit_text_mode()
    assert win._edit_text_mode is False
    assert win.edit_text_btn.text() == "Edit text"
    assert win.edit_text_btn.isChecked() is False
    assert win.corr_edit.isReadOnly() is True

    # Assert self.corrected was updated and re-rendered with diff anchors
    assert win.corrected == "Hello gorgeous world\nSecond line"
    html = win.corr_edit.toHtml()
    assert "#chg" in html

    # Assert edits flow into Accept path
    accepted_texts = []
    win.accepted.connect(accepted_texts.append)
    win._accept()
    QApplication.processEvents()  # emit is deferred one tick after close
    assert len(accepted_texts) == 1
    assert accepted_texts[0] == "Hello gorgeous world\nSecond line"

    # Test Reset exits edit mode
    win2 = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win2)
    win2._render_diff(corr)
    win2._toggle_edit_text_mode()
    assert win2._edit_text_mode is True

    win2._reset()
    assert win2._edit_text_mode is False
    assert win2.edit_text_btn.text() == "Edit text"
    assert win2.corr_edit.isReadOnly() is True

    # Test Edit Text button hidden in Chat Mode
    win3 = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win3)
    win3.chat_input.setText("Rewrite this")
    win3._send_chat()
    assert win3._is_chat_mode is True
    assert win3.edit_text_btn.isHidden() is True


def test_accept_without_clicking_done_applies_edits(qtbot, dummy_cfg, mock_model):
    """Calling _accept() directly while in edit mode (without clicking Done first) commits edits and emits edited text."""
    win = make_window("Original text", dummy_cfg, mock_model)
    win._render_diff("Initial corrected text")

    # Enter edit mode
    win._toggle_edit_text_mode()
    assert win._edit_text_mode is True
    win.corr_edit.setPlainText("Newly edited text by user")

    # Directly accept without clicking Done first
    accepted_texts = []
    win.accepted.connect(accepted_texts.append)
    win._accept()
    QApplication.processEvents()

    assert win._edit_text_mode is False
    assert len(accepted_texts) == 1
    assert accepted_texts[0] == "Newly edited text by user"



def test_clean_view_toggle_noop_while_in_flight(qtbot, dummy_cfg, mock_model):
    win = make_window("Original text", dummy_cfg, mock_model)
    qtbot.addWidget(win)
    win._correction_in_flight = True

    assert win._clean_view is False
    win._toggle_clean_view()
    assert win._clean_view is False


def test_edit_text_mode_noop_while_in_flight(qtbot, dummy_cfg, mock_model):
    win = make_window("Original text", dummy_cfg, mock_model)
    qtbot.addWidget(win)
    win._correction_in_flight = True

    assert getattr(win, "_edit_text_mode", False) is False
    win._toggle_edit_text_mode()
    assert getattr(win, "_edit_text_mode", False) is False
    assert win.edit_text_btn.text() == "Edit text"
    assert win.corr_edit.isReadOnly() is True


def test_patch_mode_always_full_diff(qtbot, dummy_cfg, mock_model):
    orig = " ".join(f"old{i}" for i in range(100))
    corr = " ".join(f"new{i}" for i in range(100))
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._on_correction_ready(corr, "Patch (Full Correction)")
    assert win._clean_view is False
    html = win.corr_edit.toHtml()
    assert "#chg" in html


def test_chat_dense_answer_shows_plain_default(qtbot, dummy_cfg, mock_model):
    orig = " ".join(f"old{i}" for i in range(100))
    corr = " ".join(f"new{i}" for i in range(100))
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._is_chat_mode = True
    win.corrected = corr
    assert win._force_diff_view is False

    html = win._chat_transcript_html(final_result=corr)
    assert "#chg" not in html
    assert "color:#60a5fa" not in html
    assert "new0" in html


def test_chat_toggle_forces_diff(qtbot, dummy_cfg, mock_model):
    orig = " ".join(f"old{i}" for i in range(100))
    corr = " ".join(f"new{i}" for i in range(100))
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._is_chat_mode = True
    win.corrected = corr
    assert win._force_diff_view is False

    win._toggle_clean_view()
    assert win._force_diff_view is True

    html = win._chat_transcript_html(final_result=corr)
    assert "#chg" in html


def test_readable_chat_result_shows_diff(qtbot, dummy_cfg, mock_model):
    orig = "The quick brown fox jumps over the lazy dog."
    corr = "The fast brown fox jumps over the lazy dog."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    win._is_chat_mode = True
    win.corrected = corr
    assert win._force_diff_view is False

    html = win._chat_transcript_html(final_result=corr)
    assert "#chg" in html


def test_length_disparity_unreadable(qtbot, dummy_cfg, mock_model):
    orig = "Short text."
    corr = "This is a very long expanded text that triples the length of the original text significantly."
    win = make_window(orig, dummy_cfg, mock_model)
    qtbot.addWidget(win)

    assert win._final_result_diff_is_readable(corr) is False
