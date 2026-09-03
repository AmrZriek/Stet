"""Terminal capture guard — fail-closed regression tests (Decision 37 / Phase A).

Contract: plain Ctrl+C must NEVER be synthesized into a window whose
identity cannot be established. Unknown = terminal. Only a positively
identified non-terminal may take the Ctrl+C fallback path.

Covers:
- `_is_terminal_or_ide` fail-closed branches (elevated / unreadable /
  inspection-error identities, stale hwnd stays open).
- `_capture_selection` terminal branch never calls plain `_send_ctrl_chord`,
  sets the refusal flag, and surfaces the terminal-specific OSD hint.
"""

import ctypes
import sys
from unittest.mock import MagicMock, patch

import pytest

from stet.core.app import StetApp, _is_terminal_or_ide


def _noop_class_name(hwnd, buf, max_len):
    buf[0] = "\0"
    return 0


def _write_pid(pid_value):
    def _impl(hwnd, lpdw_process_id):
        ptr = ctypes.cast(lpdw_process_id, ctypes.POINTER(ctypes.c_ulong))
        ptr.contents.value = pid_value
        return 1

    return _impl


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native window APIs")
class TestFailClosedGuard:
    def test_none_and_zero_stay_false(self):
        # No foreground window — nothing to harm.
        assert _is_terminal_or_ide(None) is False
        assert _is_terminal_or_ide(0) is False

    def test_stale_hwnd_pid_zero_stays_false(self, monkeypatch):
        # GetWindowThreadProcessId fails on a dead hwnd and leaves pid 0:
        # no live process exists, so there is nothing Ctrl+C could kill.
        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )
        monkeypatch.setattr(
            ctypes.windll.user32,
            "GetWindowThreadProcessId",
            lambda hwnd, ppid: 0,
        )
        opened = []
        monkeypatch.setattr(
            ctypes.windll.kernel32, "OpenProcess", lambda *a: opened.append(a) or 0
        )
        assert _is_terminal_or_ide(12345) is False
        assert opened == []

    def test_openprocess_access_denied_fails_closed(self, monkeypatch):
        # Elevated terminal / admin shell: OpenProcess fails with
        # ERROR_ACCESS_DENIED (5). Must be treated as a terminal.
        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )
        monkeypatch.setattr(
            ctypes.windll.user32, "GetWindowThreadProcessId", _write_pid(9999)
        )
        monkeypatch.setattr(ctypes.windll.kernel32, "OpenProcess", lambda *a: 0)
        monkeypatch.setattr(ctypes.windll.kernel32, "GetLastError", lambda: 5)
        assert _is_terminal_or_ide(12345) is True

    def test_openprocess_other_error_stays_open(self, monkeypatch):
        # Exited process (ERROR_INVALID_PARAMETER / file-not-found class of
        # errors): nothing alive to harm, plain path stays available.
        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )
        monkeypatch.setattr(
            ctypes.windll.user32, "GetWindowThreadProcessId", _write_pid(9999)
        )
        monkeypatch.setattr(ctypes.windll.kernel32, "OpenProcess", lambda *a: 0)
        monkeypatch.setattr(ctypes.windll.kernel32, "GetLastError", lambda: 87)
        assert _is_terminal_or_ide(12345) is False

    def test_unreadable_image_name_fails_closed(self, monkeypatch):
        # Handle opens (process alive) but identity unreadable — fail closed.
        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )
        monkeypatch.setattr(
            ctypes.windll.user32, "GetWindowThreadProcessId", _write_pid(9999)
        )
        monkeypatch.setattr(ctypes.windll.kernel32, "OpenProcess", lambda *a: 1)
        monkeypatch.setattr(
            ctypes.windll.kernel32, "QueryFullProcessImageNameW", lambda *a: False
        )
        monkeypatch.setattr(ctypes.windll.kernel32, "CloseHandle", lambda *a: None)
        assert _is_terminal_or_ide(12345) is True

    def test_inspection_exception_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            ctypes.windll.user32, "GetClassNameW", _noop_class_name
        )
        monkeypatch.setattr(
            ctypes.windll.user32, "GetWindowThreadProcessId", _write_pid(9999)
        )
        monkeypatch.setattr(ctypes.windll.kernel32, "OpenProcess", lambda *a: 1)

        def _boom(*a):
            raise OSError("query failed")

        monkeypatch.setattr(
            ctypes.windll.kernel32, "QueryFullProcessImageNameW", _boom
        )
        monkeypatch.setattr(ctypes.windll.kernel32, "CloseHandle", lambda *a: None)
        assert _is_terminal_or_ide(12345) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 native window APIs")
@patch("stet.core.app.QSystemTrayIcon")
class TestTerminalCaptureRefusal:
    def _terminal_app(self, mock_tray_cls, monkeypatch):
        app = StetApp()
        monkeypatch.setattr(
            "stet.core.clipboard._read_selection_uia_struct", lambda: None
        )
        monkeypatch.setattr(
            "stet.core.app._is_terminal_or_ide", MagicMock(return_value=True)
        )
        monkeypatch.setattr("time.sleep", lambda t: None)
        app._safe_paste = MagicMock(return_value="")
        app._safe_copy = MagicMock()
        return app

    def test_terminal_branch_never_sends_plain_ctrl_c(
        self, mock_tray_cls, qtbot, monkeypatch
    ):
        """The destructive chord must not fire; only the safe Shift chord may."""
        app = self._terminal_app(mock_tray_cls, monkeypatch)
        with (
            patch("stet.core.app._send_ctrl_chord") as mock_plain,
            patch("stet.core.app._send_ctrl_shift_chord") as mock_shift,
        ):
            assert app._capture_selection() == ""
        mock_plain.assert_not_called()
        mock_shift.assert_called_once()
        assert app._terminal_capture_refused is True

    def test_terminal_hint_text(self, mock_tray_cls, qtbot, monkeypatch):
        app = self._terminal_app(mock_tray_cls, monkeypatch)
        app._terminal_capture_refused = True
        hint = app._empty_selection_hint("F9")
        assert "Terminal window detected" in hint
        assert "Ctrl+C would interrupt" in hint

    def test_generic_hint_unchanged(self, mock_tray_cls, qtbot, monkeypatch):
        app = self._terminal_app(mock_tray_cls, monkeypatch)
        app._terminal_capture_refused = False
        assert app._empty_selection_hint("F9") == (
            "Highlight some text in any app first, then press F9"
        )

    def test_hotkey_worker_surfaces_terminal_hint(
        self, mock_tray_cls, qtbot, monkeypatch
    ):
        app = self._terminal_app(mock_tray_cls, monkeypatch)
        app._capture_selection = MagicMock(return_value="")
        app._terminal_capture_refused = True
        app._old_clip = ""
        received = []
        app._silent_osd_signal.connect(lambda *a: received.append(a))
        app._last_empty_notify_ts = 0.0
        app._hotkey_busy.acquire()
        try:
            app._hotkey_worker()
        finally:
            pass  # worker releases the lock itself
        assert received
        assert "Terminal window detected" in received[0][0]
