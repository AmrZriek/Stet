"""Tests for Phase 1 Unified Contracts & IPC Wire Protocol (Phases 1a, 1b, 1c)."""

import hashlib
import struct
import pytest

from stet.core.input import (
    InputCode,
    PasteResult,
    SelectionCapture,
    SelectionSource,
    TargetToken,
    UndoToken,
    canonical_selection,
    sha256_fingerprint,
)
from stet.core.ipc_client import (
    MAX_FRAME_BYTES,
    FrameCodec,
    IpcError,
    compute_auth_proof,
    verify_auth_proof,
)
from stet.core.engine_types import (
    CorrectionRequest,
    CorrectionResult,
    TaskSpec,
    TaskType,
)


class TestPhase1aContracts:
    """Test unified contract types and fingerprint functions."""

    def test_sha256_fingerprint_deterministic(self):
        text = "The quick brown fox jumps over the lazy dog."
        fp1 = sha256_fingerprint(text)
        fp2 = sha256_fingerprint(text)
        assert fp1 == fp2
        assert len(fp1) == 64
        assert fp1 == hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_canonical_selection_line_endings_only(self):
        crlf = "Line 1\r\nLine 2\rLine 3\n"
        expected = "Line 1\nLine 2\nLine 3\n"
        assert canonical_selection(crlf) == expected
        # Must preserve whitespace, tabs, and content exactly
        ws = "   leading and trailing   \t\n"
        assert canonical_selection(ws) == "   leading and trailing   \t\n"

    def test_selection_capture_dual_fingerprints(self):
        text = "Hello\r\nWorld"
        capture = SelectionCapture(text=text, source=SelectionSource.UIA)
        assert capture.text == text
        assert capture.source == SelectionSource.UIA
        assert capture.raw_selection_fingerprint == sha256_fingerprint("Hello\r\nWorld")
        assert capture.selection_fingerprint == sha256_fingerprint("Hello\nWorld")

    def test_target_token_creation(self):
        token = TargetToken(
            pid=1234,
            process_creation_time=1000,
            session_id=1,
            window_handle=5678,
            control_identity="Edit",
            title_hash="hash123",
            capture_source=SelectionSource.ACCESSIBILITY,
            session_mode="local",
            selection_fingerprint="fp1",
            raw_selection_fingerprint="raw_fp1",
        )
        assert token.pid == 1234
        assert token.window_handle == 5678
        assert token.control_identity == "Edit"

    def test_undo_token_creation(self):
        token = TargetToken(
            pid=1234,
            process_creation_time=1000,
            session_id=1,
            window_handle=5678,
            control_identity="Edit",
            title_hash="hash123",
            capture_source=SelectionSource.ACCESSIBILITY,
            session_mode="local",
            selection_fingerprint="fp1",
            raw_selection_fingerprint="raw_fp1",
        )
        undo = UndoToken(target_token=token, replacement_fingerprint="rep_fp")
        assert undo.target_token == token
        assert undo.replacement_fingerprint == "rep_fp"

    def test_paste_result_ok_property(self):
        ok_res = PasteResult(code=InputCode.OK, status="pasted", transaction_id="tx1")
        assert ok_res.ok is True

        unverified_res = PasteResult(code=InputCode.OK, status="unverified", transaction_id="tx2")
        assert unverified_res.ok is False

        aborted_res = PasteResult(code=InputCode.ABORTED_WRONG_TARGET, status="aborted")
        assert aborted_res.ok is False


class TestPhase1bIpcProtocol:
    """Test frame encoding, decoding, length caps, and HMAC handshake."""

    def test_frame_encode_decode_roundtrip(self):
        msg = {"jsonrpc": "2.0", "id": 42, "method": "command.paste_text", "params": {"text": "hello"}}
        encoded = FrameCodec.encode(msg)
        assert len(encoded) > 8
        prefix = struct.unpack(">Q", encoded[:8])[0]
        assert prefix == len(encoded) - 8

        buf = bytearray(encoded)
        frames, consumed = FrameCodec.decode(buf)
        assert len(frames) == 1
        assert consumed == len(encoded)
        assert frames[0] == msg

    def test_frame_codec_handles_partial_frames(self):
        msg = {"jsonrpc": "2.0", "method": "event.hotkey_triggered", "params": {"action": "correct"}}
        encoded = FrameCodec.encode(msg)

        # Feed first 5 bytes (less than 8-byte prefix)
        buf = bytearray(encoded[:5])
        frames, consumed = FrameCodec.decode(buf)
        assert len(frames) == 0
        assert consumed == 0

        # Feed remaining bytes
        buf.extend(encoded[5:])
        frames, consumed = FrameCodec.decode(buf)
        assert len(frames) == 1
        assert consumed == len(encoded)
        assert frames[0] == msg

    def test_frame_codec_rejects_oversized_payload(self):
        huge_text = "x" * (MAX_FRAME_BYTES + 10)
        msg = {"data": huge_text}
        with pytest.raises(IpcError) as exc:
            FrameCodec.encode(msg)
        assert "frame_too_large" in str(exc.value)

    def test_hmac_auth_proof_verification(self):
        secret = b"0123456789abcdef0123456789abcdef"
        proto_ver = 2
        min_core = "1.5.0"
        pid = 9999
        nonce = "testnonce64bytes"

        proof = compute_auth_proof(secret, proto_ver, min_core, pid, nonce)
        assert proof
        assert verify_auth_proof(secret, proto_ver, min_core, pid, nonce, proof) is True

        # Invalid secret or altered field must fail
        wrong_secret = b"wrong_secret_32_bytes_long_xxxx"
        assert verify_auth_proof(wrong_secret, proto_ver, min_core, pid, nonce, proof) is False
        assert verify_auth_proof(secret, proto_ver, min_core, pid + 1, nonce, proof) is False

    def test_ipc_client_defaults_and_disconnected_methods(self):
        from stet.core.ipc_client import IpcClient
        client = IpcClient()
        assert client.secret is not None
        assert len(client.secret) >= 16
        assert client.is_connected() is False
        assert client.capture_selection() is None
        assert client.paste_text("test") is None
        client.close()
        assert client.is_connected() is False


class TestPhase1cEngineProtocol:
    """Test TaskSpec and CorrectionRequest/Result contracts."""

    def test_task_spec_defaults(self):
        spec = TaskSpec()
        assert spec.task_type == TaskType.CORRECT
        assert spec.budget_policy.context_size == 12800
        assert spec.guard_set.preserve_code_blocks is True

    def test_correction_request_and_result(self):
        spec = TaskSpec(task_type=TaskType.REWRITE, user_instruction="Polish style")
        req = CorrectionRequest(text="Initial draft text.", task_spec=spec, request_id="req123")
        assert req.text == "Initial draft text."
        assert req.task_spec.task_type == TaskType.REWRITE

        res = CorrectionResult(
            text="Polished draft text.",
            changed=True,
            status="success",
            finish_reason="stop",
            token_usage={"prompt_tokens": 50, "completion_tokens": 10},
        )
        assert res.ok is True
        assert res.changed is True

        trunc_res = CorrectionResult(
            text="Incomplete...",
            changed=True,
            status="generation_truncated",
            finish_reason="length",
        )
        assert trunc_res.ok is False
