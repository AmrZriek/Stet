import sys
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

import stet.core.config as config_mod
from stet.core.config import ConfigManager
from stet.ui.welcome_window import WelcomeWindow

# Ensure winreg is mocked on non-Windows systems for testing
if sys.platform != "win32":
    winreg_mock = MagicMock()
    sys.modules["winreg"] = winreg_mock


@pytest.fixture
def temp_config_setup(tmp_path, monkeypatch):
    """Fixture to set up a temporary config file."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "SCRIPT_DIR", tmp_path)
    return config_file


def test_import():
    from stet.ui.welcome_window import WelcomeWindow
    assert WelcomeWindow is not None


def test_creation(temp_config_setup, qtbot):
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)
    assert not w.isVisible()
    w.close()


def test_sample_text_prefilled(temp_config_setup, qtbot):
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)
    text = w._sample_input.toPlainText()
    assert "Stet" in text or "him and me" in text
    w.close()


def test_template_buttons_present(temp_config_setup, qtbot):
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)
    template_btns = [c for c in w.findChildren(QPushButton) if c.objectName() == "welcomeTemplateBtn"]
    # Should have at least the 3 default template buttons
    assert len(template_btns) >= 3
    w.close()


def test_startup_checkboxes_default(temp_config_setup, qtbot):
    cfg = ConfigManager()
    # Check default values from ConfigManager/constants
    assert cfg.get("startup_on_login") is False
    assert cfg.get("show_welcome_on_startup") is True

    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)

    # Verify checkboxes reflect defaults
    assert not w._startup_cb.isChecked()
    assert w._show_on_launch_cb.isChecked()

    # Toggle and verify config updates
    w._startup_cb.setChecked(True)
    assert cfg.get("startup_on_login") is True

    w._show_on_launch_cb.setChecked(False)
    assert cfg.get("show_welcome_on_startup") is False

    w.close()


def test_signals(temp_config_setup, qtbot):
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)

    # Test settings_requested signal through link buttons
    links = [c for c in w.findChildren(QPushButton) if c.objectName() == "welcomeLinkBtn"]
    assert len(links) >= 4
    with qtbot.waitSignal(w.settings_requested, timeout=1000):
        links[0].click()

    # Test correction_requested signal when correct button is clicked
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True

    w_with_model = WelcomeWindow(cfg=cfg, ac_model=mock_model)
    qtbot.addWidget(w_with_model)

    assert w_with_model._correct_btn.isEnabled() is True

    with qtbot.waitSignal(w_with_model.correction_requested, timeout=1000) as blocker:
        w_with_model._correct_btn.click()

    assert blocker.args[0] == w_with_model._sample_input.toPlainText()
    assert blocker.args[1] == "full_correction"
    assert blocker.args[2] == ""

    # Now select a template and click correct
    first_tmpl_btn = w_with_model._template_btns[0]
    first_tmpl_btn.setChecked(True)
    w_with_model._on_template_clicked(first_tmpl_btn, first_tmpl_btn.text())

    with qtbot.waitSignal(w_with_model.correction_requested, timeout=1000) as blocker2:
        w_with_model._correct_btn.click()

    assert blocker2.args[2] == first_tmpl_btn.text()

    w.close()
    w_with_model.close()


def test_closed_signal(temp_config_setup, qtbot):
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)

    with qtbot.waitSignal(w.closed_signal, timeout=1000):
        w.close()


def test_stet_app_welcome_flag_polling(temp_config_setup, qtbot, monkeypatch):
    from pathlib import Path
    import tempfile
    from stet.core.app import StetApp
    from stet.llm.model_manager import ModelManager

    monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
    app = StetApp()

    # Create the flag file
    flag_path = Path(tempfile.gettempdir()) / "stet_show_welcome.flag"
    flag_path.write_text("show", encoding="utf-8")

    show_welcome_called = False
    def mock_show_welcome():
        nonlocal show_welcome_called
        show_welcome_called = True
    monkeypatch.setattr(app, "_show_welcome", mock_show_welcome)

    app._check_welcome_flag()

    assert not flag_path.exists()
    assert show_welcome_called is True


def test_stet_app_show_welcome(temp_config_setup, qtbot, monkeypatch):
    from stet.core.app import StetApp
    from stet.llm.model_manager import ModelManager
    from unittest.mock import patch

    monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
    app = StetApp()

    assert app._welcome_window is None
    app._show_welcome()
    assert app._welcome_window is not None
    qtbot.addWidget(app._welcome_window)

    # Call show_welcome again, it should just show/raise the existing one
    welcome_win = app._welcome_window
    with patch.object(welcome_win, "raise_") as mock_raise:
        app._show_welcome()
        mock_raise.assert_called_once()

    app._on_welcome_closed()
    assert app._welcome_window is None


def test_stet_app_welcome_correction_flow(temp_config_setup, qtbot, monkeypatch):
    from stet.core.app import StetApp
    from stet.llm.model_manager import ModelManager
    from unittest.mock import patch, MagicMock

    monkeypatch.setattr(ModelManager, "load_model", lambda *a, **k: None)
    app = StetApp()

    # Mock ac_model loading and correction
    app.ac_model.is_loaded = MagicMock(return_value=True)
    app.ac_model.correct_text_patch = MagicMock(return_value=("corrected text", 1))

    app._show_welcome()
    qtbot.addWidget(app._welcome_window)

    # Trigger correction
    with patch.object(app._welcome_window, "set_corrected_text") as mock_set_text:
        app._on_welcome_correction("input text", "full_correction", "")

        # Wait for the thread/QTimer to fire and complete the correction
        qtbot.waitUntil(lambda: mock_set_text.called, timeout=3000)
        mock_set_text.assert_called_once_with("input text", "corrected text")


def test_set_corrected_text_in_layout_forces_show_and_scroll(temp_config_setup, qtbot):
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)
    w.show()
    assert w._unified_output.isVisible() is False

    w.set_corrected_text("original here.", "corrected here.")

    assert w._unified_output.isVisible() is True
    assert hasattr(w, "_scroll")
    assert hasattr(w, "_scroll_content")
    assert hasattr(w, "_scroll_lay")
    w.close()


def test_welcome_content_width_invariant_through_result_panel(temp_config_setup, qtbot):
    """Showing the unified output must not change the horizontal width of the
    scroll content. Regression guard against the 'shift-left/clip' UX bug."""
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)
    w.show()
    w.resize(560, 600)
    qtbot.waitExposed(w)

    before_w = w._scroll_content.width()
    w.set_corrected_text("hello", "world")
    qtbot.wait(50)
    after_w = w._scroll_content.width()

    # After showing unified output, content must not have grown wider
    assert after_w <= before_w
    w.close()


def test_unified_output_shows_correction(temp_config_setup, qtbot):
    """After set_corrected_text, _unified_output is visible and _result_output
    has diff HTML (contains an escaped word)."""
    cfg = ConfigManager()
    w = WelcomeWindow(cfg=cfg)
    qtbot.addWidget(w)
    w.show()

    w.set_corrected_text("original here.", "corrected here.")

    assert w._unified_output.isVisible() is True
    assert w._output_mode == "correction"
    assert w._output_title.text() == "CORRECTED TEXT"
    html = w._result_output.toHtml()
    assert "corrected" in html or "here" in html
    assert hasattr(w, "_corrected_text") and w._corrected_text == "corrected here."
    w.close()


def test_chat_shows_unified_output(temp_config_setup, qtbot):
    """After chat send, _unified_output is visible and shows streaming text."""
    from unittest.mock import MagicMock
    cfg = ConfigManager()
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True
    mock_model.make_stream_worker = MagicMock(return_value=MagicMock())
    w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
    qtbot.addWidget(w)
    w.show()

    w._chat_input.setText("rewrite this")
    w._on_chat_send()

    # _unified_output should be visible immediately on send (Generating...)
    assert w._unified_output.isVisible() is True
    assert w._output_mode == "chat"
    assert w._output_title.text() == "CHAT"
    txt = w._result_output.toHtml()
    assert "Generating" in txt
    w.close()


def test_chat_done_shows_transcript_not_diff(temp_config_setup, qtbot):
    """After _on_chat_done, _result_output shows a chat transcript and does not
    treat the response as a correction."""
    from unittest.mock import MagicMock
    cfg = ConfigManager()
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True
    w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
    qtbot.addWidget(w)
    w.show()

    w._sample_input.setPlainText("hello world this is a test")
    w._chat_input.setText("rewrite")
    w._on_chat_send()
    w._on_chat_done("hello world this is a revised test")

    # Chat responses must not become the corrected text.
    assert w._output_mode == "chat"
    assert w._output_title.text() == "CHAT"
    assert not hasattr(w, "_corrected_text") or w._corrected_text != "hello world this is a revised test"
    html = w._result_output.toHtml()
    # The transcript should contain both the user message and the assistant reply.
    assert "rewrite" in html
    assert "revised" in html
    assert w._last_assistant_response == "hello world this is a revised test"
    w.close()


def test_reset_hides_chat_and_restores_correction(temp_config_setup, qtbot):
    """_on_chat_reset hides the chat transcript and restores the correction
    diff view when a correction result exists."""
    from unittest.mock import MagicMock
    cfg = ConfigManager()
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True
    w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
    qtbot.addWidget(w)
    w.show()

    w.set_corrected_text("hello", "hello fixed")
    assert w._unified_output.isVisible() is True
    assert w._output_mode == "correction"

    # Start a chat turn
    w._chat_input.setText("shorten")
    w._on_chat_send()
    w._on_chat_done("short")

    assert w._output_mode == "chat"

    w._on_chat_reset()

    # Chat history should be cleared and output restored to correction view.
    assert len(w._chat_history) == 0
    assert w._output_mode == "correction"
    assert w._output_title.text() == "CORRECTED TEXT"
    assert w._unified_output.isVisible() is True
    html = w._result_output.toHtml()
    assert "fixed" in html
    w.close()


def test_reset_without_correction_hides_output(temp_config_setup, qtbot):
    """When no correction exists, reset hides the unified output area."""
    from unittest.mock import MagicMock
    cfg = ConfigManager()
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True
    w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
    qtbot.addWidget(w)
    w.show()

    w._chat_input.setText("hello")
    w._on_chat_send()
    w._on_chat_done("hi there")
    assert w._unified_output.isVisible() is True

    w._on_chat_reset()
    assert w._unified_output.isVisible() is False
    w.close()


def test_copy_button_copies_last_assistant_response_in_chat(temp_config_setup, qtbot):
    """Copy button copies the last assistant response when chat is shown."""
    from unittest.mock import MagicMock
    cfg = ConfigManager()
    mock_model = MagicMock()
    mock_model.is_loaded.return_value = True
    w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
    qtbot.addWidget(w)
    w.show()

    w.set_corrected_text("orig", "corrected")
    w._chat_input.setText("translate")
    w._on_chat_send()
    w._on_chat_done("translated text")

    w._on_copy_clicked()
    assert QApplication.clipboard().text() == "translated text"

    # After reset (correction restored), copy should return the corrected text.
    w._on_chat_reset()
    w._on_copy_clicked()
    assert QApplication.clipboard().text() == "corrected"
    w.close()


class TestWelcomeWindowControls:
    def test_header_has_minimize_maximize_close_buttons(self, temp_config_setup, qtbot):
        cfg = ConfigManager()
        w = WelcomeWindow(cfg=cfg)
        qtbot.addWidget(w)
        w.show()
        assert w._min_btn is not None
        assert w._max_btn is not None
        assert w._close_btn is not None
        w.close()

    def test_window_has_minimum_size(self, temp_config_setup, qtbot):
        cfg = ConfigManager()
        w = WelcomeWindow(cfg=cfg)
        qtbot.addWidget(w)
        assert w.minimumWidth() >= 480
        assert w.minimumHeight() >= 480
        w.close()


class TestWelcomeChat:
    """Tests for the unified chat transcript in the WelcomeWindow."""

    def test_chat_message_html(self, temp_config_setup, qtbot):
        """_chat_message_html returns role-aligned HTML."""
        cfg = ConfigManager()
        w = WelcomeWindow(cfg=cfg)
        qtbot.addWidget(w)

        user_html = w._chat_message_html("user", "hello")
        assert "text-align:right" in user_html
        assert "#93c5fd" in user_html
        assert "hello" in user_html

        assistant_html = w._chat_message_html("assistant", "hi")
        assert "text-align:left" in assistant_html
        assert "#e2e8f0" in assistant_html
        assert "hi" in assistant_html
        w.close()

    def test_chat_send_renders_transcript(self, temp_config_setup, qtbot):
        """Sending a chat message renders a transcript with user + assistant placeholder."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = True
        mock_model.make_stream_worker = MagicMock(return_value=MagicMock())
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)
        w.show()

        w._chat_input.setText("fix this text")
        w._on_chat_send()

        html = w._result_output.toHtml()
        assert "fix this text" in html
        assert "Generating" in html
        assert w._chat_input.text() == ""
        assert w._unified_output.isVisible()
        assert w._output_title.text() == "CHAT"
        w.close()

    def test_chat_send_injects_sample_text(self, temp_config_setup, qtbot):
        """Chat system prompt includes the sample input text so the LLM has context."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = True
        mock_model.make_stream_worker = MagicMock(return_value=MagicMock())
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)

        w._sample_input.setPlainText("This is my sample text to rewrite.")
        w._chat_input.setText("make it shorter")
        w._on_chat_send()

        assert len(w._chat_history) >= 2
        assert w._chat_history[0]["role"] == "system"
        assert "This is my sample text to rewrite." in w._chat_history[0]["content"]
        w.close()

    def test_chat_conversation_updates_system_text(self, temp_config_setup, qtbot):
        """In Conversation mode, changing the sample text updates the system prompt."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = True
        mock_model.make_stream_worker = MagicMock(return_value=MagicMock())
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)

        w._chat_combo.setCurrentText("Conversation")
        w._sample_input.setPlainText("First text.")
        w._chat_input.setText("rewrite")
        w._on_chat_send()

        assert "First text." in w._chat_history[0]["content"]

        # Change the sample text and send again — system prompt should update
        w._sample_input.setPlainText("Second text.")
        w._chat_input.setText("shorten")
        w._on_chat_send()

        assert "Second text." in w._chat_history[0]["content"]
        assert "First text." not in w._chat_history[0]["content"]
        w.close()

    def test_chat_reset_clears_transcript(self, temp_config_setup, qtbot):
        """Reset button clears chat history and last assistant response."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = True
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)
        w.show()

        w._chat_input.setText("hello")
        w._on_chat_send()
        w._on_chat_done("hi there")
        assert len(w._chat_history) > 0

        w._on_chat_reset()

        assert len(w._chat_history) == 0
        assert w._last_assistant_response == ""
        assert w._unified_output.isVisible() is False
        w.close()

    def test_chat_token_updates_transcript(self, temp_config_setup, qtbot):
        """Streaming tokens update the current assistant message in the transcript."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = True
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)

        w._chat_input.setText("count")
        w._on_chat_send()

        w._on_chat_token("one")
        w._on_chat_token(" two")
        html = w._result_output.toHtml()
        assert "one two" in html
        w.close()

    def test_chat_send_not_loaded_shows_error_transcript(self, temp_config_setup, qtbot):
        """Sending chat when model not loaded renders an error in the unified output."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = False
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)
        w.show()

        w._chat_input.setText("test message")
        w._on_chat_send()

        assert w._unified_output.isVisible()
        assert w._output_title.text() == "CHAT"
        html = w._result_output.toHtml()
        assert "not loaded" in html.lower() or "Model not loaded" in html
        w.close()

    def test_chat_fresh_mode_clears_history(self, temp_config_setup, qtbot):
        """In Fresh mode, chat history resets before each message."""
        from unittest.mock import MagicMock
        cfg = ConfigManager()
        mock_model = MagicMock()
        mock_model.is_loaded.return_value = True
        mock_model.make_stream_worker = MagicMock(return_value=MagicMock())
        w = WelcomeWindow(cfg=cfg, ac_model=mock_model)
        qtbot.addWidget(w)

        w._chat_combo.setCurrentText("Fresh")

        w._chat_history = [{"role": "system", "content": "old"}, {"role": "user", "content": "old"}]

        w._chat_input.setText("new message")
        w._on_chat_send()

        # History should be truncated to system + the new user message.
        assert len(w._chat_history) == 2
        assert w._chat_history[-1]["content"] == "new message"
        w.close()
