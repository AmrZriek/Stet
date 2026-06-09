import tempfile
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt


def _checkbox_css() -> str:
    """Return QSS for checkboxes with a visible checkmark icon.

    Writes a small SVG to disk once (Qt QSS cannot embed data URIs for images).
    Uses the system temp directory so it works even when the install directory
    is read-only (e.g. Program Files).
    """
    svg_path = Path(tempfile.gettempdir()) / "stet_checkmark.svg"
    try:
        if not svg_path.exists():
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12">'
                '<path d="M2 6L5 9L10 3" stroke="white" stroke-width="2.2" '
                'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                encoding="utf-8",
            )
        p = str(svg_path).replace("\\", "/")
        return (
            "QCheckBox { color: #94a3b8; spacing: 8px; }"
            "QCheckBox:checked { color: #e2e8f0; }"
            "QCheckBox::indicator {"
            " width: 16px; height: 16px;"
            " border: 1.5px solid rgba(212,163,115,0.35);"
            " border-radius: 4px; background: rgba(4,10,28,0.8); }"
            "QCheckBox::indicator:hover { border: 1.5px solid rgba(212,163,115,0.65); }"
            f"QCheckBox::indicator:checked {{ background: #d4a373;"
            f' border: 1.5px solid #d4a373; image: url("{p}"); }}'
        )
    except Exception:
        return ""


class _IgnoreWheelFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        return super().eventFilter(obj, event)


_IGNORE_WHEEL = _IgnoreWheelFilter()


def no_scroll(widget):
    widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    widget.installEventFilter(_IGNORE_WHEEL)
    return widget
