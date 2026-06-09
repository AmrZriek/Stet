import os
import glob
import zipfile
import subprocess
import time
import stat
from pathlib import Path


def _safe_extract(zip_ref, dest):
    dest = Path(dest).resolve()
    for member in zip_ref.infolist():
        mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise RuntimeError(f"Refusing symlink in ZIP: {member.filename}")
        target = (dest / member.filename).resolve()
        if dest not in target.parents:
            raise RuntimeError(f"Unsafe path in ZIP: {member.filename}")
    zip_ref.extractall(dest)


def test_e2e_launch():
    dist_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "dist"))
    zip_pattern = os.path.join(dist_dir, "Stet_*_Windows.zip")

    zip_files = glob.glob(zip_pattern)
    assert zip_files, f"No zip files found matching {zip_pattern}"

    # Sort by creation time to get the latest
    latest_zip = max(zip_files, key=os.path.getctime)

    test_dir = os.path.join(os.path.dirname(__file__), "temp_e2e_launch")
    os.makedirs(test_dir, exist_ok=True)

    print(f"Extracting {latest_zip} to {test_dir}")
    with zipfile.ZipFile(latest_zip, "r") as zip_ref:
        _safe_extract(zip_ref, test_dir)

    # The zip usually contains a folder, so let's find the exe inside test_dir
    exe_path = None
    for root, dirs, files in os.walk(test_dir):
        if "Stet.exe" in files:
            exe_path = os.path.join(root, "Stet.exe")
            break

    assert exe_path is not None, "Stet.exe not found in the extracted zip"

    print(f"Launching {exe_path}")
    process = subprocess.Popen(
        [exe_path],
        cwd=os.path.dirname(exe_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        print("Waiting for 5 seconds...")
        time.sleep(5)

        # Check if process is still running
        poll_result = process.poll()
        if poll_result is not None:
            # Read app_debug.log
            log_path = os.path.join(os.path.dirname(exe_path), "app_debug.log")
            log_content = ""
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8") as f:
                    log_content = f.read()

            stdout_out, stderr_out = process.communicate(timeout=1)

            # If it exited because the app is already running, that's a successful test of the binary executing
            if poll_result == 0 and (
                "already running" in stdout_out
                or "already running" in stderr_out
                or "already running" in log_content
            ):
                return

            assert False, (
                f"Stet.exe crashed or exited prematurely with code {poll_result}.\nStdout: {stdout_out}\nStderr: {stderr_out}\nLog:\n{log_content}"
            )

    finally:
        if process.poll() is None:
            print("Terminating process...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
