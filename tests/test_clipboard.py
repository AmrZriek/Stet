import sys
import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only path", allow_module_level=True)

import ctypes
import time

from stet.core.clipboard import _clipboard_read_text, _clipboard_write_text

def _is_clipboard_available():
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for _ in range(5):
            if user32.OpenClipboard(None):
                user32.CloseClipboard()
                return True
            time.sleep(0.02)
    except Exception:
        pass
    return False

pytestmark = pytest.mark.skipif(
    not _is_clipboard_available(),
    reason="System clipboard is locked/unavailable (Access Denied)",
)


def test_clipboard_roundtrip_basic_unicode():
    sample = "Σ Ω π μ ∑ √ Δ"
    _clipboard_write_text(sample)
    assert _clipboard_read_text() == sample


def test_clipboard_roundtrip_emoji():
    sample = "😀🚀✨"  # each = surrogate pair in UTF-16
    _clipboard_write_text(sample)
    assert _clipboard_read_text() == sample


def test_clipboard_roundtrip_mixed_ascii_unicode():
    sample = "Cost = 42 Ω, area ≈ πr², emoji 🎯 done."
    _clipboard_write_text(sample)
    assert _clipboard_read_text() == sample


def test_clipboard_roundtrip_empty_string():
    _clipboard_write_text("")
    assert _clipboard_read_text() == ""


def test_read_selection_uia_success():
    """Verify _read_selection_uia returns the string when COM calls succeed."""
    import ctypes
    from unittest.mock import MagicMock, patch

    from stet.core.clipboard import _read_selection_uia

    mock_ole32 = MagicMock()
    mock_ole32.CoInitializeEx.return_value = 0  # S_OK
    mock_ole32.CoCreateInstance.return_value = 0  # S_OK

    mock_oleaut32 = MagicMock()
    keep_alives = []

    def mock_call_com_method_with_pointers(interface_ptr, index, prototype, *args):
        if index == 8:  # GetFocusedElement (IUIAutomation vtable index 8)
            args[0].contents.value = 12345
        elif index == 16:  # GetCurrentPattern
            args[1].contents.value = 23456
        elif index == 3 and prototype[-1] == ctypes.POINTER(
            ctypes.c_void_p
        ):  # GetSelection
            args[0].contents.value = 34567
        elif index == 3 and prototype[-1] == ctypes.POINTER(ctypes.c_int):  # get_Length
            args[0].contents.value = 1
        elif index == 4:  # GetElement
            args[1].contents.value = 45678
        elif index == 12:  # GetText
            s = "mocked selected text"
            ka = ctypes.c_wchar_p(s)
            keep_alives.append(ka)
            addr = ctypes.cast(ka, ctypes.c_void_p).value
            ctypes.cast(args[1], ctypes.POINTER(ctypes.c_void_p))[0] = addr
        return 0

    def mock_cocreateinstance(rclsid, pUnkOuter, dwClsContext, riid, ppv):
        ppv.contents.value = 11111
        return 0

    mock_ole32.CoCreateInstance.side_effect = mock_cocreateinstance

    with (
        patch("ctypes.windll.ole32", new=mock_ole32),
        patch("ctypes.windll.oleaut32", new=mock_oleaut32),
        patch(
            "stet.core.clipboard.call_com_method",
            new=mock_call_com_method_with_pointers,
        ),
        patch("stet.core.clipboard.release_com_ptr"),
    ):
        res = _read_selection_uia()
        assert res == "mocked selected text"


def test_read_selection_uia_failure():
    """Verify _read_selection_uia returns None when any COM call fails."""
    from unittest.mock import MagicMock, patch

    from stet.core.clipboard import _read_selection_uia

    mock_ole32 = MagicMock()
    mock_ole32.CoInitializeEx.return_value = 0  # S_OK
    mock_ole32.CoCreateInstance.return_value = -1  # E_FAIL

    with patch("ctypes.windll.ole32", new=mock_ole32):
        res = _read_selection_uia()
        assert res is None


