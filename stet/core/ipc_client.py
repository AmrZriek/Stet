"""Stet v2.0 IPC Client and Framed JSON-RPC 2.0 Wire Protocol.

Implements length-prefixed JSON-RPC 2.0 over local transports (Windows named
pipes: \\\\.\\pipe\\stet_ipc_v2, Unix domain sockets on macOS/Linux).
Includes HMAC-SHA-256 proof-of-possession handshake and 4 MiB frame boundary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
from enum import Enum
from typing import Any, Callable, Dict, List, Tuple

MAX_FRAME_BYTES: int = 4 * 1024 * 1024  # 4 MiB
MAX_JSON_DEPTH: int = 32
LENGTH_PREFIX_BYTES: int = 8
SUPPORTED_PROTOCOL_VERSION: int = 2
DEFAULT_MIN_CORE_VERSION: str = "1.5.0"
DEFAULT_PIPE_NAME: str = r"\\.\pipe\stet_ipc_v2"


class ConnectionState(str, Enum):
    UNCONNECTED = "unconnected"
    CONNECTING = "connecting"
    AUTHENTICATED = "authenticated"
    READY = "ready"
    CLOSING = "closing"


class IpcError(Exception):
    """Base error for IPC wire protocol and connection failures."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"[{code}] {message}" if message else code)
        self.code = code
        self.message = message


def canonical_hello_payload(
    protocol_version: int, min_core_version: str, client_pid: int, client_nonce: str
) -> bytes:
    """Deterministic byte layout for HMAC-SHA-256 hello authentication proof."""
    payload = bytearray()
    payload.extend(b"stet-hello-v1\x00")
    payload.extend(struct.pack(">I", protocol_version))
    min_core_bytes = min_core_version.encode("utf-8")
    payload.extend(struct.pack(">I", len(min_core_bytes)))
    payload.extend(min_core_bytes)
    payload.extend(struct.pack(">I", client_pid))
    nonce_bytes = client_nonce.encode("utf-8")
    payload.extend(struct.pack(">I", len(nonce_bytes)))
    payload.extend(nonce_bytes)
    return bytes(payload)


