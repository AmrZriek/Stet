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
    python build.py --skip-installer    # skip StetSetup.exe (Windows only)

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
LICENSE_FILE = ROOT / "LICENSE"

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


def _windows_resource_version(version: str) -> str:
    """Convert release labels like 3.2.0-test to a Windows version tuple string."""
    import re
    parts = [p for p in re.split(r"\D+", version) if p]
    parts = (parts + ["0", "0", "0", "0"])[:4]
    return ".".join(parts)


# ── Resolve llama-server directory ────────────────────────────────────────────

def _find_llama_dir() -> Path | None:
    """Locate the llama-server binary directory for bundling."""
    exe = "llama-server.exe" if PLATFORM == "Windows" else "llama-server"
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
    if llama:
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
    extra_flags: list[str] | None = None,
) -> list:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "-y",
        "--clean",
        f"--workpath={artifacts_dir / 'build'}",
        f"--distpath={artifacts_dir}",
        f"--name={output_name}",
    ]

    if mode == "onefile":
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if console == "disable":
        cmd.append("--noconsole")
        if PLATFORM == "macOS":
            cmd.append("--windowed")
    else:
        cmd.append("--console")

    if PLATFORM == "Windows" and ICON_ICO.exists():
        cmd.append(f"--icon={ICON_ICO}")
    elif PLATFORM == "macOS" and ICON_PNG.exists():
        cmd.append(f"--icon={ICON_PNG}")

    if extra_flags:
        cmd.extend(extra_flags)

    return cmd


