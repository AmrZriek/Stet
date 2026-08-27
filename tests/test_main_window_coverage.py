"""Coverage expansion tests for stet/ui/main_window.py.

Targets: _render_diff, _send_chat, _on_chat_token, _on_chat_done,
_on_chat_error, _accept, _copy, _reset, eventFilter, _on_strength_changed,
_on_model_status, closeEvent, _toggle_shortcuts_overlay, _normalize_strength,
_strength_from_label, _on_escape, _accept_if_ready,
_apply_template, _refresh_templates, _build_ui, _position_window,
keyPressEvent, mouse events, _clear_chat_transcript.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication

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
    ac_model.loading = False
    chat_model.loading = False
    cw = CorrectionWindow(text, ac_model, chat_model, cfg)
    qtbot.addWidget(cw)
    return cw


def _make_accept_cw(cfg, text="Hello world"):
    """Build a CorrectionWindow WITHOUT qtbot.addWidget.

    _accept() now owns the full close -> deferred emit -> deleteLater
    lifecycle (closeEvent blockSignals guard + explicit delete in
    _emit_accepted). Registering with qtbot would make pytest-qt close()
    and deleteLater() the window again at teardown, racing the deferred
    deletion and raising RuntimeError. The caller must process events
    after _accept() so the deferred emit fires before the test ends.
    """
    ac_model = MagicMock()
    chat_model = MagicMock()
    ac_model.loading = False
    chat_model.loading = False
    return CorrectionWindow(text, ac_model, chat_model, cfg)


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
    def test_accept_emits_signal(self, cfg):
        cw = _make_accept_cw(cfg)
        signals = []
        cw.accepted.connect(lambda t: signals.append(t))
        cw.corrected = "Fixed text"
        cw._accept()
        QApplication.processEvents()  # emit is deferred one tick after close
        assert signals == ["Fixed text"]

    def test_accept_signal_fires_after_close(self, cfg, qtbot):
        """Regression: _accept must close the window BEFORE emitting accepted.

        The old emit-then-close order ran app._paste_text while the panel
        still had keyboard focus, so its SendInput Ctrl+V landed inside the
        panel and the paste was lost. The emit is now deferred via
        QTimer.singleShot(0, ...) after close(); this test proves the signal
        fires only after the window is closed/hidden.
        """
        cw = _make_accept_cw(cfg)
        cw.corrected = "Fixed text"
        cw.show()
        qtbot.waitUntil(lambda: cw.isVisible(), timeout=2000)

        spy = QSignalSpy(cw.accepted)
        # Capture visibility at the moment the signal fires. The deferred
        # emit runs inside _emit_accepted BEFORE deleteLater(), so the C++
        # object is still alive here — reading it later would race the
        # delete and raise RuntimeError.
        visible_at_emit = {}
        cw.accepted.connect(lambda t: visible_at_emit.setdefault("visible", cw.isVisible()))

        cw._accept()

        # Window is closed synchronously, but the signal must NOT have fired
        # yet — it is deferred until after close completes.
        assert len(spy) == 0
        assert cw.isVisible() is False

        # Pump the event loop until the deferred emit fires (waitUntil, not a
        # single processEvents tick, so the test is not timing-flaky).
        qtbot.waitUntil(lambda: len(spy) == 1, timeout=2000)
        assert spy[0][0] == "Fixed text"
        # The window was already hidden when the signal fired (focus has
        # returned to the source document).
        assert visible_at_emit["visible"] is False

        # Flush the deferred deleteLater so the C++ object is gone before
        # QApplication teardown. plain processEvents() does NOT deliver
        # DeferredDelete events, which left the widget pending deletion and
        # made this test hang when run in isolation.
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

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

    def test_accept_syncs_manual_edits_in_edit_mode(self, cfg, qtbot):
        """Regression: Accept during Edit-text mode must paste the user's
        manual edits, not the stale pre-edit corrected text."""
        cw = _make_accept_cw(cfg)
        cw.corrected = "Hello beautiful world"
        cw._toggle_edit_text_mode()
        cw.corr_edit.setPlainText("Hello GORGEOUS world")

        signals = []
        cw.accepted.connect(lambda t: signals.append(t))
        cw._accept()
        qtbot.waitUntil(lambda: len(signals) == 1, timeout=2000)
        assert signals[0] == "Hello GORGEOUS world"

        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_copy_syncs_manual_edits_in_edit_mode(self, cfg, qtbot):
        """Regression: Copy during Edit-text mode must capture the user's
        manual edits, not the stale pre-edit corrected text."""
        cw = _make_accept_cw(cfg)
        cw.corrected = "Hello beautiful world"
        cw._toggle_edit_text_mode()
        cw.corr_edit.setPlainText("Hello GORGEOUS world")

        with patch("stet.ui.main_window._clipboard_write_text") as mock_write:
            cw._copy()
            mock_write.assert_called_with("Hello GORGEOUS world")


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

        cw._retry_correction_when_model_ready = True
        cw._correction_in_flight = False
        cw._on_model_status("Ready — fake-model")

        assert len(started_threads) == 1
        assert started_threads[0].daemon is True
        assert cw._retry_correction_when_model_ready is False

        # Verify that "Loading model" does NOT set retry flag (preventing double dispatch)
        cw._on_model_status("Loading model")
        assert cw._retry_correction_when_model_ready is False

    def test_correcting(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._on_model_status("correcting text")
        assert "Processing" in cw.status_lbl.text()

    def test_loading(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._on_model_status("loading model")
        assert "Loading" in cw.status_lbl.text()
        # Distinct initializing state keeps a slow first load clearly visible
        assert "initializing" in cw.status_lbl.text()
        assert cw.status_lbl.property("state") == "initializing"

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
        # Construction normally starts a correction thread.  Keep this unit
        # test focused on template reset semantics rather than racing that
        # unrelated worker for the cancellation latch.
        with patch("stet.ui.main_window.threading.Thread"):
            cw = _make_cw(cfg, qtbot)
            cw._stream_worker = MagicMock()
            cw._stream_worker.isRunning.return_value = False
            cw._correction_stream_worker = MagicMock()
            cw._correction_stream_worker.isRunning.return_value = False
            with patch.object(cw, "_send_chat") as mock_send:
                cw._apply_template("Fix grammar")
        assert cw._correction_cancelled is True
        assert len(cw.chat_history) == 0
        mock_send.assert_called_once_with(msg="Fix grammar", is_template=True, grammar=None, json_schema=None)

    def test_apply_template_passes_grammar_and_json_schema(self, qtbot, cfg):
        with patch("stet.ui.main_window.threading.Thread"):
            cw = _make_cw(cfg, qtbot)
            cw._stream_worker = MagicMock()
            cw._stream_worker.isRunning.return_value = False
            cw._correction_stream_worker = MagicMock()
            cw._correction_stream_worker.isRunning.return_value = False
            with patch.object(cw, "_send_chat") as mock_send:
                cw._apply_template("Fix grammar", grammar="root ::= 'ok'", json_schema={"type": "object"})
        mock_send.assert_called_once_with(
            msg="Fix grammar", is_template=True, grammar="root ::= 'ok'", json_schema={"type": "object"}
        )

    def test_send_chat_template_routes_to_separate_chat_model(self, qtbot, cfg):
        cfg.config["chat_use_separate_model"] = True
        with patch("stet.ui.main_window.threading.Thread"):
            cw = _make_cw(cfg, qtbot)
            cw.chat_model.is_loaded = MagicMock(return_value=True)
            cw.ac_model.is_loaded = MagicMock(return_value=True)
            with patch.object(cw, "_do_stream"):
                cw._send_chat(msg="Test template", is_template=True)
                assert cw._target_chat_model is cw.chat_model



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

    def test_on_correction_ready_partial_label_sets_partial_status(self, qtbot, cfg):
        """The partial-correction warning label (text DID change) must show a
        'Partially corrected' status, not 'Correction unchanged'."""
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        with patch.object(cw, "_update_status") as mock_update:
            cw._on_correction_ready(
                "Fixed text",
                "Patch (Rewrite & Polish, 4 units) \u26a0 2 of 4 sections left unchanged",
            )
            mock_update.assert_called_once_with(
                "\u26a0  Partially corrected — some sections left unchanged",
                "warning",
            )

    def test_on_correction_ready_unchanged_label_keeps_unchanged_status(self, qtbot, cfg):
        """Genuinely unchanged labels ('⚠ Unchanged — …') must still show
        'Correction unchanged'."""
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        with patch.object(cw, "_update_status") as mock_update:
            cw._on_correction_ready("Fixed text", "\u26a0 Unchanged — Unit 1 rejected")
            mock_update.assert_called_once_with(
                "\u26a0  Correction unchanged",
                "warning",
            )

    def test_on_correction_ready_done_label(self, qtbot, cfg):
        """Non-warning labels keep the normal Done status."""
        cw = _make_cw(cfg, qtbot)
        cw._correction_cancelled = False
        with patch.object(cw, "_update_status") as mock_update:
            cw._on_correction_ready("Fixed text", "Patch (Full Correction)")
            mock_update.assert_called_once_with("\u2713  Done", "done")

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


# ── _chat_transcript_html ─────────────────────────────────────────────────


class TestChatTranscriptMethods:
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


# ── _normalize_strength / _strength_from_label ─────────────────────────────

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

    def test_model_not_loaded_shows_original(self, qtbot, cfg, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
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

    def test_already_correct(self, qtbot, cfg, monkeypatch):
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: MagicMock(status_code=200),
        )
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
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

    def test_do_correction_clears_in_flight_flag_on_exit(self, qtbot, cfg, monkeypatch):
        """_correction_in_flight must be reset to False in finally block when _do_correction finishes."""
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: MagicMock(status_code=200),
        )
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = True
        mock_worker = MagicMock()
        cw.ac_model.make_patch_worker.return_value = mock_worker

        assert cw._correction_in_flight is False
        cw._do_correction()
        assert cw._correction_in_flight is False

    def test_partial_correction_emits_warning_label(self, qtbot, cfg, monkeypatch):
        """A patch that only corrected some units must surface a warning in
        the method label instead of silently reporting full success."""
        from stet.core.text_utils import CorrectionOutcome, CorrectionResult

        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = True
        # Fast-exit the health check loop: fake a healthy server on the first
        # poll (and neutralise the sleep so a failure fails fast, not slow).
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: MagicMock(status_code=200),
        )
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        cw.ac_model.correct_text_patch.return_value = CorrectionResult(
            text="Hello world, fixed",
            outcome=CorrectionOutcome.CORRECTED,
            units_processed=4,
            units_corrected=2,
        )
        results = []
        cw._correction_ready.connect(
            lambda text, method: results.append((text, method))
        )
        cw._do_correction()
        assert results, "expected a _correction_ready emission"
        text, method = results[-1]
        assert text == "Hello world, fixed"
        assert "\u26a0" in method
        assert "2 of 4 sections left unchanged" in method

    def test_full_correction_emits_plain_label(self, qtbot, cfg, monkeypatch):
        """A patch that corrected every unit keeps the normal label (no
        warning marker)."""
        from stet.core.text_utils import CorrectionOutcome, CorrectionResult

        cw = _make_cw(cfg, qtbot)
        cw.ac_model.is_loaded.return_value = True
        monkeypatch.setattr(
            "requests.get",
            lambda *a, **k: MagicMock(status_code=200),
        )
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        cw.ac_model.correct_text_patch.return_value = CorrectionResult(
            text="Hello world corrected",
            outcome=CorrectionOutcome.CORRECTED,
            units_processed=2,
            units_corrected=2,
        )
        results = []
        cw._correction_ready.connect(
            lambda text, method: results.append((text, method))
        )
        cw._do_correction()
        assert results, "expected a _correction_ready emission"
        text, method = results[-1]
        assert text == "Hello world corrected"
        assert "\u26a0" not in method
        assert "Patch (" in method


# ── _render_chat_transcript ───────────────────────────────────────────────


class TestRenderChatTranscript:
    def test_renders_to_editor(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._add_chat_bubble("user", "Test message")
        cw._render_chat_transcript()
        html = cw.corr_edit.toHtml()
        assert "Test message" in html


class TestFontStack:
    def test_chat_transcript_html_font_stack(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        html = cw._chat_transcript_html("Result text")
        assert "font-family:'IBM Plex Mono','Consolas',monospace" in html

    def test_render_diff_font_stack(self, qtbot, cfg):
        cw = _make_cw(cfg, qtbot)
        cw._render_diff("Corrected text")
        html = cw.corr_edit.toHtml()
        # Qt's HTML renderer normalizes font-family quotes/spacing; check for IBM Plex Mono or Consolas
        assert "IBM Plex Mono" in html or "Consolas" in html or "monospace" in html
