"""Tests for stet.ui.settings — SettingsDialog, navigation, drag events."""

import json

import pytest
from PyQt6.QtCore import QPoint, Qt
from unittest.mock import patch

from stet.core.config import ConfigManager
from stet.llm.gguf_info import GgufModelInfo, GgufReadError
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
                "streaming_strength": "full_correction",
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

    def test_sidebar_has_pages(self, dialog):
        assert dialog.nav_list.count() == 6

    def test_stack_has_pages(self, dialog):
        assert dialog.stack.count() == 6

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
            "Protected Terms",
        ]

    def test_nav_changes_stack(self, dialog):
        dialog.nav_list.setCurrentRow(1)
        assert dialog.stack.currentIndex() == 1
        dialog.nav_list.setCurrentRow(4)
        assert dialog.stack.currentIndex() == 4

    def test_parameters_page_has_model_and_chat_tabs(self, dialog):
        dialog.nav_list.setCurrentRow(1)
        tabs = dialog.params_page.tabs
        assert tabs.count() == 2
        assert tabs.tabText(0) == "Model Parameters"
        assert tabs.tabText(1) == "Chat Parameters"


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

    def test_kv_cache_loaded(self, dialog):
        assert dialog.kv_cache_k_combo.currentText() == "q8_0"
        assert dialog.kv_cache_v_combo.currentText() == "q8_0"
        assert dialog.chat_kv_cache_k_combo.currentText() == "q8_0"
        assert dialog.chat_kv_cache_v_combo.currentText() == "q8_0"


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

    def test_save_updates_kv_cache(self, dialog):
        dialog.kv_cache_k_combo.setCurrentText("q4_0")
        dialog.kv_cache_v_combo.setCurrentText("f16")
        dialog.chat_kv_cache_k_combo.setCurrentText("f16")
        dialog.chat_kv_cache_v_combo.setCurrentText("q4_0")
        dialog._save()
        assert dialog.cfg.get("kv_cache_type_k") == "q4_0"
        assert dialog.cfg.get("kv_cache_type_v") == "f16"
        assert dialog.cfg.get("chat_kv_cache_type_k") == "f16"
        assert dialog.cfg.get("chat_kv_cache_type_v") == "q4_0"


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


# ── Interactive Style Builder ──────────────────────────────────────────────


class TestTemplatesPageStyleBuilder:
    """TemplatesPage interactive style template creation."""

    def test_create_style_template_with_samples(self, dialog, qtbot):
        dialog.nav_list.setCurrentRow(4)  # Templates page
        page = dialog.templates_page
        page.sample1_edit.setPlainText("Sample text 1 written by user.")
        page.sample2_edit.setPlainText("Sample text 2 written by user.")

        page._create_style_template()

        # Verify template created in _temp_templates
        my_style = next((t for t in dialog._temp_templates if t.get("name") == "My Style"), None)
        assert my_style is not None
        assert "Sample text 1 written by user." in my_style["prompt"]
        assert "Sample text 2 written by user." in my_style["prompt"]

        # Verify list item selected
        curr_item = dialog.templates_list_w.currentItem()
        assert curr_item is not None
        assert curr_item.text() == "My Style"

    def test_create_style_template_fallback_without_samples(self, dialog, qtbot):
        dialog.nav_list.setCurrentRow(4)
        page = dialog.templates_page
        page.sample1_edit.clear()
        page.sample2_edit.clear()
        page.sample3_edit.clear()

        page._create_style_template()

        my_style = next((t for t in dialog._temp_templates if t.get("name") == "My Style"), None)
        assert my_style is not None
        assert "MY STYLE SAMPLES:" in my_style["prompt"]


# ── Model info row & effective context ────────────────────────────────────


