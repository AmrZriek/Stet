#!/usr/bin/env python3
"""Validate a Stet macOS release bundle or print its release contract.

Examples:
    python scripts/macos/verify_bundle.py --dry-run --arch arm64
    python scripts/macos/verify_bundle.py dist/Stet.app --arch arm64
    python scripts/macos/verify_bundle.py dist/Stet.app --arch x86_64 --archive dist/Stet-1.1.1-macos-x86_64.zip

The verifier is intentionally standalone: it uses only the Python standard
library and the macOS command-line toolchain when validating a real bundle.
It rejects universal/mixed Mach-O output, Windows payloads, missing metadata,
and (unless explicitly relaxed for local development) unsigned bundles.
"""

from __future__ import annotations

import argparse
import platform
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path


BUNDLE_ID = "com.amrzriek.Stet"
MINIMUM_MACOS = "14.0"
ARCHES = {"arm64", "x86_64"}
FORBIDDEN_NAMES = {
    "startup.vbs",
    "run.bat",
    "download_model.bat",
    "download_backend.bat",
    "unblock_stet.bat",
    "windows_installer_payload.py",
    "update.py",
    "uninstall.py",
    "llama-server.exe",
}
FORBIDDEN_SUFFIXES = {".bat", ".cmd", ".ps1", ".vbs", ".dll", ".exe"}
ICNS_TYPES = {b"icp4", b"icp5", b"icp6", b"ic07", b"ic08", b"ic09", b"ic10"}


def host_arch() -> str:
    machine = platform.machine().lower()
    return {"aarch64": "arm64", "amd64": "x86_64", "x64": "x86_64"}.get(machine, machine)


def resolve_arch(value: str, require_native: bool = False) -> str:
    target = host_arch() if value == "auto" else value
    if target not in ARCHES:
        raise ValueError(f"unsupported architecture {target!r}")
    if require_native and sys.platform == "darwin" and target != host_arch():
        raise ValueError(
            f"refusing non-native validation target {target}; current Python is {host_arch()}"
        )
    return target


def macho_arches(path: Path) -> set[str]:
    try:
        result = subprocess.run(
            ["lipo", "-archs", str(path)], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return set(result.stdout.split())
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["file", "-b", str(path)], capture_output=True, text=True, check=False
        )
    except OSError:
        return set()
    output = result.stdout.lower()
    return {arch for arch in ARCHES if arch in output}


def macho_files(app: Path) -> list[Path]:
    return [
        path
        for path in sorted(p for p in app.rglob("*") if p.is_file())
        if macho_arches(path)
    ]


def forbidden_payloads(root: Path) -> list[Path]:
    result = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.lower() in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            result.append(path)
    return result


def verify_icns(path: Path) -> None:
    """Check the structural ICNS container without requiring Pillow."""
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"icns":
        raise ValueError(f"invalid ICNS header: {path}")
    declared_size = int.from_bytes(data[4:8], "big")
    if declared_size != len(data):
        raise ValueError(f"invalid ICNS size: {path}")
    offset = 8
    chunks = set()
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError(f"truncated ICNS chunk: {path}")
        kind = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "big")
        if length < 8 or offset + length > len(data):
            raise ValueError(f"invalid ICNS chunk length: {path}")
        chunks.add(kind)
        offset += length
    if not ICNS_TYPES.issubset(chunks):
        raise ValueError(f"ICNS is missing standard PNG representations: {path}")


def verify_signature(app: Path) -> None:
    if sys.platform != "darwin":
        raise ValueError("signature verification requires macOS codesign")
    result = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).strip() or "codesign verification failed")
    details = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(app)],
        capture_output=True, text=True, check=False,
    )
    if "com.apple.security.get-task-allow" in details.stdout + details.stderr:
        raise ValueError("release entitlements contain com.apple.security.get-task-allow")


