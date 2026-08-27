"""Repo-wide pytest hooks.

Cleanup: pytest imports create __pycache__ directories across stet/, tests/,
scripts/ (and anything else Python touches). They were previously left behind
after every run. This hook removes them when the session ends, pruning
non-source trees (venv, archives, build output) entirely.
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Never descend into these trees (not ours to clean).
_PRUNE = {
    "venv",
    ".venv",
    ".git",
    "archive",
    "dist",
    "build",
    "node_modules",
    "backends",
    "Models",
    "downloads",
    "graphify-out",
    ".ruff_cache",
    ".pytest_cache",
}


def pytest_sessionfinish(session, exitstatus):
    # Frozen builds never run pytest; belt-and-braces guard anyway.
    if getattr(sys, "frozen", False):
        return
    for dirpath, dirnames, _filenames in os.walk(ROOT):
        for name in list(dirnames):
            if name == "__pycache__":
                shutil.rmtree(Path(dirpath) / name, ignore_errors=True)
                dirnames.remove(name)
            elif name in _PRUNE:
                dirnames.remove(name)