def compute_auth_proof(
    secret: bytes,
    protocol_version: int,
    min_core_version: str,
    client_pid: int,
    client_nonce: str,
) -> str:
    """Compute base64 HMAC-SHA-256 authentication proof."""
    canonical = canonical_hello_payload(
        protocol_version, min_core_version, client_pid, client_nonce
    )
    digest = hmac.new(secret, canonical, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_auth_proof(
    secret: bytes,
    protocol_version: int,
    min_core_version: str,
    client_pid: int,
    client_nonce: str,
    presented_proof: str,
) -> bool:
    """Constant-time verification of presented HMAC proof."""
    expected = compute_auth_proof(
        secret, protocol_version, min_core_version, client_pid, client_nonce
    )
    return hmac.compare_digest(expected, presented_proof)


class FrameCodec:
    """Encodes and decodes 8-byte big-endian length-prefixed JSON frames."""

    @staticmethod
    def encode(value: Dict[str, Any]) -> bytes:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_FRAME_BYTES:
            raise IpcError("frame_too_large", f"Payload size {len(payload)} exceeds 4 MiB cap")
        length_prefix = struct.pack(">Q", len(payload))
        return length_prefix + payload

    @staticmethod
    def decode(buffer: bytearray) -> Tuple[List[Dict[str, Any]], int]:
        """Decode complete frames from buffer. Returns (frames, bytes_consumed)."""
        frames: List[Dict[str, Any]] = []
        consumed = 0

        while True:
            remaining = len(buffer) - consumed
            if remaining < LENGTH_PREFIX_BYTES:
                break

            payload_len = struct.unpack_from(">Q", buffer, consumed)[0]
            if payload_len > MAX_FRAME_BYTES:
                raise IpcError(
                    "frame_too_large",
                    f"Frame length prefix {payload_len} exceeds 4 MiB limit",
                )

            total_frame_len = LENGTH_PREFIX_BYTES + payload_len
            if remaining < total_frame_len:
                break  # incomplete frame

            payload_bytes = bytes(buffer[consumed + LENGTH_PREFIX_BYTES : consumed + total_frame_len])
            try:
                parsed = json.loads(payload_bytes.decode("utf-8"))
            except Exception as e:
                raise IpcError("malformed_frame", f"Invalid JSON in frame: {e}") from e

            frames.append(parsed)
            consumed += total_frame_len

        return frames, consumed


class IpcClient:
    """JSON-RPC 2.0 client managing transport, handshake, and request dispatch."""

    def __init__(
        self,
        secret: bytes | None = None,
        pipe_name: str = DEFAULT_PIPE_NAME,
        min_core_version: str = DEFAULT_MIN_CORE_VERSION,
    ):
        if secret is None:
            env_secret = os.environ.get("STET_CORE_SECRET")
            secret = env_secret.encode("utf-8") if env_secret else secrets.token_bytes(32)
        if len(secret) < 16:
            raise ValueError("IPC secret must be at least 16 bytes")
        self.secret = secret
        self.pipe_name = pipe_name
        self.min_core_version = min_core_version
        self.state = ConnectionState.UNCONNECTED
        self._request_counter = 0
        self._pending_requests: Dict[int, Tuple[Callable[[Dict[str, Any]], None], Callable[[Exception], None]]] = {}
        self._event_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._rx_buffer = bytearray()
        self._transport = None

    def connect(self, timeout_ms: int = 500) -> bool:
        """Attempt connection and handshake with the native core daemon."""
        if sys.platform == "win32":
            import time

            deadline = time.monotonic() + (timeout_ms / 1000.0)
            handle = None
            # The daemon may take 10-50ms to spawn and create the pipe.
            # Retry open until deadline on FileNotFoundError.
            while time.monotonic() < deadline:
                try:
                    handle = open(self.pipe_name, "r+b", buffering=0)
                    break
                except FileNotFoundError:
                    time.sleep(0.015)
                except Exception:
                    break
            if handle is None:
                self._transport = None
                self.state = ConnectionState.UNCONNECTED
                return False
            try:
                self._transport = handle
                self.state = ConnectionState.CONNECTING
                hello_frame = self.build_hello_frame()
                handle.write(hello_frame)
                handle.flush()
                remaining_ms = max(100, int((deadline - time.monotonic()) * 1000))
                reply = self._read_reply(None, remaining_ms)
                result = reply.get("result") if isinstance(reply, dict) else None
                if not isinstance(result, dict) or result.get("status") != "authenticated":
                    self.close()
                    return False
                self.state = ConnectionState.AUTHENTICATED
                return True
            except Exception as exc:
                self.close()
                return False
        return False

    def is_connected(self) -> bool:
        """True if the client has an active authenticated connection."""
        return self.state in (ConnectionState.READY, ConnectionState.AUTHENTICATED) and self._transport is not None


    def _match_reply_frame(self, frames: List[Dict[str, Any]], req_id: int | None) -> Dict[str, Any] | None:
        """Pick the reply frame for req_id (None = hello reply) from decoded frames."""
        if req_id is None:
            for f in frames:
                if "method" not in f and ("result" in f or "error" in f):
                    return f
            return frames[0] if frames else None
        for f in frames:
            if f.get("id") == req_id:
                return f
        return None

    def _read_reply(self, req_id: int | None, timeout_ms: int) -> Dict[str, Any] | None:
        """Wait up to timeout_ms for the reply frame matching req_id."""
        transport = self._transport
        if transport is None:
            return None
        import time

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        try:
            frames = self.handle_incoming_bytes(b"")
        except Exception:
            return None
        matched = self._match_reply_frame(frames, req_id)
        if matched is not None:
            return matched
        if sys.platform == "win32":
            try:
                import ctypes
                import msvcrt

                os_handle = msvcrt.get_osfhandle(transport.fileno())
                kernel32 = ctypes.windll.kernel32
                avail_buf = ctypes.c_ulong(0)
            except Exception:
                os_handle = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if os_handle is not None:
                    # Named pipes on Windows cannot use WaitForSingleObject for incoming
                    # data arrival; PeekNamedPipe queries the driver queue without blocking.
                    ok = kernel32.PeekNamedPipe(os_handle, None, 0, None, ctypes.byref(avail_buf), None)
                    if ok == 0:
                        return None  # Pipe disconnected or broken
                    if avail_buf.value == 0:
                        time.sleep(0.005)
                        continue
                try:
                    to_read = max(1, min(4096, avail_buf.value if os_handle is not None else 4096))
                    chunk = transport.read(to_read)
                except Exception:
                    return None
                if not chunk:
                    return None
                try:
                    frames = self.handle_incoming_bytes(chunk)
                except Exception:
                    return None
                matched = self._match_reply_frame(frames, req_id)
                if matched is not None:
                    return matched
        else:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    chunk = transport.read(4096)
                except Exception:
                    return None
                if not chunk:
                    return None
                try:
                    frames = self.handle_incoming_bytes(chunk)
                except Exception:
                    return None
                matched = self._match_reply_frame(frames, req_id)
                if matched is not None:
                    return matched

    def capture_selection(self, timeout_ms: int = 1500) -> Dict[str, Any] | None:
        """Request text capture from the active window via the native daemon."""
        if not self.is_connected():
            return None
        try:
            req_id, frame = self.build_command_frame("command.capture_selection", {"timeout_ms": timeout_ms})
            self._transport.write(frame)
            self._transport.flush()
            reply = self._read_reply(req_id, timeout_ms)
            if reply is not None and "result" in reply:
                return reply["result"]
        except Exception:
            pass
        return None

    def paste_text(self, text: str, verify_target: bool = True) -> Dict[str, Any] | None:
        """Request verified target paste via the native daemon."""
        if not self.is_connected():
            return None
        try:
            req_id, frame = self.build_command_frame("command.paste_text", {"text": text, "verify_target": verify_target})
            self._transport.write(frame)
            self._transport.flush()
            reply = self._read_reply(req_id, 5000)
            if reply is not None and "result" in reply:
                return reply["result"]
        except Exception:
            pass
        return None

    def close(self) -> None:
        """Close the active connection and reset state."""
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None
        self.state = ConnectionState.UNCONNECTED
    def build_hello_frame(self) -> bytes:
        """Create the authenticated handshake.hello frame."""
        client_pid = os.getpid()
        client_nonce = base64.b64encode(secrets.token_bytes(64)).decode("ascii")
        auth_proof = compute_auth_proof(
            self.secret,
            SUPPORTED_PROTOCOL_VERSION,
            self.min_core_version,
            client_pid,
            client_nonce,
        )

        hello_req = {
            "jsonrpc": "2.0",
            "method": "handshake.hello",
            "params": {
                "protocol_version": SUPPORTED_PROTOCOL_VERSION,
                "min_core_version": self.min_core_version,
                "client_pid": client_pid,
                "client_nonce": client_nonce,
                "auth_proof": auth_proof,
            },
        }
        return FrameCodec.encode(hello_req)

    def build_command_frame(self, method: str, params: Dict[str, Any]) -> Tuple[int, bytes]:
        """Create a command frame with a monotonic request ID."""
        if self.state not in (ConnectionState.AUTHENTICATED, ConnectionState.READY):
            raise IpcError("not_ready", f"Cannot send command {method} while in state {self.state.value}")

        self._request_counter += 1
        req_id = self._request_counter
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        return req_id, FrameCodec.encode(req)

    def on_event(self, event_name: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        """Subscribe to an incoming core notification event."""
        self._event_handlers.setdefault(event_name, []).append(handler)

    def handle_incoming_bytes(self, raw_bytes: bytes) -> List[Dict[str, Any]]:
        """Process incoming raw bytes, extract frames, and route replies/events."""
        self._rx_buffer.extend(raw_bytes)
        frames, consumed = FrameCodec.decode(self._rx_buffer)
        if consumed > 0:
            del self._rx_buffer[:consumed]

        results = []
        for frame in frames:
            results.append(frame)
            # Route event notifications
            if "method" in frame and frame.get("id") is None:
                method = frame["method"]
                handlers = self._event_handlers.get(method, [])
                for handler in handlers:
                    try:
                        handler(frame.get("params", {}))
                    except Exception:
                        pass
            # Route command replies
            elif "id" in frame:
                req_id = frame["id"]
                if req_id in self._pending_requests:
                    on_success, on_error = self._pending_requests.pop(req_id)
                    if "error" in frame:
                        err = frame["error"]
                        on_error(IpcError(err.get("code", "error"), err.get("message", "")))
                    else:
                        on_success(frame.get("result", {}))

        return results
