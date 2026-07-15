"""
update.py — Stet app and dependency updater
=============================================
Updates all Python dependencies and optionally downloads the latest
Stet release from GitHub.

Usage
-----
    python update.py             # update Python deps only (for dev)
    python update.py --app       # update Stet app to latest release
    python update.py --all       # update everything
    StetUpdater.exe --app --install-dir <dir> --wait-pid <pid> --restart

What it does
------------
1. Upgrades pip itself
2. Installs / upgrades all packages from requirements.txt
3. (Optional) Downloads the latest Stet release zip for your OS
   and extracts it over the current installation (preserving user config/models).
"""

import sys
import os
import subprocess
import urllib.request
import urllib.error
import zipfile
import shutil
import json
import re
import time
import tempfile
import hashlib
import logging
import traceback
from pathlib import Path

# Use the same GitHub API constant as the GUI flow to avoid drift.
try:
    from stet.constants import GITHUB_RELEASES_API
except Exception:  # pragma: no cover — standalone script without stet package
    GITHUB_RELEASES_API = "https://api.github.com/repos/AmrZriek/Stet/releases/latest"

ROOT = Path(__file__).parent.resolve()
VENV_PY = (
    ROOT / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
)
REQ_FILE = ROOT / "requirements.txt"
# Backwards-compat alias: legacy callers (and tests) reference GITHUB_API.
GITHUB_API = GITHUB_RELEASES_API

# ── Logging (used by F-7 sanitized errors) ───────────────────────────────────
_LOG = logging.getLogger("stet.update")


# ── Helpers ───────────────────────────────────────────────────────────────────
def banner(msg: str):
    print(f"\n{'─' * 60}\n  {msg}\n{'─' * 60}")


