#!/bin/zsh
# run_mac.command — run Stet from this checkout without compiling Stet.app.
#
# Double-click in Finder or run `./run_mac.command` from Terminal.  The script
# keeps its Python environment in ./venv and installs dependencies only when
# requirements.txt changes.
set -euo pipefail

cd "$(dirname "$0")"

if [[ -x "venv/bin/python" ]]; then
    PYTHON="venv/bin/python"
else
    for candidate in "${STET_PYTHON:-}" python3.12 python3; do
        [[ -n "$candidate" ]] || continue
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    done

    if [[ -z "${PYTHON:-}" ]]; then
        echo "Stet needs Python 3.12 or newer to run from source."
        echo "Install Python 3.12 (for example from python.org), then run this again."
        exit 1
    fi

    "$PYTHON" -m venv venv
    PYTHON="venv/bin/python"
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    echo "Stet needs Python 3.12 or newer. Set STET_PYTHON to a Python 3.12 path,"
    echo "remove the old venv directory, and run this script again."
    exit 1
fi

REQUIREMENTS_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
STAMP="venv/.stet-requirements.sha256"
if [[ ! -f "$STAMP" || "$(<"$STAMP")" != "$REQUIREMENTS_HASH" ]]; then
    echo "==> Installing Stet's local macOS dependencies..."
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r requirements.txt
    print -r -- "$REQUIREMENTS_HASH" > "$STAMP"
fi

echo "==> Starting Stet from source (no app compilation)..."
exec "$PYTHON" -m stet.main "$@"
