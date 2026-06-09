"""Source-level wiring assertions: confirms call sites use the Win32-safe
helpers instead of the higher-level libraries that exhibit the symptoms
documented in context.md Rule #21."""

import re
from pathlib import Path

SRC = "\n".join(
    f.read_text(encoding="utf-8")
    for f in (Path(__file__).resolve().parent.parent / "stet").rglob("*.py")
)


def test_hotkey_worker_uses_send_ctrl_chord_for_copy():
    # _capture_selection (called by _hotkey_worker) must use _send_ctrl_chord(VK_C), not keyboard.send.
    body = re.search(
        r"def _hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    capture = re.search(
        r"def _capture_selection\(self\).*?:.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    body += capture
    assert "_send_ctrl_chord(VK_C)" in body
    assert "keyboard.send('ctrl+c')" not in body
    assert 'keyboard.send("ctrl+c")' not in body


def test_paste_text_uses_send_ctrl_chord_for_paste():
    body = re.search(r"def _paste_text\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_send_ctrl_chord(VK_V)" in body
    assert "keyboard.send('ctrl+v')" not in body
    assert 'keyboard.send("ctrl+v")' not in body


def test_safe_paste_uses_clipboard_helper():
    body = re.search(r"def _safe_paste\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_clipboard_read_text" in body


def test_safe_copy_uses_clipboard_helper():
    body = re.search(r"def _safe_copy\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    assert "_clipboard_write_text" in body


def test_hotkey_worker_uses_vk_c_not_vk_insert():
    """_hotkey_worker must use VK_C for copy (via _capture_selection)."""
    body = re.search(
        r"def _hotkey_worker\(self\):.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    capture = re.search(
        r"def _capture_selection\(self\).*?:.*?(?=\n    def )", SRC, re.DOTALL
    ).group(0)
    assert "VK_C" in body + capture


def test_paste_text_uses_vk_v_not_vk_insert():
    """_paste_text must use VK_INSERT with Shift for paste (the working version)."""
    body = re.search(r"def _paste_text\(self.*?(?=\n    def )", SRC, re.DOTALL).group(0)
    # Must use VK_V
    assert "VK_V" in body
