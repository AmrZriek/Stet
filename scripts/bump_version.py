"""Bump Stet's version in one shot (all canonical spots, nothing else).

Usage:
    python scripts/bump_version.py            # 1.2.0 -> 1.2.1 (patch bump)
    python scripts/bump_version.py 1.3.0      # explicit target
    python scripts/bump_version.py --dry-run  # show what would change

Why this exists: version strings appear in THREE kinds of places.
1. Canonical (must bump): stet/constants.py APP_VERSION, README badge + release URLs.
2. Historical (must NOT bump): docs/SESSION_LOG.md, docs/BACKEND_UPDATE_GUIDE.md
   entries describing past releases. A blind grep+replace corrupts those.
This script touches only the whitelist below.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (file, [(old_pattern, replacement_template)]) — ordered, first match wins per file.
TARGETS: list[tuple[Path, list[tuple[str, str]]]] = [
    (
        ROOT / "stet" / "constants.py",
        [
            (r'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', 'APP_VERSION = "{new}"'),
        ],
    ),
    (
        ROOT / "README.md",
        [
            (r"Latest release: v([0-9]+\.[0-9]+\.[0-9]+)", "Latest release: v{new}"),
            (r"releases/tag/v([0-9]+\.[0-9]+\.[0-9]+)", "releases/tag/v{new}"),
        ],
    ),
]


def current_version() -> str:
    text = (ROOT / "stet" / "constants.py").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', text)
    if not m:
        sys.exit("ERROR: could not read APP_VERSION from stet/constants.py")
    return m.group(1)


def next_patch(version: str) -> str:
    major, minor, patch = (int(p) for p in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def apply(file: Path, rules: list[tuple[str, str]], new: str, dry_run: bool) -> tuple[int, int]:
    text = file.read_text(encoding="utf-8")
    count = 0
    for pattern, template in rules:
        text, n = re.subn(pattern, template.format(new=new), text)
        count += n
    if count == 0:
        return 0, 0
    if not dry_run:
        file.write_text(text, encoding="utf-8")
    return count, len(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="explicit target version (default: next patch)")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = parser.parse_args()

    old = current_version()
    new = args.version if args.version else next_patch(old)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", new):
        sys.exit(f"ERROR: invalid version {new!r} (expected X.Y.Z)")

    total = 0
    for file, rules in TARGETS:
        count, _ = apply(file, rules, new, dry_run=args.dry_run)
        total += count
        if count:
            print(f"  {file.relative_to(ROOT)}: {count} replacement(s) -> v{new}")
    print(f"SUMMARY: {total} replacement(s), {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
