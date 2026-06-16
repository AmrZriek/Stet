"""Tests for stet.ui.main_window — CorrectionWindow static helpers, strength
normalization, and UI-independent utility methods."""

import pytest

from stet.ui.main_window import CorrectionWindow


class TestStrengthNormalization:
    """CorrectionWindow._normalize_strength maps legacy values to new ones."""

    @pytest.mark.parametrize(
        "input_val, expected",
        [
            ("spelling_only", "spelling_only"),
            ("full_correction", "full_correction"),
            ("rewrite_polish", "rewrite_polish"),
            ("conservative", "spelling_only"),
            ("aggressive", "rewrite_polish"),
            ("smart_fix", "full_correction"),
            (None, "full_correction"),
            ("unknown", "unknown"),
        ],
    )
    def test_normalize(self, input_val, expected):
        assert CorrectionWindow._normalize_strength(input_val) == expected


class TestStrengthFromLabel:
    """CorrectionWindow._strength_from_label maps display labels to config values."""

    @pytest.mark.parametrize(
        "label, expected",
        [
            ("Spelling Only", "spelling_only"),
            ("Conservative Spelling", "spelling_only"),
            ("Full Correction", "full_correction"),
            ("Smart Fix", "full_correction"),
            ("Rewrite & Polish", "rewrite_polish"),
            ("Aggressive Rewrite", "rewrite_polish"),
        ],
    )
    def test_label_mapping(self, label, expected):
        assert CorrectionWindow._strength_from_label(label) == expected


class TestStrengthIndex:
    """CorrectionWindow._strength_index maps strength to combo box index."""

    @pytest.mark.parametrize(
        "strength, index",
        [
            ("spelling_only", 0),
            ("conservative", 0),
            ("full_correction", 1),
            ("smart_fix", 1),
            ("rewrite_polish", 2),
            ("aggressive", 2),
        ],
    )
    def test_index(self, strength, index):
        assert CorrectionWindow._strength_index(strength) == index


def test_send_button_enabled_state(qtbot, monkeypatch):
    from stet.ui.main_window import CorrectionWindow

    class WindowCfg:
        def get(self, key, default=None):
            values = {
                "streaming_strength": "smart_fix",
                "correction_modes": [],
                "chat_mode": "conversation"
            }
            return values.get(key, default)
            
    class MockModel:
        def __init__(self):
            from PyQt6.QtCore import pyqtSignal, QObject
            class Signals(QObject):
                status_changed = pyqtSignal(str)
            self.signals = Signals()
            self.status_changed = self.signals.status_changed
            self.label = "Mock"
            
        def is_loaded(self):
            return True
        def make_stream_worker(self, *args, **kwargs):
            from PyQt6.QtCore import QThread, pyqtSignal
            class DummyWorker(QThread):
                token = pyqtSignal(str)
                done = pyqtSignal(str)
                error = pyqtSignal(str)
                def run(self): pass
            return DummyWorker()
        def mark_used(self):
            pass

    ac_model = MockModel()
    chat_model = MockModel()
    
    monkeypatch.setattr(CorrectionWindow, "_do_correction", lambda self: None)
    monkeypatch.setattr(CorrectionWindow, "_do_stream", lambda self: None)
    
    win = CorrectionWindow(
        original="Test content",
        ac_model=ac_model,
        chat_model=chat_model,
        cfg=WindowCfg()
    )
    qtbot.addWidget(win)
    
    assert win.send_btn.isEnabled() == False
    
    win.chat_input.setText("hello")
    assert win.send_btn.isEnabled() == True
    
    win.chat_input.setText("   ")
    assert win.send_btn.isEnabled() == False
    
    win._send_chat(msg="fix this")
    assert win.send_btn.isEnabled() == False
    
    win._stream_buf = "mock output"
    win._on_chat_done("mock output")
    
    assert win.send_btn.isEnabled() == True
