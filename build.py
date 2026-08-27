"""
build.py — Stet release packager (v2)
==========================================
Produces a structured release folder and portable ZIP in dist/.

Supports Windows (MSVC compiler), macOS, and Linux.
Must be run on the target platform (Nuitka binaries are not cross-platform).

Usage
-----
    python build.py                     # full release build
    python build.py --version 1.0.0     # override version tag
    python build.py --keep-folder       # keep intermediate build dir
    python build.py --skip-installer    # skip installer build (Windows only)

Requirements
------------
    pip install -r requirements.txt
    pip install pyinstaller
    Windows: Visual Studio Build Tools (MSVC) — install with:
        winget install Microsoft.VisualStudio.2022.BuildTools
"""

import sys
import os
import hashlib
import shutil
import subprocess
import zipfile
import argparse
import json
import platform as host_platform
import plistlib
import re
import struct
import time
from pathlib import Path
from datetime import datetime

# ── Force UTF-8 output ───────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"

# ── Venv auto-relaunch ────────────────────────────────────────────────────────
_venv_py = (
    ROOT / "venv" / "Scripts" / "python.exe"
    if sys.platform == "win32"
    else ROOT / "venv" / "bin" / "python"
)
if _venv_py.exists() and Path(sys.executable).resolve() != _venv_py.resolve():
    print(f"[build] Re-launching with venv Python: {_venv_py}")
    sys.exit(subprocess.run([str(_venv_py)] + sys.argv).returncode)

# ── Platform ──────────────────────────────────────────────────────────────────
PLATFORM = {
    "win32": "Windows",
    "darwin": "macOS",
    "linux": "Linux",
}.get(sys.platform, sys.platform)

MAIN_SCRIPT = ROOT / "stet" / "main.py"
UPDATER_SCRIPT = ROOT / "stet" / "update.py"
INSTALLER_SCRIPT = ROOT / "stet" / "windows_installer_payload.py"
UNINSTALLER_SCRIPT = ROOT / "stet" / "uninstall.py"
ICON_ICO = ROOT / "logo.ico"
ICON_PNG = ROOT / "logo.png"
ICON_ICNS = ROOT / "logo.icns"
LICENSE_FILE = ROOT / "LICENSE"

