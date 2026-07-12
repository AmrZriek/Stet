# release.ps1 - Stet release flow.
# Usage:  .\release.ps1                  -> dynamically gets version from constants.py
#         .\release.ps1 -Version 1.0.0   -> custom version
[CmdletBinding()]
param(
    [string]$Version,
    [string]$Message = "feat: replace llama.cpp updater with full application auto-updater"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not $Version) {
    $Version = & .\venv\Scripts\python.exe -c "import build; print(build._get_version())"
    $Version = $Version.Trim()
}


Write-Host "==> Verifying clean working tree" -ForegroundColor Cyan
$gitStatus = git status --porcelain
if ($gitStatus) {
    Write-Host "Pending changes detected - staging selected files only." -ForegroundColor Yellow
}

Write-Host "==> Running tests (Chunk 1)" -ForegroundColor Cyan
& .\venv\Scripts\python.exe -m pytest tests/ -v --ignore=tests/test_main_window_coverage.py --ignore=tests/test_ui_fixes.py --ignore=tests/test_app_coverage.py
if ($LASTEXITCODE -ne 0) { throw "Tests failed in Chunk 1 (exit $LASTEXITCODE) - aborting release" }

Write-Host "==> Running tests (Chunk 2)" -ForegroundColor Cyan
& .\venv\Scripts\python.exe -m pytest tests/test_main_window_coverage.py tests/test_ui_fixes.py tests/test_app_coverage.py -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed in Chunk 2 (exit $LASTEXITCODE) - aborting release" }

Write-Host "==> Staging files" -ForegroundColor Cyan
git add stet/ build.py requirements.txt .gitignore release.ps1 tests/


# Only commit if there are staged changes
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> Committing" -ForegroundColor Cyan
    $body = $Message
    git commit -m $body
    if ($LASTEXITCODE -ne 0) { throw "git commit failed (exit $LASTEXITCODE)" }
} else {
    Write-Host "No staged changes to commit." -ForegroundColor Yellow
}

Write-Host "==> Pushing to origin/main" -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw "git push failed (exit $LASTEXITCODE)" }

Write-Host "==> Building release v$Version" -ForegroundColor Cyan
& .\venv\Scripts\python.exe build.py --version $Version --keep-folder
if ($LASTEXITCODE -ne 0) { throw "build.py failed (exit $LASTEXITCODE)" }

Write-Host "==> Running post-build smoke test" -ForegroundColor Cyan
& .\venv\Scripts\python.exe scripts/smoke_test_build.py
if ($LASTEXITCODE -ne 0) { throw "Smoke test failed (exit $LASTEXITCODE) - aborting release" }


# Collect release artifacts
$artifacts = @()
$zip = Get-ChildItem dist -Filter "stet_portable.zip" | Select-Object -First 1
if (-not $zip) {
    $zip = Get-ChildItem dist -Filter "*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $zip) { throw "Portable ZIP not found in dist/" }
$artifacts += $zip.FullName

$installer = Get-ChildItem dist -Filter "StetSetup.exe" | Select-Object -First 1
if ($installer) {
    $artifacts += $installer.FullName
    $installerSizeMb = [math]::Round($installer.Length / 1MB, 1)
    Write-Host "    Found: $($installer.Name) ($installerSizeMb MB)" -ForegroundColor Green
}

$zipSizeMb = [math]::Round($zip.Length / 1MB, 1)
Write-Host "    Found: $($zip.Name) ($zipSizeMb MB)" -ForegroundColor Green

# Compute SHA-256 checksums for release notes
$checksumLines = @()
foreach ($f in $artifacts) {
    $hash = (Get-FileHash -Path $f -Algorithm SHA256).Hash.ToLower()
    $name = Split-Path $f -Leaf
    $checksumLines += "$hash  $name"
}
$checksumBlock = ($checksumLines -join "`n")

Write-Host "==> Tagging v$Version" -ForegroundColor Cyan
git tag -a "v$Version" -m "Release v$Version"
git push origin "v$Version"
if ($LASTEXITCODE -ne 0) { throw "git push tag failed (exit $LASTEXITCODE)" }

Write-Host "==> Creating GitHub release" -ForegroundColor Cyan
$notes = @"
## Stet v$Version

Stet is a local, privacy-first AI autocorrect and text rewriting tool. Runs entirely offline.

**Installation:**
1. Download and extract **stet_portable.zip**.
2. Run **Unblock_Stet.bat** (right-click → Run as administrator) to remove Windows security warnings from downloaded scripts.
3. Run **download_backend.bat** to fetch the llama.cpp backend (~652 MB, one-time).
4. Run **download_model.bat** to fetch the AI model (~1.8 GB).
5. Run **Stet.exe** (or run.bat).

> **Windows SmartScreen note:** Stet.exe is not code-signed. If Windows shows a "Windows protected your PC" warning, click **More info** → **Run anyway**. This warning will disappear once the executable builds reputation with Microsoft.

**Requirements:**
- Windows 10/11 (64-bit)
- NVIDIA GPU recommended for GPU-accelerated AI inference
- ~2.5 GB disk space for the backend + model

**SHA-256 Checksums:**
``````
$checksumBlock
``````

**Full Changelog**: https://github.com/AmrZriek/Stet/commits/v$Version
"@
$notesFile = Join-Path $env:TEMP "stet-release-notes.txt"
$notes | Out-File -FilePath $notesFile -Encoding utf8
$env:GITHUB_TOKEN = $null
gh release create "v$Version" $artifacts --title "Stet v$Version" -F $notesFile
if ($LASTEXITCODE -ne 0) {
    if (Test-Path $notesFile) { Remove-Item $notesFile }
    throw "gh release create failed (exit $LASTEXITCODE)"
}
if (Test-Path $notesFile) { Remove-Item $notesFile }

# Write-Host "==> Updating Gumroad Listing" -ForegroundColor Cyan
# $grStatus = & gumroad auth status
# if ($grStatus -like "*Not logged in*") {
#     throw "Gumroad CLI is not authenticated. Please run 'gumroad auth login' in your terminal to authenticate."
# }
# 
# $productId = "crcezg"
# 
# Write-Host "    Uploading portable ZIP to Gumroad..." -ForegroundColor Cyan
# & gumroad products update $productId --file $zip.FullName --file-name "stet_portable.zip" --non-interactive
# if ($LASTEXITCODE -ne 0) { throw "Gumroad product update failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "Done. https://github.com/AmrZriek/Stet/releases/tag/v$Version" -ForegroundColor Green
# Write-Host "Gumroad listing updated successfully!" -ForegroundColor Green
