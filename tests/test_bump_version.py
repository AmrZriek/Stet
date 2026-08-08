"""Coverage for scripts/bump_version.py: canonical version spots update, historical docs stay untouched."""

import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bump_version.py"


def _make_tree(tmp_path: Path) -> Path:
    """Scratch repo with the canonical files + an untouchable historical doc."""
    root = tmp_path / "repo"
    (root / "stet").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "stet" / "constants.py").write_text(
        'APP_VERSION = "1.2.0"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        "<strong>Latest release: v1.2.0</strong> "
        '<a href="https://github.com/AmrZriek/Stet/releases/tag/v1.2.0">Download</a>\n'
        "https://github.com/AmrZriek/Stet/releases/tag/v1.2.0\n",
        encoding="utf-8",
    )
    (root / "docs" / "SESSION_LOG.md").write_text(
        "Session for Stet v1.2.0\n", encoding="utf-8"
    )
    shutil.copy(SCRIPT, root / "scripts" / "bump_version.py")
    return root


def _run(root: Path, *args: str):
    return subprocess.run(
        [sys.executable, "scripts/bump_version.py", *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_bump_default_next_patch(tmp_path):
    root = _make_tree(tmp_path)
    r = _run(root)
    assert r.returncode == 0, r.stderr
    assert 'APP_VERSION = "1.2.1"' in (root / "stet" / "constants.py").read_text(
        encoding="utf-8"
    )
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert readme.count("v1.2.1") == 3  # badge text + two release hrefs
    # Historical doc must NOT be touched.
    assert "v1.2.0" in (root / "docs" / "SESSION_LOG.md").read_text(encoding="utf-8")


def test_bump_explicit_version(tmp_path):
    root = _make_tree(tmp_path)
    r = _run(root, "2.0.0")
    assert r.returncode == 0, r.stderr
    assert 'APP_VERSION = "2.0.0"' in (root / "stet" / "constants.py").read_text(
        encoding="utf-8"
    )
    assert "v2.0.0" in (root / "README.md").read_text(encoding="utf-8")


def test_bump_dry_run_writes_nothing(tmp_path):
    root = _make_tree(tmp_path)
    r = _run(root, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert 'APP_VERSION = "1.2.0"' in (root / "stet" / "constants.py").read_text(
        encoding="utf-8"
    )
    assert "v1.2.1" not in (root / "README.md").read_text(encoding="utf-8")


def test_bump_invalid_version_rejected(tmp_path):
    root = _make_tree(tmp_path)
    r = _run(root, "not-a-version")
    assert r.returncode != 0
    assert 'APP_VERSION = "1.2.0"' in (root / "stet" / "constants.py").read_text(
        encoding="utf-8"
    )
