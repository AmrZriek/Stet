"""
test_release_script.py — static sanity checks for release.ps1.

Source-level guards (no PowerShell execution): the release pipeline MUST
upload SHA256SUMS.txt as a release asset. The updater (stet/update.py, F-1)
refuses to install a release without its checksum file, so shipping a release
without it breaks auto-update for every user.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PS1 = ROOT / "release.ps1"


def _script_text() -> str:
    return RELEASE_PS1.read_text(encoding="utf-8")


def test_release_script_references_sha256sums():
    assert "SHA256SUMS.txt" in _script_text(), (
        "release.ps1 must reference the SHA256SUMS.txt release asset"
    )


def test_release_script_uploads_checksum_asset():
    text = _script_text()
    assert re.search(r"\$artifacts\s*\+=\s*\$checksumFile\.FullName", text), (
        "release.ps1 must add SHA256SUMS.txt (via $checksumFile) to $artifacts "
        "so gh release create uploads it as a release asset"
    )
    # The same "$artifacts +=" line should carry the SHA256SUMS.txt comment.
    m = re.search(r"\$artifacts\s*\+=\s*\$checksumFile\.FullName[^\n]*", text)
    assert m and "SHA256SUMS.txt" in m.group(0), (
        "the $artifacts += line must identify the file as the SHA256SUMS.txt asset"
    )


def test_release_script_aborts_when_checksum_missing():
    text = _script_text()
    m = re.search(r"if\s*\(-not\s+\$checksumFile\)\s*\{(.*?)\}", text, re.S)
    assert m, "release.ps1 must guard on a missing SHA256SUMS.txt in dist/"
    assert "throw" in m.group(1), (
        "release.ps1 must throw (abort) when SHA256SUMS.txt is missing — an "
        "unverifiable release breaks the updater for every user"
    )
