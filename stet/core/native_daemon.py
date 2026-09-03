"""Spawn and bootstrap the Stet native Rust core daemon.

The Python app launches ``stet-core`` as a child process, hands it a fresh
32-byte secret over its stdin (45-byte bootstrap frame), then talks to it
over the ``\\\\.\\pipe\\stet_ipc_v2`` named pipe via :class:`IpcClient`.

The secret travels over stdin only — it never appears in argv, env, or logs.
Any launch failure returns ``None`` so the caller falls back to in-process
Win32 behavior.
"""

from __future__ import annotations

import os
import secrets
import struct
import subprocess
import sys
from pathlib import Path

BOOTSTRAP_MAGIC = b"STET"
BOOTSTRAP_VERSION = 0x01
BOOTSTRAP_SECRET_LEN = 32
BOOTSTRAP_TOTAL_BYTES = 45

DEV_BINARY_RELATIVE = Path("crates") / "target" / "debug" / "stet-core.exe"
INSTALLED_BINARY_NAME = "stet-core.exe"


def encode_bootstrap(secret: bytes, client_pid: int) -> bytes:
    """Encode the 45-byte stdin bootstrap frame mirror of the Rust framing."""
    if len(secret) != BOOTSTRAP_SECRET_LEN:
        raise ValueError(
            f"Bootstrap secret must be exactly {BOOTSTRAP_SECRET_LEN} bytes, "
            f"got {len(secret)}"
        )
    return (
        BOOTSTRAP_MAGIC
        + bytes((BOOTSTRAP_VERSION,))
        + struct.pack("<I", BOOTSTRAP_SECRET_LEN)
        + bytes(secret)
        + struct.pack("<I", client_pid)
    )


def find_daemon_binary() -> Path | None:
    """Locate the daemon binary: dev build first, then the install dir."""
    from stet.constants import SCRIPT_DIR

    dev = SCRIPT_DIR / DEV_BINARY_RELATIVE
    if dev.is_file():
        return dev
    installed = SCRIPT_DIR / INSTALLED_BINARY_NAME
    if installed.is_file():
        return installed
    return None


def launch_daemon() -> tuple[subprocess.Popen, bytes] | None:
    """Spawn the daemon, pass it a fresh secret over stdin, verify it is alive.

    Returns ``(process, secret)`` on success, ``None`` on any failure so the
    caller falls back to in-process Win32 behavior.
    """
    try:
        binary = find_daemon_binary()
        if binary is None:
            return None
        secret = secrets.token_bytes(BOOTSTRAP_SECRET_LEN)
        bootstrap = encode_bootstrap(secret, os.getpid())
        popen_kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.Popen([str(binary)], **popen_kwargs)
        try:
            if proc.stdin is None:
                raise OSError("Daemon stdin unavailable")
            proc.stdin.write(bootstrap)
            proc.stdin.close()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
            return None
        if proc.poll() is not None:
            return None
        return proc, secret
    except Exception:
        return None
