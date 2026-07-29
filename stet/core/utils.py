import os
import threading
from datetime import datetime

from stet.constants import DEBUG_LOG, MACOS, WINDOWS


def _release_zip_asset(data: dict) -> dict | None:
    assets = data.get("assets", [])
    os_kw = "windows" if WINDOWS else ("macos" if MACOS else "linux")
    if MACOS:
        for asset in assets:
            name = str(asset.get("name", "")).lower()
            if name.endswith(".dmg") and os_kw in name:
                return asset
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".zip") and os_kw in name:
            return asset

    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".zip"):
            return asset
    return None


_log_lock = threading.Lock()
_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB — one rotated backup is kept


def log(msg: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _log_lock:
            try:
                if (
                    os.path.exists(DEBUG_LOG)
                    and os.path.getsize(DEBUG_LOG) > _LOG_MAX_BYTES
                ):
                    backup = str(DEBUG_LOG) + ".1"
                    if os.path.exists(backup):
                        os.remove(backup)
                    os.replace(DEBUG_LOG, backup)
            except OSError:
                pass  # rotation is best-effort; never lose the message
            with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def friendly_name(path: str) -> str:
    n = os.path.basename(path).replace(".gguf", "")
    for old, new in [
        ("-it-", " IT "),
        ("-F16", " F16"),
        ("-BF16", " BF16"),
        ("-Q4_K_M", " Q4_K_M"),
        ("-Q8_0", " Q8"),
        ("-Q4_K_XL", " Q4_K_XL"),
        ("-IQ4_NL", " IQ4"),
        ("-GGUF", ""),
        ("-gguf", ""),
    ]:
        n = n.replace(old, new)
    return n
