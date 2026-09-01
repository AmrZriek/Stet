"""Tests for Task 0g: diff-based transactional hotkey registration."""

from stet.core.app import compute_hotkey_diff


def test_hotkey_diff_basic():
    """old minus new -> to_unregister, new minus old -> to_register, intersection -> unchanged."""
    old = {"f9", "f10"}
    new = {"f9", "shift+f9"}
    to_unregister, to_register, unchanged = compute_hotkey_diff(old, new)
    assert to_unregister == ["f10"]
    assert to_register == ["shift+f9"]
    assert unchanged == ["f9"]


def test_hotkey_diff_no_change():
    """When old == new, nothing to unregister/register; all unchanged."""
    old = {"f9", "f10"}
    to_unregister, to_register, unchanged = compute_hotkey_diff(old, set(old))
    assert to_unregister == []
    assert to_register == []
    assert unchanged == sorted(["f9", "f10"])


def test_hotkey_diff_empty_to_new():
    """From empty to {f9}: only register, no unregister."""
    to_unregister, to_register, unchanged = compute_hotkey_diff(set(), {"f9"})
    assert to_unregister == []
    assert to_register == ["f9"]
    assert unchanged == []


def test_hotkey_diff_all_removed():
    """From {f9, f10} to empty: unregister all, register none."""
    to_unregister, to_register, unchanged = compute_hotkey_diff({"f9", "f10"}, set())
    assert to_unregister == ["f10", "f9"]
    assert to_register == []
    assert unchanged == []
