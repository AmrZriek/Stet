"""Tests for TargetToken / CompoundIdentity / SelectionCapture and fingerprint helpers.

Task 0b: capture compound target identity at hotkey trigger, store both raw and
canonical fingerprints per the spec, and bind a correction to its target via a
single-use expiry-bound TargetToken.
"""

import time

import pytest

from stet.core.input import (
    CompoundIdentity,
    SelectionCapture,
    TargetToken,
    canonical_selection,
    sha256_fingerprint,
)


def test_sha256_fingerprint_stable():
    """sha256_fingerprint is deterministic and hex."""
    a = sha256_fingerprint("hello")
    b = sha256_fingerprint("hello")
    assert a == b
    assert len(a) == 64
    assert a != sha256_fingerprint("hello world")


def test_canonical_selection_lineendings_only():
    """Canonicalization changes ONLY CRLF/CR -> LF; never strips/collapses."""
    raw = "hello\r\nworld\r\n"
    assert canonical_selection(raw) == "hello\nworld\n"
    assert canonical_selection("line1\rline2") == "line1\nline2"


def test_canonical_selection_preserves_whitespace():
    """Exact leading/trailing/internal whitespace is NOT stripped or collapsed."""
    text = "  a  b  \n\n  "
    assert canonical_selection(text) == text  # unchanged (only line-endings normalized)


def test_fingerprints_exact_and_canonical_differ():
    """raw (exact text) fingerprint must differ from canonical fingerprint for CRLF input."""
    raw = "hello\r\nworld\r\n"
    assert sha256_fingerprint(raw) != sha256_fingerprint(canonical_selection(raw))


def test_compound_identity_fields():
    """CompoundIdentity carries all captured identity fields."""
    ident = CompoundIdentity(
        hwnd=12345,
        pid=678,
        process_creation_time=1690000000,
        session_id=1,
        window_class="Chrome_WidgetWin_1",
        title_hash="abc123",
    )
    assert ident.hwnd == 12345
    assert ident.pid == 678
    assert ident.process_creation_time == 1690000000
    assert ident.session_id == 1
    assert ident.window_class == "Chrome_WidgetWin_1"
    assert ident.title_hash == "abc123"


def test_selection_capture_records_fingerprints_and_policies():
    """SelectionCapture stores both fingerprints + capture/policy metadata."""
    raw = "The quick\r\nbrown fox.\r\n"
    capture = SelectionCapture(
        text=raw,
        target=CompoundIdentity(hwnd=1, pid=2, process_creation_time=3, session_id=4, window_class="W", title_hash="h"),
        raw_selection_fingerprint=sha256_fingerprint(raw),
        selection_fingerprint=sha256_fingerprint(canonical_selection(raw)),
        capture_source="clipboard",
        fingerprint_policy="both",
        newline_policy="normalize",
        session_mode="panel",
    )
    assert capture.raw_selection_fingerprint == sha256_fingerprint(raw)
    assert capture.selection_fingerprint == sha256_fingerprint("The quick\nbrown fox.\n")
    assert capture.capture_source == "clipboard"
    assert capture.fingerprint_policy == "both"
    assert capture.newline_policy == "normalize"
    assert capture.session_mode == "panel"


def test_target_token_single_use_and_expiry():
    """TargetToken is single-use and expires at its deadline."""
    capture = SelectionCapture(
        text="x",
        target=CompoundIdentity(hwnd=1, pid=2, process_creation_time=3, session_id=4, window_class="W", title_hash="h"),
        raw_selection_fingerprint="raw",
        selection_fingerprint="sel",
        capture_source="clipboard",
        fingerprint_policy="both",
        newline_policy="preserve",
        session_mode="panel",
    )
    tok = TargetToken(capture=capture, replacement_fingerprint="repl", expires_at=time.monotonic() + 60)
    assert tok.version == "v1"
    assert tok.capture is capture
    assert tok.replacement_fingerprint == "repl"
    assert tok.expires_at > time.monotonic()
    # Single-use: first consume flags it; second is rejected.
    consumed = tok.consume()
    assert consumed is True
    assert tok.consume() is False  # already used


def test_target_token_expired():
    """A token past its deadline is not usable."""
    capture = SelectionCapture(
        text="x",
        target=CompoundIdentity(hwnd=1, pid=2, process_creation_time=3, session_id=4, window_class="W", title_hash="h"),
        raw_selection_fingerprint="raw",
        selection_fingerprint="sel",
        capture_source="clipboard",
        fingerprint_policy="both",
        newline_policy="preserve",
        session_mode="panel",
    )
    tok = TargetToken(capture=capture, replacement_fingerprint="repl", expires_at=time.monotonic() - 1)
    assert tok.is_expired()
    assert tok.consume() is False  # expired -> cannot consume
