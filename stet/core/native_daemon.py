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

def _terminate_stale_daemons() -> None:
    """Ensure no orphaned stet-core daemon holds the named pipe or hotkeys."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        h_snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if h_snap == -1:
            return

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        my_pid = os.getpid()
        if kernel32.Process32FirstW(h_snap, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.lower()
                if (name == "stet-core.exe" or name.startswith("stet-core")) and pe.th32ProcessID != my_pid:
                    h_proc = kernel32.OpenProcess(0x0001, False, pe.th32ProcessID)  # PROCESS_TERMINATE
                    if h_proc:
                        kernel32.TerminateProcess(h_proc, 1)
                        kernel32.CloseHandle(h_proc)
                if not kernel32.Process32NextW(h_snap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(h_snap)
    except Exception:
        pass

def _attach_job_object(proc: subprocess.Popen) -> object | None:
    """Attach daemon subprocess to a Job Object so it terminates if Python crashes."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        ctypes.windll.kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

        proc_handle = ctypes.c_void_p(int(proc._handle))
        ctypes.windll.kernel32.AssignProcessToJobObject(job, proc_handle)
        return job
    except Exception:
        return None


def launch_daemon() -> tuple[subprocess.Popen, bytes] | None:
    """Spawn the daemon, pass it a fresh secret over stdin, verify it is alive.

    Returns ``(process, secret)`` on success, ``None`` on any failure so the
    caller falls back to in-process Win32 behavior.
    """
    try:
        _terminate_stale_daemons()
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
        try:
            from stet.constants import LOG_FILE
            log_dir = LOG_FILE.parent
            log_dir.mkdir(parents=True, exist_ok=True)
            _err_log = open(log_dir / "daemon_stderr.log", "ab", buffering=0)
            popen_kwargs["stderr"] = _err_log
        except Exception:
            _err_log = None
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.Popen([str(binary)], **popen_kwargs)
        # Keep the sink alive for the daemon's lifetime; Popen does not own it.
        job = _attach_job_object(proc)
        if job is not None:
            proc._job_object = job  # type: ignore[attr-defined]
        if _err_log is not None:
            proc._stderr_sink = _err_log  # type: ignore[attr-defined]
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
            try:
                if _err_log is not None:
                    _err_log.close()
            except Exception:
                pass
            return None
        if proc.poll() is not None:
            try:
                if _err_log is not None:
                    _err_log.close()
            except Exception:
                pass
            return None
        return proc, secret
    except Exception:
        return None