def _pyinstaller_cmd(version: str, artifacts_dir: Path) -> list:
    extra = []
    sep = os.pathsep
    for asset in ("logo.ico", "logo.png"):
        src = ROOT / asset
        if src.exists():
            extra.append(f"--add-data={src}{sep}.")

    cmd = _base_pyinstaller_cmd(
        "Stet", artifacts_dir, version=version,
        mode="onedir", console="disable",
        product_name="Stet", description="Stet - AI Writing Assistant",
        extra_flags=[
            f"--add-data={ROOT / 'stet'}{sep}stet",
            "--hidden-import=PyQt6",
            "--hidden-import=requests",
            "--hidden-import=pyperclip",
            "--hidden-import=spellchecker",
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
    extra = [f"--add-data={portable_zip}{sep}."]
    for asset in ("logo.ico", "logo.png"):
        src = ROOT / asset
        if src.exists():
            extra.append(f"--add-data={src}{sep}.")

    cmd = _base_pyinstaller_cmd(
        "StetSetup", artifacts_dir, version=version,
        mode="onefile", console="disable",
        product_name="Stet Setup",
        description="Stet desktop writing assistant installer",
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
    # Sampling parameters
    "temperature": 0.1,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
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
RUN_SH = "#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\n./Stet\n"

# ── llama.cpp backend auto-download ──────────────────────────────────────────
# The llama-server binaries + CUDA runtime are downloaded on first run instead
# of bundled in the installer (keeps installer under 120 MB to avoid AV flags).

_LLAMA_BACKEND_VERSION = "b9577"
_LLAMA_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{_LLAMA_BACKEND_VERSION}"

DOWNLOAD_BACKEND_BAT = rf"""@echo off
setlocal
cd /d "%~dp0"

set LLAMA_URL={_LLAMA_BASE}/llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64.zip
set CUDA_URL={_LLAMA_BASE}/cudart-llama-bin-win-cuda-12.4-x64.zip
set LLAMA_HASH=49A7FFB9E68A6306A2CB0A7284D1565049CD978C3B130EAA1D2197E471F4F5D2
set CUDA_HASH=8C79A9B226DE4B3CACFD1F83D24F962D0773BE79F1E7B75C6AF4DED7E32AE1D6
set DEST=llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64

echo.
echo  ===================================================
echo   Stet - Downloading llama.cpp backend (b9577)
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
LLAMA_HASH="49a7ffb9e68a6306a2cb0a7284d1565049cd978c3b130eaa1d2197e471f4f5d2"
CUDA_HASH="8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"
DEST="llama-{_LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64"

echo ""
echo "==================================================="
echo "  Stet - Downloading llama.cpp backend (b9577)"
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
_RECOMMENDED_MODEL_HASH = "b8906b8c5e05e57b657646bbc657bd35814a269b2c20f0a2579047fafa1a67dd"

DOWNLOAD_SH = f"""#!/usr/bin/env bash
MODEL_URL="{_RECOMMENDED_MODEL_URL}"
DEST="{_RECOMMENDED_MODEL_FILE}"
EXPECTED_HASH="{_RECOMMENDED_MODEL_HASH}"
echo "Downloading $DEST ..."
if command -v curl &>/dev/null; then curl -L --progress-bar -o "$DEST" "$MODEL_URL"
elif command -v wget &>/dev/null; then wget -O "$DEST" "$MODEL_URL"
else echo "Error: neither curl nor wget found."; exit 1; fi

echo "Verifying integrity (SHA-256)..."
if command -v sha256sum &>/dev/null; then
    ACTUAL_HASH=$(sha256sum "$DEST" | awk '{{print $1}}')
elif command -v shasum &>/dev/null; then
    ACTUAL_HASH=$(shasum -a 256 "$DEST" | awk '{{print $1}}')
else
    echo "WARNING: sha256sum or shasum not found. Skipping integrity check."
    echo "Done. Open Settings and set Model Path."
    exit 0
fi

# Convert both to lowercase for comparison
ACTUAL_LOWER=$(echo "$ACTUAL_HASH" | tr '[:upper:]' '[:lower:]')
EXPECTED_LOWER=$(echo "$EXPECTED_HASH" | tr '[:upper:]' '[:lower:]')

if [ "$ACTUAL_LOWER" = "$EXPECTED_LOWER" ]; then
    echo "Integrity verification successful!"
    echo "Done. Open Settings and set Model Path to: $(pwd)/$DEST"
else
    echo "WARNING: SHA-256 mismatch!"
    echo "Expected: $EXPECTED_HASH"
    echo "Actual:   $ACTUAL_HASH"
    rm "$DEST"
    exit 1
fi
"""

DOWNLOAD_BAT = rf"""@echo off
set MODEL_URL={_RECOMMENDED_MODEL_URL}
set DEST={_RECOMMENDED_MODEL_FILE}
set EXPECTED_HASH={_RECOMMENDED_MODEL_HASH}
echo Downloading %DEST% ...
curl -L --progress-bar -o "%DEST%" "%MODEL_URL%"
if errorlevel 1 (
    echo Download failed.
    goto end
)
echo Verifying integrity (SHA-256)...
for /f "skip=1 delims=" %%i in ('certutil -hashfile "%DEST%" SHA256') do (
    set ACTUAL_HASH=%%i
    goto check
)
:check
set ACTUAL_HASH=%ACTUAL_HASH: =%
if /i "%ACTUAL_HASH%"=="%EXPECTED_HASH%" (
    echo Integrity verification successful!
    echo Done. Open Settings and set Model Path.
) else (
    echo WARNING: SHA-256 mismatch! File might be corrupted or tampered with.
    echo Expected: %EXPECTED_HASH%
    echo Actual:   %ACTUAL_HASH%
    del "%DEST%"
)
:end
pause
"""


# ── Builder ──────────────────────────────────────────────────────────────────

class PlatformBuilder:
    """Orchestrates the complete build pipeline for the current platform."""

    def __init__(self, version: str, keep_folder: bool = False, skip_installer: bool = False):
        self.version = version
        self.keep_folder = keep_folder
        self.skip_installer = skip_installer
        self.release_name = f"Stet_{version}_{PLATFORM}"
        self.release_dir = DIST / self.release_name
        self.portable_dir = self.release_dir / "stet_portable"
        self.artifacts_dir = self.release_dir / "build_artifacts"
        self.llama_dir = _find_llama_dir()
        self.cuda_dir = _find_cuda_dir()

    def clean(self):
        if self.release_dir.exists():
            print(f"  Removing old {self.release_dir.name}…")
            _remove_tree(self.release_dir)
        self.release_dir.mkdir(parents=True, exist_ok=True)
        self.portable_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Compile main app ─────────────────────────────────────────

    def build_app(self):
        total = self._total_steps()
        banner(f"Step 1 / {total} — Compile Stet (PyInstaller → native binary)")
        cmd = _pyinstaller_cmd(self.version, self.artifacts_dir)
        run(cmd)

        if PLATFORM == "macOS":
            app_bundle = self.artifacts_dir / "Stet.app"
            if app_bundle.exists():
                shutil.copytree(app_bundle, self.portable_dir / "Stet.app", dirs_exist_ok=True)
            else:
                dist_dir = self.artifacts_dir / "Stet"
                if dist_dir.exists():
                    shutil.copytree(dist_dir, self.portable_dir, dirs_exist_ok=True)
                else:
                    print("ERROR: PyInstaller output not found.")
                    sys.exit(1)
        else:
            dist_dir = self.artifacts_dir / "Stet"
            if not dist_dir.exists():
                print(f"ERROR: PyInstaller output not found at {dist_dir}")
                sys.exit(1)
            shutil.copytree(dist_dir, self.portable_dir, dirs_exist_ok=True)

        print(f"  Copied compiled app to {self.portable_dir.name}/")

    # ── Step 2: Compile updater ──────────────────────────────────────────

    def build_updater(self):
        total = self._total_steps()
        banner(f"Step 2 / {total} — Compile StetUpdater (onefile)")
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
        total = self._total_steps()
        banner(f"Step 3 / {total} — Copy extras (config, assets)")

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

        # Root-level icons (safety net — also included via --include-data-files)
        for asset in ("logo.png", "logo.ico"):
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

        # Windows startup script
        startup_vbs = ROOT / "startup.vbs"
        if startup_vbs.exists():
            shutil.copy2(startup_vbs, self.portable_dir / "startup.vbs")
            print("  Copied startup.vbs")

    # ── Step 4: Launcher scripts ─────────────────────────────────────────

    def build_launchers(self):
        total = self._total_steps()
        banner(f"Step 4 / {total} — Create launcher & download scripts")
        if PLATFORM == "Windows":
            (self.portable_dir / "run.bat").write_text(RUN_BAT, encoding="utf-8")
            (self.portable_dir / "download_model.bat").write_text(DOWNLOAD_BAT, encoding="utf-8")
            (self.portable_dir / "download_backend.bat").write_text(DOWNLOAD_BACKEND_BAT, encoding="utf-8")
            print("  Created run.bat, download_model.bat, download_backend.bat")
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
        total = self._total_steps()
        banner(f"Step 5 / {total} — Package portable ZIP")
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

    # ── Step 6: Self-contained installer (Windows only) ──────────────────

    def build_installer(self):
        if PLATFORM != "Windows" or self.skip_installer:
            return
        if not INSTALLER_SCRIPT.exists():
            print("  Skipping installer (windows_installer_payload.py not found)")
            return

        total = self._total_steps()
        banner(f"Step 6 / {total} — Compile self-contained StetSetup.exe")

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

        installer_exe = self.artifacts_dir / "StetSetup.exe"
        if installer_exe.exists():
            final_path = DIST / "StetSetup.exe"
            shutil.copy2(installer_exe, final_path)
            size_mb = final_path.stat().st_size / 1_048_576
            print(f"  Created: dist/StetSetup.exe  ({size_mb:.1f} MB)")
        else:
            print("  WARNING: StetSetup.exe not found in build output")

    # ── Step 7: SHA-256 checksums ────────────────────────────────────────

    def generate_checksums(self):
        total = self._total_steps()
        step = total  # Always the last step
        banner(f"Step {step} / {total} — Generate SHA-256 checksums")
        checksum_lines = []
        for f in sorted(DIST.iterdir()):
            if f.is_file() and f.suffix in (".zip", ".exe"):
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

def build(version: str, keep_folder: bool = False, skip_installer: bool = False):
    builder = PlatformBuilder(version, keep_folder, skip_installer)
    builder.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Stet release")
    parser.add_argument("--version", default=_get_version(), help="Version tag (default: from constants.py)")
    parser.add_argument("--keep-folder", action="store_true",
                        help="Keep intermediate dist/<release>/ folder for debugging")
    parser.add_argument("--skip-installer", action="store_true",
                        help="Skip building the self-contained StetSetup.exe installer")
    args = parser.parse_args()
    build(args.version, args.keep_folder, args.skip_installer)
