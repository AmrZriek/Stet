"""Tests for app-level standalone helpers — parse_hotkey_string, WinHotkeyFilter,
_quote_cmd, _startup_command, _source_startup_python."""

from unittest.mock import MagicMock

import pytest

from stet.core.app import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WinHotkeyFilter,
    _MOD_MAP,
    _VK_MAP,
    _quote_cmd,
    _source_startup_python,
    _startup_command,
    parse_hotkey_string,
)


# ── parse_hotkey_string ──────────────────────────────────────────────────


class TestParseHotkeyString:
    """Parse 'ctrl+shift+f9' into (modifiers, vk_code)."""

    def test_single_fkey(self):
        mods, vk = parse_hotkey_string("f9")
        assert mods == MOD_NOREPEAT
        assert vk == 0x78

    def test_ctrl_shift_f9(self):
        mods, vk = parse_hotkey_string("ctrl+shift+f9")
        assert mods & MOD_CONTROL
        assert mods & MOD_SHIFT
        assert vk == 0x78

    def test_alt_key(self):
        mods, vk = parse_hotkey_string("alt+a")
        assert mods & MOD_ALT
        assert vk == 0x41

    def test_ctrl_space(self):
        mods, vk = parse_hotkey_string("ctrl+space")
        assert mods & MOD_CONTROL
        assert vk == 0x20

    def test_invalid_key_returns_zero(self):
        mods, vk = parse_hotkey_string("ctrl+invalidkey")
        assert mods == 0
        assert vk == 0

    @pytest.mark.parametrize(
        "combo",
        [
            "ctrl+shift+space",
            "ctrl+shift+c",
            "ctrl+alt+t",
            "shift+f10",
            "ctrl+f1",
        ],
    )
    def test_various_combos(self, combo):
        mods, vk = parse_hotkey_string(combo)
        assert vk != 0, f"Failed to parse {combo}"

    def test_norepeat_always_set(self):
        mods, vk = parse_hotkey_string("f1")
        assert mods & MOD_NOREPEAT

    def test_letter_keys(self):
        for letter in "abcdefghijklmnopqrstuvwxyz":
            assert letter in _VK_MAP

    def test_number_keys(self):
        for num in "0123456789":
            assert num in _VK_MAP

    def test_punctuation_keys(self):
        for key in ["period", "slash", "comma", "semicolon"]:
            assert key in _VK_MAP


# ── WinHotkeyFilter ─────────────────────────────────────────────────────


class TestWinHotkeyFilter:
    """Native event filter for Win32 hotkey messages."""

    def test_construction(self):
        f = WinHotkeyFilter()
        assert f._callbacks == {}

    def test_register_callback(self):
        f = WinHotkeyFilter()
        cb = MagicMock()
        f.register_callback(1000, cb)
        assert 1000 in f._callbacks

    def test_clear_callbacks(self):
        f = WinHotkeyFilter()
        f.register_callback(1000, MagicMock())
        f.register_callback(1001, MagicMock())
        f.clear_callbacks()
        assert f._callbacks == {}


# ── _quote_cmd ───────────────────────────────────────────────────────────


class TestQuoteCmd:
    """Command-line quoting for subprocess."""

    def test_simple_args(self):
        result = _quote_cmd(["python", "-m", "stet.main"])
        assert "python" in result
        assert "stet.main" in result

    def test_path_with_spaces(self):
        result = _quote_cmd(["C:\\Program Files\\python.exe", "main.py"])
        assert '"C:\\Program Files\\python.exe"' in result


# ── _source_startup_python ──────────────────────────────────────────────


class TestSourceStartupPython:
    """Find pythonw.exe for startup registry entry."""

    def test_returns_string(self):
        result = _source_startup_python()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_executable(self):
        result = _source_startup_python()
        assert "python" in result.lower()


# ── _startup_command ─────────────────────────────────────────────────────


class TestStartupCommand:
    """Build the Windows startup registry command."""

    def test_returns_string(self):
        result = _startup_command()
        assert isinstance(result, str)
        assert len(result) > 0


# ── _VK_MAP / _MOD_MAP constants ─────────────────────────────────────────


class TestHotkeyMaps:
    """Virtual key and modifier maps are complete."""

    def test_vk_map_has_fkeys(self):
        for i in range(1, 13):
            assert f"f{i}" in _VK_MAP

    def test_mod_map_has_ctrl_alt_shift(self):
        assert "ctrl" in _MOD_MAP
        assert "alt" in _MOD_MAP
        assert "shift" in _MOD_MAP
        assert "win" in _MOD_MAP

    def test_mod_map_aliases(self):
        assert "control" in _MOD_MAP
        assert _MOD_MAP["control"] == _MOD_MAP["ctrl"]
        assert "windows" in _MOD_MAP
        assert _MOD_MAP["windows"] == _MOD_MAP["win"]