# macOS release contract.  These values are intentionally kept here, next to
# the builder, so the app metadata, signing, archives, and standalone
# verifier cannot drift apart.
MACOS_BUNDLE_ID = "com.amrzriek.Stet"
# Fallback only for a deliberately compatibility-built backend such as b9000.
# Every real artifact derives its deployment target from the bundled backend's
# Mach-O metadata.  In particular, the current upstream b10068 Metal library
# requires macOS 26.0 and must not be advertised as a macOS 14 app.
MACOS_MINIMUM_SYSTEM_VERSION = "14.0"
MACOS_APP_NAME = "Stet.app"
MACOS_ARCHES = {"arm64", "x86_64"}
MACOS_FORBIDDEN_PAYLOAD_NAMES = {
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

# ── Version ───────────────────────────────────────────────────────────────────

def _get_version() -> str:
    """Read APP_VERSION from stet/constants.py (the canonical location)."""
    import re
    constants_file = ROOT / "stet" / "constants.py"
    try:
        text = constants_file.read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*[\'"]([0-9\.]+)[\'"]', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    fallback = datetime.now().strftime("%Y.%m.%d")
    print(f"[build] WARNING: Could not read APP_VERSION from {constants_file}, using fallback: {fallback}")
    return fallback


def _macos_host_arch() -> str:
    """Return the architecture of the running Python process.

    A native build is required.  In particular, an arm64 Mac running a shell
    under Rosetta reports x86_64 here and therefore cannot accidentally emit
    an artifact labelled arm64.
    """
    machine = host_platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    return machine


def _resolve_macos_arch(requested: str) -> str:
    """Resolve and validate a native macOS target architecture."""
    if PLATFORM != "macOS":
        raise RuntimeError("macOS packaging must run on macOS; cross-platform builds are not supported")
    host_arch = _macos_host_arch()
    target = host_arch if requested in ("auto", "") else requested
    if target not in MACOS_ARCHES:
        raise RuntimeError(f"Unsupported macOS architecture {target!r}; use arm64 or x86_64")
    if host_arch != target:
        raise RuntimeError(
            f"Refusing non-native macOS build: Python is {host_arch}, requested {target}. "
            "Run the build natively on the target Mac (do not use Rosetta)."
        )
    return target


def _macos_bundle_version(version: str) -> str:
    """Convert a release label to CFBundleVersion's numeric form."""
    import re
    numbers = [part for part in re.split(r"\D+", version) if part]
    return ".".join((numbers + ["0", "0", "0"])[:3])


def _write_macos_entitlements(artifacts_dir: Path) -> Path:
    """Write the minimal hardened-runtime entitlements plan.

    Stet is distributed outside the Mac App Store and does not need App
    Sandbox, JIT, unsigned executable memory, or task inspection.  The empty
    entitlement dictionary is deliberate; TCC Accessibility/Post Events
    consent is requested at runtime and is not granted by entitlements.
    In particular, get-task-allow must never be present in a release build.
    """
    path = artifacts_dir / "Stet.entitlements.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        plistlib.dump({}, stream, sort_keys=False)
    return path


def _generate_macos_icns(artifacts_dir: Path) -> Path:
    """Create a valid ICNS from logo.png using only macOS system tooling.

    PyInstaller delegates PNG-to-ICNS conversion to Pillow, which makes a
    normal macOS build unexpectedly depend on a development-only package.
    Instead, ``sips`` creates the PNG representations and this standard
    library writer packages their PNG bytes in Apple's documented ICNS
    container.  A checked-in logo.icns still takes precedence when present.
    """
    if ICON_ICNS.exists():
        return ICON_ICNS
    if not ICON_PNG.exists():
        raise RuntimeError(f"Missing macOS icon source: {ICON_PNG}")
    sips = shutil.which("sips")
    if not sips:
        raise RuntimeError("macOS system tool 'sips' is required to generate Stet.icns")

    icon_dir = artifacts_dir / "Stet.icon-inputs"
    if icon_dir.exists():
        _remove_tree(icon_dir)
    icon_dir.mkdir(parents=True, exist_ok=True)
    output = artifacts_dir / "Stet.icns"
    if output.exists():
        output.unlink()

    # Modern ICNS chunks embed PNG directly.  Include all standard sizes so
    # Finder has a crisp representation on both low- and high-density displays.
    chunks = (
        ("icp4", 16),
        ("icp5", 32),
        ("icp6", 64),
        ("ic07", 128),
        ("ic08", 256),
        ("ic09", 512),
        ("ic10", 1024),
    )
    payload = bytearray()
    for chunk_type, size in chunks:
        image = icon_dir / f"{size}.png"
        run([
            sips, "-s", "format", "png", "-z", str(size), str(size),
            str(ICON_PNG), "--out", str(image),
        ], stdout=subprocess.DEVNULL)
        data = image.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"sips did not produce a PNG icon representation: {image}")
        payload.extend(chunk_type.encode("ascii"))
        payload.extend(struct.pack(">I", len(data) + 8))
        payload.extend(data)
    output.write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    if output.read_bytes()[:4] != b"icns":
        raise RuntimeError(f"Failed to create a valid ICNS container at {output}")
    return output


def _write_macos_info_plist(
    app_bundle: Path,
    version: str,
    arch: str,
    minimum_macos: str,
    icon_name: str = "Stet.icns",
) -> Path:
    """Apply stable, notarization-compatible metadata to a PyInstaller app."""
    plist_path = app_bundle / "Contents" / "Info.plist"
    if not plist_path.exists():
        raise RuntimeError(f"PyInstaller did not create {plist_path}")
    with plist_path.open("rb") as stream:
        info = plistlib.load(stream)
    info.update({
        "CFBundleDisplayName": "Stet",
        "CFBundleName": "Stet",
        "CFBundleIdentifier": MACOS_BUNDLE_ID,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": _macos_bundle_version(version),
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": icon_name,
        "LSMinimumSystemVersion": minimum_macos,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": f"Copyright © {datetime.now().year} AmrZriek",
        # Custom release metadata consumed by the verifier and diagnostics.
        "StetArchitecture": arch,
        "StetBackendMode": "cpu" if arch == "x86_64" else "auto",
    })
    with plist_path.open("wb") as stream:
        plistlib.dump(info, stream, sort_keys=False)
    return plist_path


def _macos_minos(path: Path) -> str | None:
    """Read the deployment target embedded in a Mach-O file, when available."""

    try:
        result = subprocess.run(
            ["otool", "-l", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    match = re.search(r"\bminos\s+([0-9]+(?:\.[0-9]+)*)", result.stdout)
    return match.group(1) if match else None


def _macos_backend_minimum_version(server: Path | None) -> str:
    """Derive the app target from the actual runtime backend, not a constant."""

    if server is None:
        return MACOS_MINIMUM_SYSTEM_VERSION
    # The Metal dylib is the restrictive dependency on Apple Silicon.  For an
    # Intel CPU build, the CPU backend or server supplies its own target.
    candidates = [
        *sorted(server.parent.glob("libggml-metal*.dylib")),
        *sorted(server.parent.glob("libggml-cpu*.dylib")),
        server,
    ]
    versions = [version for candidate in candidates if (version := _macos_minos(candidate))]
    if not versions:
        return MACOS_MINIMUM_SYSTEM_VERSION
    return max(versions, key=lambda value: tuple(int(part) for part in value.split(".")))


def _llama_release_from_path(server: Path) -> int | None:
    """Extract the official bNNNNN release marker from an archive path."""

    for part in (server.name, *(parent.name for parent in server.parents)):
        match = re.search(r"(?:^|[-_])b(\d+)(?:[-_]|$)", part, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _windows_resource_version(version: str) -> str:
    """Convert release labels like 3.2.0-test to a Windows version tuple string."""
    import re
    parts = [p for p in re.split(r"\D+", version) if p]
    parts = (parts + ["0", "0", "0", "0"])[:4]
    return ".".join(parts)


def _generate_manifest_file(name: str, artifacts_dir: Path, admin: bool = False) -> Path:
    """Generate custom Windows manifest declaring compatibility with Win 10 & 11."""
    level = "requireAdministrator" if admin else "asInvoker"
    content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity
    version="1.0.0.0"
    processorArchitecture="amd64"
    name="{name}"
    type="win32"
  />
  <description>{name} application</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="{level}" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Windows 10 and Windows 11 -->
      <supportedOS Id="{{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}}"/>
      <!-- Windows 8.1 -->
      <supportedOS Id="{{1f676c76-80e1-4239-95bb-83d0f6d0da78}}"/>
      <!-- Windows 8 -->
      <supportedOS Id="{{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}}"/>
      <!-- Windows 7 -->
      <supportedOS Id="{{35138b9a-5d96-4fbd-8e2d-a2440225f93a}}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity
        type="win32"
        name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0"
        processorArchitecture="*"
        publicKeyToken="6595b64144ccf1df"
        language="*"
      />
    </dependentAssembly>
  </dependency>
</assembly>
"""
    path = artifacts_dir / f"{name}_manifest.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _generate_version_file(
    version: str,
    product_name: str,
    description: str,
    internal_name: str,
    artifacts_dir: Path,
) -> Path:
    """Generate a Windows VERSIONINFO file for PyInstaller --version-file."""
    parts = _windows_resource_version(version).split(".")
    major, minor, patch, build_num = (parts + ["0", "0", "0", "0"])[:4]
    ver_str = f"{major}.{minor}.{patch}.{build_num}"
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build_num}),
    prodvers=({major}, {minor}, {patch}, {build_num}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'Stet'),
            StringStruct('FileDescription', '{description}'),
            StringStruct('FileVersion', '{ver_str}'),
            StringStruct('InternalName', '{internal_name}'),
            StringStruct('OriginalFilename', '{internal_name}.exe'),
            StringStruct('ProductName', '{product_name}'),
            StringStruct('ProductVersion', '{ver_str}'),
            StringStruct('LegalCopyright', 'Copyright (C) {datetime.now().year} AmrZriek'),
            StringStruct('LegalTrademarks', 'GPLv3'),
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)"""
    path = artifacts_dir / f"{internal_name}_version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── Resolve llama-server directory ────────────────────────────────────────────

def _find_llama_dir() -> Path | None:
    """Locate the llama-server binary directory for bundling."""
    exe = "llama-server.exe" if PLATFORM == "Windows" else "llama-server"
    # A development checkout is self-contained.  Prefer its local runtime
    # over a stale config entry or an unrelated globally installed backend.
    # Current source runs keep the backend visibly at the checkout root.  The
    # hidden location is read only as a migration fallback for an older local
    # checkout; it must never win over the current project runtime.
    for project_backends in (ROOT / "backends", ROOT / ".stet-runtime" / "backends"):
        if not project_backends.is_dir():
            continue
        candidates = [
            directory for directory in project_backends.iterdir()
            if directory.is_dir() and (directory / exe).is_file()
        ]
        if candidates:
            return max(
                candidates,
                key=lambda directory: _llama_release_from_path(directory / exe) or -1,
            )
    cfg_file = ROOT / "config.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            sp = cfg.get("llama_server_path", "")
            if sp:
                d = Path(sp).parent
                if d.exists() and (d / exe).exists():
                    return d
        except Exception:
            pass
    for candidate in sorted(ROOT.iterdir()):
        if candidate.is_dir() and "llama" in candidate.name.lower():
            if (candidate / exe).exists():
                return candidate
    legacy = ROOT / "llama_cpp"
    if legacy.exists() and (legacy / exe).exists():
        return legacy
    return None


CUDA_DLLS = ["cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"]


def _find_cuda_dir() -> Path | None:
    """Locate CUDA runtime DLLs for GPU-accelerated llama.cpp (Windows only)."""
    if PLATFORM != "Windows":
        return None
    search = [
        Path(os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin")),
        Path(os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin")),
        Path(os.path.expandvars(r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.0\bin")),
        Path(os.path.expandvars(r"%APPDATA%")) / "AnythingLLM" / "resources" / "ollama" / "lib" / "ollama" / "cuda_v12",
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12")),
        Path(os.path.expandvars(r"%APPDATA%\Ollama\lib\ollama\cuda_v12")),
    ]
    llama = _find_llama_dir()
    if llama and llama.parent.is_dir():
        for d in sorted(llama.parent.iterdir()):
            if d.is_dir() and "cuda" in d.name.lower():
                search.append(d)
    for d in search:
        if d.exists() and all((d / dll).exists() for dll in CUDA_DLLS):
            return d
    return None


# ── MSVC detection ────────────────────────────────────────────────────────────

def _check_msvc_available() -> bool:
    """Check if MSVC (Visual Studio Build Tools) is installed."""
    if PLATFORM != "Windows":
        return False
    vswhere = (
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio"
        / "Installer"
        / "vswhere.exe"
    )
    if not vswhere.exists():
        return False
    try:
        result = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd: list, **kw):
    """Run a subprocess command with echo."""
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def banner(msg: str):
    print(f"\n{'─' * 64}")
    print(f"  {msg}")
    print(f"{'─' * 64}")


def _remove_tree(path: Path, retries: int = 8, delay: float = 1.0):
    """Remove a directory tree with retry for Windows file locks."""
    for attempt in range(retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
        except FileNotFoundError:
            return


def _sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── PyInstaller commands ─────────────────────────────────────────────────────────

def _base_pyinstaller_cmd(
    output_name: str,
    artifacts_dir: Path,
    *,
    version: str = "",
    mode: str = "onedir",
    console: str = "disable",
    product_name: str = "Stet",
    description: str = "Stet - AI Writing Assistant",
    admin: bool = False,
    macos_arch: str | None = None,
    macos_icon: Path | None = None,
    extra_flags: list[str] | None = None,
) -> list:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "-y",
        "--clean",
        "--noupx",
        f"--workpath={artifacts_dir / 'build'}",
        f"--distpath={artifacts_dir}",
        f"--specpath={artifacts_dir}",
        f"--name={output_name}",
    ]

    # Exclude unused modules to reduce surface area and trigger fewer heuristic AV warnings
    for mod in [
        "unittest", "test", "_testcapi", "_testinternalcapi",
        "tkinter", "_tkinter", "lib2to3", "pydoc", "doctest",
        "multiprocessing"
    ]:
        cmd.append(f"--exclude-module={mod}")

    if mode == "onefile":
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if console == "disable":
        cmd.append("--noconsole")
        cmd.append("--disable-windowed-traceback")
        if PLATFORM == "macOS":
            cmd.append("--windowed")
    else:
        cmd.append("--console")

    if PLATFORM == "Windows" and ICON_ICO.exists():
        cmd.append(f"--icon={ICON_ICO}")
    elif PLATFORM == "macOS":
        if not macos_icon:
            macos_icon = ICON_ICNS if ICON_ICNS.exists() else artifacts_dir / "Stet.icns"
        cmd.append(f"--icon={macos_icon}")

    if PLATFORM == "macOS":
        # Keep direct callers (including metadata tests and release tooling)
        # safe by selecting the native host architecture when omitted.  Do
        # not silently turn an explicit universal2 request into a thin build.
        macos_arch = _resolve_macos_arch(macos_arch or "auto")
        cmd.extend([
            f"--target-architecture={macos_arch}",
            f"--osx-bundle-identifier={MACOS_BUNDLE_ID}",
        ])
        entitlements = artifacts_dir / "Stet.entitlements.plist"
        if entitlements.exists():
            cmd.append(f"--osx-entitlements-file={entitlements}")

    # Embed VERSIONINFO, custom Manifest (reduces AV heuristics)
    if PLATFORM == "Windows":
        if version:
            ver_file = _generate_version_file(version, product_name, description, output_name, artifacts_dir)
            cmd.append(f"--version-file={ver_file}")
        manifest_file = _generate_manifest_file(output_name, artifacts_dir, admin=admin)
        cmd.append(f"--manifest={manifest_file}")

    if extra_flags:
        cmd.extend(extra_flags)

    return cmd


def _pyinstaller_cmd(
    version: str,
    artifacts_dir: Path,
    macos_arch: str | None = None,
    macos_icon: Path | None = None,
) -> list:
    extra = []
    sep = os.pathsep
    assets = ("logo.png",) if PLATFORM == "macOS" else ("logo.ico", "logo.png")
    for asset in assets:
        src = ROOT / asset
        if src.exists():
            extra.append(f"--add-data={src}{sep}.")

    data_flags = [f"--add-data={ROOT / 'stet'}{sep}stet"]
    if PLATFORM == "macOS":
        macos_icon = macos_icon or (ICON_ICNS if ICON_ICNS.exists() else artifacts_dir / "Stet.icns")
        # Never add the whole source package on macOS: that would copy the
        # Windows installer/updater payload into the signed application.
        data_flags = []
        for relative, destination in (
            (Path("stet") / "ui" / "stet.qss", "stet/ui"),
            (Path("stet") / "logo.svg", "stet"),
        ):
            source = ROOT / relative
            if source.exists():
                data_flags.append(f"--add-data={source}{sep}{destination}")

    cmd = _base_pyinstaller_cmd(
        "Stet", artifacts_dir, version=version,
        mode="onedir", console="disable",
        product_name="Stet", description="Stet - AI Writing Assistant",
        macos_arch=macos_arch,
        macos_icon=macos_icon,
        extra_flags=[
            *data_flags,
            "--hidden-import=PyQt6",
            "--hidden-import=requests",
            "--hidden-import=pyperclip",
            "--hidden-import=spellchecker",
            "--hidden-import=gguf",
            *extra,
        ],
    )
    cmd.append(str(MAIN_SCRIPT))
    return cmd


def _updater_pyinstaller_cmd(version: str, artifacts_dir: Path) -> list:
    cmd = _base_pyinstaller_cmd(
        "StetUpdater", artifacts_dir, version=version,
        mode="onefile", console="force",
        product_name="Stet Updater", description="Stet auto-updater utility",
    )
    cmd.append(str(UPDATER_SCRIPT))
    return cmd


def _installer_pyinstaller_cmd(version: str, artifacts_dir: Path, portable_zip: Path) -> list:
    sep = os.pathsep
    extra = [f"--add-data={portable_zip}{sep}.", "--hidden-import=stet.ui.downloader"]
    for asset in ("logo.ico", "logo.png"):
        src = ROOT / asset
        if src.exists():
            extra.append(f"--add-data={src}{sep}.")

    installer_base = f"StetSetup_v{version}"
    cmd = _base_pyinstaller_cmd(
        installer_base, artifacts_dir, version=version,
        mode="onefile", console="disable",
        product_name="Stet Setup",
        description="Stet desktop writing assistant installer",
        admin=True,
        extra_flags=extra,
    )
    cmd.append(str(INSTALLER_SCRIPT))
    return cmd


def _uninstaller_pyinstaller_cmd(version: str, artifacts_dir: Path) -> list:
    cmd = _base_pyinstaller_cmd(
        "StetUninstall", artifacts_dir, version=version,
        mode="onefile", console="disable",
        product_name="Stet Uninstaller",
        description="Stet uninstaller",
        admin=True,
    )
    cmd.append(str(UNINSTALLER_SCRIPT))
    return cmd

# ── Release config & launchers ───────────────────────────────────────────────

RELEASE_CONFIG = {
    # llama.cpp server (blank — auto-detected at runtime)
    "llama_server_path": "",
    "model_path": "",
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "context_size": 12800,
    "gpu_layers": 99,
    "mtp_enabled": True,
    "mtp_max_draft": 3,
    "mtp_min_draft": 0,
    "mtp_p_min": 0.75,
    # Sampling parameters
    "temperature": 0.0,
    "top_k": 1,
    "top_p": 0.95,
    "min_p": 0.0,
    "repeat_penalty": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    # Model lifecycle
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "recent_models": [],
    # Chat model (separate from autocorrect)
    "chat_model_path": "",
    "chat_use_separate_model": False,
    "chat_keep_loaded": False,
    "chat_idle_timeout_seconds": 60,
    "chat_mtp_enabled": True,
    "chat_mtp_max_draft": 3,
    "chat_mtp_min_draft": 0,
    "chat_mtp_p_min": 0.75,
    # Hotkeys
    "hotkeys": [
        {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
        {"shortcut": "f10", "mode": "silent", "strength": "spelling_only"},
        {"shortcut": "shift+f9", "mode": "panel", "strength": "rewrite_polish"},
    ],
    # Misc
    "system_prompt": "",
    "correction_method": "patch",
    "streaming_strength": "full_correction",
    "custom_templates": [],
    "chat_mode": "conversation",
    # correction_modes intentionally omitted — ConfigManager populates
    # the full correction_modes list from DEFAULT_CONFIG at runtime.
    # Including the multi-paragraph prompts here would bloat config.json
    # and create a maintenance sync burden.
}

RUN_BAT = "@echo off\ncd /d \"%~dp0\"\nStet.exe\n"

UNBLOCK_BAT = r"""@echo off
echo.
echo  ===================================================
echo   Stet - Unblocking downloaded files
echo   This removes the "Mark of the Web" that triggers
echo   Windows security warnings on downloaded scripts.
echo  ===================================================
echo.

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -Command "Get-ChildItem -LiteralPath $env:SCRIPT_DIR -Recurse | Unblock-File"
if errorlevel 1 (
    echo ERROR: Failed to unblock files. Try right-clicking this
    echo script and selecting "Run as administrator".
    pause
    exit /b 1
)

echo  Done! All files in this folder have been unblocked.
echo  You can now run download_model.bat and download_backend.bat
echo  without security warnings.
echo.
pause
"""
RUN_SH = "#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\n./Stet\n"

# ── llama.cpp backend auto-download ──────────────────────────────────────────
# The llama-server binaries + CUDA runtime are downloaded on first run instead
# of bundled in the installer (keeps installer under 120 MB to avoid AV flags).

_LLAMA_BACKEND_VERSION = "b10639"
_LLAMA_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{_LLAMA_BACKEND_VERSION}"

DOWNLOAD_BACKEND_BAT = rf"""@echo off
setlocal
cd /d "%~dp0"

set LLAMA_URL={_LLAMA_BASE}/llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64.zip
set CUDA_URL={_LLAMA_BASE}/cudart-llama-bin-win-cuda-12.4-x64.zip
set LLAMA_HASH=D2A9263AE118E514B6FC61329D5AB7A588A17D600B96DF07CB723C820151A22A
set CUDA_HASH=8C79A9B226DE4B3CACFD1F83D24F962D0773BE79F1E7B75C6AF4DED7E32AE1D6
set DEST=llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64

echo.
echo  ===================================================
echo   Stet - Downloading llama.cpp backend ({_LLAMA_BACKEND_VERSION})
echo   This is a one-time download (~652 MB).
echo  ===================================================
echo.

if not exist "%DEST%" mkdir "%DEST%"

echo [1/4] Downloading llama-server binaries (~261 MB)...
curl -L --progress-bar -o "%TEMP%\llama_backend.zip" "%LLAMA_URL%"
if errorlevel 1 (
    echo ERROR: Download failed. Check your internet connection.
    goto fail
)

echo [2/4] Downloading CUDA runtime DLLs (~391 MB)...
curl -L --progress-bar -o "%TEMP%\cuda_backend.zip" "%CUDA_URL%"
if errorlevel 1 (
    echo ERROR: Download failed. Check your internet connection.
    goto fail
)

echo [3/4] Verifying integrity (SHA-256)...
for /f "skip=1 delims=" %%i in ('certutil -hashfile "%TEMP%\llama_backend.zip" SHA256') do (
    set "ACTUAL=%%i"
    goto check_llama
)
:check_llama
set "ACTUAL=%ACTUAL: =%"
if /i not "%ACTUAL%"=="%LLAMA_HASH%" (
    echo ERROR: SHA-256 mismatch for llama ZIP!
    echo   Expected: %LLAMA_HASH%
    echo   Actual:   %ACTUAL%
    goto fail
)

for /f "skip=1 delims=" %%i in ('certutil -hashfile "%TEMP%\cuda_backend.zip" SHA256') do (
    set "ACTUAL=%%i"
    goto check_cuda
)
:check_cuda
set "ACTUAL=%ACTUAL: =%"
if /i not "%ACTUAL%"=="%CUDA_HASH%" (
    echo ERROR: SHA-256 mismatch for CUDA ZIP!
    echo   Expected: %CUDA_HASH%
    echo   Actual:   %ACTUAL%
    goto fail
)
echo    Integrity verified.

echo [4/4] Extracting...
powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\llama_backend.zip' -DestinationPath '%DEST%' -Force"
powershell -NoProfile -Command "Expand-Archive -Path '%TEMP%\cuda_backend.zip' -DestinationPath '%DEST%' -Force"
del "%TEMP%\llama_backend.zip" 2>nul
del "%TEMP%\cuda_backend.zip" 2>nul

echo.
echo  Done! llama.cpp backend installed to %DEST%\
echo  You can now launch Stet.
echo.
pause
exit /b 0

:fail
del "%TEMP%\llama_backend.zip" 2>nul
del "%TEMP%\cuda_backend.zip" 2>nul
echo.
echo  Download failed. Please download manually from:
echo    https://github.com/ggml-org/llama.cpp/releases/tag/{_LLAMA_BACKEND_VERSION}
echo.
pause
exit /b 1
"""

DOWNLOAD_BACKEND_SH = f"""#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

LLAMA_URL="{_LLAMA_BASE}/llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64.zip"
CUDA_URL="{_LLAMA_BASE}/cudart-llama-bin-win-cuda-12.4-x64.zip"
LLAMA_HASH="d2a9263ae118e514b6fc61329d5ab7a588a17d600b96df07cb723c820151a22a"
CUDA_HASH="8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"
DEST="llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64"

echo ""
echo "==================================================="
echo "  Stet - Downloading llama.cpp backend ({_LLAMA_BACKEND_VERSION})"
echo "  This is a one-time download (~652 MB)."
echo "==================================================="
echo ""

mkdir -p "$DEST"

echo "[1/4] Downloading llama-server binaries (~261 MB)..."
curl -L --progress-bar -o /tmp/llama_backend.zip "$LLAMA_URL"

echo "[2/4] Downloading CUDA runtime DLLs (~391 MB)..."
curl -L --progress-bar -o /tmp/cuda_backend.zip "$CUDA_URL"

echo "[3/4] Verifying integrity (SHA-256)..."
check_hash() {{
    local file="$1" expected="$2" label="$3"
    local actual
    if command -v sha256sum &>/dev/null; then
        actual=$(sha256sum "$file" | awk '{{print $1}}')
    elif command -v shasum &>/dev/null; then
        actual=$(shasum -a 256 "$file" | awk '{{print $1}}')
    else
        echo "WARNING: Cannot verify integrity (no sha256sum/shasum)."
        return 0
    fi
    actual=$(echo "$actual" | tr '[:upper:]' '[:lower:]')
    local exp_lower=$(echo "$expected" | tr '[:upper:]' '[:lower:]')
    if [ "$actual" != "$exp_lower" ]; then
        echo "ERROR: SHA-256 mismatch for $label!"
        echo "  Expected: $expected"
        echo "  Actual:   $actual"
        rm -f /tmp/llama_backend.zip /tmp/cuda_backend.zip
        exit 1
    fi
    echo "   $label integrity verified."
}}

check_hash /tmp/llama_backend.zip "$LLAMA_HASH" "llama"
check_hash /tmp/cuda_backend.zip "$CUDA_HASH" "CUDA"

echo "[4/4] Extracting..."
unzip -o /tmp/llama_backend.zip -d "$DEST"
unzip -o /tmp/cuda_backend.zip -d "$DEST"
rm -f /tmp/llama_backend.zip /tmp/cuda_backend.zip

echo ""
echo "Done! llama.cpp backend installed to $DEST/"
echo "You can now launch Stet."
"""

_RECOMMENDED_MODEL_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-UD-Q4_K_XL.gguf"
_RECOMMENDED_MODEL_FILE = "gemma-4-E2B-it-UD-Q4_K_XL.gguf"
_RECOMMENDED_MODEL_HASH = "b52f438017efaec5debf1c0d8be690571e212a07c312f1102bbce927258cfc32"

_RECOMMENDED_MTP_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mtp-gemma-4-E2B-it.gguf"
_RECOMMENDED_MTP_FILE = "mtp-gemma-4-E2B-it.gguf"
_RECOMMENDED_MTP_HASH = "9eba819938efccfd6044f8af84e3bbfddc639a2bcf32ebc36420e6a649191919"

DOWNLOAD_SH = f"""#!/usr/bin/env bash
set -e

MODEL_URL="{_RECOMMENDED_MODEL_URL}"
DEST="{_RECOMMENDED_MODEL_FILE}"
EXPECTED_HASH="{_RECOMMENDED_MODEL_HASH}"

MTP_URL="{_RECOMMENDED_MTP_URL}"
MTP_DEST="{_RECOMMENDED_MTP_FILE}"
MTP_EXPECTED_HASH="{_RECOMMENDED_MTP_HASH}"

download_and_verify() {{
    local url="$1"
    local dest="$2"
    local expected="$3"

    echo "Downloading $dest ..."
    if command -v curl &>/dev/null; then
        curl -L --progress-bar -o "$dest.tmp" "$url"
    elif command -v wget &>/dev/null; then
        wget -O "$dest.tmp" "$url"
    else
        echo "Error: neither curl nor wget found."; exit 1
    fi

    echo "Verifying $dest integrity (SHA-256)..."
    if command -v sha256sum &>/dev/null; then
        ACTUAL_HASH=$(sha256sum "$dest.tmp" | awk '{{print $1}}')
    elif command -v shasum &>/dev/null; then
        ACTUAL_HASH=$(shasum -a 256 "$dest.tmp" | awk '{{print $1}}')
    else
        echo "WARNING: sha256sum or shasum not found. Skipping integrity check."
        mv "$dest.tmp" "$dest"
        return 0
    fi

    ACTUAL_LOWER=$(echo "$ACTUAL_HASH" | tr '[:upper:]' '[:lower:]')
    EXPECTED_LOWER=$(echo "$expected" | tr '[:upper:]' '[:lower:]')

    if [ "$ACTUAL_LOWER" = "$EXPECTED_LOWER" ]; then
        echo "Integrity verification successful for $dest!"
        mv "$dest.tmp" "$dest"
    else
        echo "WARNING: SHA-256 mismatch for $dest!"
        echo "Expected: $expected"
        echo "Actual:   $ACTUAL_HASH"
        rm -f "$dest.tmp"
        exit 1
    fi
}}

echo "=== Stet Model Downloader (Gemma 4 + MTP) ==="
download_and_verify "$MODEL_URL" "$DEST" "$EXPECTED_HASH"
download_and_verify "$MTP_URL" "$MTP_DEST" "$MTP_EXPECTED_HASH"

echo ""
echo "Done. Open Settings and set Model Path to: $(pwd)/$DEST"
"""

DOWNLOAD_BAT = rf"""@echo off
set MODEL_URL={_RECOMMENDED_MODEL_URL}
set DEST={_RECOMMENDED_MODEL_FILE}
set EXPECTED_HASH={_RECOMMENDED_MODEL_HASH}

set MTP_URL={_RECOMMENDED_MTP_URL}
set MTP_DEST={_RECOMMENDED_MTP_FILE}
set MTP_EXPECTED_HASH={_RECOMMENDED_MTP_HASH}

echo ===================================================
echo   Downloading Stet Recommended AI Model + MTP Draft
echo ===================================================
echo.

echo [1/2] Downloading %DEST% ...
curl -L --progress-bar -o "%DEST%.tmp" "%MODEL_URL%"
if errorlevel 1 (
    echo Error: Failed to download %DEST%
    del "%DEST%.tmp" 2>nul
    goto fail
)

echo Verifying %DEST% integrity (SHA-256)...
for /f "skip=1 delims=" %%i in ('certutil -hashfile "%DEST%.tmp" SHA256') do (
    set ACTUAL_HASH=%%i
    goto check_base
)
:check_base
set ACTUAL_HASH=%ACTUAL_HASH: =%
if /i not "%ACTUAL_HASH%"=="%EXPECTED_HASH%" (
    echo WARNING: SHA-256 mismatch for %DEST%!
    echo Expected: %EXPECTED_HASH%
    echo Actual:   %ACTUAL_HASH%
    del "%DEST%.tmp" 2>nul
    goto fail
)
move /y "%DEST%.tmp" "%DEST%" >nul
echo %DEST% verified successfully.
echo.

echo [2/2] Downloading %MTP_DEST% ...
curl -L --progress-bar -o "%MTP_DEST%.tmp" "%MTP_URL%"
if errorlevel 1 (
    echo Error: Failed to download %MTP_DEST%
    del "%MTP_DEST%.tmp" 2>nul
    goto fail
)

echo Verifying %MTP_DEST% integrity (SHA-256)...
for /f "skip=1 delims=" %%i in ('certutil -hashfile "%MTP_DEST%.tmp" SHA256') do (
    set MTP_ACTUAL_HASH=%%i
    goto check_mtp
)
:check_mtp
set MTP_ACTUAL_HASH=%MTP_ACTUAL_HASH: =%
if /i not "%MTP_ACTUAL_HASH%"=="%MTP_EXPECTED_HASH%" (
    echo WARNING: SHA-256 mismatch for %MTP_DEST%!
    echo Expected: %MTP_EXPECTED_HASH%
    echo Actual:   %MTP_ACTUAL_HASH%
    del "%MTP_DEST%.tmp" 2>nul
    goto fail
)
move /y "%MTP_DEST%.tmp" "%MTP_DEST%" >nul
echo %MTP_DEST% verified successfully.
echo.

echo Done! Both base model and MTP draft model downloaded and verified.
echo Open Settings in Stet and set Model Path to: %DEST%
goto end

:fail
echo.
echo Model download failed or verification error. Please try again.
:end
pause
"""


# ── macOS bundle validation/signing ──────────────────────────────────────────

def _macho_arches(path: Path) -> set[str]:
    """Return Mach-O architectures reported by the host toolchain."""
    try:
        result = subprocess.run(
            ["lipo", "-archs", str(path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return set(result.stdout.split())
    except (FileNotFoundError, OSError):
        pass
    try:
        result = subprocess.run(
            ["file", "-b", str(path)],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return set()
    output = result.stdout.lower()
    return {arch for arch in MACOS_ARCHES if arch in output}


def _macho_files(root: Path) -> list[Path]:
    """Find Mach-O files without treating ordinary resources as code."""
    return [
        path for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())
        if _macho_arches(path)
    ]


def _is_framework_alias(path: Path) -> bool:
    """Return whether a Mach-O path is a duplicated framework entry.

    Some PyQt wheels ship framework roots and ``Versions/Current`` as copied
    files rather than symlinks.  codesign rejects those entries (and the
    enclosing framework) as ambiguous.  The canonical signed code is under
    ``Versions/<actual-version>/``.
    """
    for parent in path.parents:
        if parent.suffix.lower() != ".framework":
            continue
        relative = path.relative_to(parent).parts
        return (
            len(relative) < 3
            or relative[0] != "Versions"
            or relative[1] == "Current"
        )
    return False


def _signable_macho_files(root: Path) -> list[Path]:
    """Return canonical native code paths suitable for codesign."""
    app_executables = root / "Contents" / "MacOS"
    return [
        path for path in _macho_files(root)
        if not _is_framework_alias(path) and path.parent != app_executables
    ]


def _macos_forbidden_payloads(root: Path) -> list[Path]:
    forbidden = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        lower_name = path.name.lower()
        if lower_name in MACOS_FORBIDDEN_PAYLOAD_NAMES:
            forbidden.append(path)
        elif path.suffix.lower() in {".bat", ".cmd", ".ps1", ".vbs", ".dll", ".exe"}:
            forbidden.append(path)
    return forbidden


def _validate_macos_bundle(
    app_bundle: Path,
    arch: str,
    minimum_macos: str,
    require_signed: bool = False,
):
    """Validate metadata, payload policy, and exact native architecture."""
    if PLATFORM != "macOS":
        raise RuntimeError("macOS bundle validation must run on macOS")
    if not app_bundle.is_dir() or app_bundle.suffix != ".app":
        raise RuntimeError(f"Expected a macOS .app bundle at {app_bundle}")
    plist_path = app_bundle / "Contents" / "Info.plist"
    if not plist_path.exists():
        raise RuntimeError(f"Missing app Info.plist: {plist_path}")
    with plist_path.open("rb") as stream:
        info = plistlib.load(stream)
    expected = {
        "CFBundleIdentifier": MACOS_BUNDLE_ID,
        "LSMinimumSystemVersion": minimum_macos,
        "StetArchitecture": arch,
    }
    for key, value in expected.items():
        if info.get(key) != value:
            raise RuntimeError(f"Info.plist {key} must be {value!r}, got {info.get(key)!r}")

    forbidden = _macos_forbidden_payloads(app_bundle)
    if forbidden:
        names = ", ".join(str(p.relative_to(app_bundle)) for p in forbidden[:10])
        raise RuntimeError(f"Windows payload found in macOS app: {names}")

    binaries = _macho_files(app_bundle)
    if not binaries:
        raise RuntimeError("No Mach-O code found in macOS app bundle")
    mismatched = []
    for binary in binaries:
        actual = _macho_arches(binary)
        if actual != {arch}:
            mismatched.append(f"{binary.relative_to(app_bundle)}={sorted(actual) or ['unknown']}")
    if mismatched:
        raise RuntimeError(
            f"macOS app contains non-native or universal code for {arch}: "
            + ", ".join(mismatched[:10])
        )

    if require_signed:
        run(["codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app_bundle)])
        entitlements = subprocess.run(
            ["codesign", "-d", "--entitlements", ":-", str(app_bundle)],
            capture_output=True, text=True, check=False,
        )
        if "com.apple.security.get-task-allow" in entitlements.stdout + entitlements.stderr:
            raise RuntimeError("Release app must not contain com.apple.security.get-task-allow")


# ── Builder ──────────────────────────────────────────────────────────────────

class PlatformBuilder:
    """Orchestrates the complete build pipeline for the current platform."""

    def __init__(
        self,
        version: str,
        keep_folder: bool = False,
        skip_installer: bool = False,
        macos_arch: str = "auto",
        sign_identity: str | None = None,
        notarize: bool = False,
        allow_missing_macos_backend: bool = False,
        dry_run: bool = False,
    ):
        self.version = version
        self.keep_folder = keep_folder
        self.skip_installer = skip_installer
        self.dry_run = dry_run
        self.notarize = notarize
        self.allow_missing_macos_backend = allow_missing_macos_backend
        self.macos_arch = _resolve_macos_arch(macos_arch) if PLATFORM == "macOS" else None
        self.sign_identity = sign_identity or os.environ.get("STET_MACOS_SIGN_IDENTITY", "-")
        self.release_name = f"Stet_{version}_{PLATFORM}"
        self.release_dir = DIST / self.release_name
        self.portable_dir = self.release_dir / "stet_portable"
        self.artifacts_dir = self.release_dir / "build_artifacts"
        self.llama_dir = _find_llama_dir()
        self.cuda_dir = _find_cuda_dir()
        self.macos_backend = self._resolve_macos_backend() if PLATFORM == "macOS" else None
        self.macos_minimum_system_version = (
            _macos_backend_minimum_version(self.macos_backend)
            if PLATFORM == "macOS"
            else None
        )
        self.macos_zip = None
        self.macos_dmg = None

    def _resolve_macos_backend(self) -> Path | None:
        """Resolve an already-built native llama-server; never download one."""
        configured = os.environ.get("STET_MACOS_BACKEND", "").strip()
        candidates = [Path(configured)] if configured else []
        if self.llama_dir:
            candidates.append(self.llama_dir / "llama-server")
        found = shutil.which("llama-server")
        if found:
            candidates.append(Path(found))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve()
        return None

    def _step(self, name: str) -> str:
        if PLATFORM == "macOS":
            numbers = {"app": 1, "extras": 2, "launchers": 3, "package": 4, "checksums": 5}
            return f"Step {numbers[name]} / 5"
        numbers = {"app": 1, "updater": 2, "extras": 3, "launchers": 4, "package": 5, "checksums": self._total_steps()}
        return f"Step {numbers[name]} / {self._total_steps()}"

    def clean(self):
        if self.release_dir.exists():
            print(f"  Removing old {self.release_dir.name}…")
            _remove_tree(self.release_dir)
        self.release_dir.mkdir(parents=True, exist_ok=True)
        self.portable_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Compile main app ─────────────────────────────────────────

    def build_app(self):
        banner(f"{self._step('app')} — Compile Stet (PyInstaller → native binary)")
        macos_icon = None
        if PLATFORM == "macOS":
            _write_macos_entitlements(self.artifacts_dir)
            macos_icon = _generate_macos_icns(self.artifacts_dir)
            print(f"  Generated macOS app icon: {macos_icon.name}")
        cmd = _pyinstaller_cmd(self.version, self.artifacts_dir, self.macos_arch, macos_icon)
        pyinstaller_env = None
        if PLATFORM == "macOS":
            # Keep PyInstaller's cache inside this release's intermediate
            # directory.  Apart from being reproducible, this avoids --clean
            # touching a user-wide Application Support cache.
            pyinstaller_env = os.environ.copy()
            pyinstaller_env["PYINSTALLER_CONFIG_DIR"] = str(self.artifacts_dir / "pyinstaller-cache")
        run(cmd, env=pyinstaller_env)

        if PLATFORM == "macOS":
            app_bundle = self.artifacts_dir / "Stet.app"
            if not app_bundle.exists():
                raise RuntimeError(f"PyInstaller output not found at {app_bundle}")
            destination = self.portable_dir / MACOS_APP_NAME
            # PyInstaller's macOS bundle relies on framework and cross-tree
            # symlinks.  Dereferencing them creates duplicated framework
            # binaries that codesign rejects as structurally ambiguous.
            shutil.copytree(app_bundle, destination, dirs_exist_ok=True, symlinks=True)
            resources = destination / "Contents" / "Resources"
            resources.mkdir(parents=True, exist_ok=True)
            shutil.copy2(macos_icon, resources / "Stet.icns")
            _write_macos_info_plist(
                destination,
                self.version,
                self.macos_arch,
                self.macos_minimum_system_version,
            )
            self._bundle_macos_backend(destination)
            self._verify_macos_app(destination, allow_unsigned=True)
        else:
            dist_dir = self.artifacts_dir / "Stet"
            if not dist_dir.exists():
                print(f"ERROR: PyInstaller output not found at {dist_dir}")
                sys.exit(1)
            shutil.copytree(dist_dir, self.portable_dir, dirs_exist_ok=True)

        print(f"  Copied compiled app to {self.portable_dir.name}/")

    # ── Step 2: Compile updater ──────────────────────────────────────────

    def build_updater(self):
        if PLATFORM == "macOS":
            print("  Skipping Windows file-overlay updater on macOS (signed app updates belong to Sparkle).")
            return
        banner(f"{self._step('updater')} — Compile StetUpdater (onefile)")
        cmd = _updater_pyinstaller_cmd(self.version, self.artifacts_dir)
        run(cmd)
        updater_name = "StetUpdater.exe" if PLATFORM == "Windows" else "StetUpdater"
        updater_exe = self.artifacts_dir / updater_name
        if not updater_exe.exists():
            print(f"ERROR: StetUpdater output not found at {updater_exe}")
            sys.exit(1)
        shutil.copy2(updater_exe, self.portable_dir / updater_name)
        print(f"  Copied updater: {updater_name}")

    # ── Step 2.5: Compile uninstaller (Windows only) ─────────────────────

    def build_uninstaller(self):
        if PLATFORM != "Windows":
            return
        if not UNINSTALLER_SCRIPT.exists():
            print("  Skipping uninstaller (uninstall.py not found)")
            return
        total = self._total_steps()
        banner(f"Step 2.5 / {total} — Compile StetUninstall (onefile)")
        cmd = _uninstaller_pyinstaller_cmd(self.version, self.artifacts_dir)
        run(cmd)
        exe_name = "StetUninstall.exe"
        exe_path = self.artifacts_dir / exe_name
        if not exe_path.exists():
            print(f"  WARNING: {exe_name} not found at {exe_path}")
            return
        shutil.copy2(exe_path, self.portable_dir / exe_name)
        print(f"  Copied uninstaller: {exe_name}")

    # ── Step 3: Copy extras ──────────────────────────────────────────────

    def build_extras(self):
        banner(f"{self._step('extras')} — Copy extras (config, assets)")

        # NOTE: The llama-server backend is no longer bundled in the portable
        # directory.  It is downloaded at first run via download_backend scripts
        # to keep the installer under 120 MB (avoids Windows Defender ML flags).

        # Release config
        (self.portable_dir / "config.json").write_text(
            json.dumps(RELEASE_CONFIG, indent=2), encoding="utf-8"
        )
        print("  Created config.json")

        # Version file
        (self.portable_dir / "VERSION").write_text(self.version, encoding="utf-8")

        # License and README
        if LICENSE_FILE.exists():
            shutil.copy(LICENSE_FILE, self.portable_dir / "LICENSE")
        readme = ROOT / "README.md"
        if readme.exists():
            shutil.copy(readme, self.portable_dir / "README.md")

        # Root-level icons (safety net — also included via --include-data-files).
        # The Windows ICO is not copied into a macOS release directory.
        assets = ("logo.png",) if PLATFORM == "macOS" else ("logo.png", "logo.ico")
        for asset in assets:
            src = ROOT / asset
            if src.exists():
                shutil.copy2(src, self.portable_dir / asset)

        # QSS stylesheet (safety net — also included via --include-package-data)
        qss_src = ROOT / "stet" / "ui" / "stet.qss"
        if qss_src.exists():
            qss_dst_dir = self.portable_dir / "stet" / "ui"
            qss_dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(qss_src, qss_dst_dir / "stet.qss")
            print("  Copied stet/ui/stet.qss (safety net)")

        # SVG logo
        svg_src = ROOT / "stet" / "logo.svg"
        if svg_src.exists():
            svg_dst_dir = self.portable_dir / "stet"
            svg_dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(svg_src, svg_dst_dir / "logo.svg")

        # Windows startup script.  It is intentionally never copied to the
        # macOS release directory or app bundle.
        if PLATFORM != "macOS":
            startup_vbs = ROOT / "startup.vbs"
            if startup_vbs.exists():
                shutil.copy2(startup_vbs, self.portable_dir / "startup.vbs")
                print("  Copied startup.vbs")

    # ── Step 4: Launcher scripts ─────────────────────────────────────────

    def build_launchers(self):
        banner(f"{self._step('launchers')} — Create launcher & download scripts")
        if PLATFORM == "Windows":
            (self.portable_dir / "run.bat").write_text(RUN_BAT, encoding="utf-8")
            (self.portable_dir / "download_model.bat").write_text(DOWNLOAD_BAT, encoding="utf-8")
            (self.portable_dir / "download_backend.bat").write_text(DOWNLOAD_BACKEND_BAT, encoding="utf-8")
            (self.portable_dir / "Unblock_Stet.bat").write_text(UNBLOCK_BAT, encoding="utf-8")
            print("  Created run.bat, download_model.bat, download_backend.bat, Unblock_Stet.bat")
        elif PLATFORM == "macOS":
            run_sh = """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
open "$SCRIPT_DIR/Stet.app"
"""
            p = self.portable_dir / "run.sh"
            p.write_text(run_sh, encoding="utf-8")
            p.chmod(0o755)
            print("  Created run.sh (opens Stet.app)")
        else:
            for name, content in [
                ("run.sh", RUN_SH),
                ("download_model.sh", DOWNLOAD_SH),
                ("download_backend.sh", DOWNLOAD_BACKEND_SH),
            ]:
                p = self.portable_dir / name
                p.write_text(content, encoding="utf-8")
                p.chmod(0o755)
            print("  Created run.sh, download_model.sh, download_backend.sh")

    # ── Step 5: Package portable ZIP ─────────────────────────────────────

    def package(self):
        if PLATFORM == "macOS":
            self._package_macos()
            return
        banner(f"{self._step('package')} — Package portable ZIP")
        zip_path = DIST / "stet_portable.zip"
        print("  Creating stet_portable.zip from portable directory...")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in sorted(self.portable_dir.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(self.portable_dir)
                    # Exclude the llama-server backend directory — it is
                    # downloaded at first run via download_backend scripts.
                    parts = rel.parts
                    if parts and "llama" in parts[0].lower():
                        continue
                    zf.write(f, rel)
        size_mb = zip_path.stat().st_size / 1_048_576
        print(f"  Created: dist/stet_portable.zip  ({size_mb:.1f} MB)")
        self._portable_zip = zip_path

    def _bundle_macos_backend(self, app_bundle: Path):
        """Copy the server and all adjacent runtime dylibs into Resources."""
        if self.macos_backend is None:
            if self.allow_missing_macos_backend:
                print("  WARNING: no native llama-server supplied; release is not runtime-complete")
                return
            raise RuntimeError(
                "No native macOS llama-server found. Set STET_MACOS_BACKEND to an executable "
                "built for the target architecture."
            )
        actual = _macho_arches(self.macos_backend)
        if actual != {self.macos_arch}:
            raise RuntimeError(
                f"Backend architecture mismatch: {self.macos_backend} is "
                f"{sorted(actual) or ['unknown']}, expected {self.macos_arch}"
            )
        destination_dir = app_bundle / "Contents" / "Resources" / "backend" / self.macos_arch
        destination = destination_dir / "llama-server"
        destination_dir.mkdir(parents=True, exist_ok=True)
        # llama-server uses @loader_path to locate its shared libraries.  Copy
        # every adjacent dylib, including versioned files and their symlinks,
        # rather than a lone executable that would only work while the source
        # backend remained installed elsewhere on the developer's machine.
        runtime_files = [self.macos_backend, *sorted(self.macos_backend.parent.glob("*.dylib"))]
        copied_names: set[str] = set()
        for source in runtime_files:
            if source.name in copied_names:
                continue
            copied_names.add(source.name)
            shutil.copy2(source, destination_dir / source.name, follow_symlinks=False)
        destination.chmod(destination.stat().st_mode | 0o111)
        if not any(destination_dir.glob("libggml*.dylib")):
            raise RuntimeError(
                f"No llama.cpp runtime dylibs were found beside {self.macos_backend}; "
                "supply the server from an official complete release archive."
            )
        manifest = app_bundle / "Contents" / "Resources" / "backend-manifest.json"
        manifest.write_text(json.dumps({
            "architecture": self.macos_arch,
            "backend_mode": "cpu" if self.macos_arch == "x86_64" else "auto",
            "minimum_macos": self.macos_minimum_system_version,
            "release": _llama_release_from_path(self.macos_backend),
            "server": "llama-server",
            "source": str(self.macos_backend),
            "sha256": _sha256(destination),
        }, indent=2) + "\n", encoding="utf-8")
        print(
            f"  Bundled native llama-server plus {len(copied_names) - 1} runtime dylibs "
            f"({self.macos_arch}, macOS {self.macos_minimum_system_version}+)"
        )

    def _verify_macos_app(self, app_bundle: Path, allow_unsigned: bool = False):
        verifier = ROOT / "scripts" / "macos" / "verify_bundle.py"
        if verifier.exists():
            cmd = [
                sys.executable, str(verifier), str(app_bundle), "--arch", self.macos_arch,
                "--minimum-macos", self.macos_minimum_system_version,
            ]
            if allow_unsigned:
                cmd.append("--allow-unsigned")
            if self.allow_missing_macos_backend:
                cmd.append("--allow-missing-backend")
            run(cmd)
        else:
            _validate_macos_bundle(
                app_bundle,
                self.macos_arch,
                self.macos_minimum_system_version,
                require_signed=not allow_unsigned,
            )

    def _sign_macos_path(self, path: Path, entitlements: Path):
        args = ["codesign", "--force"]
        # Library validation from the hardened runtime requires all nested
        # code to carry one Developer ID team.  Ad-hoc signatures have no
        # team, so enabling the runtime on a local build makes macOS reject
        # its bundled libpython at launch.  Production Developer ID builds
        # keep the hardened runtime (and timestamp) as required for
        # notarization.
        if self.sign_identity != "-":
            args.extend(["--options", "runtime", "--timestamp"])
        args.extend(["--sign", self.sign_identity, "--entitlements", str(entitlements), str(path)])
        run(args)

    def _sign_macos_app(self, app_bundle: Path):
        """Sign nested Mach-O code inside-out, then sign the outer app."""
        if self.sign_identity == "-":
            print("  Signing with ad-hoc identity for local use; set STET_MACOS_SIGN_IDENTITY for release notarization.")
        else:
            print(f"  Signing with Developer ID identity: {self.sign_identity}")
        entitlements = _write_macos_entitlements(self.artifacts_dir)
        for binary in sorted(_signable_macho_files(app_bundle), key=lambda p: len(p.parts), reverse=True):
            self._sign_macos_path(binary, entitlements)
        nested_bundles = [
            path for path in app_bundle.rglob("*")
            if (
                path.is_dir()
                and not path.is_symlink()
                and path.suffix.lower() in {".bundle", ".xpc"}
            )
        ]
        for bundle in sorted(nested_bundles, key=lambda p: len(p.parts), reverse=True):
            self._sign_macos_path(bundle, entitlements)
        self._sign_macos_path(app_bundle, entitlements)

    def _package_macos(self):
        banner(f"{self._step('package')} — Sign and package macOS {self.macos_arch} release")
        app_bundle = self.portable_dir / MACOS_APP_NAME
        self._verify_macos_app(app_bundle, allow_unsigned=True)
        self._sign_macos_app(app_bundle)
        self._verify_macos_app(app_bundle)

        self.macos_zip = DIST / f"Stet-{self.version}-macos-{self.macos_arch}.zip"
        run(["ditto", "-c", "-k", "--keepParent", str(app_bundle), str(self.macos_zip)])
        print(f"  Created: {self.macos_zip.name}")

        staging = self.artifacts_dir / "dmg-staging"
        if staging.exists():
            _remove_tree(staging)
        staging.mkdir(parents=True)
        shutil.copytree(app_bundle, staging / MACOS_APP_NAME, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        self.macos_dmg = DIST / f"Stet-{self.version}-macos-{self.macos_arch}.dmg"
        run([
            "hdiutil", "create", "-volname", f"Stet {self.version}",
            "-srcfolder", str(staging), "-format", "UDZO", "-ov", str(self.macos_dmg),
        ])
        print(f"  Created: {self.macos_dmg.name}")

        if self.notarize:
            self._notarize_macos_dmg()

    def _notarize_macos_dmg(self):
        if self.sign_identity == "-":
            raise RuntimeError("--notarize requires a Developer ID signing identity")
        apple_id = os.environ.get("APPLE_ID", "")
        team_id = os.environ.get("APPLE_TEAM_ID", "")
        password = os.environ.get("APPLE_APP_PASSWORD", "")
        if not all((apple_id, team_id, password)):
            raise RuntimeError("Notarization requires APPLE_ID, APPLE_TEAM_ID, and APPLE_APP_PASSWORD")
        run([
            "xcrun", "notarytool", "submit", str(self.macos_dmg),
            "--apple-id", apple_id, "--team-id", team_id, "--password", password, "--wait",
        ])
        run(["xcrun", "stapler", "staple", str(self.macos_dmg)])
        run(["xcrun", "stapler", "validate", str(self.macos_dmg)])
        run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", str(self.macos_dmg)])

    # ── Step 6: Self-contained installer (Windows only) ──────────────────

    def build_installer(self):
        if PLATFORM != "Windows" or self.skip_installer:
            return

        installer_filename = f"StetSetup_v{self.version}.exe"
        output_base_filename = f"StetSetup_v{self.version}"

        total = self._total_steps()
        banner(f"Step 6 / {total} — Compile self-contained {installer_filename}")

        # Check if Inno Setup compiler (ISCC) is available
        iscc = shutil.which("ISCC.exe") or shutil.which("iscc")
        if not iscc:
            # Fallback to standard installation paths
            paths = [
                Path(os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe")),
                Path(os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe")),
                Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")),
            ]
            for p in paths:
                if p.exists():
                    iscc = str(p)
                    break

        if iscc:
            print("  ✓ Inno Setup compiler detected — compiling native installer...")
            # Generate setup.iss dynamically in the artifacts directory
            icon_line = f'SetupIconFile="{ICON_ICO.resolve()}"' if ICON_ICO.exists() else ""
            iss_content = f"""; Dynamic setup.iss generated at build time
[Setup]
AppName=Stet
AppVersion={self.version}
AppPublisher=Amr Zriek
AppPublisherURL=https://github.com/AmrZriek/Stet
DefaultDirName={{autopf}}\\Stet
DefaultGroupName=Stet
UninstallDisplayIcon={{app}}\\Stet.exe
Compression=lzma2/max
SolidCompression=yes
OutputDir="{DIST.resolve()}"
OutputBaseFilename={output_base_filename}
{icon_line}
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "{self.portable_dir.resolve()}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\Stet"; Filename: "{{app}}\\Stet.exe"
Name: "{{autodesktop}}\\Stet"; Filename: "{{app}}\\Stet.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional tasks:"

[Run]
Filename: "{{app}}\\Stet.exe"; Description: "Launch Stet"; Flags: postinstall nowait
"""
            iss_path = self.artifacts_dir / "setup.iss"
            iss_path.write_text(iss_content, encoding="utf-8")
            
            try:
                # Run the Inno Setup compiler
                run([iscc, str(iss_path)])
                print(f"  ✓ Native Inno Setup installer compiled successfully: dist/{installer_filename}")
                return
            except subprocess.CalledProcessError as e:
                print(f"  WARNING: Inno Setup compilation failed (exit {e.returncode}) — falling back to PyInstaller...")
            except Exception as e:
                print(f"  WARNING: Inno Setup compiler error ({e}) — falling back to PyInstaller...")
            finally:
                # Delete temporary setup.iss to keep things clean
                if iss_path.exists():
                    iss_path.unlink()

        # Fallback to PyInstaller onefile installer script if ISCC is not installed or failed
        if not INSTALLER_SCRIPT.exists():
            print("  Skipping installer (windows_installer_payload.py not found)")
            return

        zip_path = getattr(self, "_portable_zip", DIST / "stet_portable.zip")
        if not zip_path.exists():
            print("  ERROR: stet_portable.zip not found — cannot build installer")
            return

        cmd = _installer_pyinstaller_cmd(self.version, self.artifacts_dir, zip_path)
        try:
            run(cmd)
        except subprocess.CalledProcessError as e:
            print(f"  WARNING: Installer build failed (exit {e.returncode})")
            print("  The portable ZIP is still available.")
            return

        installer_exe = self.artifacts_dir / installer_filename
        if not installer_exe.exists():
            # Check for legacy StetSetup.exe if PyInstaller output name varied
            fallback_exe = self.artifacts_dir / "StetSetup.exe"
            if fallback_exe.exists():
                installer_exe = fallback_exe

        if installer_exe.exists():
            final_path = DIST / installer_filename
            shutil.copy2(installer_exe, final_path)
            size_mb = final_path.stat().st_size / 1_048_576
            print(f"  Created: dist/{installer_filename} (PyInstaller fallback)  ({size_mb:.1f} MB)")
        else:
            print(f"  WARNING: {installer_filename} not found in build output")

    # ── Step 7: SHA-256 checksums ────────────────────────────────────────

    def generate_checksums(self):
        banner(f"{self._step('checksums')} — Generate SHA-256 checksums")
        checksum_lines = []
        for f in sorted(DIST.iterdir()):
            if f.is_file() and (
                f.suffix in (".zip", ".exe")
                if PLATFORM != "macOS"
                else f in {self.macos_zip, self.macos_dmg}
            ):
                h = _sha256(f)
                checksum_lines.append(f"{h}  {f.name}")
                print(f"  {h}  {f.name}")
        if checksum_lines:
            (DIST / "SHA256SUMS.txt").write_text(
                "\n".join(checksum_lines) + "\n", encoding="utf-8"
            )
            print("  Wrote SHA256SUMS.txt")

    # ── Finish ───────────────────────────────────────────────────────────

    def finish(self):
        if self.keep_folder:
            print(f"\n  Keeping {self.release_dir.name}/ (--keep-folder)")
        elif self.release_dir.exists():
            print(f"\n  Cleaning up {self.release_dir.name}/...")
            _remove_tree(self.release_dir)

        banner("Build complete!  Final deliverables in dist/:")
        for f in sorted(DIST.iterdir()):
            if f.is_file():
                size_mb = f.stat().st_size / 1_048_576
                print(f"  {f.name}  ({size_mb:.1f} MB)")
            elif f.is_dir():
                print(f"  {f.name}/  (directory)")

    # ── Orchestrator ─────────────────────────────────────────────────────

    def _total_steps(self) -> int:
        """Total build steps for the current platform."""
        if PLATFORM == "macOS":
            return 5
        steps = 5  # app + updater + extras + launchers + zip
        if PLATFORM == "Windows":
            if UNINSTALLER_SCRIPT.exists():
                steps += 1  # uninstaller
            skip_installer = getattr(self, "skip_installer", False)
            if not skip_installer and INSTALLER_SCRIPT.exists():
                steps += 1  # installer
        steps += 1  # checksums (always last)
        return steps

    def run(self):
        banner(f"Stet build  v{self.version}  [{PLATFORM}]")

        if self.dry_run:
            if PLATFORM == "macOS":
                print(f"  Target architecture: {self.macos_arch} (native {_macos_host_arch()})")
                print(f"  Expected ZIP: Stet-{self.version}-macos-{self.macos_arch}.zip")
                print(f"  Expected DMG: Stet-{self.version}-macos-{self.macos_arch}.dmg")
                print(f"  Signing identity: {self.sign_identity}")
                print(f"  Native backend: {self.macos_backend or 'not found (required for a real release)'}")
                print(f"  Minimum macOS: {self.macos_minimum_system_version}")
            else:
                print("  Dry-run is informational on non-macOS platforms; no files were changed.")
            return

        # Pre-flight checks
        if PLATFORM == "Windows":
            if _check_msvc_available():
                print("  ✓ MSVC detected — builds will use Visual Studio compiler")
            else:
                print("  ✗ MSVC not found — builds will use MinGW (may trigger antivirus)")
        if self.llama_dir:
            print(f"  ✓ llama-server found: {self.llama_dir.name}")
        else:
            print("  ⚠ llama-server not found — empty placeholder will be created")
        if PLATFORM == "macOS":
            if self.macos_backend:
                print(f"  ✓ native macOS backend found: {self.macos_backend}")
            else:
                print("  ✗ native macOS backend not found — real macOS releases require STET_MACOS_BACKEND")
        if self.cuda_dir:
            print(f"  ✓ CUDA DLLs found: {self.cuda_dir}")

        self.clean()
        self.build_app()
        self.build_updater()
        self.build_uninstaller()
        self.build_extras()
        self.build_launchers()
        self.package()
        self.build_installer()
        self.generate_checksums()
        self.finish()


# ── CLI ──────────────────────────────────────────────────────────────────────

def build(
    version: str,
    keep_folder: bool = False,
    skip_installer: bool = False,
    macos_arch: str = "auto",
    sign_identity: str | None = None,
    notarize: bool = False,
    allow_missing_macos_backend: bool = False,
    dry_run: bool = False,
):
    builder = PlatformBuilder(
        version,
        keep_folder,
        skip_installer,
        macos_arch,
        sign_identity,
        notarize,
        allow_missing_macos_backend,
        dry_run,
    )
    builder.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Stet release")
    parser.add_argument("--version", default=_get_version(), help="Version tag (default: from constants.py)")
    parser.add_argument("--keep-folder", action="store_true",
                        help="Keep intermediate dist/<release>/ folder for debugging")
    parser.add_argument("--skip-installer", action="store_true",
                        help="Skip building the self-contained StetSetup.exe installer")
    parser.add_argument("--macos-arch", choices=("auto", "arm64", "x86_64"), default="auto",
                        help="Native macOS target architecture (default: auto; macOS only)")
    parser.add_argument("--sign-identity", default=None,
                        help="macOS codesign identity (default: STET_MACOS_SIGN_IDENTITY or ad hoc '-')")
    parser.add_argument("--notarize", action="store_true",
                        help="Submit the signed macOS DMG to Apple and staple the ticket")
    parser.add_argument("--allow-missing-macos-backend", action="store_true",
                        help="Development-only: allow a macOS app without bundled llama-server")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the selected macOS release contract without building")
    args = parser.parse_args()
    build(
        args.version,
        args.keep_folder,
        args.skip_installer,
        args.macos_arch,
        args.sign_identity,
        args.notarize,
        args.allow_missing_macos_backend,
        args.dry_run,
    )
