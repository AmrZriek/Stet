# -*- coding: utf-8 -*-
"""Tests for the DocumentValidator chunked-retry fallback (§3d).

Per-paragraph sub-validation with chunked-retry fallback: identify which
paragraphs failed to appear in the output, so the engine can retry just those
chunks rather than rejecting the whole document.
"""

from stet.llm.validator import (
    DocumentValidator,
)


def test_retry_plan_identifies_missing_paragraphs():
    dv = DocumentValidator()
    plan = dv.build_retry_plan(
        ["para one here", "para two here", "para three here"],
        "para one here. para three here.",
    )
    # "para two here" is missing from the output.
    assert plan.missing_paragraph_indices == (1,)
    assert plan.retryable


def test_retry_plan_empty_when_output_complete():
    dv = DocumentValidator()
    plan = dv.build_retry_plan(["alpha", "beta"], "alpha beta")
    assert plan.missing_paragraph_indices == ()
    assert not plan.retryable


def test_retry_plan_handles_whitespace_normalization():
    dv = DocumentValidator()
    plan = dv.build_retry_plan(["some   spaced text"], "some spaced text")
    assert plan.missing_paragraph_indices == ()


def test_chunked_retry_validates_recovered_chunks():
    dv = DocumentValidator()
    # First pass: para 1 missing. After a retry recovers it, validation passes.
    first = dv.validate(["p1", "p2"], "p1")
    assert not first.is_valid
    recovered = "p1 p2"
    second = dv.validate(["p1", "p2"], recovered)
    assert second.is_valid