#!/usr/bin/env bash
# build_mac.command — native, signed, notarization-ready macOS release builder
set -euo pipefail

cd "$(dirname "$0")"

echo "=================================================================="
echo "             STET NATIVE MACOS RELEASE BUILDER                   "
echo "=================================================================="
echo "This builds one native architecture per Mac: arm64 by default;"
echo "x86_64 is CPU-only and must be built on an Intel Mac natively."
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is not installed. Install it, then run this script again."
    exit 1
fi
if ! command -v sips >/dev/null 2>&1; then
    echo "The macOS system image tool 'sips' is unavailable. Reinstall Command Line Tools, then retry."
    exit 1
fi

echo "==> Creating/refreshing the local Python build environment..."
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

ARCH="${STET_MACOS_ARCH:-auto}"
ARGS=(--macos-arch "$ARCH")

if [[ -n "${STET_MACOS_SIGN_IDENTITY:-}" ]]; then
    ARGS+=(--sign-identity "$STET_MACOS_SIGN_IDENTITY")
else
    echo "==> No signing identity supplied; local output will be ad-hoc signed."
    echo "    For distribution, set STET_MACOS_SIGN_IDENTITY to a Developer ID identity."
fi

if [[ -n "${STET_MACOS_BACKEND:-}" ]]; then
    echo "==> Using native llama-server: $STET_MACOS_BACKEND"
else
    echo "==> Searching for an executable native llama-server..."
    echo "    Set STET_MACOS_BACKEND explicitly if it is outside the project."
fi
VERSION="${STET_VERSION:-$(python -c 'from build import _get_version; print(_get_version())')}"
ARGS+=(--version "$VERSION")

if [[ "${STET_MACOS_NOTARIZE:-0}" == "1" ]]; then
    ARGS+=(--notarize)
fi

echo "==> Building Stet.app and architecture-specific ZIP/DMG..."
echo "    The app icon is generated as Stet.icns with macOS system tools; Pillow is not required."
python build.py "${ARGS[@]}"

echo ""
echo "=================================================================="
echo "                    BUILD COMPLETE                               "
echo "=================================================================="
find dist -maxdepth 1 -type f \( -name "Stet-*-macos-*.zip" -o -name "Stet-*-macos-*.dmg" -o -name "SHA256SUMS.txt" \) -print | sort

if [[ "${STET_OPEN_ARTIFACTS:-0}" == "1" ]]; then
    open -R "$(find dist -maxdepth 1 -type f -name "Stet-*-macos-*.dmg" | sort | tail -n 1)"
fi
