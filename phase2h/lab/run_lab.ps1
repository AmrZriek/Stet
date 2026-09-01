# run_lab.ps1 — Phase 2h interactive Windows lab driver
#
# Runs the per-target scenario matrix from matrix.md against the link-proven FFI
# skeleton. Reqires an interactive Windows desktop (keyboard + window station +
# real clipboard). Use:
#   .\run_lab.ps1                 # full matrix
#   .\run_lab.ps1 -Focus Edit     # one target class
#   .\run_lab.ps1 -BuildOnly      # just link-check the lab probes

param(
    [string]$Focus = "",
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$crate = Join-Path (Split-Path -Parent $here) "ffi-skel"

Write-Host "== Phase 2h lab driver ==" -ForegroundColor Cyan
Write-Host "crate: $crate"

# 1. Link-check the lab-enabled test harness (proves the FFI symbols resolve).
Push-Location $crate
$env:CARGO_INCREMENTAL = "0"
Write-Host "`n[01] cargo test --features lab --no-run (link check)..." -ForegroundColor Yellow
cargo test --features lab --no-run -j 1
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "[01] FFI lab harness FAILED to link" }
Write-Host "      PASS" -ForegroundColor Green

if ($BuildOnly) {
    Pop-Location
    Write-Host "`nBuild-only complete. Interactive scenarios require a desktop session."
    exit 0
}

Write-Host "`n[02] Running interactive scenario probes..." -ForegroundColor Yellow
# The lab feature compiles interactive probes that call the real OS. These need a live
# desktop and may block (e.g. OleGetClipboard, SendInput). They are marked #[ignore] by
# default so a headless `cargo test` skips them; the driver removes the ignore.
$filter = if ($Focus) { "lab_$Focus" } else { "lab_" }
cargo test --features lab -j 1 -- --ignored --test-threads=1 $filter
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "" -ForegroundColor Red
    Write-Host "*** One or more interactive scenarios FAILED. ***" -ForegroundColor Red
    Write-Host "The strict gate does not hold for the affected target class; do not accept"
    Write-Host "shadow-mode field data for it until the matrix row is green (see matrix.md)."
    exit 1
}
Pop-Location

Write-Host "`n[03] Matrix result (from matrix.md acceptance rule):" -ForegroundColor Cyan
Write-Host "      All exercised scenarios passed." -ForegroundColor Green
Write-Host "      Full PASS required for each class before native cutover."
Write-Host "`nDone."

