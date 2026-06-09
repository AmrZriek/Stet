import os
import threading
from datetime import datetime

from stet.constants import DEBUG_LOG, MACOS, WINDOWS


def _release_zip_asset(data: dict) -> dict | None:
    assets = data.get("assets", [])
    os_kw = "windows" if WINDOWS else ("macos" if MACOS else "linux")
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


def log(msg: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _log_lock:
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
