#!/usr/bin/env bash
# build_mac.command — USB-portable macOS release builder
# ======================================================
# Hand this repo on a USB stick to someone with a Mac.  They double-click
# this file (or run `./build_mac.command` in Terminal) and get a DMG back.
#
# What you need on the Mac:
#   • macOS 14 or newer
#   • Xcode Command Line Tools (the script will prompt to install if missing)
#   • Python 3.12+ (Homebrew `brew install python` works)
#   • Internet (first run only — pip downloads dependencies)
#
# The resulting DMG does NOT bundle llama-server unless you provide one:
#   STET_MACOS_BACKEND=/path/to/llama-server ./build_mac.command
# Without it the app auto-downloads the backend on first launch.
set -euo pipefail

cd "$(dirname "$0")"

echo "=================================================================="
echo "             STET — NATIVE MACOS RELEASE BUILDER                  "
echo "=================================================================="
echo ""

# ── Step 0: Restore execute bits (USB/ZIP strips them) ───────────────
echo "==> Restoring file permissions..."
chmod +x build_mac.command run_mac.command 2>/dev/null || true
find scripts/macos -name '*.py' -exec chmod +x {} + 2>/dev/null || true

# ── Step 1: Xcode Command Line Tools ────────────────────────────────
echo "==> Checking for Xcode Command Line Tools..."
if ! xcode-select -p >/dev/null 2>&1; then
    echo "    Xcode Command Line Tools are not installed."
    echo "    Installing now (you may see a system dialog)..."
    xcode-select --install
    echo ""
    echo "    After the install finishes, run this script again."
    exit 1
fi
echo "    ✓ Found: $(xcode-select -p)"

# Verify the specific tools we need
for tool in hdiutil codesign sips ditto; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "    ✗ Required tool '$tool' is missing. Reinstall Xcode Command Line Tools:"
        echo "      sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install"
        exit 1
    fi
done
echo "    ✓ hdiutil, codesign, sips, ditto all present"

# ── Step 2: Python 3.12+ ────────────────────────────────────────────
echo "==> Checking Python version..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "    ✗ Python 3 is not installed."
    echo "    Install it with:  brew install python"
    echo "    Or download from: https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MAJOR="$(python3 -c 'import sys; print(sys.version_info.major)')"
PYTHON_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"

if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 12 ]]; }; then
    echo "    ✗ Python $PYTHON_VERSION found, but Stet requires Python 3.12 or newer."
    echo "    Install it with:  brew install python@3.13"
    echo "    Or download from: https://www.python.org/downloads/"
    exit 1
fi
echo "    ✓ Python $PYTHON_VERSION"

# ── Step 3: Virtual environment & dependencies ──────────────────────
echo "==> Creating/refreshing the local Python build environment..."
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo "    ✓ All dependencies installed"

# ── Step 4: Build arguments ─────────────────────────────────────────
ARCH="${STET_MACOS_ARCH:-auto}"
ARGS=(--macos-arch "$ARCH" --allow-missing-macos-backend)

if [[ -n "${STET_MACOS_SIGN_IDENTITY:-}" ]]; then
    ARGS+=(--sign-identity "$STET_MACOS_SIGN_IDENTITY")
else
    echo "==> No signing identity supplied; output will be ad-hoc signed."
    echo "    For distribution, set STET_MACOS_SIGN_IDENTITY to a Developer ID identity."
fi

if [[ -n "${STET_MACOS_BACKEND:-}" ]]; then
    echo "==> Using native llama-server: $STET_MACOS_BACKEND"
else
    echo "==> No llama-server provided (STET_MACOS_BACKEND not set)."
    echo "    The app will auto-download the backend on first launch."
    echo "    To bundle one: STET_MACOS_BACKEND=/path/to/llama-server ./build_mac.command"
fi

if [[ -n "${STET_VERSION:-}" ]]; then
    ARGS+=(--version "$STET_VERSION")
    echo "==> Version override: $STET_VERSION"
fi

if [[ "${STET_MACOS_NOTARIZE:-0}" == "1" ]]; then
    ARGS+=(--notarize)
fi

# ── Step 5: Build ───────────────────────────────────────────────────
echo ""
echo "==> Building Stet.app and architecture-specific ZIP/DMG..."
echo "    The app icon is generated as Stet.icns with macOS system tools; Pillow is not required."
python build.py "${ARGS[@]}"

# ── Done ────────────────────────────────────────────────────────────
echo ""
echo "=================================================================="
echo "                    BUILD COMPLETE                                "
echo "=================================================================="
echo ""
echo "Deliverables:"
find dist -maxdepth 1 -type f \( -name "Stet-*-macos-*.zip" -o -name "Stet-*-macos-*.dmg" -o -name "SHA256SUMS.txt" \) -print | sort | while read -r f; do
    SIZE=$(du -h "$f" | cut -f1)
    echo "  $SIZE  $(basename "$f")"
done

echo ""
echo "To give someone the installer: copy the .dmg file from the dist/ folder."

if [[ "${STET_OPEN_ARTIFACTS:-0}" == "1" ]]; then
    DMG="$(find dist -maxdepth 1 -type f -name "Stet-*-macos-*.dmg" | sort | tail -n 1)"
    if [[ -n "$DMG" ]]; then
        open -R "$DMG"
    fi
fi
