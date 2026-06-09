"""Tests for stet.ui.utils — _checkbox_css(), no_scroll(), _IgnoreWheelFilter."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSpinBox

from stet.ui.utils import _checkbox_css, no_scroll


class TestCheckboxCss:
    """_checkbox_css() returns valid QSS and writes SVG to temp."""

    def test_returns_non_empty_string(self):
        result = _checkbox_css()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_checkbox_selector(self):
        result = _checkbox_css()
        assert "QCheckBox" in result

    def test_contains_indicator_rules(self):
        result = _checkbox_css()
        assert "indicator" in result

    def test_contains_image_url(self):
        result = _checkbox_css()
        assert "url(" in result
        assert "stet_checkmark" in result


class TestNoScroll:
    """no_scroll() installs event filter on widgets."""

    def test_returns_same_widget(self, qtbot):
        w = QSpinBox()
        qtbot.addWidget(w)
        result = no_scroll(w)
        assert result is w

    def test_sets_strong_focus(self, qtbot):
        w = QSpinBox()
        qtbot.addWidget(w)
        no_scroll(w)
        assert w.focusPolicy() == Qt.FocusPolicy.StrongFocus
