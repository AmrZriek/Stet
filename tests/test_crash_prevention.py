"""Tests for Issues #3 + #6 — hotkey stability and crash prevention."""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Issue #6: Crash on deleted CorrectionWindow ─────────────────────────────
def test_handle_hotkey_fired_uses_is_window_alive():
    """_handle_hotkey_fired should use _is_window_alive() instead of direct isVisible() call."""
    from stet.core.app import StetApp

    src = inspect.getsource(StetApp._handle_hotkey_fired)
    # Should NOT directly call self._window.isVisible()
    assert "self._window.isVisible()" not in src, (
        "_handle_hotkey_fired must NOT call self._window.isVisible() directly (causes crash on deleted C++)"
    )
    # Should use the safe helper
    assert "_is_window_alive" in src, (
        "_handle_hotkey_fired should use _is_window_alive() for safe window check"
    )


def test_is_window_alive_method_exists():
    """StetApp should have _is_window_alive method."""
    from stet.core.app import StetApp

    assert hasattr(StetApp, "_is_window_alive")
    src = inspect.getsource(StetApp._is_window_alive)
    # Should handle RuntimeError (deleted C++ object)
    assert "RuntimeError" in src
    # Should use sip.isdeleted for proactive check
    assert "sip.isdeleted" in src or "isdeleted" in src


def test_is_window_alive_clears_stale_reference():
    """_is_window_alive should set self._window = None when C++ is deleted."""
    from stet.core.app import StetApp

    src = inspect.getsource(StetApp._is_window_alive)
    assert "self._window = None" in src, (
        "_is_window_alive should clear self._window when C++ object is deleted"
    )


def test_handle_hotkey_fired_has_try_except_guard():
    """_handle_hotkey_fired should wrap window check in try/except to prevent stuck semaphore."""
    from stet.core.app import StetApp

    src = inspect.getsource(StetApp._handle_hotkey_fired)
    # Should have exception handling
    assert "except Exception" in src or "except RuntimeError" in src, (
        "_handle_hotkey_fired should catch exceptions from window check to prevent stuck _hotkey_busy"
    )


def test_show_window_connects_destroyed_signal():
    """_show_window should connect to the window's destroyed signal to clear stale refs."""
    from stet.core.app import StetApp

    src = inspect.getsource(StetApp._show_window)
    assert "destroyed" in src, (
        "_show_window should connect to destroyed signal to clear self._window on close"
    )


def test_on_window_destroyed_method_exists():
    """StetApp should have _on_window_destroyed method."""
    from stet.core.app import StetApp

    assert hasattr(StetApp, "_on_window_destroyed")
    src = inspect.getsource(StetApp._on_window_destroyed)
    assert "self._window = None" in src


# ── Issue #3: Hotkey busy semaphore never permanently stuck ──────────────────
def test_hotkey_worker_always_releases_semaphore():
    """_hotkey_worker should release _hotkey_busy in finally block."""
    from stet.core.app import StetApp

    src = inspect.getsource(StetApp._hotkey_worker)
    assert "finally:" in src
    assert "_hotkey_busy.release()" in src


def test_handle_hotkey_fired_releases_semaphore_on_window_focus():
    """When window is already open, _handle_hotkey_fired should release semaphore after focusing."""
    from stet.core.app import StetApp

    src = inspect.getsource(
        StetApp._handle_hotkey_fired
    )  # After focusing the window, it should release
    assert "_hotkey_busy.release()" in src
