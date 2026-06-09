from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget


class SilentCorrectionOSD(QWidget):
    """Frameless floating notification for silent (background) correction results.

    Three visual states, using the same status-dot vocabulary as the main
    CorrectionWindow header:
    - "loading" — amber dot (pulsing), signals "working on it"
    - "success" — green dot, auto-dismiss
    - "warning" — red dot, for errors/no-change

    Appears at the bottom-center of the active screen with fade-in/out animation.
    """

    # Status dot colors — same palette as CorrectionWindow.status_lbl
    _STATE_COLORS = {
        "loading": "#fbbf24",  # amber — matches "⏳ Processing" in main window
        "success": "#4ade80",  # green — matches "✓ Done" in main window
        "warning": "#f87171",  # red   — matches "⚠ Could not correct"
    }

    def __init__(self, message: str, state: str = "success", parent=None):
        """state: 'loading', 'success', or 'warning'"""
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # No WA_TranslucentBackground — the OSD is a solid #121315 surface,
        # matching the app's dark card background. Translucency was only
        # needed by the old design to expose padding outside the nested card.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._state = state
        self._build_ui(message, state)
        self._position()

    def _build_ui(self, message: str, state: str):
        # The OSD itself IS the surface — no nested card widget.
        self.setStyleSheet(
            "SilentCorrectionOSD{background: #121315;border: 1px solid #28292c;}"
        )

        inner = QHBoxLayout(self)
        inner.setContentsMargins(20, 12, 24, 12)
        inner.setSpacing(0)

        # App identity label — dim, uppercase, matches the method badge style
        app_lbl = QLabel("STET")
        app_lbl.setStyleSheet(
            "QLabel{"
            "color: #3a3b3e;"
            "font-size: 10px;"
            "font-weight: 600;"
            "letter-spacing: 0.08em;"
            "font-family: 'IBM Plex Mono', 'Consolas', monospace;"
            "background: transparent;"
            "padding-right: 12px;"
            "}"
        )
        inner.addWidget(app_lbl)

        # Status dot — same visual language as the main window header
        dot_color = self._STATE_COLORS.get(state, "#88898c")
        self._dot_lbl = QLabel("●")
        self._dot_lbl.setStyleSheet(
            f"QLabel{{"
            f"color: {dot_color};"
            f"font-size: 9px;"
            f"background: transparent;"
            f"padding-right: 8px;"
            f"}}"
        )
        inner.addWidget(self._dot_lbl)

        # Message text
        msg_lbl = QLabel(message)
        msg_lbl.setStyleSheet(
            "QLabel{"
            "color: #ededee;"
            "font-size: 12px;"
            "font-weight: 500;"
            "background: transparent;"
            "font-family: 'IBM Plex Mono', 'Consolas', monospace;"
            "letter-spacing: 0.2px;"
            "}"
        )
        msg_lbl.setWordWrap(False)
        inner.addWidget(msg_lbl)

        self.adjustSize()

    def _position(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        sr = screen.availableGeometry()
        self.adjustSize()
        w = self.width()
        h = self.height()
        x = sr.x() + (sr.width() - w) // 2
        y = sr.y() + sr.height() - h - 52
        self.move(x, y)

    def show_animated(self, auto_dismiss: bool = True):
        """Fade in. If auto_dismiss, hold 2.5 s then fade out and close."""
        from PyQt6.QtCore import (
            QEasingCurve,
            QPropertyAnimation,
            QSequentialAnimationGroup,
        )

        self.setWindowOpacity(0.0)
        self.show()

        fade_in = QPropertyAnimation(self, b"windowOpacity")
        fade_in.setDuration(160)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutQuart)

        if not auto_dismiss:
            # Loading state: fade in, stay visible, pulse the status dot
            self._anim_seq = fade_in
            fade_in.start()
            self._start_dot_pulse()
            return

        hold = QPropertyAnimation(self, b"windowOpacity")
        hold.setDuration(2500)
        hold.setStartValue(1.0)
        hold.setEndValue(1.0)

        fade_out = QPropertyAnimation(self, b"windowOpacity")
        fade_out.setDuration(400)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InQuart)

        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(fade_in)
        seq.addAnimation(hold)
        seq.addAnimation(fade_out)
        seq.finished.connect(self.close)
        self._anim_seq = seq
        seq.start()

    def _start_dot_pulse(self):
        """Pulse the status dot opacity for the loading state.

        Motion conveys state (active processing), not decoration.
        Uses a QTimer cycle: 800ms period, toggles between full and dim.
        """
        self._pulse_visible = True
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(800)
        self._pulse_timer.timeout.connect(self._toggle_dot)
        self._pulse_timer.start()

    def _toggle_dot(self):
        """Toggle the dot between full color and dim to indicate activity."""
        self._pulse_visible = not self._pulse_visible
        dot_color = self._STATE_COLORS.get(self._state, "#88898c")
        if self._pulse_visible:
            self._dot_lbl.setStyleSheet(
                f"QLabel{{color:{dot_color};font-size:9px;"
                f"background:transparent;padding-right:8px;}}"
            )
        else:
            self._dot_lbl.setStyleSheet(
                "QLabel{color:#3a3b3e;font-size:9px;"
                "background:transparent;padding-right:8px;}"
            )
