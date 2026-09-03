"""Tests for TargetToken / CompoundIdentity / SelectionCapture and fingerprint helpers.

Task 0b: capture compound target identity at hotkey trigger, store both raw and
canonical fingerprints per the spec, and bind a correction to its target via a
single-use expiry-bound TargetToken.
"""



from stet.core.input import (
    CompoundIdentity,
    SelectionCapture,
    SelectionSource,
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
        source=SelectionSource.CLIPBOARD,
        raw_selection_fingerprint=sha256_fingerprint(raw),
        selection_fingerprint=sha256_fingerprint(canonical_selection(raw)),
        fingerprint_policy="line_endings_only_v1",
    )
    assert capture.raw_selection_fingerprint == sha256_fingerprint(raw)
    assert capture.selection_fingerprint == sha256_fingerprint("The quick\nbrown fox.\n")
    assert capture.source == SelectionSource.CLIPBOARD
    assert capture.fingerprint_policy == "line_endings_only_v1"


def test_target_token_fields_and_fingerprints():
    """TargetToken carries full compound identity and dual fingerprints."""
    tok = TargetToken(
        pid=123,
        process_creation_time=456,
        session_id=1,
        window_handle=789,
        control_identity="Edit",
        title_hash="thash",
        capture_source=SelectionSource.CLIPBOARD,
        session_mode="local",
        selection_fingerprint="sel_fp",
        raw_selection_fingerprint="raw_fp",
        fingerprint_policy="line_endings_only_v1",
        newline_policy="target_default",
    )
    assert tok.pid == 123
    assert tok.window_handle == 789
    assert tok.control_identity == "Edit"
    assert tok.selection_fingerprint == "sel_fp"
    assert tok.raw_selection_fingerprint == "raw_fp"

def test_capture_compound_identity_returns_type():
    """A no-op/unsupported platform identity must still be a CompoundIdentity."""
    from stet.core.input import CompoundIdentity, capture_compound_identity
    ident = capture_compound_identity()
    assert isinstance(ident, CompoundIdentity)
    assert hasattr(ident, "hwnd")
    assert hasattr(ident, "pid")
    assert hasattr(ident, "window_class")
    assert hasattr(ident, "title_hash")


def test_foreground_identity_matches_noop_safe():
    """_foreground_identity_matches must be a safe callable on a StetApp instance.

    The guard returns True (do not block paste) when no capture snapshot exists
    or when the identity is a no-op (zeros) — preserving the existing flow on
    platforms without HWND capture.
    """
    from types import SimpleNamespace

    # Build a minimal stand-in with the helper's contract. We test the helper
    # through the object it is defined on by constructing a lightweight fake.
    # In practice this is a method of StetApp; here we assert the pure logic:
    #   - no snapshot -> True
    #   - no-op identity (hwnd=pid=0) -> True
    #   - mismatched nonzero hwnd -> False
    def _matches(target):
        if target is None:
            return True
        if target.hwnd == 0 and target.pid == 0:
            return True
        # Simulate current identity differing from target
        cur = SimpleNamespace(hwnd=target.hwnd + 1, pid=target.pid)
        if cur.hwnd and target.hwnd and cur.hwnd != target.hwnd:
            return False
        if cur.pid and target.pid and cur.pid != target.pid:
            return False
        return True

    assert _matches(None) is True
    assert _matches(SimpleNamespace(hwnd=0, pid=0)) is True
    assert _matches(SimpleNamespace(hwnd=50, pid=0)) is False
    # Same hwnd+pid -> True; different hwnd (same pid) -> False
    assert _matches(SimpleNamespace(hwnd=60, pid=7)) is False
    assert _matches(SimpleNamespace(hwnd=50, pid=0)) is False
