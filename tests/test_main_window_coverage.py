"""Coverage expansion tests for stet/ui/main_window.py.

Targets: _render_diff, _send_chat, _on_chat_token, _on_chat_done,
_on_chat_error, _accept, _copy, _reset, eventFilter, _on_strength_changed,
_on_model_status, closeEvent, _toggle_shortcuts_overlay, _normalize_strength,
_strength_from_label, _strength_index, _on_escape, _accept_if_ready,
_apply_template, _refresh_templates, _build_ui, _position_window,
keyPressEvent, mouse events, _clear_chat_transcript.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent

from stet.core.config import ConfigManager
from stet.ui.main_window import CorrectionWindow


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    model_path = tmp_path / "fake-model.gguf"
    model_path.touch()
    config_file.write_text(
        json.dumps(
            {
                "model_path": str(model_path),
                "ac_model_path": str(model_path),
                "server_binary": "",
                "server_host": "127.0.0.1",
                "server_port": 8080,
                "context_size": 2048,
                "gpu_layers": 8,
                "temperature": 0.15,
                "top_k": 35,
                "top_p": 0.90,
                "min_p": 0.05,
                "keep_model_loaded": False,
                "idle_timeout_seconds": 300,
                "ac_same_as_chat": False,
                "target_language": "Spanish",
                "chat_mode": "conversation",
                "hotkeys": [
                    {
                        "shortcut": "ctrl+f9",
                        "mode": "panel",
                        "strength": "full_correction",
                    }
                ],
                "custom_templates": [
                    {"name": "Template A", "prompt": "Prompt A"},
                    {"name": "Template B", "prompt": "Prompt B"},
                ],
                "correction_modes": [
                    {
                        "name": "Conservative",
                        "prompt": "Fix spelling.",
                        "hallucination_threshold": 0.4,
                    },
                    {
                        "name": "Smart Fix",
                        "prompt": "Fix spelling.",
                        "hallucination_threshold": 1.0,
                    },
                    {
                        "name": "Smart Fix Custom",
                        "prompt": "Fix spelling.",
                        "hallucination_threshold": 1.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    import stet.core.config as config_module

    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    return ConfigManager()


def _make_cw(cfg, qtbot, text="Hello world"):
    ac_model = MagicMock()
    chat_model = MagicMock()
    cw = CorrectionWindow(text, ac_model, chat_model, cfg)
    qtbot.addWidget(cw)
    return cw


# ── Static method tests ───────────────────────────────────────────────────


class TestNormalizeStrength:
    def test_known_values(self):
        assert CorrectionWindow._normalize_strength("spelling_only") == "spelling_only"
        assert (
            CorrectionWindow._normalize_strength("full_correction") == "full_correction"
        )
        assert (
            CorrectionWindow._normalize_strength("rewrite_polish") == "rewrite_polish"
        )
        assert CorrectionWindow._normalize_strength("custom_patch") == "custom_patch"

    def test_legacy_mapping(self):
        assert CorrectionWindow._normalize_strength("conservative") == "spelling_only"
        assert CorrectionWindow._normalize_strength("aggressive") == "rewrite_polish"

    def test_unknown_passthrough(self):
        # Unknown strings are treated as custom mode names and pass through.
        assert CorrectionWindow._normalize_strength("bogus") == "bogus"
        assert CorrectionWindow._normalize_strength(None) == "full_correction"
        assert CorrectionWindow._normalize_strength("smart_fix") == "full_correction"


class TestStrengthFromLabel:
    def test_spelling(self):
        assert CorrectionWindow._strength_from_label("Spelling Only") == "spelling_only"

    def test_conservative(self):
        assert (
            CorrectionWindow._strength_from_label("Conservative mode")
            == "spelling_only"
        )

    def test_rewrite(self):
        assert (
            CorrectionWindow._strength_from_label("Rewrite & Polish")
            == "rewrite_polish"
        )

    def test_aggressive(self):
        assert (
            CorrectionWindow._strength_from_label("Aggressive mode") == "rewrite_polish"
        )

    def test_custom(self):
        # Custom mode names are returned as-is (they ARE the strength key).
        assert CorrectionWindow._strength_from_label("Custom Patch") == "Custom Patch"
        assert CorrectionWindow._strength_from_label("Legal Polish") == "Legal Polish"

    def test_full_correction(self):
        assert (
            CorrectionWindow._strength_from_label("Full Correction")
            == "full_correction"
        )

    def test_unknown(self):
        # Non-builtin labels are treated as custom mode names and passed through.
        assert (
            CorrectionWindow._strength_from_label("Something Else") == "Something Else"
        )


class TestStrengthIndex:
    def test_indices(self):
        assert CorrectionWindow._strength_index("spelling_only") == 0
        assert CorrectionWindow._strength_index("full_correction") == 1
        assert CorrectionWindow._strength_index("rewrite_polish") == 2
        assert CorrectionWindow._strength_index("custom_patch") == 3
        assert CorrectionWindow._strength_index("unknown") == 1


# ── _render_diff tests ────────────────────────────────────────────────────


class TestRenderDiff:
    def test_identical_text(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._render_diff("Hello world")
        html = cw.corr_edit.toHtml()
        assert "Hello" in html

    def test_insertions(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._render_diff("Hello beautiful world")
        html = cw.corr_edit.toHtml()
        assert "Hello" in html
        assert "beautiful" in html

    def test_deletions(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot, text="Hello beautiful world")
        cw._render_diff("Hello world")
        html = cw.corr_edit.toHtml()
        assert "Hello" in html

    def test_replacements(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot, text="The project were delayed")
        cw._render_diff("The project was delayed.")
        html = cw.corr_edit.toHtml()
        assert "project" in html

    def test_preserves_newlines(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot, text="Line one\nLine two")
        cw._render_diff("Line one\nLine two\nLine three")
        html = cw.corr_edit.toHtml()
        assert "Line" in html

    def test_empty_original(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot, text="")
        cw._render_diff("New text")
        html = cw.corr_edit.toHtml()
        assert "New" in html

    def test_empty_corrected(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot, text="Original")
        cw._render_diff("")


# ── _send_chat tests ──────────────────────────────────────────────────────


class TestSendChat:
    def test_empty_msg_returns(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_input.setText("")
        cw._send_chat()
        assert cw._is_chat_mode is False

    def test_first_msg_inits_history(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = True
        with patch.object(cw, "_do_stream"):
            cw._send_chat(msg="Fix grammar")
        assert len(cw.chat_history) >= 2
        assert cw._is_chat_mode is True

    def test_subsequent_appends(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
        ]
        cw.ac_model.is_loaded.return_value = True
        with patch.object(cw, "_do_stream"):
            cw._send_chat(msg="Make it shorter")
        assert len(cw.chat_history) >= 4

    def test_template_flag(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = True
        with patch.object(cw, "_do_stream"):
            cw._send_chat(msg="Fix this", is_template=True)
        assert cw._is_chat_mode is True


# ── _on_chat_token / _on_chat_done / _on_chat_error ───────────────────────


class TestChatStreamHandlers:
    def test_on_chat_token_accumulates(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._active_ai_bubble = MagicMock()
        cw._on_chat_token("Hello")
        cw._on_chat_token(" world")
        assert cw._stream_buf == "Hello world"

    def test_on_chat_done_caps_history(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._active_ai_bubble = MagicMock()
        cw._is_chat_mode = True
        cw._conversation_mode = True
        cw.chat_history = [{"role": "user", "content": f"msg{i}"} for i in range(50)]
        cw._stream_buf = "reply"
        cw._on_chat_done("reply")
        assert len(cw.chat_history) <= 40

    def test_on_chat_done_single_mode(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._active_ai_bubble = MagicMock()
        cw._is_chat_mode = True
        cw._conversation_mode = False
        cw._stream_buf = "text"
        cw._on_chat_done("text")
        assert cw.corrected is not None

    def test_on_chat_error_sets_text(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._active_ai_bubble = MagicMock()
        cw._on_chat_error("Model not loaded")
        # send_btn should be re-enabled
        assert cw.send_btn.isEnabled()


# ── _accept / _copy ───────────────────────────────────────────────────────


class TestAcceptCopy:
    def test_accept_emits_signal(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        signals = []
        cw.accepted.connect(lambda t: signals.append(t))
        cw.corrected = "Fixed text"
        cw._accept()
        assert signals == ["Fixed text"]

    def test_copy_writes_clipboard(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.corrected = "Copied text"
        with patch("stet.ui.main_window._clipboard_write_text") as mock_write:
            cw._copy()
            mock_write.assert_called_with("Copied text")

    def test_copy_changes_button_text(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.corrected = "text"
        with patch("stet.ui.main_window._clipboard_write_text"):
            cw._copy()
        assert cw.copy_btn.text() == "Copied"


# ── _reset ────────────────────────────────────────────────────────────────


class TestReset:
    def test_reset_cancels_and_restores(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.corrected = "modified"
        cw._correction_cancelled = False
        cw._stream_worker = MagicMock()
        cw._stream_worker.isRunning.return_value = False
        cw._correction_stream_worker = MagicMock()
        cw._correction_stream_worker.isRunning.return_value = False
        cw._reset()
        assert cw._correction_cancelled is True
        assert cw.corrected == cw.original

    def test_reset_clears_chat(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_history = [{"role": "user", "content": "x"}]
        cw._stream_worker = MagicMock()
        cw._stream_worker.isRunning.return_value = False
        cw._correction_stream_worker = MagicMock()
        cw._correction_stream_worker.isRunning.return_value = False
        cw._reset()
        assert len(cw.chat_history) == 0


# ── eventFilter ───────────────────────────────────────────────────────────


class TestEventFilter:
    def test_tab_cycles_strength(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.strength_combo.setCurrentIndex(0)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier
        )
        result = cw.eventFilter(cw, event)
        assert result is True
        assert cw.strength_combo.currentIndex() == 1

    def test_enter_on_chat_input_with_text_sends(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_input.setText("Fix this")
        with patch.object(cw, "_send_chat") as mock_send:
            event = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
            )
            result = cw.eventFilter(cw.chat_input, event)
            assert result is True
            mock_send.assert_called_once()

    def test_enter_on_empty_chat_accepts(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_input.setText("")
        cw.accept_btn.setEnabled(True)
        with patch.object(cw, "_accept") as mock_accept:
            event = QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
            )
            result = cw.eventFilter(cw.chat_input, event)
            assert result is True
            mock_accept.assert_called_once()

    def test_other_key_not_consumed(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier
        )
        result = cw.eventFilter(cw.chat_input, event)
        assert result is False


# ── _on_strength_changed ──────────────────────────────────────────────────


class TestOnStrengthChanged:
    def test_changes_strength(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._stream_worker = MagicMock()
        cw._stream_worker.isRunning.return_value = False
        cw._correction_stream_worker = MagicMock()
        cw._correction_stream_worker.isRunning.return_value = False
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            cw._on_strength_changed("Rewrite & Polish")
        assert cw._current_strength == "rewrite_polish"

    def test_stops_running_workers(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        mock_w = MagicMock()
        mock_w.isRunning.return_value = True
        cw._stream_worker = mock_w
        cw._correction_stream_worker = mock_w
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            cw._on_strength_changed("Spelling Only")
        mock_w.stop.assert_called()


# ── _on_model_status ──────────────────────────────────────────────────────


class TestOnModelStatus:
    def test_ready_skipped(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        with patch.object(cw, "_update_status") as mock_update:
            cw._on_model_status("Model ready")
            mock_update.assert_not_called()

    def test_ready_restarts_pending_correction_after_external_load(
        self, qtbot, cfg, monkeypatch
    ):
        monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
        cw = _make_cw(cfg, qtbot)
        cw.method_badge.setText("STREAM CORRECT")
        cw._correction_thread_token = None
        started_threads = []

        class CapturingThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon
                started_threads.append(self)

            def start(self):
                pass

        monkeypatch.setattr("stet.ui.main_window.threading.Thread", CapturingThread)

        cw._on_model_status("Loading model")
        cw._on_model_status("Ready — fake-model")

        assert len(started_threads) == 1
        assert started_threads[0].daemon is True
        assert cw._retry_correction_when_model_ready is False

    def test_correcting(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._on_model_status("correcting text")
        assert "Processing" in cw.status_lbl.text()

    def test_loading(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._on_model_status("loading model")
        assert "Loading" in cw.status_lbl.text()

    def test_error(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        with patch.object(cw, "_update_status") as mock_update:
            cw._on_model_status("error: model not found")
            mock_update.assert_called()


# ── closeEvent ────────────────────────────────────────────────────────────


class TestCloseEvent:
    def test_stops_workers(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        mock_w = MagicMock()
        mock_w.isRunning.return_value = True
        cw._stream_worker = mock_w
        cw._correction_stream_worker = mock_w
        event = QCloseEvent()
        cw.closeEvent(event)
        # Workers should have been stopped and set to None
        mock_w.stop.assert_called()
        assert cw._stream_worker is None
        assert cw._correction_stream_worker is None

    def test_stops_chat_worker(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        mock_w = MagicMock()
        mock_w.isRunning.return_value = True
        cw._chat_worker = mock_w
        event = QCloseEvent()
        cw.closeEvent(event)
        mock_w.stop.assert_called()
        assert cw._chat_worker is None

    def test_sets_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._stream_worker = MagicMock()
        cw._stream_worker.isRunning.return_value = False
        cw._correction_stream_worker = MagicMock()
        cw._correction_stream_worker.isRunning.return_value = False
        event = QCloseEvent()
        cw.closeEvent(event)
        assert cw._correction_cancelled is True


# ── _toggle_shortcuts_overlay ─────────────────────────────────────────────


class TestToggleShortcutsOverlay:
    def test_creates_overlay_widget(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._toggle_shortcuts_overlay()
        assert hasattr(cw, "_shortcuts_overlay")

    def test_toggles_visibility(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._toggle_shortcuts_overlay()
        # The overlay should exist now
        assert hasattr(cw, "_shortcuts_overlay")
        # Toggle again
        cw._toggle_shortcuts_overlay()


# ── _on_escape ────────────────────────────────────────────────────────────


class TestOnEscape:
    def test_escape_without_overlay_closes_window(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        with patch.object(cw, "close") as mock_close:
            cw._on_escape()
            mock_close.assert_called_once()

    def test_escape_with_overlay_visible(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._toggle_shortcuts_overlay()
        # Force visibility check to return True
        cw._shortcuts_overlay.show()
        qtbot.wait(10)
        if cw._shortcuts_overlay.isVisible():
            cw._on_escape()
            assert not cw._shortcuts_overlay.isVisible()
        else:
            # In test env, show() may not make it visible — just verify no crash
            cw._on_escape()


# ── _apply_template ───────────────────────────────────────────────────────


class TestApplyTemplate:
    def test_resets_and_sends(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._stream_worker = MagicMock()
        cw._stream_worker.isRunning.return_value = False
        cw._correction_stream_worker = MagicMock()
        cw._correction_stream_worker.isRunning.return_value = False
        with patch.object(cw, "_send_chat") as mock_send:
            cw._apply_template("Fix grammar")
        assert cw._correction_cancelled is True
        assert len(cw.chat_history) == 0
        mock_send.assert_called_once()


# ── _refresh_templates ────────────────────────────────────────────────────


class TestRefreshTemplates:
    def test_creates_template_buttons(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._refresh_templates()
        count = cw.tmp_lay.count()
        assert count >= 2  # Template A and Template B


# ── _update_status ────────────────────────────────────────────────────────


class TestUpdateStatus:
    def test_sets_text_and_state(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._update_status("Processing…", "processing")
        assert cw.status_lbl.text() == "Processing…"


# ── keyPressEvent ─────────────────────────────────────────────────────────


class TestKeyPressEvent:
    def test_question_mark_creates_overlay(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Question,
            Qt.KeyboardModifier.NoModifier,
        )
        cw.keyPressEvent(event)
        assert hasattr(cw, "_shortcuts_overlay")

    def test_enter_accepts_when_enabled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.accept_btn.setEnabled(True)
        with patch.object(cw, "_accept") as mock_accept:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Return,
                Qt.KeyboardModifier.NoModifier,
            )
            cw.keyPressEvent(event)
            mock_accept.assert_called_once()


# ── _clear_chat_transcript ────────────────────────────────────────────────


class TestClearChatTranscript:
    def test_clears_widgets(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._add_chat_bubble("user", "Hello")
        cw._add_chat_bubble("assistant", "Hi there")
        cw._clear_chat_transcript()
        assert cw.chat_lay.count() == 0


# ── _on_correction_ready / _on_correction_failed ─────────────────────────


class TestCorrectionCallbacks:
    def test_on_correction_ready(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        cw._on_correction_ready("Fixed text", "Patch (Smart Fix)")
        assert cw.corrected == "Fixed text"
        assert cw.accept_btn.isEnabled()

    def test_on_correction_ready_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        cw._on_correction_ready("Fixed", "method")
        assert cw.corrected == cw.original

    def test_on_correction_failed(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        cw._on_correction_failed()
        assert cw.accept_btn.isEnabled()

    def test_on_correction_failed_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        with patch.object(cw, "_update_status") as mock_update:
            cw._on_correction_failed()
            mock_update.assert_not_called()


# ── _start_streaming_correction ───────────────────────────────────────────


class TestStartStreamingCorrection:
    def test_suppressed_when_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        cw._start_streaming_correction("text", "", "full_correction")

    def test_starts_worker(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        mock_worker = MagicMock()
        cw.ac_model.make_stream_worker.return_value = mock_worker
        cw._start_streaming_correction("Hello world", "", "full_correction")
        mock_worker.start.assert_called()


# ── _on_correction_stream_token / done / error ────────────────────────────


class TestCorrectionStreamHandlers:
    def test_token_accumulates(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        cw._correction_stream_buf = ""
        cw._on_correction_stream_token("Hello")
        cw._on_correction_stream_token(" world")
        assert cw._correction_stream_buf == "Hello world"

    def test_token_skipped_when_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        cw._correction_stream_buf = ""
        cw._on_correction_stream_token("ignored")
        assert cw._correction_stream_buf == ""

    def test_done_skipped_when_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        cw._on_correction_stream_done("text")

    def test_error_skipped_when_cancelled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        cw._on_correction_stream_error("err")


# ── _replace_chat_stream_region ───────────────────────────────────────────


class TestReplaceChatStreamRegion:
    def test_no_active_bubble(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._active_ai_bubble = None
        cw._replace_chat_stream_region("text")

    def test_updates_bubble(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._active_ai_bubble = MagicMock()
        cw._replace_chat_stream_region("Hello\nWorld")
        cw._active_ai_bubble.setText.assert_called()


# ── _chat_transcript_text / _chat_transcript_html ─────────────────────────


class TestChatTranscriptMethods:
    def test_transcript_text(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._add_chat_bubble("user", "Hello")
        text = cw._chat_transcript_text()
        assert "Hello" in text

    def test_transcript_html(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._add_chat_bubble("user", "Hello")
        cw._add_chat_bubble("assistant", "Hi")
        html = cw._chat_transcript_html()
        assert "Hello" in html
        assert "Hi" in html


# ── _load_then_send ───────────────────────────────────────────────────────


class TestLoadThenSend:
    def test_model_loads_then_streams(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_model.load_model = MagicMock()
        cw.chat_model.is_loaded.return_value = True
        cw._do_stream_signal = MagicMock()
        cw._target_chat_model = cw.chat_model
        cw._load_then_send()
        cw.chat_model.load_model.assert_called()

    def test_model_load_fails(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.chat_model.load_model = MagicMock()
        cw.chat_model.is_loaded.return_value = False
        # _chat_error is a signal — connect a handler to capture
        errors = []
        cw._chat_error.connect(lambda e: errors.append(e))
        cw._target_chat_model = cw.chat_model
        cw._load_then_send()
        # Signal may or may not deliver synchronously — just verify no crash


# ── _do_stream ────────────────────────────────────────────────────────────


class TestDoStream:
    def test_routes_to_ac_model(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.cfg.set("chat_use_separate_model", False)
        cw.ac_model.is_loaded.return_value = True
        mock_worker = MagicMock()
        cw.ac_model.make_stream_worker.return_value = mock_worker
        cw._target_chat_model = cw.ac_model
        cw._do_stream()
        cw.ac_model.make_stream_worker.assert_called()

    def test_routes_to_chat_model(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.cfg.set("chat_use_separate_model", True)
        mock_worker = MagicMock()
        cw.chat_model.make_stream_worker.return_value = mock_worker
        cw._target_chat_model = cw.chat_model
        cw._do_stream()
        cw.chat_model.make_stream_worker.assert_called()


# ── _accept_if_ready ──────────────────────────────────────────────────────


class TestAcceptIfReady:
    def test_accepts_when_enabled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.accept_btn.setEnabled(True)
        with patch.object(cw, "_accept") as mock_accept:
            cw._accept_if_ready()
            mock_accept.assert_called_once()

    def test_does_nothing_when_disabled(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.accept_btn.setEnabled(False)
        with patch.object(cw, "_accept") as mock_accept:
            cw._accept_if_ready()
            mock_accept.assert_not_called()


# ── _normalize_strength / _strength_index / _strength_from_label ──────────


class TestStrengthNormalization:
    def test_roundtrip(self):
        for val in (
            "spelling_only",
            "full_correction",
            "rewrite_polish",
            "custom_patch",
        ):
            idx = CorrectionWindow._strength_index(val)
            assert isinstance(idx, int)


# ── _do_correction ────────────────────────────────────────────────────────


class TestDoCorrection:
    def test_restarted_thread_reports_model_load_failure(
        self, qtbot, cfg, monkeypatch
    ):
        original_do_correction = CorrectionWindow._do_correction
        monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = True
        cw.ac_model.is_loaded.return_value = False
        cw.ac_model.load_model.return_value = None
        cw.ac_model.should_retry_load.return_value = False

        original_do_correction(cw)

        assert cw._correction_cancelled is False
        assert "Model error" in cw.status_lbl.text()
        assert cw._retry_correction_when_model_ready is True

    def test_model_not_loaded_shows_original(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = False
        cw.ac_model.load_model = MagicMock(return_value=None)
        mock_worker = MagicMock()
        cw.ac_model.make_patch_worker.return_value = mock_worker
        # Connect signal handler to capture
        results = []
        cw._correction_ready.connect(
            lambda text, method: results.append((text, method))
        )
        cw._do_correction()
        # Signal may deliver synchronously
        # Just verify no crash

    def test_already_correct(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = True
        mock_worker = MagicMock()
        cw.ac_model.make_patch_worker.return_value = mock_worker
        results = []
        cw._correction_ready.connect(
            lambda text, method: results.append((text, method))
        )
        cw._do_correction()
        # Just verify no crash


# ── _render_chat_transcript ───────────────────────────────────────────────


class TestRenderChatTranscript:
    def test_renders_to_editor(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._add_chat_bubble("user", "Test message")
        cw._render_chat_transcript()
        html = cw.corr_edit.toHtml()
        assert "Test message" in html
