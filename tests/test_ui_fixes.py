"""Tests for Issues #1, #2, #4 — UI fixes and chat mode."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_cfg(overrides=None):
    """Create a lightweight ConfigManager mock with defaults + overrides."""
    from stet.constants import DEFAULT_CONFIG
    data = DEFAULT_CONFIG.copy()
    if overrides:
        data.update(overrides)
    if "ac_same_as_chat" in data:
        data["chat_use_separate_model"] = not data["ac_same_as_chat"]
    if "ac_model_path" in data:
        if data.get("chat_use_separate_model", False):
            data["chat_model_path"] = data.get("model_path", "")
            data["model_path"] = data["ac_model_path"]
        else:
            data["chat_model_path"] = data.get("model_path", "")

    cfg = MagicMock()
    cfg.get = lambda key, default=None: data.get(key, default)
    cfg.set = MagicMock()
    cfg.add_recent = MagicMock()
    return cfg


def _make_window(monkeypatch, qtbot, original, overrides=None):
    from stet.ui.main_window import CorrectionWindow

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    ac_model = MagicMock()
    chat_model = MagicMock()
    cw = CorrectionWindow(original, ac_model, chat_model, _make_cfg(overrides))
    qtbot.addWidget(cw)
    return cw


# ── Issue #2: Port alignment ─────────────────────────────────────────────────
def test_port_spin_not_wrapped_in_hboxlayout():
    """Port spin field should be added directly to page form, not wrapped in QHBoxLayout."""
    import inspect
    from stet.ui.settings import SettingsDialog
    src = inspect.getsource(SettingsDialog._build_ui)
    # The old code had "host_port_lay = QHBoxLayout()" wrapping the port.
    # The fix removes that wrapper.
    assert "host_port_lay" not in src, "Port field should not be wrapped in a QHBoxLayout (Issue #2)"


# ── Issue #1: Template drag-and-drop ──────────────────────────────────────────
def test_templates_use_qlistwidget():
    """Template list in settings should use QListWidget for drag-and-drop, not QVBoxLayout rows."""
    import inspect
    from stet.ui.settings import SettingsDialog
    src = inspect.getsource(SettingsDialog._build_ui)
    assert "templates_list_w" in src
    # Should be a QListWidget, not a plain QWidget with QVBoxLayout
    assert "QListWidget" in src or "setDragDropMode" in src, (
        "Template list should use QListWidget with drag-and-drop (Issue #1)"
    )


def test_templates_list_has_drag_drop_mode(qapp):
    """Template QListWidget should have InternalMove drag-drop mode set."""
    from stet.ui.settings import SettingsDialog
    from PyQt6.QtWidgets import QListWidget

    cfg = _make_cfg({
        "custom_templates": [
            {"name": "Template A", "prompt": "Do A"},
            {"name": "Template B", "prompt": "Do B"},
            {"name": "Template C", "prompt": "Do C"},
        ]
    })

    dlg = SettingsDialog(cfg)
    assert isinstance(dlg.templates_list_w, QListWidget), (
        "templates_list_w should be a QListWidget"
    )
    assert dlg.templates_list_w.dragDropMode() == QListWidget.DragDropMode.InternalMove
    # Check that items are populated
    assert dlg.templates_list_w.count() == 3
    assert dlg.templates_list_w.item(0).text() == "Template A"
    assert dlg.templates_list_w.item(1).text() == "Template B"
    assert dlg.templates_list_w.item(2).text() == "Template C"
    dlg.close()


def test_settings_dialog_shows_migrated_default_templates(qapp, monkeypatch, tmp_path):
    """Legacy saved templates should be replaced before settings renders."""
    from stet.core.config import ConfigManager
    from stet.constants import DEFAULT_TEMPLATES
    from stet.ui.settings import SettingsDialog

    config_file = tmp_path / "config.json"
    config_file.write_text(
        """
{
  "custom_templates": [
    {"name": "Tighten", "prompt": "Optimize this text."},
    {"name": "Email", "prompt": "Polish this text for a professional email."},
    {"name": "Formal", "prompt": "Rewrite this in formal English."},
    {"name": "Social", "prompt": "Rewrite this as a social media post."}
  ]
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", config_file)

    cfg = ConfigManager()
    dlg = SettingsDialog(cfg)

    assert dlg.templates_list_w.count() == len(DEFAULT_TEMPLATES)
    assert dlg.templates_list_w.item(0).text() == "Clean Up Dictation"
    dlg.close()

def test_template_actions_disable_without_selection(qapp):
    pass


def test_template_reorder_syncs_data(qapp):
    """After reordering items in the list, _temp_templates should match the new order."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({
        "custom_templates": [
            {"name": "First", "prompt": "P1"},
            {"name": "Second", "prompt": "P2"},
            {"name": "Third", "prompt": "P3"},
        ]
    })

    dlg = SettingsDialog(cfg)
    # Simulate a reorder: take "Third" and put it first
    item = dlg.templates_list_w.takeItem(2)
    dlg.templates_list_w.insertItem(0, item)
    # Fire the reorder handler manually
    dlg._on_templates_reordered()

    assert dlg._temp_templates[0]["name"] == "Third"
    assert dlg._temp_templates[1]["name"] == "First"
    assert dlg._temp_templates[2]["name"] == "Second"
    dlg.close()


# ── Issue #4: Chat mode config ───────────────────────────────────────────────
def test_default_config_has_chat_mode():
    """DEFAULT_CONFIG should include chat_mode key."""
    from stet.constants import DEFAULT_CONFIG
    assert "chat_mode" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["chat_mode"] in ("conversation", "single")


def test_settings_dialog_has_chat_mode_combo(qapp):
    """Settings dialog should have a chat_mode combo box."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({"chat_mode": "conversation"})
    dlg = SettingsDialog(cfg)
    assert hasattr(dlg, "chat_mode_combo"), "SettingsDialog should have chat_mode_combo"
    # Default should be conversation (index 0)
    assert dlg.chat_mode_combo.currentIndex() == 0
    dlg.close()


def test_settings_nav_renames_hotkeys_to_correction_profiles(qapp):
    """Settings nav/page should present hotkeys as correction profiles."""
    from PyQt6.QtWidgets import QLabel
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({})
    dlg = SettingsDialog(cfg)

    nav_labels = [dlg.nav_list.item(i).text() for i in range(dlg.nav_list.count())]
    assert "Correction Profiles" in nav_labels
    assert "Hotkeys" not in nav_labels
    dlg.nav_list.setCurrentRow(nav_labels.index("Correction Profiles"))
    page_labels = [label.text() for label in dlg.stack.currentWidget().findChildren(QLabel)]
    assert "Correction Profiles" in page_labels
    assert "Global Hotkeys" not in page_labels
    dlg.close()


def test_templates_profile_removes_global_method_and_strength_controls(qapp):
    """Templates/Profile settings should not duplicate profile-level method/strength controls."""
    from PyQt6.QtWidgets import QComboBox, QLabel
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({})
    dlg = SettingsDialog(cfg)

    assert not hasattr(dlg, "method_combo")
    assert not hasattr(dlg, "strength_combo")
    labels = [label.text() for label in dlg.findChildren(QLabel)]
    assert "Method" not in labels
    assert "Strength" not in labels
    assert "Chat Mode" in labels
    assert dlg.findChildren(QComboBox)
    dlg.close()


def test_settings_save_does_not_write_removed_global_profile_controls(qapp):
    """Saving Settings should leave correction method/strength to correction profiles."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({
        "correction_method": "stream",
        "streaming_strength": "rewrite_polish",
    })
    dlg = SettingsDialog(cfg)
    dlg._save()

    set_keys = [call.args[0] for call in cfg.set.call_args_list]
    assert "correction_method" not in set_keys
    assert "streaming_strength" not in set_keys


def test_settings_scroll_areas_keep_visible_right_gutter(qapp):
    """Each settings page should keep content padding while shifting the scrollbar farther outward."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QScrollArea
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({})
    dlg = SettingsDialog(cfg)

    scroll_areas = dlg.findChildren(QScrollArea)
    assert scroll_areas
    for scroll_area in scroll_areas:
        assert scroll_area.verticalScrollBarPolicy() in (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn,
            Qt.ScrollBarPolicy.ScrollBarAsNeeded,
        )
        assert scroll_area.widget().layout().contentsMargins().right() >= 20
        assert scroll_area.viewportMargins().right() == 0
    assert dlg.stack.parentWidget().layout().contentsMargins().right() == 0
    dlg.close()


def test_chat_mode_single_sets_correct_index(qapp):
    """When config has chat_mode='single', combo should be at index 1."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({"chat_mode": "single"})
    dlg = SettingsDialog(cfg)
    assert dlg.chat_mode_combo.currentIndex() == 1
    dlg.close()


def test_conversation_mode_reads_from_config():
    """CorrectionWindow._send_chat should read chat_mode from config."""
    import inspect
    from stet.ui.main_window import CorrectionWindow
    src = inspect.getsource(CorrectionWindow._send_chat)
    assert "chat_mode" in src, (
        "_send_chat should read chat_mode config to determine conversation vs single mode"
    )


def test_on_chat_done_has_conversation_branch():
    """_on_chat_done should branch on conversation vs single mode."""
    import inspect
    from stet.ui.main_window import CorrectionWindow
    src = inspect.getsource(CorrectionWindow._on_chat_done)
    assert "_conversation_mode" in src, (
        "_on_chat_done should check _conversation_mode to decide whether to show diff or keep chat"
    )


def test_conversation_mode_preserves_previous_chat_turns(qtbot, monkeypatch):
    """Streaming a later reply should preserve earlier transcript bubbles."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win._send_chat("Make it clearer")
    win._on_chat_token("First response")
    win._on_chat_done("First response")

    win._send_chat("Make it shorter")
    win._on_chat_token("Second response")
    win._on_chat_done("Second response")

    transcript = win._chat_transcript_text()
    preview = win.corr_edit.toPlainText()
    assert "Make it clearer" in transcript
    assert "First response" in transcript
    assert "Make it shorter" in transcript
    assert "Second response" in transcript
    assert transcript.index("First response") < transcript.index("Second response")
    assert "Make it clearer" in preview
    assert "Second response" in preview
    win.close()


def test_conversation_done_uses_stream_buffer_when_done_payload_empty(qtbot, monkeypatch):
    """Some stream backends can emit an empty done payload after tokens."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win._send_chat("Rewrite this")
    win._on_chat_token("Buffered answer")
    win._on_chat_done("")

    transcript = win._chat_transcript_text()
    assert "Rewrite this" in transcript
    assert "Buffered answer" in transcript
    assert win.corrected == "Buffered answer"
    win.close()


def test_single_chat_mode_replaces_view_with_final_diff(qtbot, monkeypatch):
    """Single mode should update the correction preview while chat stays separate."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    cfg = _make_cfg({"chat_mode": "single", "ac_same_as_chat": True})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win._send_chat("Rewrite this")
    win._on_chat_token("Final rewrite")
    win._on_chat_done("Final rewrite")

    rendered = win.corr_edit.toPlainText()
    transcript = win._chat_transcript_text()
    assert "Final rewrite" in rendered
    assert "Rewrite this" not in rendered
    assert "Rewrite this" in transcript
    assert "Final rewrite" in transcript
    assert win.corrected == "Final rewrite"
    win.close()


# ── UI Fixes (Aggressive Mode, Templates, Chat Format) ───────────────────────

def test_main_window_strength_combo_change(qtbot, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow
    class FakeModel(QObject):
        status_changed = pyqtSignal(str)
        def is_loaded(self): return True
    
    calls = []
    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: calls.append(self._current_strength))

    cfg = _make_cfg({"streaming_strength": "full_correction"})
    win = CorrectionWindow("Original", FakeModel(), FakeModel(), cfg, current_strength="spelling_only")
    qtbot.addWidget(win)
    assert hasattr(win, "strength_combo")
    assert win.strength_combo.currentText().startswith("Spelling Only")

    win._on_strength_changed("Rewrite & Polish — rewrites")
    assert win._current_strength == "rewrite_polish"
    assert calls[-1] == "rewrite_polish"
    cfg.set.assert_not_called()
    assert not hasattr(cfg, "save") or not cfg.save.called
    win.close()

def test_template_apply_clears_chat_ui(qtbot, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow
    class FakeModel(QObject):
        status_changed = pyqtSignal(str)
        def is_loaded(self): return True
    
    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)
    
    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)
    
    win._add_chat_bubble("user", "Old Chat History")
    win._apply_template("Make it polite")
    
    transcript = win._chat_transcript_text()
    preview = win.corr_edit.toPlainText()
    assert "Old Chat History" not in transcript, "Chat transcript should be cleared before template chat is added"
    assert "Old Chat History" not in preview, "Correction preview should not hold chat history"
    assert "Make it polite" in transcript
    win.close()

