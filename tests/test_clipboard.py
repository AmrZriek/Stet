import sys

import pytest

from stet.core.clipboard import _clipboard_read_text, _clipboard_write_text

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path")


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