def test_read_selection_uia_bounds_length():
    """Verify _read_selection_uia requests bounded text length (50000) to prevent OOM."""
    import ctypes
    from unittest.mock import MagicMock, patch

    from stet.core.clipboard import _read_selection_uia

    mock_ole32 = MagicMock()
    mock_ole32.CoInitializeEx.return_value = 0  # S_OK
    mock_ole32.CoCreateInstance.return_value = 0  # S_OK

    mock_oleaut32 = MagicMock()
    keep_alives = []

    gettext_args = {}

    def mock_call_com_method_with_pointers(interface_ptr, index, prototype, *args):
        if index == 8:  # GetFocusedElement
            args[0].contents.value = 12345
        elif index == 16:  # GetCurrentPattern
            args[1].contents.value = 23456
        elif index == 3 and prototype[-1] == ctypes.POINTER(ctypes.c_void_p):  # GetSelection
            args[0].contents.value = 34567
        elif index == 3 and prototype[-1] == ctypes.POINTER(ctypes.c_int):  # get_Length
            args[0].contents.value = 1
        elif index == 4:  # GetElement
            args[1].contents.value = 45678
        elif index == 12:  # GetText
            gettext_args["max_length"] = args[0]
            s = "mocked selected text"
            ka = ctypes.c_wchar_p(s)
            keep_alives.append(ka)
            addr = ctypes.cast(ka, ctypes.c_void_p).value
            ctypes.cast(args[1], ctypes.POINTER(ctypes.c_void_p))[0] = addr
        return 0

    def mock_cocreateinstance(rclsid, pUnkOuter, dwClsContext, riid, ppv):
        ppv.contents.value = 11111
        return 0

    mock_ole32.CoCreateInstance.side_effect = mock_cocreateinstance

    with (
        patch("ctypes.windll.ole32", new=mock_ole32),
        patch("ctypes.windll.oleaut32", new=mock_oleaut32),
        patch(
            "stet.core.clipboard.call_com_method",
            new=mock_call_com_method_with_pointers,
        ),
        patch("stet.core.clipboard.release_com_ptr"),
    ):
        _read_selection_uia()
        assert "max_length" in gettext_args, "GetText was not called"
        assert gettext_args["max_length"] == 50000


def test_clipboard_sequence_number():
    from stet.core.clipboard import _clipboard_sequence_number, _clipboard_write_text

    seq1 = _clipboard_sequence_number()
    assert isinstance(seq1, int)
    assert seq1 >= 0

    _clipboard_write_text("sequence_test_token_123")
    seq2 = _clipboard_sequence_number()
    assert isinstance(seq2, int)
    # Sequence number should advance after a clipboard write
    assert seq2 != seq1



def test_send_ctrl_chord_releases_held_shift(monkeypatch):
    """When Shift is physically down (e.g. from Shift+F9), _send_ctrl_chord prepends Shift key-up."""
    from unittest.mock import MagicMock
    import stet.core.clipboard as clip

    mock_send_input = MagicMock()
    monkeypatch.setattr(clip._user32, "SendInput", mock_send_input)

    # Emulate Shift physically down (0x8000 bit set)
    def mock_get_async_key_state(vk):
        return -32768 if vk == clip.VK_SHIFT else 0

    monkeypatch.setattr(clip._user32, "GetAsyncKeyState", mock_get_async_key_state)

    clip._send_ctrl_chord(clip.VK_C)

    mock_send_input.assert_called_once()
    n_inputs, arr, size = mock_send_input.call_args[0]
    assert n_inputs == 5  # 1 (Shift UP) + 4 (Ctrl+C chord)
    # First event must be Shift key-up
    assert arr[0].ki.wVk == clip.VK_SHIFT
    assert arr[0].ki.dwFlags == clip.KEYEVENTF_KEYUP
    # Next event must be Ctrl key-down
    assert arr[1].ki.wVk == clip.VK_CONTROL
    assert arr[1].ki.dwFlags == 0


def test_send_ctrl_chord_normal_when_no_modifiers_held(monkeypatch):
    """When no modifiers are held, _send_ctrl_chord sends standard 4-input batch."""
    from unittest.mock import MagicMock
    import stet.core.clipboard as clip

    mock_send_input = MagicMock()
    monkeypatch.setattr(clip._user32, "SendInput", mock_send_input)
    monkeypatch.setattr(clip._user32, "GetAsyncKeyState", lambda vk: 0)

    clip._send_ctrl_chord(clip.VK_C)

    mock_send_input.assert_called_once()
    n_inputs, arr, size = mock_send_input.call_args[0]
    assert n_inputs == 4
    assert arr[0].ki.wVk == clip.VK_CONTROL
    assert arr[0].ki.dwFlags == 0


def test_send_ctrl_shift_chord_releases_held_alt_win(monkeypatch):
    """When Alt is physically held, _send_ctrl_shift_chord prepends Alt key-up."""
    from unittest.mock import MagicMock
    import stet.core.clipboard as clip

    mock_send_input = MagicMock()
    monkeypatch.setattr(clip._user32, "SendInput", mock_send_input)

    def mock_get_async_key_state(vk):
        return -32768 if vk == clip.VK_MENU else 0

    monkeypatch.setattr(clip._user32, "GetAsyncKeyState", mock_get_async_key_state)

    clip._send_ctrl_shift_chord(clip.VK_C)

    mock_send_input.assert_called_once()
    n_inputs, arr, size = mock_send_input.call_args[0]
    assert n_inputs == 7  # 1 (Alt UP) + 6 (Ctrl+Shift+C chord)
    assert arr[0].ki.wVk == clip.VK_MENU
    assert arr[0].ki.dwFlags == clip.KEYEVENTF_KEYUP
