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



def test_send_button_enabled_state(qtbot, monkeypatch):
    from stet.ui.main_window import CorrectionWindow

    class WindowCfg:
        def get(self, key, default=None):
            values = {
                "streaming_strength": "full_correction",
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
    
    assert not win.send_btn.isEnabled()
    
    win.chat_input.setText("hello")
    assert win.send_btn.isEnabled()
    
    win.chat_input.setText("   ")
    assert not win.send_btn.isEnabled()
    
    win._send_chat(msg="fix this")
    assert not win.send_btn.isEnabled()
    
    win._stream_buf = "mock output"
    win._on_chat_done("mock output")
    
    assert win.send_btn.isEnabled()


class TestOpcodesSplitting:
    """CorrectionWindow._split_opcodes_by_nl splits opcodes on newlines to prevent layout scramble."""

    def test_split_opcodes_by_nl(self):
        nl = "\x00NL\x00"
        orig_words = ['too', nl, 'so', 'yes']
        corr_words = ['too.', nl, 'So', 'yes,']
        opcodes = [('replace', 0, 4, 0, 4)]

        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('replace', 0, 1, 0, 1),
            ('equal', 1, 2, 1, 2),
            ('replace', 2, 4, 2, 4)
        ]

    def test_split_opcodes_empty_line_both_sides(self):
        nl = "\x00NL\x00"
        orig_words = [nl, 'hello']
        corr_words = [nl, 'hello']
        opcodes = [('replace', 0, 2, 0, 2)]
        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('equal', 0, 1, 0, 1),
            ('equal', 1, 2, 1, 2)
        ]

    def test_split_opcodes_pure_delete(self):
        nl = "\x00NL\x00"
        orig_words = ['one', 'two', nl]
        corr_words = [nl]
        opcodes = [('replace', 0, 3, 0, 1)]
        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('delete', 0, 2, 0, 0),
            ('equal', 2, 3, 0, 1)
        ]

    def test_split_opcodes_pure_insert(self):
        nl = "\x00NL\x00"
        orig_words = [nl]
        corr_words = ['one', 'two', nl]
        opcodes = [('replace', 0, 1, 0, 3)]
        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('insert', 0, 0, 0, 2),
            ('equal', 0, 1, 2, 3)
        ]

    def test_split_opcodes_mismatched_line_counts_delete(self):
        nl = "\x00NL\x00"
        orig_words = ['one', nl, 'two', nl, 'three']
        corr_words = ['one', 'two', 'three']
        opcodes = [('replace', 0, 5, 0, 3)]
        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('replace', 0, 1, 0, 3),
            ('delete', 1, 2, 3, 3),
            ('delete', 2, 3, 3, 3),
            ('delete', 3, 4, 3, 3),
            ('delete', 4, 5, 3, 3)
        ]

    def test_split_opcodes_mismatched_line_counts_insert(self):
        nl = "\x00NL\x00"
        orig_words = ['one', 'two', 'three']
        corr_words = ['one', nl, 'two', nl, 'three']
        opcodes = [('replace', 0, 3, 0, 5)]
        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('replace', 0, 3, 0, 1),
            ('insert', 3, 3, 1, 2),
            ('insert', 3, 3, 2, 3),
            ('insert', 3, 3, 3, 4),
            ('insert', 3, 3, 4, 5)
        ]

    def test_split_opcodes_consecutive_replace(self):
        nl = "\x00NL\x00"
        orig_words = ['a', nl, 'b', nl]
        corr_words = ['x', nl, 'y', nl]
        opcodes = [('replace', 0, 2, 0, 2), ('replace', 2, 4, 2, 4)]
        result = CorrectionWindow._split_opcodes_by_nl(None, orig_words, corr_words, opcodes, nl)
        assert result == [
            ('replace', 0, 1, 0, 1),
            ('equal', 1, 2, 1, 2),
            ('replace', 2, 3, 2, 3),
            ('equal', 3, 4, 3, 4)
        ]
