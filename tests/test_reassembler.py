# -*- coding: utf-8 -*-
"""Tests for the Reassembler (§3d): windowed offset splicing.

Each window owns a disjoint character-offset interval [start_anchor, end_anchor).
Overlap is context only; the reassembler discards output outside the owning
interval. It rejects missing/duplicated seams and GENERATION_TRUNCATED, and
computes changed strictly after post-fixes.
"""

import pytest

from stet.llm.reassembler import (
    ReassemblyError,
    Reassembler,
    WindowResult,
    compute_changed,
)


def test_single_window_is_passthrough():
    inputs = [WindowResult(start=0, end=10, text="abcdefghij", truncated=False)]
    r = Reassembler(inputs, original="abcdegfhij").run()
    assert r.text == "abcdefghij"
    assert r.changed is True


def test_two_disjoint_windows_splice_by_offset():
    inputs = [
        WindowResult(start=0, end=5, text="01234", truncated=False),
        WindowResult(start=5, end=10, text="56789", truncated=False),
    ]
    r = Reassembler(inputs).run()
    assert r.text == "0123456789"


def test_overlap_is_discarded_from_owning_interval():
    inputs = [
        WindowResult(start=0, end=5, text="01234", truncated=False),
        WindowResult(start=5, end=10, text="56789xxx", truncated=False),
    ]
    r = Reassembler(inputs).run()
    assert r.text == "0123456789"


def test_missing_seam_is_rejected():
    inputs = [
        WindowResult(start=0, end=5, text="01234", truncated=False),
        WindowResult(start=10, end=15, text="abcde", truncated=False),
    ]
    with pytest.raises(ReassemblyError):
        Reassembler(inputs).run()


def test_duplicate_seam_is_rejected():
    inputs = [
        WindowResult(start=0, end=5, text="01234", truncated=False),
        WindowResult(start=5, end=10, text="56789", truncated=False),
        WindowResult(start=5, end=10, text="56789xxx", truncated=False),
    ]
    with pytest.raises(ReassemblyError):
        Reassembler(inputs).run()


def test_generation_truncated_is_rejected():
    inputs = [
        WindowResult(start=0, end=5, text="01234", truncated=False),
        WindowResult(start=5, end=10, text="5678", truncated=True),
    ]
    with pytest.raises(ReassemblyError):
        Reassembler(inputs).run()


def test_changed_is_strictly_after_postfix():
    assert compute_changed("hello", "hello") is False
    assert compute_changed("hello", "helo") is True


def test_window_out_of_bounds_is_rejected():
    inputs = [WindowResult(start=5, end=3, text="on", truncated=False)]
    with pytest.raises(ReassemblyError):
        Reassembler(inputs).run()