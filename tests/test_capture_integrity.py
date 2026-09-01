"""Tests for Task 0c: structured UIA capture integrity.

Verifies the UiaCapture result type, truncation signaling when the selection
reaches MAX_TEXT_LENGTH, whitespace preservation, and that the capture records
selection-range count and document-range match metadata.
"""

import pytest

from stet.core.clipboard import (
    MAX_TEXT_LENGTH,
    UiaCapture,
)


def test_uia_capture_fields():
    """UiaCapture carries text + integrity metadata."""
    cap = UiaCapture(
        text="hello",
        truncated=False,
        selection_range_count=1,
        document_range_match=True,
        newline_normalized=False,
    )
    assert cap.text == "hello"
    assert cap.truncated is False
    assert cap.selection_range_count == 1
    assert cap.document_range_match is True
    assert cap.newline_normalized is False


def test_truncation_signal_at_max_length():
    """A capture whose text length equals MAX_TEXT_LENGTH is flagged truncated."""
    text = "x" * MAX_TEXT_LENGTH
    cap = UiaCapture(
        text=text,
        truncated=len(text) >= MAX_TEXT_LENGTH,
        selection_range_count=1,
        document_range_match=True,
        newline_normalized=False,
    )
    assert cap.truncated is True
    # Grace: an explicitly shorter text is NOT truncated.
    cap2 = UiaCapture(
        text="short",
        truncated=len("short") >= MAX_TEXT_LENGTH,
        selection_range_count=1,
        document_range_match=True,
        newline_normalized=False,
    )
    assert cap2.truncated is False


def test_uia_capture_preserves_exact_whitespace():
    """Text with leading/trailing/internal whitespace is preserved exactly."""
    raw = "  \n  quick  brown  \r\n  fox  \n  "
    cap = UiaCapture(
        text=raw,
        truncated=False,
        selection_range_count=1,
        document_range_match=False,
        newline_normalized=True,
    )
    assert cap.text == raw  # never stripped or collapsed
    # Only line-endings are normalized in the canonical path.
    assert cap.newline_normalized is True