class TestModelInfoRow:
    """GGUF metadata info row + effective-ctx label on the server page."""

    @staticmethod
    def _mock_gguf_info(**overrides):
        info = GgufModelInfo(
            path="/models/gemma-4.gguf",
            architecture="llama",
            name="Gemma 4",
            chat_template="<start_of_turn>user",
            n_ctx_train=8192,
            reasoning_capable=False,
        )
        for key, value in overrides.items():
            setattr(info, key, value)
        return info

    def test_info_row_populated_from_gguf(self, dialog):
        with patch(
            "stet.ui.settings_pages.get_gguf_info_cached",
            return_value=self._mock_gguf_info(),
        ):
            dialog.model_edit.setText("/models/gemma-4.gguf")
        text = dialog.model_info_lbl.text()
        assert "Gemma 4" in text
        assert "llama" in text
        assert "8192" in text
        assert "template: yes" in text
        assert "reasoning: no" in text

    def test_info_row_populates_filename_and_reasoning(self, dialog):
        with patch(
            "stet.ui.settings_pages.get_gguf_info_cached",
            return_value=self._mock_gguf_info(
                name=None,
                chat_template="<think>{{prompt}}</think>",
                reasoning_capable=True,
            ),
        ):
            dialog.model_edit.setText("/models/gemma-4.gguf")
        text = dialog.model_info_lbl.text()
        assert "gemma-4.gguf" in text
        assert "template: yes" in text
        assert "reasoning: yes" in text

    def test_info_row_unavailable_on_error(self, dialog):
        with patch(
            "stet.ui.settings_pages.get_gguf_info_cached",
            side_effect=GgufReadError("GGUF file not found"),
        ):
            dialog.model_edit.setText("/models/missing.gguf")
        assert "unavailable" in dialog.model_info_lbl.text()
        assert dialog.ctx_effective_lbl.isHidden()

    def test_info_row_unavailable_on_missing_package(self, dialog):
        with patch(
            "stet.ui.settings_pages.get_gguf_info_cached",
            side_effect=GgufReadError("gguf package is not installed"),
        ):
            dialog.model_edit.setText("/models/gemma-4.gguf")
        assert "unavailable" in dialog.model_info_lbl.text()
        # Dialog still fully usable — no crash, controls intact.
        assert dialog.ctx_spin.isEnabled()

    def test_effective_ctx_label_tracks_auto_and_custom(self, dialog):
        with patch(
            "stet.ui.settings_pages.get_gguf_info_cached",
            return_value=self._mock_gguf_info(n_ctx_train=8192),
        ):
            dialog.model_edit.setText("/models/gemma-4.gguf")
            # Custom ctx (fixture loads False for auto) → label hidden, spin enabled.
            assert dialog.ctx_effective_lbl.isHidden()
            assert dialog.ctx_spin.isEnabled()
            # Switch to auto → effective = min(8192, 12800) = 8192, spin disabled.
            dialog.ctx_auto_cb.setChecked(True)
            assert not dialog.ctx_effective_lbl.isHidden()
            assert dialog.ctx_effective_lbl.text() == "(effective: 8192)"
            assert not dialog.ctx_spin.isEnabled()
            # Custom ctx again → label hidden, spin enabled.
            dialog.ctx_auto_cb.setChecked(False)
            assert dialog.ctx_effective_lbl.isHidden()
            assert dialog.ctx_spin.isEnabled()

    def test_effective_ctx_hidden_when_info_missing(self, dialog):
        with patch(
            "stet.ui.settings_pages.get_gguf_info_cached",
            side_effect=GgufReadError("GGUF file not found"),
        ):
            dialog.ctx_auto_cb.setChecked(True)
            dialog.model_edit.setText("/models/missing.gguf")
        assert dialog.ctx_effective_lbl.isHidden()

    def test_save_context_size_manual_12800(self, dialog):
        """User can explicitly set 12800 tokens without triggering auto."""
        dialog.ctx_spin.setValue(12800)
        dialog.ctx_auto_cb.setChecked(False)
        dialog._save()
        assert dialog.cfg.get("context_size") == 12800
        assert dialog.cfg.get("context_size_auto") is False

        dialog.ctx_auto_cb.setChecked(True)
        dialog._save()
        assert dialog.cfg.get("context_size_auto") is True



