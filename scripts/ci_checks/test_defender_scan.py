import os
import subprocess
import pytest
from pathlib import Path


def test_defender_scan():
    defender_path = r"C:\Program Files\Windows Defender\MpCmdRun.exe"
    dist_dir = str(Path(__file__).resolve().parents[2] / "dist")

    if not os.path.exists(defender_path):
        pytest.skip(f"Windows Defender not found at {defender_path}")

    if not os.path.exists(dist_dir):
        pytest.skip(f"dist directory not found at {dist_dir}")

    command = [defender_path, "-Scan", "-ScanType", "3", "-File", dist_dir]

    result = subprocess.run(command, capture_output=True, text=True)

    # Exit code 0 means no threats found. Exit code 2 usually means malware found.
    assert result.returncode == 0, (
        f"Defender scan failed or found threats. Exit code: {result.returncode}\nStdout: {result.stdout}\nStderr: {result.stderr}"
    )