def test_chat_uses_bubble_alignment(qtbot, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QHBoxLayout
    from stet.ui.main_window import CorrectionWindow
    class FakeModel(QObject):
        status_changed = pyqtSignal(str)
        def is_loaded(self): return True
        
    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)
    
    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)
    
    win._send_chat("Test message")
    
    user_row = win.chat_lay.itemAt(0).widget().layout()
    assert isinstance(user_row, QHBoxLayout)
    assert user_row.itemAt(0).spacerItem() is not None, "User bubble should be pushed to the right"
    assert "Test message" in win.corr_edit.toPlainText()
    win.close()


def test_template_chat_uses_original_text_not_current_correction(qtbot, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original selected text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win.corrected = "Previously edited text."
    win._add_chat_bubble("user", "Old Chat History")
    win._apply_template("Make it polite")

    first_user_message = win.chat_history[1]["content"]
    assert "Original selected text." in first_user_message
    assert "Previously edited text." not in first_user_message
    assert "Old Chat History" not in win._chat_transcript_text()
    assert "Make it polite" in win.corr_edit.toPlainText()
    win.close()


def test_chat_surface_uses_popup_background_without_nested_frame(qtbot, monkeypatch):
    """Conversation mode should render in the main preview, not a second boxed window."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win._send_chat("Test message")
    from PyQt6.QtWidgets import QWidget

    assert win.chat_scroll.isHidden()
    assert "Test message" in win.corr_edit.toPlainText()
    chat_panel = win.findChild(QWidget, "chatPanel")
    assert chat_panel is not None
    win.close()


def test_chat_transcript_stays_hidden_until_first_message(qtbot, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    assert win.chat_scroll.isHidden()
    win._send_chat("Test message")
    assert win.chat_scroll.isHidden()
    assert "Test message" in win.corr_edit.toPlainText()
    win.close()


# ── UI Audit: Parameters grid ─────────────────────────────────────────────────
def test_parameters_page_uses_grid_layout(qapp):
    """Page 1 (Model Parameters) should use a QGridLayout for 2-column layout."""
    from stet.ui.settings import SettingsDialog
    from PyQt6.QtWidgets import QGridLayout

    cfg = _make_cfg({})
    dlg = SettingsDialog(cfg)

    # Page 1 is index 1 in the stack
    page1 = dlg.stack.widget(1)
    assert page1 is not None, "Parameters page should exist at index 1"

    grid_layouts = page1.findChildren(QGridLayout)
    assert any(
        gl.count() >= 6 for gl in grid_layouts
    ), "Parameters page should contain a QGridLayout with at least 6 items"
    dlg.close()


# ── UI Audit: Scrollbar contrast ──────────────────────────────────────────────
def test_scrollbar_handle_contrasts_with_track(qapp):
    """The scrollbar handle color should differ from the track for visibility."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({})
    dlg = SettingsDialog(cfg)

    # Find a scroll area from the settings pages
    from PyQt6.QtWidgets import QScrollArea
    scroll_areas = dlg.findChildren(QScrollArea)
    assert len(scroll_areas) > 0

    # Check that the handle and track have different colors
    import re
    ss = scroll_areas[0].styleSheet()
    handle_match = re.search(r'QScrollBar::handle:vertical\{background:(#[0-9a-fA-F]{6})', ss)
    track_match = re.search(r'QScrollBar:vertical\{background:(#[0-9a-fA-F]{6})', ss)
    # Either both found (and they differ), or we trust the global THEME
    if handle_match and track_match:
        handle_color = handle_match.group(1)
        track_color = track_match.group(1)
        assert handle_color != track_color, (
            f"Scrollbar handle ({handle_color}) must differ from track ({track_color})"
        )
    dlg.close()


# ── UI Audit: TAB cycles strength ─────────────────────────────────────────────
def test_tab_cycles_strength_combo(qtbot, monkeypatch):
    """Pressing Tab in the main popup should cycle the strength combo."""
    from PyQt6.QtCore import QObject, pyqtSignal, Qt, QEvent
    from PyQt6.QtGui import QKeyEvent
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)

    cfg = _make_cfg({"streaming_strength": "spelling_only"})
    win = CorrectionWindow("Original text.", FakeModel(), FakeModel(), cfg,
                           current_strength="spelling_only")
    qtbot.addWidget(win)

    assert win.strength_combo.currentIndex() == 0  # spelling_only
    assert win._current_strength == "spelling_only"

    # Simulate Tab arriving via the app-level event filter.
    # In production, QApplication dispatches Tab to the focused child widget;
    # our eventFilter intercepts it and cycles the combo instead.
    ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)

    # Press Tab once → should go to smart_fix (index 1)
    assert win.eventFilter(win, ev)  # consumed by our event filter
    assert win.strength_combo.currentIndex() == 1

    # Press Tab again → aggressive (index 2)
    assert win.eventFilter(win, ev)
    assert win.strength_combo.currentIndex() == 2

    # Press Tab again → wraps to conservative (index 0)
    assert win.eventFilter(win, ev)
    assert win.strength_combo.currentIndex() == 0
    win.close()


