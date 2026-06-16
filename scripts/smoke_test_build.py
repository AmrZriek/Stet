"""Smoke test for Stet compiled build (Nuitka).

Run this after 'python build.py --no-zip --keep-folder' to verify the
compiled binary starts correctly and loads config.

Usage:
    python scripts/smoke_test_build.py [path/to/Stet.exe]

Default: auto-discovers the most recent dist/ folder.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()
DIST = ROOT / "dist"


def find_latest_build() -> Path | None:
    """Find the most recently built Stet.exe in dist/."""
    candidates = sorted(
        DIST.glob("**/Stet.exe"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def check(condition: bool, message: str):
    if condition:
        print(f"  [PASS] {message}")
    else:
        print(f"  [FAIL] {message}")
        return False
    return True


def check_pe_metadata(exe: Path) -> bool:
    """Check PE metadata on Windows using PowerShell."""
    if sys.platform != "win32":
        return True
    
    ps_cmd = f"(Get-Item '{exe}').VersionInfo | ConvertTo-Json"
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=True
        )
        info = json.loads(res.stdout)
        
        # Verify specific fields
        company = info.get("CompanyName")
        product = info.get("ProductName")
        desc = info.get("FileDescription")
        
        if company is None and product is None and desc is None:
            print("  [WARN] PE metadata is empty (compiled with PyInstaller without version resource)")
            return True
            
        pass_company = check(company == "Stet", f"PE CompanyName is 'Stet' (got {company!r})")
        pass_product = check(product == "Stet", f"PE ProductName is 'Stet' (got {product!r})")
        pass_desc = check(desc == "Stet - AI Writing Assistant", f"PE FileDescription is 'Stet - AI Writing Assistant' (got {desc!r})")
        
        return pass_company and pass_product and pass_desc
    except Exception as e:
        print(f"  [FAIL] Failed to read PE metadata: {e}")
        return False


def run_smoke_test(exe: Path) -> int:
    print("\nStet Compiled Build Smoke Test")
    print(f"Binary: {exe}")
    print(f"{'=' * 60}")
    failures = 0

    # 1. Exe exists and is non-zero size
    print("\n[1] Binary checks")
    if not check(exe.exists(), "Stet.exe exists"):
        failures += 1
        return failures  # Can't continue without the exe
    size_mb = exe.stat().st_size / 1_048_576
    if not check(size_mb > 3, f"Binary is >3 MB (got {size_mb:.1f} MB)"):
        failures += 1

    # Check PE metadata
    if sys.platform == "win32":
        print("\n[1.5] PE Metadata checks")
        if not check_pe_metadata(exe):
            failures += 1

    # 2. Check for required sibling files
    print("\n[2] Required files")
    build_dir = exe.parent
    for required in ["config.json", "logo.ico", "logo.png"]:
        if not check((build_dir / required).exists(), f"{required} exists"):
            failures += 1
    if not check((build_dir / "stet" / "ui" / "stet.qss").exists(), "stet/ui/stet.qss stylesheet exists"):
        failures += 1

    # 3. Verify config.json is valid JSON with blank model paths
    print("\n[3] Release config validation")
    config_path = build_dir / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            if not check(cfg.get("model_path", "MISSING") == "",
                         "model_path is blank in release config"):
                failures += 1
            if not check("server_port" in cfg, "server_port key present"):
                failures += 1
            if not check(cfg.get("keep_model_loaded") is True, "keep_model_loaded=True"):
                failures += 1
        except Exception as e:
            print(f"  [FAIL] config.json parse error: {e}")
            failures += 1

    # 4. Launch and verify it starts (5s timeout)
    print("\n[4] Process launch test")
    print("  Launching Stet.exe (5s timeout)...")
    proc = None
    try:
        # Pass the STET_LOCK_KEY environment variable to avoid colliding with active dev running instance
        env = os.environ.copy()
        env["STET_LOCK_KEY"] = f"StetSingleInstanceLock_SmokeTest_{time.time()}"
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(build_dir),
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        time.sleep(3)
        if not check(proc.poll() is None, "Process is still running after 3s"):
            failures += 1
        else:
            print("  Process alive — sending terminate...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
                print("  [PASS] Terminated cleanly")
            except subprocess.TimeoutExpired:
                proc.kill()
                print("  [WARN] Had to kill process (did not terminate in 5s)")
    except Exception as e:
        print(f"  [FAIL] Launch error: {e}")
        failures += 1
    finally:
        if proc and proc.poll() is None:
            proc.kill()

    # 5. Check debug log was created
    print("\n[5] Boot log check")
    debug_log = build_dir / "app_debug.log"
    if debug_log.exists():
        content = debug_log.read_text(errors="replace")[-2000:]
        if not check("[BOOT]" in content, "Boot log entries present in app_debug.log"):
            failures += 1
        if not check("[BOOT CRASH]" not in content, "No BOOT CRASH in log"):
            failures += 1
            print(f"  Log tail:\n{content[-500:]}")
    else:
        print("  [WARN] app_debug.log not found (may not have been created yet)")

    # Summary
    print(f"\n{'=' * 60}")
    if failures == 0:
        print("RESULT: ALL CHECKS PASSED")
    else:
        print(f"RESULT: {failures} CHECK(S) FAILED")
    return failures


def main():
    parser = argparse.ArgumentParser(description="Stet compiled build smoke test")
    parser.add_argument("exe", nargs="?", help="Path to Stet.exe (auto-discovered if omitted)")
    args = parser.parse_args()

    if args.exe:
        exe = Path(args.exe)
    else:
        exe = find_latest_build()
        if not exe:
            print(f"ERROR: No Stet.exe found in {DIST}/*/Stet.exe")
            print("Run 'python build.py --no-zip --keep-folder' first.")
            sys.exit(1)
        print(f"Auto-discovered: {exe}")

    sys.exit(run_smoke_test(exe))


if __name__ == "__main__":
    main()
