# -*- coding: utf-8 -*-
"""Tests for the WindowSplitter (§3a sequential windowed pass).

Splits a document into windows with a 2-sentence overlap for context. Each
window owns a disjoint character-offset interval [start_anchor, end_anchor);
the overlap is context only. The reassembler discards output outside the
owning interval. Part of the sequential windowed pass regime.
"""

import pytest

from stet.llm.window_splitter import (
    SplitError,
    WindowSplitter,
    Window,
)


def test_document_splits_into_disjoint_owning_intervals():
    text = "S1. S2. S3. S4. S5. S6." * 5
    windows = WindowSplitter().split(text, max_sentences=2)
    # Owning intervals must be disjoint and contiguous.
    cursor = 0
    for w in windows:
        assert w.start_anchor == cursor
        assert w.end_anchor >= w.start_anchor
        cursor = w.end_anchor
    assert cursor == len(text)


def test_overlap_is_present_for_context():
    text = "S1. S2. S3. S4. S5. S6." * 5
    windows = WindowSplitter().split(text, max_sentences=2)
    assert len(windows) > 1
    # A window beyond the first grants overlap with the previous window.
    second = windows[1]
    assert second.context_start < second.start_anchor


def test_single_short_document_is_one_window():
    text = "Only one sentence here."
    windows = WindowSplitter().split(text, max_sentences=2)
    assert len(windows) == 1
    assert windows[0].text.strip() == text.strip()


def test_owning_text_derived_from_bounding_offsets():
    text = "First sentence. Second sentence here."
    w = WindowSplitter().split(text, max_sentences=2)[0]
    # The owning text is exactly the span [start_anchor, end_anchor).
    assert w.text == text[w.start_anchor : w.end_anchor]


def test_whole_document_fits_in_single_window():
    text = "A B C D" * 10
    windows = WindowSplitter().split(text, max_sentences=1000)
    assert len(windows) == 1


def test_empty_document_yields_no_windows():
    windows = WindowSplitter().split("", max_sentences=2)
    assert windows == []


def test_overlap_granularity_is_two_sentences():
    text = "S1. S2. S3. S4. S5. S6. S7. S8."
    windows = WindowSplitter().split(text, max_sentences=2, overlap_sentences=2)
    assert len(windows) >= 3
