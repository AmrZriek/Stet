import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLineEdit, QWidget


class HotkeyEdit(QLineEdit):
    shortcut_changed = pyqtSignal(str)

    _IDLE = """
        QLineEdit {
            background: transparent; border: none;
            padding: 6px 12px; color: #e2e8f0; font-size: 13px;
            text-transform: uppercase;
            selection-background-color: rgba(212,163,115,0.3);
            selection-color: #f5d6a8;
        }
        QLineEdit:focus { border: none; }
    """
    _REC = """
        QLineEdit {
            background: transparent; border: none;
            padding: 6px 12px; color: #f5d6a8; font-size: 13px;
            text-transform: uppercase;
            selection-background-color: rgba(212,163,115,0.3);
            selection-color: #f5d6a8;
        }
        QLineEdit:focus { border: none; }
    """
    _EDIT = """
        QLineEdit {
            background: transparent; border: none;
            padding: 6px 12px; color: #86efac; font-size: 13px;
            text-transform: uppercase;
            selection-background-color: rgba(74,222,128,0.3);
            selection-color: #86efac;
        }
        QLineEdit:focus { border: none; }
    """

    def __init__(self, parent=None, re_register_cb=None):
        super().__init__(parent)
        self._combo = ""
        self._recording = False
        self._manual_editing = False
        self._re_register_cb = re_register_cb
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(self._IDLE)
        self._refresh()
        self.returnPressed.connect(self._commit_manual_edit)

    def text(self) -> str:
        return self._combo

    def setText(self, val: str):
        self._combo = val.lower().strip()
        self._recording = False
        self.setStyleSheet(self._IDLE)
        self._refresh()
        self._update_container_style()

    def _refresh(self):
        display = (
            " + ".join(p.upper() for p in self._combo.split("+"))
            if self._combo
            else "Click to record"
        )
        super().setText(display)

    def _update_container_style(self):
        container = self.parent()
        if not isinstance(container, QWidget):
            return
        if self._recording:
            s = "#hotkey_container{background:rgba(212,163,115,0.10);border:1px solid rgba(212,163,115,0.8);border-radius:0px;}"
        elif self._manual_editing:
            s = "#hotkey_container{background:rgba(5,40,20,0.8);border:1px solid rgba(74,222,128,0.7);border-radius:0px;}"
        elif self.hasFocus():
            s = "#hotkey_container{background:transparent;border:1px solid rgba(212,163,115,0.5);border-radius:0px;}"
        else:
            s = "#hotkey_container{background:transparent;border:1px solid #28292c;border-radius:0px;}"
        container.setStyleSheet(
            s + "#hotkey_container:hover{border:1px solid #d4a373;}"
        )

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._update_container_style()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._recording:
            self._recording = True
            self.setStyleSheet(self._REC)
            super().setText("Press keys" + "\u2026")
            self._update_container_style()

    def focusOutEvent(self, e):
        if self._recording:
            self._recording = False
            self.setStyleSheet(self._IDLE)
            self._refresh()
        elif self._manual_editing:
            self._commit_manual_edit()
        super().focusOutEvent(e)
        self._update_container_style()

    def enable_manual_edit(self):
        """Enter manual-typing mode so the user can type a hotkey string directly.

        Switches the field to editable (green border), populates it with the
        current combo string (e.g. "ctrl+f9"), and awaits Enter or focus-loss
        to commit. Escape cancels back to the previous value.
        """
        self._recording = False
        self._manual_editing = True
        self.setReadOnly(False)
        self.setStyleSheet(self._EDIT)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        super().setText(self._combo.upper())
        self.selectAll()
        self._update_container_style()

    def _commit_manual_edit(self):
        """Validate and save the manually typed hotkey string.

        Accepts any non-empty string that contains at least one word character
        (native registration is permissive and will raise on registration if
        it's bad — the user will see a tray error rather than a crash here).
        """
        if not self._manual_editing:
            return
        self._manual_editing = False
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        typed = super().text().strip().lower()
        if typed and re.search(r"\w", typed):
            self._combo = typed
            self.shortcut_changed.emit(typed)
            if self._re_register_cb:
                try:
                    self._re_register_cb()
                except Exception:
                    pass
        # Restore display (reverts to previous _combo if typed was invalid/empty)
        self.setStyleSheet(self._IDLE)
        self._refresh()
        self._update_container_style()

    def keyPressEvent(self, e):
        if self._manual_editing:
            key = e.key()
            if key == Qt.Key.Key_Escape:
                self._manual_editing = False
                self.setReadOnly(True)
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setStyleSheet(self._IDLE)
                self._refresh()
                self._update_container_style()
                return
            # Let Qt handle normal character input; Enter is caught by returnPressed
            super().keyPressEvent(e)
            return
        if not self._recording:
            return
        key = e.key()
        mods = e.modifiers()
        if key == Qt.Key.Key_Escape:
            self._recording = False
            self.setStyleSheet(self._IDLE)
            self._refresh()
            self._update_container_style()
            # Escape cancels recording — no hotkey changed, no re-register needed.
            return
        if key in _MOD_KEYS:
            return
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        kn = _QT_KEYS.get(key) or (e.text().lower() or None)
        if not parts:
            if key in _STANDALONE_OK and kn:
                parts.append(kn)
                combo = kn
                self._recording = False
                self._combo = combo
                self.setStyleSheet(self._IDLE)
                self._refresh()
                self._update_container_style()
                self.shortcut_changed.emit(combo)
                if self._re_register_cb:
                    try:
                        self._re_register_cb()
                    except Exception:
                        pass
                return
            super().setText("Add Ctrl / Shift / Alt…")
            return
        if not kn:
            return
        parts.append(kn)
        combo = "+".join(parts)
        self._recording = False
        self._combo = combo
        self.setStyleSheet(self._IDLE)
        self._refresh()
        self._update_container_style()
        self.shortcut_changed.emit(combo)
        if self._re_register_cb:
            try:
                self._re_register_cb()
            except Exception:
                pass


_QT_KEYS = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Escape: "escape",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page up",
    Qt.Key.Key_PageDown: "page down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_F1: "f1",
    Qt.Key.Key_F2: "f2",
    Qt.Key.Key_F3: "f3",
    Qt.Key.Key_F4: "f4",
    Qt.Key.Key_F5: "f5",
    Qt.Key.Key_F6: "f6",
    Qt.Key.Key_F7: "f7",
    Qt.Key.Key_F8: "f8",
    Qt.Key.Key_F9: "f9",
    Qt.Key.Key_F10: "f10",
    Qt.Key.Key_F11: "f11",
    Qt.Key.Key_F12: "f12",
    Qt.Key.Key_Pause: "pause",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_ScrollLock: "scroll lock",
    Qt.Key.Key_Print: "print screen",
    Qt.Key.Key_Menu: "menu",
}

_MOD_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
}

_STANDALONE_OK = {
    Qt.Key.Key_F1,
    Qt.Key.Key_F2,
    Qt.Key.Key_F3,
    Qt.Key.Key_F4,
    Qt.Key.Key_F5,
    Qt.Key.Key_F6,
    Qt.Key.Key_F7,
    Qt.Key.Key_F8,
    Qt.Key.Key_F9,
    Qt.Key.Key_F10,
    Qt.Key.Key_F11,
    Qt.Key.Key_F12,
    Qt.Key.Key_Pause,
    Qt.Key.Key_Insert,
    Qt.Key.Key_ScrollLock,
    Qt.Key.Key_Print,
    Qt.Key.Key_Menu,
}
