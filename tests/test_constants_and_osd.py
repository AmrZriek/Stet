"""Tests for CORE_TEMPLATES, DEFAULT_CONFIG, and the OSD widget class."""

from PyQt6.QtCore import Qt

from stet.constants import DEFAULT_CONFIG, DEFAULT_TEMPLATES
from stet.ui.osd import SilentCorrectionOSD


def test_core_templates_is_module_level():
    """DEFAULT_TEMPLATES should be accessible as a module-level constant."""
    assert isinstance(DEFAULT_TEMPLATES, list)
    assert len(DEFAULT_TEMPLATES) >= 4


def test_core_templates_are_tuples():
    """Each entry should be a dict with name and prompt strings."""
    for entry in DEFAULT_TEMPLATES:
        assert isinstance(entry, dict)
        name, prompt = entry["name"], entry["prompt"]
        assert isinstance(name, str) and len(name) > 0
        assert isinstance(prompt, str) and len(prompt) > 10


def test_default_config_has_hotkeys_list():
    """DEFAULT_CONFIG should contain a hotkeys list."""
    assert "hotkeys" in DEFAULT_CONFIG
    assert isinstance(DEFAULT_CONFIG["hotkeys"], list)


def test_default_config_has_no_legacy_hotkey_keys():
    """DEFAULT_CONFIG hotkeys should be the only hotkey source of truth."""
    assert "hotkey" not in DEFAULT_CONFIG
    assert "silent_hotkey" not in DEFAULT_CONFIG
    assert "silent_strength" not in DEFAULT_CONFIG


def test_osd_instantiation_success(qtbot):
    """SilentCorrectionOSD can be created in 'success' state without crashing."""
    osd = SilentCorrectionOSD("Test message", state="success")
    assert osd.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert osd._state == "success"
    osd.close()


def test_osd_instantiation_loading(qtbot):
    """SilentCorrectionOSD can be created in 'loading' state."""
    osd = SilentCorrectionOSD("Working...", state="loading")
    assert osd._state == "loading"
    osd.close()


def test_osd_instantiation_warning(qtbot):
    """SilentCorrectionOSD can be created in 'warning' state."""
    osd = SilentCorrectionOSD("Error!", state="warning")
    assert osd._state == "warning"
    osd.close()