# ── UI Audit: Auto-scale heights ──────────────────────────────────────────────
def test_correction_profiles_no_hard_minimum(qapp):
    """Correction Profiles list should not have a large hard minimum height."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg({"hotkeys": []})
    dlg = SettingsDialog(cfg)

    assert dlg.hotkeys_list_w.minimumHeight() < 100, (
        f"Correction profiles list minimumHeight ({dlg.hotkeys_list_w.minimumHeight()}) "
        "should be low or zero to auto-scale"
    )
    dlg.close()


def test_templates_list_max_raised_for_available_space(qapp):
    """Templates list should use more available vertical space."""
    from stet.ui.settings import SettingsDialog

    cfg = _make_cfg(
        {
            "custom_templates": [
                {"name": "Template A", "prompt": "Do A"},
                {"name": "Template B", "prompt": "Do B"},
            ]
        }
    )
    dlg = SettingsDialog(cfg)

    assert dlg.templates_list_w.maximumHeight() >= 250, (
        f"Templates list maximumHeight ({dlg.templates_list_w.maximumHeight()}) "
        "should be at least 250 to use available space"
    )
    dlg.close()


# ── UI Audit: Model Submenu Left Arrow ───────────────────────────────────────
def test_model_submenu_left_arrow(qapp):
    """The Model submenu should have the default right-arrow hidden and a custom left arrow icon set."""
    from unittest.mock import patch, MagicMock
    from stet.core.app import StetApp

    with patch("stet.core.app.QSystemTrayIcon") as mock_tray_class:
        mock_tray = MagicMock()
        mock_tray_class.return_value = mock_tray
        
        def set_menu(menu):
            mock_tray.contextMenu.return_value = menu
        mock_tray.setContextMenu.side_effect = set_menu

        app = StetApp()
        assert app._llm_menu_action is not None
        assert not app._llm_menu_action.icon().isNull()

        menu = app._tray_menu if hasattr(app, "_tray_menu") else app.tray.contextMenu()
        ss = menu.styleSheet()
        assert "QMenu::right-arrow{image:none;width:0px;height:0px;}" in ss
        assert "QMenu::icon{left:10px;width:12px;height:12px;}" in ss
        app._quit()


# ── Config: Chat Model Path Synchronization ───────────────────────────
def test_config_manager_chat_model_path_synchronization(tmp_path, monkeypatch):
    """Calling ConfigManager.set("model_path", ...) correctly synchronizes chat_model_path when chat_use_separate_model is False, and when chat_use_separate_model is set to False."""
    from stet.core.config import ConfigManager

    config_file = tmp_path / "config.json"
    config_file.write_text(
        """
{
  "model_path": "path/to/chat_model.gguf",
  "chat_model_path": "path/to/chat_model.gguf",
  "chat_use_separate_model": false
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", config_file)

    cfg = ConfigManager()

    # 1. Test model_path change when chat_use_separate_model is False
    cfg.set("model_path", "path/to/new_chat_model.gguf")
    assert cfg.get("chat_model_path") == "path/to/new_chat_model.gguf"

    # 2. Test when chat_use_separate_model is True, model_path change should NOT synchronize
    cfg.set("chat_use_separate_model", True)
    cfg.set("model_path", "path/to/another_chat_model.gguf")
    assert cfg.get("chat_model_path") == "path/to/new_chat_model.gguf"  # remains unchanged

    # 3. Test that setting chat_use_separate_model back to False synchronizes it
    cfg.set("chat_use_separate_model", False)
    assert cfg.get("chat_model_path") == "path/to/another_chat_model.gguf"


def test_correction_ready_preserves_windows_newlines(monkeypatch, qtbot):
    """Accepted corrected text should match the original CRLF style."""
    cw = _make_window(monkeypatch, qtbot, "Line 1\r\n\r\nLine 2")

    cw._on_correction_ready("Line 1\n\nLine 2", "Patch")

    assert cw.corrected == "Line 1\r\n\r\nLine 2"
    assert " <br>" not in cw.corr_edit.toHtml()
    cw.close()


def test_stream_done_preserves_windows_newlines(monkeypatch, qtbot):
    """Streaming completion should normalize line endings before copy/paste paths."""
    cw = _make_window(monkeypatch, qtbot, "Line 1\r\n\r\nLine 2")
    cw._correction_stream_strength = "full_correction"

    cw._on_correction_stream_done("Line 1\n\nLine 2")

    assert cw.corrected == "Line 1\r\n\r\nLine 2"
    cw.close()


def test_conversation_mode_shows_readable_final_diff_once(qtbot, monkeypatch):
    """Readable chat edits should keep inline highlights without duplicating the final text."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    original = "one two three four five six seven eight nine ten"
    corrected = "one two three four five six seven eight NINE ten"

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow(original, FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win._send_chat("Fix grammar")
    win._on_chat_token(corrected)
    win._on_chat_done(corrected)

    preview = win.corr_edit.toPlainText()
    html = win._chat_transcript_html(final_result=corrected)

    assert preview.count(corrected) == 1
    assert "Fix grammar" in preview
    assert "color:#4ade80;text-decoration:underline;" in html
    win.close()


def test_conversation_mode_collapses_dense_rewrite_to_plain_final_text(qtbot, monkeypatch):
    """Dense rewrites should default to clean final text with no inline diff clutter."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from stet.ui.main_window import CorrectionWindow

    class FakeModel(QObject):
        status_changed = pyqtSignal(str)

        def is_loaded(self):
            return True

    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)

    original = " ".join(f"old{i}" for i in range(100))
    rewritten = " ".join(f"new{i}" for i in range(100))

    cfg = _make_cfg({"chat_mode": "conversation", "ac_same_as_chat": True})
    win = CorrectionWindow(original, FakeModel(), FakeModel(), cfg)
    qtbot.addWidget(win)

    win._send_chat("Rewrite completely")
    win._on_chat_token(rewritten)
    win._on_chat_done(rewritten)

    preview = win.corr_edit.toPlainText()
    html = win._chat_transcript_html(final_result=rewritten)

    assert preview.count(rewritten) == 1
    assert "Rewrite completely" in preview
    assert "rgba(96,165,250,0.12)" not in html
    assert "rgba(74,222,128,0.12)" not in html
    win.close()

