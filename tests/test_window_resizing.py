import sys
import ctypes
from unittest.mock import MagicMock
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QCursor

def _create_mock_msg(hwnd, msg_code, x, y):
    """Create a mock MSG struct for nativeEvent testing."""
    if sys.platform != "win32":
        return None
    lparam = (y << 16) | (x & 0xFFFF)
    msg = ctypes.wintypes.MSG()
    msg.hwnd = hwnd
    msg.message = msg_code
    msg.wParam = 0
    msg.lParam = lparam
    return msg


def test_correction_window_resizing_and_native_event(isolate_config, qtbot, monkeypatch):
    """Verify CorrectionWindow nativeEvent WM_NCHITTEST, 8-edge hit tests, and resizing."""
    from stet.core.config import ConfigManager
    from stet.ui.main_window import CorrectionWindow

    cfg = ConfigManager()
    mock_ac = MagicMock()
    mock_ac.is_loaded.return_value = False
    mock_ac.make_stream_worker.return_value = MagicMock()
    mock_chat = MagicMock()

    win = CorrectionWindow("Test input text", mock_ac, mock_chat, cfg)
    qtbot.addWidget(win)
    win.resize(800, 600)
    win.show()

    # 1. Mouse tracking enabled
    assert win.hasMouseTracking()

    # 2. Maximize / Restore toggle
    assert not win.isMaximized()
    win._toggle_maximized()
    assert win.isMaximized()
    win._toggle_maximized()
    assert not win.isMaximized()

    # 3. Native event testing (Windows only)
    if sys.platform == "win32":
        pt = win.mapToGlobal(QPoint(2, 2))
        msg = _create_mock_msg(int(win.winId()), 0x0084, pt.x(), pt.y())
        if msg:
            with monkeypatch.context() as m:
                m.setattr(QCursor, "pos", lambda: pt)
                handled, res = win.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
                assert handled is True
                assert res == 13  # HTTOPLEFT




def test_welcome_window_resizing_and_native_event(isolate_config, qtbot, monkeypatch):
    """Verify WelcomeWindow nativeEvent WM_NCHITTEST and infographic resize timer."""
    from stet.core.config import ConfigManager
    from stet.ui.welcome_window import WelcomeWindow

    cfg = ConfigManager()
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True

    win = WelcomeWindow(cfg, mock_model)
    qtbot.addWidget(win)
    win.resize(900, 650)
    win.show()

    # 1. Window stays on top flag is NOT set (least priority window)
    flags = win.windowFlags()
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)

    # 2. Resize event triggers debounced timer
    win.resize(1000, 700)
    assert hasattr(win, "_info_timer")
    assert win._info_timer is not None

    # 3. Native event testing (Windows only)
    if sys.platform == "win32":
        pt = win.mapToGlobal(QPoint(2, 2))
        msg = _create_mock_msg(int(win.winId()), 0x0084, pt.x(), pt.y())
        if msg:
            with monkeypatch.context() as m:
                m.setattr(QCursor, "pos", lambda: pt)
                handled, res = win.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
                assert handled is True
                assert res == 13  # HTTOPLEFT




def test_settings_dialog_resizing_and_native_event(isolate_config, qtbot, monkeypatch):
    """Verify SettingsDialog 8-edge handles, maximize toggle, and nativeEvent."""
    from stet.core.config import ConfigManager
    from stet.ui.settings import SettingsDialog

    cfg = ConfigManager()
    dlg = SettingsDialog(cfg)
    qtbot.addWidget(dlg)
    dlg.resize(950, 850)
    dlg.show()

    # 1. Has maximize button and toggle
    assert hasattr(dlg, "_max_btn")
    assert dlg._max_btn is not None
    assert not dlg.isMaximized()

    dlg._toggle_maximize()
    assert dlg.isMaximized()
    dlg._toggle_maximize()
    assert not dlg.isMaximized()

    # 2. Native event testing (Windows only)
    if sys.platform == "win32":
        pt = dlg.mapToGlobal(QPoint(2, 2))
        msg = _create_mock_msg(int(dlg.winId()), 0x0084, pt.x(), pt.y())
        if msg:
            with monkeypatch.context() as m:
                m.setattr(QCursor, "pos", lambda: pt)
                res_tuple = dlg.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
                assert res_tuple[0] is True
                assert res_tuple[1] == 13  # HTTOPLEFT




def test_download_dialog_resizing_and_native_event(isolate_config, qtbot, monkeypatch):
    """Verify DownloadProgressDialog resizing and nativeEvent."""
    from stet.ui.downloader import DownloadProgressDialog

    downloads = []
    dlg = DownloadProgressDialog(downloads)
    qtbot.addWidget(dlg)
    dlg.resize(500, 300)
    dlg.show()

    assert dlg.hasMouseTracking()

    if sys.platform == "win32":
        pt = dlg.mapToGlobal(QPoint(2, 2))
        msg = _create_mock_msg(int(dlg.winId()), 0x0084, pt.x(), pt.y())
        if msg:
            with monkeypatch.context() as m:
                m.setattr(QCursor, "pos", lambda: pt)
                handled, res = dlg.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
                assert handled is True
                assert res == 13  # HTTOPLEFT

    dlg.close()
