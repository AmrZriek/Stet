#!/usr/bin/env bash
# build_mac.command — Automated macOS builder for non-technical users
set -e

# Set working directory to the folder containing this script
cd "$(dirname "$0")"

clear
echo "=================================================================="
echo "          STET AUTOMATED MACOS RELEASE BUILDER                    "
echo "=================================================================="
echo "Hi! Thank you so much for helping me compile Stet for macOS!"
echo "This script will handle all the setup and build steps automatically."
echo ""
echo "If macOS displays a popup asking to install:"
echo "   'Command Line Developer Tools' or 'Xcode'"
echo "Please click 'Install', wait for it to complete, and then"
echo "re-run this script."
echo "=================================================================="
echo ""

# 1. Check for Python 3
if ! command -v python3 &>/dev/null; then
    echo "Python 3 is not installed."
    echo "Attempting to trigger the macOS developer tools installer..."
    echo "Please click 'Install' on the popup window that appears."
    python3 --version || true
    echo ""
    echo "After the installation finishes, please run this script again!"
    echo "Press Enter to exit..."
    read
    exit 1
fi

echo "==> Python 3 detected."
echo "==> Creating temporary setup environment (this takes a moment)..."

# 2. Set up virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate

echo "==> Installing compilation libraries..."
pip install --upgrade pip
pip install -r requirements.txt
pip install nuitka

# 3. Execute the build
echo "==> Compiling Stet into a native Mac application..."
python3 build.py

# 4. Locate and present the final ZIP
echo ""
echo "=================================================================="
echo "                    BUILD COMPLETE!                               "
echo "=================================================================="

ZIP_FILE=$(find dist -maxdepth 1 -name "stet_portable.zip" | head -n 1)

if [ -n "$ZIP_FILE" ]; then
    # Copy the zip next to the script and rename to Stet_macOS.zip for clarity
    cp "$ZIP_FILE" ./Stet_macOS.zip
    FINAL_ZIP="Stet_macOS.zip"
    echo "Success! You are amazing!"
    echo "Please email/send me this file:"
    echo "    $FINAL_ZIP"
    echo "which has been created right next to this script!"
    echo "=================================================================="
    # Open Finder showing the file
    open -R "$FINAL_ZIP"
else
    echo "The build finished, but we couldn't find stet_portable.zip in dist/."
    echo "Please check if there were errors above."
    echo "=================================================================="
fi

echo "Press Enter to close this window..."
read