def run(cmd: list, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def pip_path() -> str:
    """Return the right pip executable (venv > system)."""
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable


# F-2: HTTPS enforcement wrapper. Refuses to follow any URL whose final
# landed location is not HTTPS (catches redirect-to-http downgrade attacks).
def _safe_urlopen(url: str, timeout: int = 15):
    """
    Open ``url`` only if both the request URL and the final response URL are
    HTTPS.  Returns the response object on success; raises RuntimeError on
    any downgrade attempt.  Wraps urllib.request.urlopen so all call sites
    in update.py share one chokepoint.
    """
    if not str(url).lower().startswith("https://"):
        raise RuntimeError(f"Refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(str(url), headers={"User-Agent": "Stet-updater"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    try:
        final = resp.geturl()
        if not str(final).lower().startswith("https://"):
            try:
                resp.close()
            except Exception:
                pass
            raise RuntimeError(
                f"Refusing HTTPS→HTTP downgrade: {url} → {final}"
            )
    except RuntimeError:
        raise
    except Exception:
        # If geturl() itself fails, treat the response as suspect and refuse.
        try:
            resp.close()
        except Exception:
            pass
        raise RuntimeError(f"Could not verify final URL for: {url}")
    return resp


# F-5: Validate tag_name format & length before passing to the version parser.
_TAG_RE = re.compile(r"^[vV]?\d{1,4}\.\d{1,4}(\.\d{1,4})?([\-+][A-Za-z0-9.\-]{0,32})?$")
_MAX_TAG_LEN = 64


def _validate_tag(tag: str) -> str:
    if not isinstance(tag, str) or not tag:
        raise RuntimeError(f"Refusing invalid tag_name (empty): {tag!r}")
    if len(tag) > _MAX_TAG_LEN:
        raise RuntimeError(
            f"Refusing tag_name longer than {_MAX_TAG_LEN} chars: {tag!r}"
        )
    if not _TAG_RE.match(tag):
        raise RuntimeError(f"Refusing malformed tag_name: {tag!r}")
    return tag


def get_local_version(root: Path = ROOT) -> str:
    version_file = root / "VERSION"
    if version_file.exists():
        try:
            version = version_file.read_text(encoding="utf-8").strip()
            if version:
                return version
        except Exception:
            pass

    try:
        text = (root / "stet" / "constants.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*[\'"]([0-9\.]+)[\'"]', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def _parse_version(v_str):
    v_str = re.sub(r"[^0-9\.]", "", v_str)
    parts = []
    for p in v_str.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


# ── Python dependencies ───────────────────────────────────────────────────────
def update_python_deps():
    banner("Updating Python dependencies")
    py = pip_path()

    # Upgrade pip first
    run([py, "-m", "pip", "install", "--upgrade", "pip"])

    if not REQ_FILE.exists():
        print(f"  requirements.txt not found at {REQ_FILE}")
        return

    # Upgrade all packages listed in requirements.txt
    run([py, "-m", "pip", "install", "--upgrade", "-r", str(REQ_FILE)])
    print("  All packages up to date.")


# ── App updater ─────────────────────────────────────────────────────────────
def _wait_for_pid(pid: int | None):
    if not pid:
        return

    print(f"  Waiting for Stet process {pid} to exit...")
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, 120_000)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return

    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return
        time.sleep(0.25)


def _safe_extract(zip_ref: zipfile.ZipFile, dest: Path):
    """
    F-3 hardened: rejects symlinks, absolute paths, drive-relative paths,
    and any path that escapes ``dest`` after resolution.  Raises
    RuntimeError on the first bad member; does not partially extract.
    """
    import stat
    dest = dest.resolve()
    for member in zip_ref.infolist():
        # Reject symlinks first — they can point anywhere once extracted.
        mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"Refusing symlink in ZIP: {member.filename}")
        # Reject absolute paths and Windows drive-relative paths up front
        # so we never even build a target.
        raw = member.filename
        if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", raw):
            raise RuntimeError(f"Refusing absolute path in ZIP: {member.filename}")
        target = (dest / raw).resolve()
        # target must live inside dest (allow dest itself, not its parents).
        if target != dest and dest not in target.parents:
            raise RuntimeError(f"Unsafe path in ZIP: {member.filename}")
    zip_ref.extractall(dest)


def _should_skip_update_file(rel_path: Path) -> bool:
    parts = rel_path.parts
    name = rel_path.name
    if not parts:
        return True
    if name.endswith((".gguf", ".onnx")):
        return True
    if name in {"config.json", "app_debug.log", "server_log.txt"}:
        return True
    return False


def _copy_file_atomic(src_path: Path, dest_path: Path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest_path.with_name(dest_path.name + ".updating")
    if tmp_dest.exists():
        tmp_dest.unlink()
    try:
        shutil.copy2(src_path, tmp_dest)
    except Exception:
        if tmp_dest.exists():
            tmp_dest.unlink()
        raise
    os.replace(tmp_dest, dest_path)


def update_app(
    root: Path = ROOT,
    wait_pid: int | None = None,
    restart: bool = False,
    allow_unsigned: bool = False,
):
    root = root.resolve()
    banner("Updating Stet app")
    print(f"  Install dir     : {root}")
    _wait_for_pid(wait_pid)
    time.sleep(0.5)

    print("  Fetching latest release info from GitHub...")

    try:
        with _safe_urlopen(GITHUB_RELEASES_API, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        # F-7: log full traceback for support, show friendly message to user.
        _LOG.error("GitHub API request failed: %s", traceback.format_exc())
        print("  ERROR: Could not reach GitHub API.")
        return

    # F-5: validate tag_name before any further processing.
    try:
        tag = _validate_tag(data.get("tag_name", ""))
    except RuntimeError as e:
        _LOG.error("Invalid tag from API: %s", e)
        print("  ERROR: Release has invalid tag_name; aborting update.")
        return
    assets = data.get("assets", [])

    remote_ver = tag.lstrip("vV")
    local_ver = get_local_version(root)
    remote_tuple = _parse_version(remote_ver)
    local_tuple = _parse_version(local_ver)

    if remote_tuple <= local_tuple:
        print("  You already have the latest version.")
        return

    # Find the main binary asset for the current OS
    os_kw = (
        "windows"
        if sys.platform == "win32"
        else ("macos" if sys.platform == "darwin" else "linux")
    )
    main_asset = None
    for asset in assets:
        name = asset["name"].lower()
        if name.endswith(".zip") and os_kw in name:
            main_asset = asset
            break

    if not main_asset and assets:
        for asset in assets:
            if asset["name"].lower().endswith(".zip"):
                main_asset = asset
                break

    if not main_asset:
        print(f"  No suitable ZIP asset found in release {tag}.")
        return

    # F-2: validate asset URL through the same HTTPS chokepoint.
    url = main_asset["browser_download_url"]
    if not url.lower().startswith("https://"):
        raise RuntimeError(f"Refusing non-HTTPS asset URL: {url}")
    # Reject any filename that tries to escape the work dir or contains NULs.
    filename = main_asset["name"]
    if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
        raise RuntimeError(f"Refusing unsafe asset filename: {filename!r}")

    # F-6: use a per-run unguessable temp dir (mirrors app.py:1583).
    work_dir = Path(tempfile.mkdtemp(prefix="StetUpdate_"))
    tmp_path = work_dir / filename

    print(f"  Downloading {filename} ...")
    try:
        # F-2: use the HTTPS-safe downloader. urlretrieve has its own
        # internal opener so we do the GET manually and stream to disk.
        with _safe_urlopen(url, timeout=60) as dl_resp, \
                open(tmp_path, "wb") as out_fh:
            total = int(dl_resp.headers.get("Content-Length") or 0)
            downloaded = 0
            block = 64 * 1024
            while True:
                chunk = dl_resp.read(block)
                if not chunk:
                    break
                out_fh.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = min(100, downloaded * 100 // total)
                    bar = "#" * (pct // 4)
                    print(
                        f"\r  [{bar:<25}] {pct:3d}%  {downloaded / 1_048_576:.1f} MB",
                        end="",
                        flush=True,
                    )
        print()
    except Exception:
        _LOG.error("Download failed: %s", traceback.format_exc())
        print("\n  ERROR downloading update.")
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    # F-1: mandatory SHA-256 verification.  If SHA256SUMS.txt is missing
    # for a release that should have one, REFUSE to extract — unless the
    # caller passed --allow-unsigned (dev mode).
    sha_url = url.rsplit("/", 1)[0] + "/SHA256SUMS.txt"
    sha_verified = False
    try:
        with _safe_urlopen(sha_url, timeout=10) as sha_resp:
            sha_data = sha_resp.read().decode()
    except urllib.error.HTTPError as e:
        _LOG.error("SHA256SUMS.txt fetch failed: %s", e)
        sha_data = None
    except Exception:
        _LOG.error("SHA256SUMS.txt fetch error: %s", traceback.format_exc())
        sha_data = None

    if sha_data is None:
        if allow_unsigned:
            print("  WARNING: No SHA256SUMS.txt — --allow-unsigned set, continuing.")
        else:
            print(
                "  ERROR: No SHA256SUMS.txt in release; refusing to install "
                "an unverifiable update. Re-run with --allow-unsigned to override."
            )
            tmp_path.unlink(missing_ok=True)
            shutil.rmtree(work_dir, ignore_errors=True)
            return
    else:
        expected_hash = None
        for line in sha_data.strip().splitlines():
            # SHA256SUMS.txt line format: "<sha>  <filename>" or "<sha> *<filename>"
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            sha, name = parts
            if name.lstrip("*") == filename:
                expected_hash = sha
                break
        if not expected_hash or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
            if allow_unsigned:
                print("  WARNING: No entry for this asset in SHA256SUMS.txt — "
                      "--allow-unsigned set, continuing.")
            else:
                print(
                    f"  ERROR: No SHA-256 entry for {filename} in SHA256SUMS.txt; "
                    "refusing to install. Re-run with --allow-unsigned to override."
                )
                tmp_path.unlink(missing_ok=True)
                shutil.rmtree(work_dir, ignore_errors=True)
                return
        else:
            actual_hash = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
            if actual_hash.lower() != expected_hash.lower():
                tmp_path.unlink(missing_ok=True)
                shutil.rmtree(work_dir, ignore_errors=True)
                raise RuntimeError(
                    f"SHA-256 mismatch for {filename}: "
                    f"expected {expected_hash[:16]}…, got {actual_hash[:16]}…"
                )
            sha_verified = True
            print(f"  SHA-256 verified: {actual_hash[:16]}...")

    staging_dir = work_dir / "staging"
    staging_dir.mkdir()

    print("  Extracting ...")
    try:
        with zipfile.ZipFile(tmp_path, "r") as zip_ref:
            _safe_extract(zip_ref, staging_dir)
    except RuntimeError as e:
        # F-7: log the actual offending member for support, show a clean
        # message to the user.
        _LOG.error("Unsafe ZIP rejected: %s", e)
        print("  ERROR: Downloaded update is unsafe; aborting.")
        tmp_path.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    tmp_path.unlink()

    app_dir = None
    for child in staging_dir.iterdir():
        if child.is_dir() and (child / "Stet.exe").exists():
            app_dir = child
            break

    if not app_dir:
        if (staging_dir / "Stet.exe").exists():
            app_dir = staging_dir
        else:
            print("  ERROR: Stet.exe not found in downloaded ZIP")
            shutil.rmtree(work_dir, ignore_errors=True)
            return

    print("  Applying update...")

    for src_path in app_dir.rglob("*"):
        if not src_path.is_file():
            continue

        rel_path = src_path.relative_to(app_dir)
        dest_path = root / rel_path

        if _should_skip_update_file(rel_path):
            continue

        try:
            _copy_file_atomic(src_path, dest_path)
        except PermissionError:
            print(f"  ERROR: Permission denied replacing {rel_path}.")
            print("         Please ensure Stet is completely closed before updating.")
            shutil.rmtree(work_dir, ignore_errors=True)
            return

    shutil.rmtree(work_dir, ignore_errors=True)
    suffix = "" if sha_verified else " (unverified)"
    print(f"  Stet updated to {tag}{suffix}.")

    if restart:
        exe = root / ("Stet.exe" if sys.platform == "win32" else "Stet")
        if exe.exists():
            print("  Restarting Stet...")
            subprocess.Popen([str(exe)], cwd=str(root), shell=False)



# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Stet updater")
    p.add_argument("--app", action="store_true", help="Update Stet app")
    p.add_argument(
        "--all", action="store_true", help="Update everything (app + python deps)"
    )
    p.add_argument(
        "--install-dir", default=str(ROOT), help="Stet installation directory"
    )
    p.add_argument(
        "--wait-pid",
        type=int,
        default=None,
        help="Wait for this Stet process before applying",
    )
    p.add_argument("--restart", action="store_true", help="Restart Stet after updating")
    p.add_argument(
        "--allow-unsigned",
        action="store_true",
        help="Allow install of an update whose SHA256SUMS.txt is missing "
        "or does not list this asset. Dev/QA only; production releases "
        "always publish SHA256SUMS.txt.",
    )
    args = p.parse_args()

    # Default to updating python deps if no args given (backward compat)
    if not args.app and not args.all:
        update_python_deps()

    if args.all:
        update_python_deps()

    if args.app or args.all:
        update_app(
            Path(args.install_dir),
            wait_pid=args.wait_pid,
            restart=args.restart,
            allow_unsigned=args.allow_unsigned,
        )

    banner("Update complete!")
    print("  Restart Stet to use the new versions.\n")
