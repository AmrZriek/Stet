from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen



def make_tray_icon(color: str) -> QIcon:
    from stet.ui.utils import get_logo_path
    logo_path = get_logo_path()
    if logo_path and logo_path.exists():
        base = QPixmap(str(logo_path)).scaled(
            64,
            64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    else:
        base = QPixmap(64, 64)
        base.fill(Qt.GlobalColor.transparent)
    result = QPixmap(64, 64)
    result.fill(Qt.GlobalColor.transparent)
    p = QPainter(result)
    p.drawPixmap(0, 0, base)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(44, 44, 18, 18)
    p.end()
    return QIcon(result)


def make_left_arrow_icon() -> QIcon:
    def draw_arrow(color_str: str) -> QPixmap:
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color_str))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        # Left chevron coordinates: (8, 2) -> (4, 6) -> (8, 10)
        p.drawLine(8, 2, 4, 6)
        p.drawLine(4, 6, 8, 10)
        p.end()
        return pixmap

    icon = QIcon()
    icon.addPixmap(draw_arrow("#88898c"), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(draw_arrow("#88898c"), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Selected, QIcon.State.Off)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Selected, QIcon.State.On)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Active, QIcon.State.On)
    return icon


def make_download_icon(accent: str | None = None) -> QIcon:
    """Download-arrow glyph for the tray 'Check for Updates' action.

    Idle state (``accent=None``) uses the standard menu colors like
    ``make_left_arrow_icon``.  Passing an accent color (e.g. the update
    cyan) renders the glyph in that color for all modes so the pending
    update stands out in the tray menu.
    """
    def draw_arrow(color_str: str) -> QPixmap:
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color_str))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        # Shaft down into the arrow head, then the tray baseline
        p.drawLine(6, 2, 6, 8)
        p.drawLine(9, 5, 6, 9)
        p.drawLine(3, 5, 6, 9)
        p.drawLine(2, 10, 10, 10)
        p.end()
        return pixmap

    if accent is not None:
        icon = QIcon()
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
            icon.addPixmap(draw_arrow(accent), mode, QIcon.State.Off)
            icon.addPixmap(draw_arrow(accent), mode, QIcon.State.On)
        return icon

    icon = QIcon()
    icon.addPixmap(draw_arrow("#88898c"), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(draw_arrow("#88898c"), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Selected, QIcon.State.Off)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Selected, QIcon.State.On)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(draw_arrow("#ededee"), QIcon.Mode.Active, QIcon.State.On)
    return icon

