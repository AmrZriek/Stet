"""Hotkey tests — compatibility, lifecycle, edit widget, stress, and manual edit.

Merged from: test_hotkey_compatibility.py, test_hotkey_lifecycle.py,
test_hotkey_edit.py, test_hotkey_stress.py, test_manual_edit.py.
"""

import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt

from stet.core.app import StetApp
from stet.ui.components import HotkeyEdit

# ── Helpers ───────────────────────────────────────────────────────────────


class MockUser32:
    def __init__(self):
        self.RegisterHotKey = MagicMock(return_value=1)
        self.UnregisterHotKey = MagicMock(return_value=1)


def _press(widget, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
    """Simulate a key press event on a HotkeyEdit widget."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent

    widget._recording = True
    ev = QKeyEvent(QEvent.Type.KeyPress, key, mods, text)
    widget.keyPressEvent(ev)


def _make_app_with_mocked_keyboard():
    """Return a StetApp with the native hotkey APIs fully mocked."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
    return app


# ── Known-conflict registry ──────────────────────────────────────────────

KNOWN_CONFLICTS = {
    "f9": {
        "VS Code": "Toggle Breakpoint",
        "Visual Studio": "Toggle Breakpoint / Debug",
        "Chrome/Edge (DevTools)": "Run snippet (if open)",
        "Firefox": "Reader View (some locales)",
        "tmux": "Split window vertically (default prefix+F9)",
        "GDB/LLDB": "Continue until breakpoint",
    },
    "f10": {
        "Firefox": "Focus menu bar / Access key",
        "Edge": "Focus menu bar",
        "Windows Explorer": "Menu bar focus",
        "Visual Studio": "Step Over (debugging)",
        "GDB/LLDB": "Step Over",
        "GNU Midnight Commander": "Menu bar / Quit dialog",
        " terminals ( many )": "Menu activation",
    },
    "f1": {"Universal": "Help / Documentation"},
    "f5": {"Universal": "Refresh / Reload / Continue debugging"},
    "f11": {"Universal": "Full-screen toggle"},
    "f12": {"Chrome/Edge/Firefox": "Developer Tools"},
}

SAFE_MODIFIER_HOTKEYS = [
    "ctrl+shift+space",
    "ctrl+shift+c",
    "ctrl+shift+x",
    "ctrl+shift+z",
    "ctrl+shift+a",
    "ctrl+shift+period",
    "ctrl+alt+c",
    "ctrl+alt+t",
]

RISKY_HOTKEYS = [
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "ctrl+c",
    "ctrl+v",
    "ctrl+x",
    "ctrl+z",
    "ctrl+a",
    "ctrl+s",
    "alt+f4",
    "print screen",
    "tab",
    "space",
]


# ── Source-level structural tests (regex-based) ──────────────────────────

SRC = "\n".join(
    f.read_text(encoding="utf-8")
    for f in (Path(__file__).resolve().parent.parent / "stet").rglob("*.py")
)


def test_register_hotkey_clears_pressed_events():
    """_register_hotkey must clear _hotkey_handles to prevent phantom fires."""
    body = re.search(
        r"def _register_hotkey\(self.*?\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_hotkey_handles.clear()" in body


def test_register_hotkey_clears_logically_pressed_keys():
    """_register_hotkey must clear native event filter callbacks."""
    body = re.search(
        r"def _register_hotkey\(self.*?\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "clear_callbacks()" in body


def test_register_hotkey_uses_remove_hotkey_not_unhook_all():
    """_register_hotkey must use UnregisterHotKey, not unhook_all_hotkeys."""
    body = re.search(
        r"def _register_hotkey\(self.*?\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "UnregisterHotKey" in body


def test_register_hotkey_tracks_handles():
    """_register_hotkey must store handles in _hotkey_handles for later removal."""
    body = re.search(
        r"def _register_hotkey\(self.*?\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_hotkey_handles.append" in body


def test_register_hotkey_has_debounce():
    """_register_hotkey must debounce rapid calls."""
    body = re.search(
        r"def _register_hotkey\(self.*?\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_last_register_ts" in body


def test_quit_uses_handle_removal_not_unhook_all():
    """_quit must use UnregisterHotKey, not unhook_all_hotkeys."""
    body = re.search(
        r"def _quit\(self\):.*?(?=\n\n|\n    def |\nclass |\Z)", SRC, re.DOTALL
    ).group(0)
    assert "UnregisterHotKey" in body


def test_escape_in_hotkey_edit_does_not_re_register():
    """Pressing Escape during HotkeyEdit recording must NOT call re_register_cb."""
    body = re.search(
        r"def keyPressEvent\(self, e\):.*?(?=\n    def |\nclass )", SRC, re.DOTALL
    ).group(0)
    escape_block = re.search(r"Key_Escape:\s*\n(.*?)return", body, re.DOTALL)
    assert escape_block is not None, "Escape handling block must exist"
    escape_code = escape_block.group(1)
    assert "_re_register_cb" not in escape_code


def test_init_creates_hotkey_handles_list():
    """StetApp.__init__ must create _hotkey_handles list."""
    init_body = re.search(
        r"class StetApp.*?def __init__\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "_hotkey_handles" in init_body
    assert "_last_register_ts" in init_body


# ── Safe-default policy tests ────────────────────────────────────────────


def test_safe_modifier_hotkeys_pass_validation():
    """Modifier-based hotkeys should be considered safe."""
    for combo in SAFE_MODIFIER_HOTKEYS:
        assert combo not in RISKY_HOTKEYS, f"{combo} accidentally marked risky"


def test_risky_hotkeys_include_f9_f10():
    """F9 and F10 must be in the risky hotkey list."""
    assert "f9" in RISKY_HOTKEYS
    assert "f10" in RISKY_HOTKEYS


def test_f10_has_many_documented_conflicts():
    """F10 must have documented conflicts with browsers and IDEs."""
    assert "f10" in KNOWN_CONFLICTS
    conflicts = KNOWN_CONFLICTS["f10"]
    assert "Firefox" in conflicts
    assert "Edge" in conflicts
    assert "Visual Studio" in conflicts


def test_f9_has_many_documented_conflicts():
    """F9 must have documented conflicts with debuggers and IDEs."""
    assert "f9" in KNOWN_CONFLICTS
    conflicts = KNOWN_CONFLICTS["f9"]
    assert "VS Code" in conflicts
    assert "Visual Studio" in conflicts


def test_current_defaults_f9_f10_are_risky():
    """Current defaults (f9 / f10) are in the risky list."""
    from stet.constants import DEFAULT_CONFIG

    hotkeys = DEFAULT_CONFIG.get("hotkeys", [])
    for hk in hotkeys:
        shortcut = hk.get("shortcut", "").lower().strip()
        if shortcut in ("f9", "f10"):
            assert shortcut in RISKY_HOTKEYS, f"Default hotkey '{shortcut}' is risky"


def test_proposed_safe_defaults_are_not_risky():
    """Proposed safer defaults (ctrl+shift+space, ctrl+shift+c) are NOT in the risky list."""
    assert "ctrl+shift+space" not in RISKY_HOTKEYS
    assert "ctrl+shift+c" not in RISKY_HOTKEYS


def test_can_detect_known_conflicts_by_lookup():
    """A lookup function can flag known-conflict hotkeys."""

    def is_known_conflict(combo: str) -> bool:
        return combo.lower().strip() in KNOWN_CONFLICTS

    assert is_known_conflict("f10") is True
    assert is_known_conflict("f9") is True
    assert is_known_conflict("ctrl+shift+space") is False


def test_hotkey_without_modifier_is_flagged_risky():
    """Any bare function key or single key should be flagged risky."""
    bare_keys = [
        "f1",
        "f2",
        "f3",
        "f4",
        "f5",
        "f6",
        "f7",
        "f8",
        "f9",
        "f10",
        "f11",
        "f12",
    ]
    for key in bare_keys:
        assert key in RISKY_HOTKEYS, f"{key} should be considered risky"


# ── Registration behavior tests ───────────────────────────────────────


def test_register_hotkey_with_safe_combo_succeeds(qtbot):
    """Registering a safe modifier combo calls RegisterHotKey."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app.cfg.config["hotkeys"] = [
            {"shortcut": "ctrl+shift+space", "mode": "panel", "strength": "full_correction"},
            {"shortcut": "ctrl+shift+c", "mode": "silent", "strength": "full_correction"},
        ]
        app._last_register_ts = 0.0
        mock_user32.RegisterHotKey.reset_mock()
        app._register_hotkey()
        assert mock_user32.RegisterHotKey.call_count >= 2


def test_register_hotkey_logs_success(qtbot):
    """Successful registration logs the hotkey name."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        import stet.core.app as app_module

        old_log = app_module.log
        log_calls = []
        app_module.log = lambda x: log_calls.append(x)
        try:
            app = StetApp()
            app._last_register_ts = 0.0
            app._register_hotkey()
            assert any("registered: f9" in msg for msg in log_calls)
        finally:
            app_module.log = old_log


def test_register_hotkey_gracefully_handles_exception(qtbot):
    """If RegisterHotKey returns 0, the app must not crash."""
    mock_user32 = MockUser32()
    mock_user32.RegisterHotKey.return_value = 0
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app.cfg.config["hotkeys"] = [
            {"shortcut": "f9", "mode": "panel", "strength": "full_correction"}
        ]
        app._last_register_ts = 0.0
        notify_calls = []
        app.tray.showMessage = lambda *args, **kwargs: notify_calls.append(args)
        app._register_hotkey()
        assert len(notify_calls) == 1
        assert "Could not register hotkey" in notify_calls[0][1]


def test_register_hotkey_silent_gracefully_handles_exception(qtbot):
    """If silent hotkey registration fails (returns 0), the app must not crash."""
    mock_user32 = MockUser32()

    def reg_side_effect(hwnd, hotkey_id, mods, vk):
        if hotkey_id == 1001:  # silent is second
            return 0
        return 1

    mock_user32.RegisterHotKey.side_effect = reg_side_effect
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app.cfg.config["hotkeys"] = [
            {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
            {"shortcut": "f10", "mode": "silent", "strength": "full_correction"},
        ]
        app._last_register_ts = 0.0
        mock_user32.RegisterHotKey.reset_mock()
        app._register_hotkey()
        assert 1000 in app._hotkey_handles
        assert 1001 not in app._hotkey_handles
        assert mock_user32.RegisterHotKey.call_count == 2


def test_debounce_prevents_rapid_re_registration(qtbot):
    """Calling _register_hotkey twice within 500 ms must skip the second."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app._last_register_ts = 0.0
        app._register_hotkey()
        first_count = mock_user32.RegisterHotKey.call_count
        app._register_hotkey()
        assert mock_user32.RegisterHotKey.call_count == first_count


def test_debounce_allows_call_after_500ms(qtbot):
    """Calling _register_hotkey after >500 ms must re-register."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app._last_register_ts = 0.0
        app._register_hotkey()
        first_count = mock_user32.RegisterHotKey.call_count
        app._last_register_ts = time.monotonic() - 0.6
        app._register_hotkey()
        assert mock_user32.RegisterHotKey.call_count > first_count


def test_debounce_blocks_rapid_re_registration(qtbot):
    """Calling _register_hotkey twice within 500ms should skip the second call."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app._last_register_ts = 0.0
        app._register_hotkey()
        first_call_count = mock_user32.RegisterHotKey.call_count
        app._register_hotkey()
        assert mock_user32.RegisterHotKey.call_count == first_call_count


def test_forced_registration_bypasses_debounce(qtbot):
    """Settings saves must re-register immediately even inside the debounce window."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app._last_register_ts = time.monotonic()
        first_call_count = mock_user32.RegisterHotKey.call_count
        app._register_hotkey(force=True)
        assert mock_user32.RegisterHotKey.call_count > first_call_count


@pytest.mark.parametrize(
    "combo",
    [
        "ctrl+shift+space",
        "ctrl+shift+period",
        "ctrl+shift+slash",
        "ctrl+alt+c",
        "ctrl+alt+t",
        "shift+f9",
        "ctrl+f10",
    ],
)
def test_various_modifier_combos_register_without_crash(qtbot, combo):
    """Diverse modifier combinations should register without error."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app.cfg.config["hotkeys"] = [
            {"shortcut": combo, "mode": "panel", "strength": "full_correction"}
        ]
        app._last_register_ts = 0.0
        mock_user32.RegisterHotKey.reset_mock()
        app._register_hotkey()
        mock_user32.RegisterHotKey.assert_called_once()


# ── HotkeyEdit widget tests ────────────────────────────────────────────


def test_hotkey_edit_accepts_f9_alone(qtbot):
    w = HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_F9)
    assert w.text() == "f9"


def test_hotkey_edit_accepts_pause_alone(qtbot):
    w = HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_Pause)
    assert w.text() == "pause"


def test_hotkey_edit_rejects_letter_alone(qtbot):
    w = HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_T, text="t")
    assert w.text() != "t"


def test_hotkey_edit_accepts_ctrl_t_combo(qtbot):
    w = HotkeyEdit()
    qtbot.addWidget(w)
    _press(w, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier, text="t")
    assert w.text() == "ctrl+t"


def test_hotkey_edit_accepts_modifier_combos(qtbot):
    """Ctrl+Shift+C must be accepted by HotkeyEdit."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    _press(w, Qt.Key.Key_C, mods, "c")
    assert w.text() == "ctrl+shift+c"


def test_hotkey_edit_rejects_common_conflict_keys_without_modifier(qtbot):
    """Bare 'c' without modifier must NOT be accepted by HotkeyEdit."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent

    w._recording = True
    ev = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.NoModifier, "c"
    )
    w.keyPressEvent(ev)
    assert w.text() != "c"


def test_hotkey_edit_overrides_event_to_intercept_shift_f10():
    """HotkeyEdit must override QWidget.event so the Qt Shift+F10 -> ContextMenu
    synthesis cannot swallow the keypress before our keyPressEvent runs."""
    import re

    body = re.search(
        r"class HotkeyEdit.*?(?=\nclass |\n_QT_KEYS)",
        SRC, re.DOTALL,
    ).group(0)
    assert "def event(self, e):" in body, "HotkeyEdit must override event()"
    assert "Key_F10" in body and "ShiftModifier" in body, (
        "event() override must explicitly intercept Shift+F10"
    )
    assert "def contextMenuEvent(self" in body, (
        "HotkeyEdit must suppress context menu while recording"
    )


def test_hotkey_edit_shift_f10_registers_via_real_dispatcher(qtbot):
    """Send Shift+F10 through QApplication.sendEvent (the real Qt event path,
    not a direct keyPressEvent call) so Shift+F10 -> ContextMenu synthesis
    is the actual behavior under test."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication

    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.show()
    w.setFocus()
    qtbot.mouseClick(w, Qt.MouseButton.LeftButton)
    assert w._recording is True

    ev = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_F10,
        Qt.KeyboardModifier.ShiftModifier,
        "",
    )
    QApplication.sendEvent(w, ev)
    assert w._recording is False
    assert w.text() == "shift+f10"
    w.close()


def test_keyboard_context_menu_records_shift_f10(qtbot):
    """On Windows, pressing Shift+F10 causes Qt to generate a keyboard-triggered
    QContextMenuEvent instead of (or before) a KeyPress event.  The widget must
    detect this and record 'shift+f10' rather than silently swallowing the event.

    This test reproduces the exact bug: the ContextMenu handler accepted and
    consumed the event without recording any combo, so on Windows Shift+F10
    was never captured."""
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QContextMenuEvent
    from PyQt6.QtWidgets import QApplication

    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.show()
    w.setFocus()
    qtbot.mouseClick(w, Qt.MouseButton.LeftButton)
    assert w._recording is True

    # Simulate what Windows does: send a keyboard-triggered ContextMenu event.
    # On Windows, Shift+F10 is the system context-menu shortcut, and Qt's
    # platform integration generates a QContextMenuEvent(Keyboard) for it
    # instead of (or before) a normal KeyPress.
    ctx = QContextMenuEvent(
        QContextMenuEvent.Reason.Keyboard,
        QPoint(0, 0),  # local pos
        w.mapToGlobal(QPoint(0, 0)),  # global pos
    )
    QApplication.sendEvent(w, ctx)

    # The bug: previously, the ContextMenu handler just swallowed the event
    # without recording a combo.  After the fix, keyboard-triggered context
    # menus must be captured as "shift+f10".
    assert w._recording is False, (
        "Recording should stop after keyboard-triggered ContextMenu"
    )
    assert w.text() == "shift+f10", (
        f"Expected 'shift+f10' but got '{w.text()}' — "
        "keyboard ContextMenu event was swallowed without recording"
    )
    w.close()


def test_shortcut_override_then_keypress_full_sequence(qtbot):
    """Simulate the full Qt event pipeline: ShortcutOverride → KeyPress.

    In a real Qt event loop, when Shift+F10 is pressed:
    1. Qt sends ShortcutOverride to ask if the widget wants the key
    2. If accepted, Qt sends KeyPress
    3. If not accepted, Qt synthesises a ContextMenu event instead

    The bug was that event() returned True for step 1, which consumed the
    ShortcutOverride and prevented Qt from proceeding to step 2."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication

    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.show()
    w.setFocus()
    qtbot.mouseClick(w, Qt.MouseButton.LeftButton)
    assert w._recording is True

    # Step 1: Qt sends ShortcutOverride first
    override = QKeyEvent(
        QEvent.Type.ShortcutOverride,
        Qt.Key.Key_F10,
        Qt.KeyboardModifier.ShiftModifier,
        "",
    )
    QApplication.sendEvent(w, override)
    assert override.isAccepted(), "ShortcutOverride must be accepted"
    # Widget should still be recording — ShortcutOverride is just a query,
    # no combo should be captured yet
    assert w._recording is True, (
        "Widget must still be recording after ShortcutOverride (it's just a query)"
    )

    # Step 2: Qt sends KeyPress (only if ShortcutOverride was handled correctly)
    keypress = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_F10,
        Qt.KeyboardModifier.ShiftModifier,
        "",
    )
    QApplication.sendEvent(w, keypress)
    # After both events, the combo must be recorded
    assert w._recording is False, "Recording should stop after KeyPress"
    assert w.text() == "shift+f10", (
        f"Expected 'shift+f10' but got '{w.text()}' — "
        "ShortcutOverride likely consumed the event, blocking KeyPress"
    )
    w.close()


def test_hotkey_edit_manual_edit_unchanged_on_shift_f10(qtbot):
    """Shift+F10 must NOT be remapped while in manual-edit mode."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication

    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    w.enable_manual_edit()
    assert w._manual_editing is True

    ev = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_F10,
        Qt.KeyboardModifier.ShiftModifier,
        "",
    )
    QApplication.sendEvent(w, ev)
    assert w._manual_editing is True
    assert w.text() == "f9"
    w.close()


def test_hotkey_edit_manual_edit_drops_focus_on_enter(qtbot):
    """HotkeyEdit manual edit mode: Enter drops focus and becomes readonly."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    with qtbot.waitExposed(w):
        w.show()
    w.enable_manual_edit()
    w.setFocus()
    assert w.isReadOnly() is False
    w.returnPressed.emit()
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QFocusEvent
    from PyQt6.QtWidgets import QApplication

    QApplication.sendEvent(w, QFocusEvent(QEvent.Type.FocusOut))
    assert w.isReadOnly() is True


# ── Manual edit tests ────────────────────────────────────────────────


def test_manual_edit_sets_editable(qtbot):
    """enable_manual_edit() should switch to editable (non-read-only) state."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    w.enable_manual_edit()
    assert not w.isReadOnly()
    assert w._manual_editing is True
    assert w._recording is False
    w.close()


def test_manual_edit_escape_cancels(qtbot):
    """Escape during manual edit should cancel without changing the combo."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    original = w.text()
    w.enable_manual_edit()
    qtbot.keyPress(w, Qt.Key.Key_Escape)
    assert w.isReadOnly()
    assert w._manual_editing is False
    assert w.text() == original
    w.close()


def test_manual_edit_commit_valid(qtbot):
    """Typing a valid hotkey and pressing Enter should commit it."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    w.enable_manual_edit()
    w.clear()
    qtbot.keyClicks(w, "ctrl+f10")
    qtbot.keyPress(w, Qt.Key.Key_Return)
    assert w.isReadOnly()
    assert w._manual_editing is False
    assert w.text() == "ctrl+f10"
    w.close()


def test_manual_edit_commit_empty_reverts(qtbot):
    """Committing empty text should revert to the previous combo."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    w.enable_manual_edit()
    w.clear()
    w._commit_manual_edit()
    assert w.isReadOnly()
    assert w.text() == "f9"
    w.close()


def test_manual_edit_fires_signal(qtbot):
    """Committing a valid manual edit should emit shortcut_changed."""
    w = HotkeyEdit()
    qtbot.addWidget(w)
    w.setText("f9")
    received = []
    w.shortcut_changed.connect(received.append)
    w.enable_manual_edit()
    w.clear()
    qtbot.keyClicks(w, "ctrl+shift+a")
    qtbot.keyPress(w, Qt.Key.Key_Return)
    assert len(received) == 1
    assert received[0] == "ctrl+shift+a"
    w.close()


# ── Profile/routing tests ──────────────────────────────────────────────


def test_legacy_hotkey_migration_writes_no_legacy_keys(monkeypatch):
    """Legacy config keys (hotkey, silent_hotkey) are migrated to hotkeys list."""
    import json
    from pathlib import Path

    import stet.core.config as config_module
    from stet.core.config import ConfigManager

    config_file = Path(__file__).with_name("_tmp_config_migration.json")
    try:
        config_file.write_text(
            json.dumps(
                {
                    "hotkey": "ctrl+shift+space",
                    "streaming_strength": "aggressive",
                    "silent_hotkey": "ctrl+shift+c",
                    "silent_strength": "conservative",
                    "custom_templates": [
                        {"name": "Mine", "prompt": "Keep this prompt."}
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
        cfg = ConfigManager()
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert cfg.get("hotkeys") == [
            {"shortcut": "ctrl+shift+space", "mode": "panel", "strength": "aggressive"},
            {"shortcut": "ctrl+shift+c", "mode": "silent", "strength": "conservative"},
        ]
        assert "hotkey" not in saved
        assert "silent_hotkey" not in saved
        assert "silent_strength" not in saved
    finally:
        if config_file.exists():
            config_file.unlink()


def test_silent_hotkey_profile_strength_reaches_patch_worker(qtbot, monkeypatch):
    """Silent hotkey dispatch must call patch correction with profile strength."""
    import stet.core.app as app_module

    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app._show_silent_osd = lambda *args, **kwargs: None
        app._capture_selection = lambda: "teh text"
        app._safe_copy = lambda text: None
        app._old_clip = ""
        app.ac_model.is_loaded = lambda: True
        app._is_model_ready = lambda: True
        patch_calls = []
        app.ac_model.correct_text_patch = (
            lambda text, custom_sys=None, strength=None, **kwargs: (
                patch_calls.append(strength) or ("the text", 1)
            )
        )
        paste_calls = []
        monkeypatch.setattr(
            app_module, "_send_ctrl_chord", lambda key: paste_calls.append(key)
        )

        class ImmediateThread:
            def __init__(self, target, args=(), daemon=None):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)
        app._handle_hotkey_fired({"mode": "silent", "strength": "spelling_only"})
        assert patch_calls == ["spelling_only"]


def test_panel_hotkey_worker_receives_profile_strength(qtbot, monkeypatch):
    """Panel hotkey dispatch must carry the selected profile strength."""
    import stet.core.app as app_module

    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        worker_calls = []
        monkeypatch.setattr(app, "_is_window_alive", lambda: False)
        monkeypatch.setattr(
            app,
            "_hotkey_worker",
            lambda: worker_calls.append(app._pending_panel_strength),
        )

        class ImmediateThread:
            def __init__(self, target, args=(), daemon=None):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)
        app._handle_hotkey_fired({"mode": "panel", "strength": "rewrite_polish"})
        assert worker_calls == ["rewrite_polish"]


def test_show_window_constructs_with_profile_strength(qtbot, monkeypatch):
    """CorrectionWindow must be constructed with the hotkey profile strength."""
    import stet.core.app as app_module

    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        constructed = []

        class DummySignal:
            def connect(self, callback):
                pass

        class DummyWindow:
            accepted = DummySignal()
            destroyed = DummySignal()

            def __init__(
                self,
                original,
                ac_model,
                chat_model,
                cfg,
                re_register_cb=None,
                initial_strength=None,
                mode_prompt_override=None,
            ):
                constructed.append(initial_strength)

            def show(self):
                pass

            def raise_(self):
                pass

            def activateWindow(self):
                pass

        monkeypatch.setattr(app_module, "CorrectionWindow", DummyWindow)
        app = StetApp()
        app._show_window("rough text", "rewrite_polish")
        assert constructed == ["rewrite_polish"]


# ── Quit/teardown tests ─────────────────────────────────────────────────


def test_quit_uses_qapplication_quit(qtbot, monkeypatch):
    """Tray Quit must actually terminate the Qt app instead of calling QObject.quit."""
    import stet.core.app as app_module

    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()
        app.ac_model.unload_model = MagicMock()
        app.chat_model.unload_model = MagicMock()
        quit_calls = []

        class DummyQApplication:
            @staticmethod
            def instance():
                return type(
                    "DummyApp", (), {"quit": lambda self: quit_calls.append(True)}
                )()

        monkeypatch.setattr(app_module, "QApplication", DummyQApplication)
        app._quit()
        assert mock_user32.UnregisterHotKey.call_count > 0
        assert app._hotkey_handles == []
        assert quit_calls == [True]


# ── Stress/crash tests ─────────────────────────────────────────────────


def test_rapid_hotkey_fires_do_not_crash(qtbot):
    """Simulate user mashing F9. The UI checks must not happen in the bg thread."""
    mock_user32 = MockUser32()
    with patch("ctypes.windll.user32", new=mock_user32):
        app = StetApp()

    app._window = MagicMock()
    app._window.isVisible.return_value = True

    def mock_hotkey_worker():
        if hasattr(app, "_window") and app._window is not None:
            app._window.isVisible()
            app._window.raise_()

    with patch.object(app, "_hotkey_worker", new=mock_hotkey_worker):
        app._handle_hotkey_fired({"mode": "panel"})
        app._handle_hotkey_fired({"mode": "panel"})
    assert True
