"""Tests for stet.ui.settings — SettingsDialog, navigation, drag events."""

import json

import pytest
from PyQt6.QtCore import QPoint, Qt

from stet.core.config import ConfigManager
from stet.ui.settings import THEME, SettingsDialog

# ── Helpers ───────────────────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Return a ConfigManager that reads from a temporary config file."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "model_path": str(tmp_path / "fake-model.gguf"),
                "server_binary": "",
                "port": 8080,
                "context_size": 4096,
                "gpu_layers": 0,
                "temperature": 0.1,
                "top_k": 40,
                "top_p": 0.95,
                "min_p": 0.05,
                "keep_alive": False,
                "idle_timeout": 300,
                "streaming_strength": "smart_fix",
                "hotkeys": [
                    {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
                ],
                "custom_templates": [
                    {"name": "Test Template", "prompt": "Fix this text."}
                ],
            }
        ),
        encoding="utf-8",
    )

    import stet.core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return ConfigManager()


@pytest.fixture
def dialog(cfg, qtbot):
    """Return a SettingsDialog instance attached to qtbot."""
    dlg = SettingsDialog(cfg)
    qtbot.addWidget(dlg)
    return dlg


# ── Construction ──────────────────────────────────────────────────────────


class TestSettingsConstruction:
    """SettingsDialog builds its UI correctly."""

    def test_dialog_creates(self, dialog):
        assert dialog is not None
        assert dialog.stack is not None

    def test_sidebar_has_four_pages(self, dialog):
        assert dialog.nav_list.count() == 5

    def test_stack_has_four_pages(self, dialog):
        assert dialog.stack.count() == 5

    def test_sidebar_labels(self, dialog):
        labels = [
            dialog.nav_list.item(i).text() for i in range(dialog.nav_list.count())
        ]
        assert labels == [
            "About",
            "Parameters",
            "Correction Profiles",
            "Correction Modes",
            "Templates",
        ]

    def test_nav_changes_stack(self, dialog):
        dialog.nav_list.setCurrentRow(1)
        assert dialog.stack.currentIndex() == 1
        dialog.nav_list.setCurrentRow(2)
        assert dialog.stack.currentIndex() == 2


# ── State loading ─────────────────────────────────────────────────────────


class TestSettingsLoad:
    """_load() populates widgets from config."""

    def test_port_loaded(self, dialog):
        assert dialog.port_spin.value() == 8080

    def test_ctx_loaded(self, dialog):
        assert dialog.ctx_spin.value() == 4096

    def test_temp_loaded(self, dialog):
        assert abs(dialog.temp_spin.value() - 0.1) < 0.01

    def test_gpu_loaded(self, dialog):
        assert dialog.gpu_spin.value() == 0

    def test_hotkeys_loaded(self, dialog):
        assert dialog.hotkeys_list_w.count() >= 1


# ── Drag events ───────────────────────────────────────────────────────────


class TestSettingsDrag:
    """Frameless dialog drag behavior."""

    def test_drag_pos_initially_none(self, dialog):
        assert dialog._drag_pos is None

    def test_mouse_release_clears_drag(self, dialog, qtbot):
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QMouseEvent

        dialog._drag_pos = QPoint(10, 10)
        ev = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(100, 100),
            QPointF(100, 100),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        dialog.mouseReleaseEvent(ev)
        assert dialog._drag_pos is None


# ── Save ──────────────────────────────────────────────────────────────────


class TestSettingsSave:
    """_save() writes settings back to config."""

    def test_save_updates_port(self, dialog):
        dialog.port_spin.setValue(9090)
        dialog._save()
        assert dialog.cfg.get("server_port") == 9090

    def test_save_emits_signal(self, dialog, qtbot):
        with qtbot.waitSignal(dialog.saved, timeout=1000):
            dialog._save()

    def test_save_updates_temperature(self, dialog):
        dialog.temp_spin.setValue(0.5)
        dialog._save()
        assert abs(dialog.cfg.get("temperature") - 0.5) < 0.01


# ── THEME constant ────────────────────────────────────────────────────────


class TestThemeConstant:
    """The THEME stylesheet is defined and contains expected CSS."""

    def test_theme_is_string(self):
        assert isinstance(THEME, str)
        assert len(THEME) > 100

    def test_theme_contains_background(self):
        assert "background" in THEME.lower()

    def test_theme_contains_checkmark_placeholder(self):
        assert "{checkmark_url}" in THEME
