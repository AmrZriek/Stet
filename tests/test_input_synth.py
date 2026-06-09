import sys
from unittest.mock import patch

import pytest

from stet.core.clipboard import (
    KEYEVENTF_KEYUP,
    VK_C,
    VK_CONTROL,
    VK_V,
    _send_ctrl_chord,
    _user32,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path")


def _captured_inputs(call_args):
    """Decode the (count, inputs_array, size) SendInput call back to a list of
    (vk_code, is_keyup) tuples in the order they were submitted."""
    count, inputs_arr, _size = call_args.args
    out = []
    for i in range(count):
        ki = inputs_arr[i].i.ki
        is_keyup = bool(ki.dwFlags & KEYEVENTF_KEYUP)
        out.append((ki.wVk, is_keyup))
    return out


def test_send_ctrl_chord_emits_keydown_c_keyup_in_order():
    with patch.object(_user32, "SendInput", return_value=4) as m:
        _send_ctrl_chord(VK_C)

    assert m.call_count == 1
    seq = _captured_inputs(m.call_args)
    assert seq == [
        (VK_CONTROL, False),
        (VK_C, False),
        (VK_C, True),
        (VK_CONTROL, True),
    ]


def test_send_ctrl_chord_handles_v_for_paste():
    with patch.object(_user32, "SendInput", return_value=4) as m:
        _send_ctrl_chord(VK_V)

    seq = _captured_inputs(m.call_args)
    assert seq == [
        (VK_CONTROL, False),
        (VK_V, False),
        (VK_V, True),
        (VK_CONTROL, True),
    ]
