"""Tests for the native Rust daemon launch + IPC handshake wiring.

Covers `stet/core/native_daemon.py` (bootstrap framing, binary lookup,
spawn discipline) and the `IpcClient` reply loop (`connect` hello
validation, `_read_reply` matching/timeout, capture/paste routing).
"""

import ctypes  # noqa: F401  (pre-import so patched-open tests never hit the FS)
import msvcrt  # noqa: F401
import os
import struct
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stet.core.ipc_client import ConnectionState, FrameCodec, IpcClient
from stet.core.native_daemon import (
    BOOTSTRAP_SECRET_LEN,
    encode_bootstrap,
    find_daemon_binary,
    launch_daemon,
)


# ── Helpers ────────────────────────────────────────────────────────────────


class StubTransport:
    """In-memory pipe stand-in: canned read chunks, captured writes."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.written = bytearray()
        self.closed = False

    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def read(self, n):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def fileno(self):
        raise OSError("stub transport has no OS handle")

    def close(self):
        self.closed = True


def _hello_reply_ok():
    return {"id": None, "result": {"code": "ok", "status": "authenticated"}}


def _open_fake_for(stub, monkeypatch):
    """Route only the named-pipe open() to the stub; everything else is real."""
    real_open = open

    def fake_open(path, *args, **kwargs):
        if isinstance(path, str) and "stet_ipc_v2" in path:
            return stub
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)


def _attach(client, stub):
    client._transport = stub
    client.state = ConnectionState.AUTHENTICATED
    return client


# ── Bootstrap framing ──────────────────────────────────────────────────────


class TestEncodeBootstrap:
    def test_45_byte_layout_vector(self):
        secret = bytes(range(32))
        pid = 1234
        blob = encode_bootstrap(secret, pid)
        assert len(blob) == 45
        assert blob[:4] == b"STET"
        assert blob[4] == 0x01
        assert struct.unpack("<I", blob[5:9])[0] == 32
        assert blob[9:41] == secret
        assert struct.unpack("<I", blob[41:45])[0] == pid

    def test_rejects_bad_secret_length(self):
        with pytest.raises(ValueError):
            encode_bootstrap(b"too-short-16-bytes!", 1)
        with pytest.raises(ValueError):
            encode_bootstrap(b"", 1)
        with pytest.raises(ValueError):
            encode_bootstrap(bytes(33), 1)

    def test_secret_length_constant(self):
        assert BOOTSTRAP_SECRET_LEN == 32


# ── Binary lookup ──────────────────────────────────────────────────────────


class TestFindDaemonBinary:
    def test_dev_build_wins_over_install_dir(self, tmp_path, monkeypatch):
        dev = tmp_path / "crates" / "target" / "debug"
        dev.mkdir(parents=True)
        dev_binary = dev / "stet-core.exe"
        dev_binary.write_bytes(b"x")
        installed = tmp_path / "stet-core.exe"
        installed.write_bytes(b"x")
        monkeypatch.setattr("stet.constants.SCRIPT_DIR", tmp_path)
        assert find_daemon_binary() == dev_binary

    def test_install_dir_fallback(self, tmp_path, monkeypatch):
        installed = tmp_path / "stet-core.exe"
        installed.write_bytes(b"x")
        monkeypatch.setattr("stet.constants.SCRIPT_DIR", tmp_path)
        assert find_daemon_binary() == installed

    def test_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("stet.constants.SCRIPT_DIR", tmp_path)
        assert find_daemon_binary() is None


# ── Spawn discipline ───────────────────────────────────────────────────────


def _mock_proc():
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.poll.return_value = None
    return proc


class TestLaunchDaemon:
    def test_none_when_binary_missing(self, monkeypatch):
        monkeypatch.setattr(
            "stet.core.native_daemon.find_daemon_binary", lambda: None
        )
        popen = MagicMock()
        monkeypatch.setattr("stet.core.native_daemon.subprocess.Popen", popen)
        assert launch_daemon() is None
        popen.assert_not_called()

    def test_success_writes_bootstrap_and_returns_secret(self, monkeypatch):
        binary = Path("C:/fake/stet-core.exe")
        monkeypatch.setattr(
            "stet.core.native_daemon.find_daemon_binary", lambda: binary
        )
        proc = _mock_proc()
        popen = MagicMock(return_value=proc)
        monkeypatch.setattr("stet.core.native_daemon.subprocess.Popen", popen)

        result = launch_daemon()
        assert result is not None
        got_proc, secret = result
        assert got_proc is proc
        assert len(secret) == 32

        # Bootstrap bytes delivered on stdin, framed for this pid.
        written = proc.stdin.write.call_args[0][0]
        assert written == encode_bootstrap(secret, os.getpid())
        proc.stdin.close.assert_called_once()

        # Secret never in argv/env: only the binary path is passed.
        argv, kwargs = popen.call_args[0][0], popen.call_args[1]
        assert argv == [str(binary)]
        env = kwargs.get("env") or {}
        assert secret not in str(argv).encode()
        for value in env.values():
            assert secret not in str(value).encode()
        assert kwargs["stdin"] is not None

    def test_spawn_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "stet.core.native_daemon.find_daemon_binary",
            lambda: Path("C:/fake/stet-core.exe"),
        )
        monkeypatch.setattr(
            "stet.core.native_daemon.subprocess.Popen",
            MagicMock(side_effect=OSError("noexec")),
        )
        assert launch_daemon() is None

    def test_early_exit_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "stet.core.native_daemon.find_daemon_binary",
            lambda: Path("C:/fake/stet-core.exe"),
        )
        proc = _mock_proc()
        proc.poll.return_value = 1  # died immediately
        monkeypatch.setattr(
            "stet.core.native_daemon.subprocess.Popen", MagicMock(return_value=proc)
        )
        assert launch_daemon() is None

    def test_stdin_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "stet.core.native_daemon.find_daemon_binary",
            lambda: Path("C:/fake/stet-core.exe"),
        )
        proc = _mock_proc()
        proc.stdin.write.side_effect = BrokenPipeError("daemon gone")
        monkeypatch.setattr(
            "stet.core.native_daemon.subprocess.Popen", MagicMock(return_value=proc)
        )
        assert launch_daemon() is None
        proc.terminate.assert_called_once()


# ── Hello validation (mocked at the _read_reply boundary) ──────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="named-pipe connect is win32-only")
class TestConnectHelloValidation:
    def test_accepts_authenticated(self, monkeypatch):
        _open_fake_for(StubTransport([]), monkeypatch)
        monkeypatch.setattr(
            IpcClient, "_read_reply", lambda self, req_id, ms: _hello_reply_ok()
        )
        client = IpcClient(secret=bytes(range(32)))
        assert client.connect() is True
        assert client.is_connected() is True

    def test_rejects_error_reply(self, monkeypatch):
        _open_fake_for(StubTransport([]), monkeypatch)
        monkeypatch.setattr(
            IpcClient,
            "_read_reply",
            lambda self, req_id, ms: {"id": None, "error": {"code": "auth_failed"}},
        )
        client = IpcClient(secret=bytes(range(32)))
        assert client.connect() is False
        assert client.is_connected() is False

    def test_rejects_timeout(self, monkeypatch):
        _open_fake_for(StubTransport([]), monkeypatch)
        monkeypatch.setattr(IpcClient, "_read_reply", lambda self, req_id, ms: None)
        client = IpcClient(secret=bytes(range(32)))
        assert client.connect() is False
        assert client.is_connected() is False

    def test_rejects_wrong_status(self, monkeypatch):
        _open_fake_for(StubTransport([]), monkeypatch)
        monkeypatch.setattr(
            IpcClient,
            "_read_reply",
            lambda self, req_id, ms: {"id": None, "result": {"status": "denied"}},
        )
        client = IpcClient(secret=bytes(range(32)))
        assert client.connect() is False


# ── _read_reply matching / timeout over a stub transport ───────────────────


class TestReadReply:
    def test_hello_reply_when_req_id_none(self):
        client = IpcClient(secret=bytes(range(32)))
        stub = StubTransport([FrameCodec.encode(_hello_reply_ok())])
        _attach(client, stub)
        assert client._read_reply(None, 500) == _hello_reply_ok()

    def test_matches_id_skipping_other_frames(self):
        client = IpcClient(secret=bytes(range(32)))
        wanted = {"id": 7, "result": {"text": "hi"}}
        other = {"id": 9, "result": {"text": "not this"}}
        payload = FrameCodec.encode(other) + FrameCodec.encode(wanted)
        stub = StubTransport([payload[:10], payload[10:]])
        _attach(client, stub)
        assert client._read_reply(7, 500) == wanted

    def test_timeout_returns_none(self):
        client = IpcClient(secret=bytes(range(32)))
        _attach(client, StubTransport([]))
        assert client._read_reply(1, 50) is None

    def test_no_transport_returns_none(self):
        client = IpcClient(secret=bytes(range(32)))
        assert client._read_reply(1, 50) is None


# ── capture/paste route through the reply loop ─────────────────────────────


class TestCapturePasteReplyLoop:
    def test_capture_uses_read_reply(self):
        client = IpcClient(secret=bytes(range(32)))
        stub = StubTransport([])
        _attach(client, stub)
        seen = {}

        def fake_read_reply(req_id, timeout_ms):
            seen["req_id"] = req_id
            seen["timeout_ms"] = timeout_ms
            return {"id": req_id, "result": {"text": "selected"}}

        client._read_reply = fake_read_reply
        assert client.capture_selection(timeout_ms=1500) == {"text": "selected"}
        # The id waited on is the id actually sent on the wire.
        frames, _ = FrameCodec.decode(bytearray(bytes(stub.written)))
        assert frames[0]["id"] == seen["req_id"]
        assert seen["timeout_ms"] == 1500

    def test_capture_none_on_timeout(self):
        client = IpcClient(secret=bytes(range(32)))
        _attach(client, StubTransport([]))
        client._read_reply = lambda req_id, ms: None
        assert client.capture_selection() is None

    def test_paste_uses_read_reply(self):
        client = IpcClient(secret=bytes(range(32)))
        stub = StubTransport([])
        _attach(client, stub)
        seen = {}

        def fake_read_reply(req_id, timeout_ms):
            seen["req_id"] = req_id
            return {"id": req_id, "result": {"status": "pasted", "chars": 5}}

        client._read_reply = fake_read_reply
        res = client.paste_text("hello")
        assert res == {"status": "pasted", "chars": 5}
        frames, _ = FrameCodec.decode(bytearray(bytes(stub.written)))
        assert frames[0]["id"] == seen["req_id"]
        assert frames[0]["params"]["text"] == "hello"

    def test_paste_none_on_timeout(self):
        client = IpcClient(secret=bytes(range(32)))
        _attach(client, StubTransport([]))
        client._read_reply = lambda req_id, ms: None
        assert client.paste_text("hello") is None

    def test_register_hotkeys_uses_read_reply(self):
        client = IpcClient(secret=bytes(range(32)))
        stub = StubTransport([])
        _attach(client, stub)
        seen = {}

        def fake_read_reply(req_id, timeout_ms):
            seen["req_id"] = req_id
            return {"id": req_id, "result": {"status": "registered", "count": 2}}

        client._read_reply = fake_read_reply
        res = client.register_hotkeys([{"vk": 0x78, "modifiers": 0, "label": "F9"}])
        assert res == {"status": "registered", "count": 2}
        frames, _ = FrameCodec.decode(bytearray(bytes(stub.written)))
        assert frames[0]["id"] == seen["req_id"]
        assert frames[0]["method"] == "command.register_hotkeys"

    def test_unregister_hotkeys_uses_read_reply(self):
        client = IpcClient(secret=bytes(range(32)))
        stub = StubTransport([])
        _attach(client, stub)
        seen = {}

        def fake_read_reply(req_id, timeout_ms):
            seen["req_id"] = req_id
            return {"id": req_id, "result": {"status": "unregistered"}}

        client._read_reply = fake_read_reply
        res = client.unregister_hotkeys()
        assert res == {"status": "unregistered"}
        frames, _ = FrameCodec.decode(bytearray(bytes(stub.written)))
        assert frames[0]["id"] == seen["req_id"]
        assert frames[0]["method"] == "command.unregister_hotkeys"
    def test_disconnected_returns_none_without_read(self):
        client = IpcClient(secret=bytes(range(32)))
        called = []
        client._read_reply = lambda req_id, ms: called.append(req_id) or {}
        assert client.capture_selection() is None
        assert client.paste_text("x") is None
        assert called == []


# ── End-to-end over fakes: real connect + real reply loop ──────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="named-pipe connect is win32-only")
class TestConnectEndToEnd:
    def test_full_handshake_then_capture(self, monkeypatch):
        hello = FrameCodec.encode(_hello_reply_ok())
        capture_reply = FrameCodec.encode({"id": 1, "result": {"text": "sel"}})
        _open_fake_for(StubTransport([hello, capture_reply]), monkeypatch)
        client = IpcClient(secret=bytes(range(32)))
        assert client.connect(timeout_ms=500) is True
        assert client.capture_selection(timeout_ms=500) == {"text": "sel"}


class _Duplex:
    """Read/write file pair behind one transport surface."""

    def __init__(self, reader, writer, sock=None):
        self._r = reader
        self._w = writer
        self._sock = sock

    def read(self, n):
        # BufferedReader.read(n) blocks for exactly n; pipes return partial.
        return self._r.read1(n)
    def write(self, data):
        return self._w.write(data)

    def flush(self):
        return self._w.flush()

    def fileno(self):
        return self._r.fileno()

    def close(self):
        # Shutdown first: unblocks a peer thread stuck in readinto
        # (closing a buffered file under a pending read deadlocks).
        try:
            if self._sock is not None:
                try:
                    self._sock.shutdown(2)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            try:
                self._r.close()
            finally:
                self._w.close()


def _loopback_pair():
    """Return (server_reader, server_writer, client_transport)."""
    import socket
    srv, cli = socket.socketpair()
    cli.settimeout(0.5)
    return srv.makefile("rb"), srv.makefile("wb"), _Duplex(cli.makefile("rb"), cli.makefile("wb"), cli)


def _read_frame(f):
    prefix = f.read(8)
    assert len(prefix) == 8
    import struct as _struct
    (length,) = _struct.unpack(">Q", prefix)
    payload = b""
    while len(payload) < length:
        chunk = f.read(length - len(payload))
        assert chunk, "EOF reading frame payload"
        payload += chunk
    import json as _json
    return _json.loads(payload.decode("utf-8"))

def _close3(*files):
    for f in files:
        try:
            f.close()
        except Exception:  # noqa: BLE001
            pass
class TestBackgroundReader:
    def _serve_one_then_push(self, srv_r, srv_w, req_id, event_first=True):
        """Fake daemon: read one command, optionally push an event first, reply."""
        import threading

        def _run():
            req = _read_frame(srv_r)
            assert req["id"] == req_id
            if event_first:
                srv_w.write(FrameCodec.encode({"method": "event.hotkey_fired", "params": {"mode": "panel"}}))
                srv_w.flush()
            srv_w.write(FrameCodec.encode({"id": req_id, "result": {"text": "sel"}}))
            srv_w.flush()

        threading.Thread(target=_run, daemon=True).start()

    def test_event_dispatched_while_reply_waiting(self):
        srv_r, srv_w, cli = _loopback_pair()
        client = IpcClient(secret=bytes(range(32)))
        _attach(client, cli)
        got = []
        client.on_event("event.hotkey_fired", got.append)
        client._start_reader()
        try:
            self._serve_one_then_push(srv_r, srv_w, 1, event_first=True)
            res = client.capture_selection(timeout_ms=3000)
            assert res == {"text": "sel"}
            deadline = time.monotonic() + 2.0
            while not got and time.monotonic() < deadline:
                time.sleep(0.01)
            assert got and got[0].get("mode") == "panel"
        finally:
            client.close()
            _close3(srv_r, srv_w)

    def test_concurrent_commands_demux_by_id(self):
        import threading
        srv_r, srv_w, cli = _loopback_pair()
        client = IpcClient(secret=bytes(range(32)))
        _attach(client, cli)
        client._start_reader()
        try:
            def _serve_two():
                first = _read_frame(srv_r)
                second = _read_frame(srv_r)
                # Reply out of order: second first.
                srv_w.write(FrameCodec.encode({"id": second["id"], "result": {"text": "two"}}))
                srv_w.flush()
                srv_w.write(FrameCodec.encode({"id": first["id"], "result": {"text": "one"}}))
                srv_w.flush()

            threading.Thread(target=_serve_two, daemon=True).start()
            results = {}
            errors = []

            def _cap(key):
                try:
                    results[key] = client.capture_selection(timeout_ms=5000)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            t1 = threading.Thread(target=_cap, args=("a",), daemon=True)
            t2 = threading.Thread(target=_cap, args=("b",), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)
            assert not errors
            assert sorted(r["text"] for r in results.values()) == ["one", "two"]
        finally:
            client.close()
            _close3(srv_r, srv_w)

    def test_early_reply_returned_without_wait(self):
        srv_r, srv_w, cli = _loopback_pair()
        client = IpcClient(secret=bytes(range(32)))
        _attach(client, cli)
        client._start_reader()
        try:
            client._deliver_replies([{"id": 41, "result": {"text": "early"}}])
            assert client._wait_for_reply(41, 500) == {"id": 41, "result": {"text": "early"}}
        finally:
            client.close()
            _close3(srv_r, srv_w)
