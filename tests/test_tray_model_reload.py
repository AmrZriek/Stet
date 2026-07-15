"""Tests for tray model load/unload routing and dynamic status menu updates."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtGui import QAction

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stet.core.app import StetApp
from stet.llm.model_manager import ModelManager


@pytest.fixture
def mock_app(monkeypatch, qtbot):
    """Create a StetApp instance with mocked UI dependencies to prevent window popups/COM errors."""
    monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)

    # Use a patch context manager to safely mock QSystemTrayIcon during StetApp init
    with patch("stet.core.app.QSystemTrayIcon") as mock_tray_cls:
        mock_tray = MagicMock()
        mock_tray_cls.return_value = mock_tray

        app = StetApp()

        # Replace real model managers with MagicMocks
        app.ac_model = MagicMock(spec=ModelManager)
        app.ac_model.label = "AC"
        app.chat_model = MagicMock(spec=ModelManager)
        app.chat_model.label = "Chat"

        # Replace menu actions and labels with mocks
        app._llm_menu_action = MagicMock(spec=QAction)
        app._status_lbl = MagicMock()
        app._chat_status_lbl = MagicMock()
        app._set_tray_icon = MagicMock()

        yield app


def test_tray_load_model(mock_app):
    """Tray load model always targets self.ac_model."""
    with patch("threading.Thread") as mock_thread:
        mock_app._tray_load_model()
        assert mock_thread.called
        target_fn = mock_thread.call_args[1].get("target")
        assert target_fn == mock_app.ac_model.load_model


def test_tray_unload_model(mock_app):
    """Tray unload model always targets self.ac_model."""
    mock_app._tray_unload_model()
    mock_app.ac_model.unload_model.assert_called_once()
    mock_app.chat_model.unload_model.assert_not_called()


def test_dynamic_menu_status_ac(mock_app):
    """_on_ac_status updates self._llm_menu_action text and status label."""
    mock_app.cfg.set("model_path", "C:/path/to/my-awesome-model.gguf")
    mock_app._on_ac_status("Ready")
    # Menu title shows only the short status; header keeps the model name.
    mock_app._llm_menu_action.setText.assert_called_with("Model: Ready")
    mock_app._status_lbl.setText.assert_called_with("● AC: Ready — my-awesome-model")


def test_dynamic_menu_status_chat_separate(mock_app):
    """_on_chat_status sets icon color and updates chat status label if separate model is used."""
    mock_app.cfg.set("chat_use_separate_model", True)
    mock_app.cfg.set("chat_model_path", "C:/path/to/my-chat-model.gguf")
    mock_app._on_chat_status("Ready")
    mock_app._set_tray_icon.assert_called_with("#a78bfa")
    mock_app._chat_status_lbl.setText.assert_called_with("● Chat: Ready — my-chat-model")


def test_dynamic_menu_status_chat_same(mock_app):
    """_on_chat_status sets icon color and hides chat status label if same model is used."""
    mock_app.cfg.set("chat_use_separate_model", False)
    mock_app._on_chat_status("Ready")
    mock_app._set_tray_icon.assert_called_with("#a78bfa")
    mock_app._chat_status_lbl.hide.assert_called_once()
