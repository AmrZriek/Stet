import time

from stet.constants import WINDOWS

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_C = 0x43
VK_V = 0x56
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

import ctypes

if WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_I(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("i",)
        _fields_ = [("type", wintypes.DWORD), ("i", _INPUT_I)]

    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT

    _user32.OpenClipboard.argtypes = (wintypes.HWND,)
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = ()
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.argtypes = ()
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = (wintypes.UINT,)
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.RegisterClipboardFormatW.argtypes = (wintypes.LPCWSTR,)
    _user32.RegisterClipboardFormatW.restype = wintypes.UINT

    _fmt_exclude = _user32.RegisterClipboardFormatW(
        "ExcludeClipboardContentFromClipboardHistory"
    )
    _fmt_cloud = _user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")

    _kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL
else:
    import pyperclip


def _open_clipboard_retry(retries: int = 10, delay: float = 0.01) -> bool:
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def _clipboard_read_text() -> str:
    """Read CF_UNICODETEXT from the system clipboard.

    Decodes as UTF-16-LE, so emoji and other astral-plane characters
    (surrogate pairs) round-trip cleanly. Returns "" if no text is present
    or the clipboard cannot be opened.
    """
    if not WINDOWS:
        return pyperclip.paste()
    if not _open_clipboard_retry():
        return ""
    try:
        h = _user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = _kernel32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(h)
    finally:
        _user32.CloseClipboard()


def _clipboard_write_text(text: str) -> None:
    """Write text to the system clipboard as CF_UNICODETEXT (UTF-16-LE).

    Encoding via `utf-16-le` preserves astral-plane characters as surrogate
    pairs, which CF_UNICODETEXT consumers expect.
    """
    if not WINDOWS:
        pyperclip.copy(text)
        return
    if not _open_clipboard_retry():
        return
    try:
        _user32.EmptyClipboard()
        
        # Windows CF_UNICODETEXT standard expects \r\n. If we extracted text via UIA 
        # (which often returns bare \r or \n), pasting it back as-is causes some apps 
        # (like Word) to interpret them incorrectly, creating duplicate blank lines.
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        data = normalized.encode("utf-16-le") + b"\x00\x00"
        
        h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return
        ptr = _kernel32.GlobalLock(h)
        if not ptr:
            _kernel32.GlobalFree(h)
            return
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            _kernel32.GlobalUnlock(h)
        if not _user32.SetClipboardData(CF_UNICODETEXT, h):
            _kernel32.GlobalFree(h)
            return

        # Suppress this clipboard write from Win+V history
        if _fmt_exclude:
            _user32.SetClipboardData(_fmt_exclude, None)
        # Suppress Cloud Clipboard sync (set to DWORD of value 0)
        if _fmt_cloud:
            h_cloud = _kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
            if h_cloud:
                ptr_cloud = _kernel32.GlobalLock(h_cloud)
                if ptr_cloud:
                    try:
                        ctypes.memset(ptr_cloud, 0, 4)
                    finally:
                        _kernel32.GlobalUnlock(h_cloud)
                    if not _user32.SetClipboardData(_fmt_cloud, h_cloud):
                        _kernel32.GlobalFree(h_cloud)
                else:
                    _kernel32.GlobalFree(h_cloud)
    finally:
        _user32.CloseClipboard()


def _send_ctrl_chord(vk: int) -> None:
    """Press Ctrl, press `vk`, release `vk`, release Ctrl — atomically.

    Uses a single SendInput call on Windows so the OS sees the four events
    in one batch. On other platforms falls back to `keyboard.send`.
    """
    if WINDOWS:
        arr = (INPUT * 4)()
        for idx, (code, flags) in enumerate(
            (
                (VK_CONTROL, 0),
                (vk, 0),
                (vk, KEYEVENTF_KEYUP),
                (VK_CONTROL, KEYEVENTF_KEYUP),
            )
        ):
            arr[idx].type = INPUT_KEYBOARD
            arr[idx].ki = _KEYBDINPUT(code, 0, flags, 0, 0)
        _user32.SendInput(4, arr, ctypes.sizeof(INPUT))
    else:
        raise NotImplementedError("Stet only supports Windows native input simulation.")


def _send_ctrl_shift_chord(vk: int) -> None:
    """Press Ctrl+Shift+`vk` and release in reverse order as one input batch."""
    if WINDOWS:
        arr = (INPUT * 6)()
        for idx, (code, flags) in enumerate(
            (
                (VK_CONTROL, 0),
                (VK_SHIFT, 0),
                (vk, 0),
                (vk, KEYEVENTF_KEYUP),
                (VK_SHIFT, KEYEVENTF_KEYUP),
                (VK_CONTROL, KEYEVENTF_KEYUP),
            )
        ):
            arr[idx].type = INPUT_KEYBOARD
            arr[idx].ki = _KEYBDINPUT(code, 0, flags, 0, 0)
        _user32.SendInput(6, arr, ctypes.sizeof(INPUT))
    else:
        raise NotImplementedError("Stet only supports Windows native input simulation.")


# ── Windows UI Automation (COM via ctypes) ───────────────────────────────


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(s: str) -> GUID:
    """Parse GUID string '{ff48dba4-bf32-4f70-a61e-f3b2f166a537}' to GUID struct."""
    s = s.strip("{}")
    parts = s.split("-")
    d1 = int(parts[0], 16)
    d2 = int(parts[1], 16)
    d3 = int(parts[2], 16)
    d4_str = parts[3] + parts[4]
    d4 = (ctypes.c_ubyte * 8)(*[int(d4_str[i : i + 2], 16) for i in range(0, 16, 2)])
    return GUID(d1, d2, d3, d4)


CLSID_CUIAutomation = _guid("ff48dba4-bf32-4f70-a61e-f3b2f166a537")
IID_IUIAutomation = _guid("30cbe57d-d9d0-4a2a-ab13-7ac5ac4825ee")
IID_IUIAutomationElement = _guid("d22108aa-8ac5-49a5-837b-37bbb3d7591e")
IID_IUIAutomationTextPattern = _guid("32bccb68-df53-4887-a28d-503d460644ac")
BSTR = ctypes.c_wchar_p


def call_com_method(interface_ptr, index, prototype, *args):
    """Invoke a COM method by index on raw interface pointer."""
    vtable = ctypes.cast(interface_ptr, ctypes.POINTER(ctypes.c_void_p)).contents
    vtable_ptr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    func_ptr = vtable_ptr[index]
    func = ctypes.WINFUNCTYPE(ctypes.c_long, *prototype)(func_ptr)
    return func(interface_ptr, *args)


def release_com_ptr(ptr_val):
    """Release COM pointer using its IUnknown::Release (index 2) method."""
    if ptr_val:
        try:
            call_com_method(ptr_val, 2, [ctypes.c_void_p])
        except Exception:
            pass


def _read_selection_uia() -> str | None:
    """Read the currently selected text via UI Automation, bypassing the clipboard.

    Returns the text if successful, or None on any failure (in which case the
    caller falls back to clipboard capture).
    """
    if not WINDOWS:
        return None

    co_init = False
    # COINIT_APARTMENTTHREADED = 2
    hr_init = ctypes.windll.ole32.CoInitializeEx(None, 2)
    if hr_init == 0 or hr_init == 1:
        co_init = True

    p_uia = ctypes.c_void_p()
    p_elem = ctypes.c_void_p()
    p_pattern = ctypes.c_void_p()
    p_ranges = ctypes.c_void_p()
    p_range = ctypes.c_void_p()
    bstr = BSTR()
    result_text = None

    try:
        # 1. Create IUIAutomation instance
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.pointer(CLSID_CUIAutomation),
            None,
            1,  # CLSCTX_INPROC_SERVER
            ctypes.pointer(IID_IUIAutomation),
            ctypes.pointer(p_uia),
        )
        if hr < 0 or not p_uia.value:
            return None

        # 2. Get focused element (IUIAutomation vtable index 8)
        hr = call_com_method(
            p_uia.value,
            8,  # GetFocusedElement
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.pointer(p_elem),
        )
        if hr < 0 or not p_elem.value:
            return None

        # 3. Get text pattern (10014)
        hr = call_com_method(
            p_elem.value,
            16,  # GetCurrentPattern
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
            10014,
            ctypes.pointer(p_pattern),
        )
        if hr < 0 or not p_pattern.value:
            return None

        # 4. Get selection text range array
        hr = call_com_method(
            p_pattern.value,
            3,  # GetSelection
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
            ctypes.pointer(p_ranges),
        )
        if hr < 0 or not p_ranges.value:
            return None

        # 5. Get length of array
        length = ctypes.c_int(0)
        hr = call_com_method(
            p_ranges.value,
            3,  # get_Length
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)],
            ctypes.pointer(length),
        )
        if hr < 0 or length.value <= 0:
            return None

        # 6. Get first text range
        hr = call_com_method(
            p_ranges.value,
            4,  # GetElement
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
            0,
            ctypes.pointer(p_range),
        )
        if hr < 0 or not p_range.value:
            return None

        # 7. Get text from range
        hr = call_com_method(
            p_range.value,
            12,  # GetText
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(BSTR)],
            -1,
            ctypes.pointer(bstr),
        )
        if hr >= 0 and bstr.value:
            result_text = bstr.value

    except Exception:
        pass
    finally:
        # Clean up all COM pointers in reverse allocation order
        if bstr.value:
            ctypes.windll.oleaut32.SysFreeString(bstr)
        if p_range.value:
            release_com_ptr(p_range.value)
        if p_ranges.value:
            release_com_ptr(p_ranges.value)
        if p_pattern.value:
            release_com_ptr(p_pattern.value)
        if p_elem.value:
            release_com_ptr(p_elem.value)
        if p_uia.value:
            release_com_ptr(p_uia.value)

        if co_init:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    return result_text
