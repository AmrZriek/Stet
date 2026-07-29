import os
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
                '<path d="M2 6L5 9L10 3" stroke="#121212" stroke-width="2.2" '
                'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                encoding="utf-8",
            )
        p = str(svg_path).replace("\\", "/")
        return (
            "QCheckBox { color: #88898c; spacing: 8px; font-size: 11px; outline: none; }"
            "QCheckBox:checked { color: #ededee; }"
            "QCheckBox::indicator {"
            " width: 14px; height: 14px;"
            " border: 1.5px solid rgba(212,163,115,0.35);"
            " border-radius: 3px; background: rgba(4,10,28,0.8); outline: none; }"
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


def get_logo_path() -> Path | None:
    """Resolve logo.ico or logo.png across source, PyInstaller (_MEIPASS), Nuitka, and app bundle modes."""
    import sys
    from stet.constants import SCRIPT_DIR, WINDOWS

    candidates = ("logo.ico", "logo.png") if WINDOWS else ("logo.png", "logo.ico")

    search_dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        search_dirs.append(Path(meipass))
    onefile_temp = os.environ.get("_NUITKA_ONEFILE_TEMP")
    if onefile_temp:
        search_dirs.append(Path(onefile_temp))
    if SCRIPT_DIR:
        search_dirs.append(SCRIPT_DIR)

    repo_root = Path(__file__).resolve().parent.parent.parent
    search_dirs.append(repo_root)
    search_dirs.append(repo_root / "stet")
    try:
        search_dirs.append(Path(sys.argv[0]).resolve().parent)
    except Exception:
        pass
    search_dirs.append(Path.cwd())

    for name in candidates:
        for directory in search_dirs:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def get_app_icon():
    """Return a QIcon for Stet logo or an empty QIcon fallback."""
    from PyQt6.QtGui import QIcon
    logo_path = get_logo_path()
    if logo_path:
        return QIcon(str(logo_path))
    return QIcon()


def set_window_icon(widget) -> None:
    """Set the window icon for any QWidget or QDialog using the resolved Stet logo."""
    icon = get_app_icon()
    if not icon.isNull():
        widget.setWindowIcon(icon)