def verify_app(
    app: Path,
    arch: str,
    minimum_macos: str,
    require_signed: bool,
    allow_missing_backend: bool,
) -> list[str]:
    if not app.is_dir() or app.suffix != ".app":
        raise ValueError(f"expected a .app directory, got {app}")
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.exists():
        raise ValueError(f"missing {plist_path}")
    with plist_path.open("rb") as stream:
        info = plistlib.load(stream)
    expected = {
        "CFBundleIdentifier": BUNDLE_ID,
        "LSMinimumSystemVersion": minimum_macos,
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "Stet.icns",
        "StetArchitecture": arch,
        "StetBackendMode": "cpu" if arch == "x86_64" else "auto",
    }
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"Info.plist {key}={info.get(key)!r}; expected {value!r}")
    verify_icns(app / "Contents" / "Resources" / "Stet.icns")

    forbidden = forbidden_payloads(app)
    if forbidden:
        names = ", ".join(str(path.relative_to(app)) for path in forbidden[:12])
        raise ValueError(f"Windows payload present: {names}")

    binaries = macho_files(app)
    if not binaries:
        raise ValueError("no Mach-O code found")
    mismatches = []
    for binary in binaries:
        actual = macho_arches(binary)
        if actual != {arch}:
            mismatches.append(f"{binary.relative_to(app)}={sorted(actual) or ['unknown']}")
    if mismatches:
        raise ValueError("non-native or universal Mach-O code: " + ", ".join(mismatches[:12]))

    backend = app / "Contents" / "Resources" / "backend" / arch / "llama-server"
    if not backend.exists() and not allow_missing_backend:
        raise ValueError(f"missing bundled native backend: {backend}")
    if backend.exists() and macho_arches(backend) != {arch}:
        raise ValueError(f"backend architecture is {sorted(macho_arches(backend))}, expected {arch}")
    if backend.exists() and not any(backend.parent.glob("libggml*.dylib")):
        raise ValueError("bundled backend has no llama.cpp runtime dylibs")
    manifest = app / "Contents" / "Resources" / "backend-manifest.json"
    if backend.exists() and not manifest.is_file():
        raise ValueError(f"missing backend manifest: {manifest}")
    if manifest.is_file():
        import json
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if metadata.get("minimum_macos") != minimum_macos:
            raise ValueError(
                "backend manifest minimum_macos="
                f"{metadata.get('minimum_macos')!r}; expected {minimum_macos!r}"
            )
    if require_signed:
        verify_signature(app)
    return [f"validated {len(binaries)} Mach-O files for {arch}"]


def verify_archive(archive: Path, arch: str) -> list[str]:
    if archive.suffix.lower() != ".zip":
        if sys.platform != "darwin":
            raise ValueError("DMG inspection requires macOS hdiutil")
        result = subprocess.run(
            ["hdiutil", "imageinfo", str(archive)], capture_output=True, text=True, check=False
        )
        if result.returncode:
            raise ValueError((result.stderr or result.stdout).strip() or "invalid DMG")
        return [f"validated DMG container {archive.name}"]
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    if not any(name.startswith("Stet.app/Contents/Info.plist") for name in names):
        raise ValueError("ZIP does not contain Stet.app/Contents/Info.plist")
    bad = [name for name in names if Path(name).name.lower() in FORBIDDEN_NAMES or Path(name).suffix.lower() in FORBIDDEN_SUFFIXES]
    if bad:
        raise ValueError("ZIP contains forbidden Windows payload: " + ", ".join(bad[:12]))
    return [f"validated ZIP archive {archive.name} ({arch})"]


def dry_run(arch: str, minimum_macos: str) -> list[str]:
    backend_mode = "cpu" if arch == "x86_64" else "auto (Metal probe with CPU retry)"
    return [
        f"native target: {arch}",
        f"backend mode: {backend_mode}",
        f"bundle identifier: {BUNDLE_ID}",
        f"minimum macOS: {minimum_macos}",
        f"artifacts: Stet-<version>-macos-{arch}.zip and .dmg",
        "signing plan: nested code inside-out, hardened runtime, no get-task-allow",
        "entitlements plan: empty outside-App-Store entitlement set; TCC is runtime consent",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", nargs="?", type=Path, help="Stet.app to validate")
    parser.add_argument("--arch", choices=("auto", "arm64", "x86_64"), default="auto")
    parser.add_argument("--archive", type=Path, help="Optional matching ZIP or DMG to inspect")
    parser.add_argument("--allow-unsigned", action="store_true", help="Local development only")
    parser.add_argument("--allow-missing-backend", action="store_true", help="Local development only")
    parser.add_argument(
        "--minimum-macos",
        default=MINIMUM_MACOS,
        help="Expected LSMinimumSystemVersion and backend-manifest target.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the architecture-aware release contract")
    args = parser.parse_args()
    try:
        arch = resolve_arch(args.arch, require_native=not args.dry_run)
        if args.dry_run:
            print("\n".join(dry_run(arch, args.minimum_macos)))
            return 0
        if args.app is None:
            parser.error("app is required unless --dry-run is used")
        for message in verify_app(
            args.app,
            arch,
            args.minimum_macos,
            not args.allow_unsigned,
            args.allow_missing_backend,
        ):
            print(message)
        if args.archive:
            for message in verify_archive(args.archive, arch):
                print(message)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
