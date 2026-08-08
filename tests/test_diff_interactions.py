"""Unit tests for diff interactions and help overlay diff legend widget."""

from unittest.mock import MagicMock
from PyQt6.QtWidgets import QLabel
import pytest

from stet.core.config import ConfigManager
import stet.core.config as config_mod
from stet.llm.model_manager import ModelManager
from stet.ui.main_window import CorrectionWindow


@pytest.fixture
def dummy_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cm = ConfigManager()
    cm.config["protected_terms"] = []
    return cm


@pytest.fixture
def mock_model():
    mm = MagicMock(spec=ModelManager)
    mm.is_loaded.return_value = True
    return mm


def make_window(original: str, dummy_cfg, mock_model) -> CorrectionWindow:
    win = CorrectionWindow(
        original="",
        ac_model=mock_model,
        chat_model=mock_model,
        cfg=dummy_cfg,
    )
    if original:
        win.original = original
    return win


def test_diff_legend_in_shortcuts_overlay_exists_and_styled(qtbot, dummy_cfg, mock_model):
    win = make_window("", dummy_cfg, mock_model)
    qtbot.addWidget(win)
    win.show()

    win._toggle_shortcuts_overlay()
    assert hasattr(win, "_shortcuts_overlay")
    assert win._shortcuts_overlay.isVisible() is True

    legend = win._shortcuts_overlay.findChild(QLabel, "helpDiffLegendLabel")
    assert legend is not None
    text = legend.text()
    assert "#f87171" in text  # deleted red
    assert "#60a5fa" in text  # added/changed blue
    assert "#4ade80" in text  # typo fix green
    assert "#88898c" in text  # secondary label color
    assert "Red: deleted" in text
    assert "Green: light single-word fix" in text
