"""Stet main entry point.

Boot-time crash logger runs BEFORE any project imports to ensure that
pythonw.exe failures (which have no stderr) are always captured in
app_debug.log.
"""

import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import builtins
_exe_stem = Path(sys.executable).stem.lower()
_is_compiled = (
    getattr(sys, "frozen", False)
    or hasattr(builtins, "__nuitka_binary_exe")
    or not _exe_stem.startswith("python")
)
if _is_compiled:
    _exe_path = Path(sys.executable).resolve()
    if sys.platform == "darwin" and ".app/Contents/MacOS" in _exe_path.as_posix():
        _SCRIPT_DIR = _exe_path.parent.parent.parent.parent.resolve()
    else:
        _SCRIPT_DIR = _exe_path.parent.resolve()
else:
    _SCRIPT_DIR = Path(__file__).parent.parent.resolve()

_LOG_FILE = _SCRIPT_DIR / "app_debug.log"




def _boot_log(msg: str):
    """Write directly to app_debug.log using only stdlib — no project imports."""
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ── Import project modules AFTER boot logger is defined ──────────────────────
try:
    _boot_log("[BOOT] Importing stet.constants...")
    import stet.constants  # noqa: F401  — triggers HiDPI env, platform detection
    _boot_log("[BOOT] Constants imported OK")
except Exception as _import_err:
    _boot_log(
        f"[BOOT CRASH] Failed to import constants: {_import_err}\n{traceback.format_exc()}"
    )
    raise

try:
    _boot_log("[BOOT] Importing stet.core.app...")
    from stet.core.app import StetApp

    _boot_log("[BOOT] App imported OK")
except Exception as _import_err:
    _boot_log(
        f"[BOOT CRASH] Failed to import app: {_import_err}\n{traceback.format_exc()}"
    )
    raise

from stet.core.utils import log


def main():
    def _excepthook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log(f"[UNCAUGHT EXCEPTION]\n{msg}")
        _boot_log(f"[UNCAUGHT EXCEPTION]\n{msg}")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args):
        msg = "".join(
            traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            )
        )
        log(f"[THREAD EXCEPTION in {args.thread}]\n{msg}")
        _boot_log(f"[THREAD EXCEPTION]\n{msg}")

    threading.excepthook = _thread_excepthook

    _boot_log("[BOOT] Acquiring single-instance lock via QSharedMemory...")
    from PyQt6.QtCore import QSharedMemory

    _lock_key = os.environ.get("STET_LOCK_KEY", "StetSingleInstanceLock")
    _shared_mem = QSharedMemory(_lock_key)
    if _shared_mem.attach():
        _boot_log(
            "[BOOT] Another instance is already running (shared memory attached). Exiting."
        )
        sys.exit(0)
    if not _shared_mem.create(1):
        _boot_log(
            "[BOOT] Could not create shared memory segment — another instance likely running. Exiting."
        )
        sys.exit(0)
    _boot_log("[BOOT] Lock acquired — this is the only instance.")

    try:
        _boot_log("[BOOT] Creating QApplication...")
        from PyQt6.QtWidgets import QApplication

        qapp = QApplication(sys.argv)
        qapp.setStyle("Fusion")
        _boot_log("[BOOT] QApplication created OK")

        _boot_log("[BOOT] Creating StetApp...")
        app = StetApp()
        _boot_log(f"[BOOT] StetApp created OK — tray visible: {app.tray.isVisible()}")

        _boot_log("[BOOT] Entering Qt event loop")
        sys.exit(qapp.exec())
    except SystemExit:
        raise
    except Exception as _e:
        _boot_log(f"[BOOT CRASH] {_e}\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()
