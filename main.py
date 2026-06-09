"""Root entry point for Stet.

Used by:
  - run.bat (manual launch)
  - Windows registry startup (HKCU\...\Run)

Wraps the real entry point (stet.main) with a pre-import crash
logger so that pythonw.exe failures are always captured.
"""

import sys
import os
import traceback
from pathlib import Path
from datetime import datetime

_SCRIPT_DIR = Path(__file__).parent.resolve()
_LOG_FILE = _SCRIPT_DIR / "app_debug.log"


def _boot_log(msg: str):
    """Write directly to app_debug.log using only stdlib — no project imports."""
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _run():
    _boot_log("[BOOT] main.py starting")
    _boot_log(f"[BOOT] sys.executable = {sys.executable}")
    _boot_log(f"[BOOT] os.getcwd() = {os.getcwd()}")
    _boot_log(f"[BOOT] __file__ = {__file__}")
    _boot_log(f"[BOOT] sys.path[0] = {sys.path[0] if sys.path else '(empty)'}")

    try:
        from stet.main import main
    except Exception as e:
        _boot_log(
            f"[BOOT CRASH] Failed to import stet.main: {e}\n{traceback.format_exc()}"
        )
        sys.exit(1)

    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _boot_log(f"[BOOT CRASH] main() raised: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    _run()
