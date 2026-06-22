"""
tests/fixtures/__init__.py — Build malicious and benign ZIP fixtures on the fly.

Why a build script instead of committed binaries?
  - Keeps the repo small and the fixtures explainable.
  - Ensures tests are deterministic across platforms.
  - Cross-platform: zipfile + stat are stdlib; no external zip binary needed.

Public entry point: ``build_all(out_dir: Path) -> dict[str, Path]``
"""

import stat
import zipfile
from pathlib import Path


FIXTURE_NAMES = {
    "sample_update": "sample-update.zip",
    "path_traversal": "malicious-zip-path-traversal.zip",
    "symlink": "malicious-zip-symlink.zip",
    "absolute_path": "malicious-zip-absolute-path.zip",
    "empty_dir": "empty-staging.zip",
}


def _build_sample_update(out: Path) -> Path:
    """Benign ZIP that mirrors a real Stet release layout (Stet/<files>)."""
    p = out / FIXTURE_NAMES["sample_update"]
    if p.exists():
        return p
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        # Mimic release ZIP layout: outer folder contains the payload.
        zf.writestr("Stet/Stet.exe", b"FAKE-EXE-CONTENT")
        zf.writestr("Stet/VERSION", "1.2.3\n")
        zf.writestr("Stet/stet.py", "# placeholder\nAPP_VERSION = '1.2.3'\n")
        zf.writestr("Stet/stet/constants.py", "APP_VERSION = '1.2.3'\n")
    return p


def _build_path_traversal(out: Path) -> Path:
    """ZIP containing a member that escapes the destination via '../'."""
    p = out / FIXTURE_NAMES["path_traversal"]
    if p.exists():
        return p
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../../etc/poisoned", "you have been pwned")
        zf.writestr("Stet/Stet.exe", b"FAKE-EXE")
    return p


def _build_symlink(out: Path) -> Path:
    """ZIP containing a symlink member (Unix mode bits set)."""
    p = out / FIXTURE_NAMES["symlink"]
    if p.exists():
        return p
    # Symlink bits: S_IFLNK = 0o120000. The high 16 bits of external_attr
    # hold the unix mode. We build a member called "Stet/leak" whose mode
    # makes _safe_extract see it as a symlink.
    sym_mode = (stat.S_IFLNK | 0o777) << 16
    info = zipfile.ZipInfo("Stet/leak")
    info.external_attr = sym_mode
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(info, "/etc/passwd")
        zf.writestr("Stet/Stet.exe", b"FAKE-EXE")
    return p


def _build_absolute_path(out: Path) -> Path:
    """ZIP containing a member with an absolute Unix path."""
    p = out / FIXTURE_NAMES["absolute_path"]
    if p.exists():
        return p
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("/etc/absolutely_not", "should never be extracted")
        zf.writestr("Stet/Stet.exe", b"FAKE-EXE")
    return p


def _build_empty_dir(out: Path) -> Path:
    """ZIP with no Stet.exe anywhere — triggers the 'not found' branch."""
    p = out / FIXTURE_NAMES["empty_dir"]
    if p.exists():
        return p
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Stet/VERSION", "9.9.9\n")
    return p


_BUILDERS = (
    _build_sample_update,
    _build_path_traversal,
    _build_symlink,
    _build_absolute_path,
    _build_empty_dir,
)


def build_all(out_dir: Path) -> dict[str, Path]:
    """Build (or reuse) all fixture ZIPs and return a name→path map."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return {fn.__name__.replace("_build_", ""): fn(out_dir) for fn in _BUILDERS}


if __name__ == "__main__":
    here = Path(__file__).parent
    paths = build_all(here)
    for name, p in paths.items():
        print(f"  {name}: {p}  ({p.stat().st_size} B)" if p.exists() else f"  {name}: MISSING")
