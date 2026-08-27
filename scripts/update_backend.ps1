# update_backend.ps1 - Reusable llama.cpp backend update pipeline
# ------------------------------------------------------------------
# Automates docs/BACKEND_UPDATE_GUIDE.md for Windows.
#
# USAGE
#   pwsh scripts/update_backend.ps1                              # full run
#   pwsh scripts/update_backend.ps1 -Phase discover              # just find newest bXXXX
#   pwsh scripts/update_backend.ps1 -NewVersion b10639           # target a release
#   pwsh scripts/update_backend.ps1 -NewVersion b10639 -Phase download,benchmark,edit
#   pwsh scripts/update_backend.ps1 -Phase verify -SkipEdit
#
# PHASES (default: discover,download,benchmark,edit,verify)
#   discover  : newest bXXXX release with win-cuda-<CudaVersion> assets
#   download  : fetch llama+cudart zips, verify sha256, co-located extract
#   benchmark : scripts/benchmark_backend_version.ps1 old-vs-new
#   edit      : apply_backend_edit.py -> constants.py/build.py/config.json/bench
#   verify    : run backend pytest suites
#
# Hashes come from the GitHub asset digest. constants.py + build.py .bat want
# UPPERCASE; build.py .sh wants lowercase - apply_backend_edit.py handles both.
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$NewVersion  = "",
    [string]$Phase       = "all",
    [switch]$SkipDownload,
    [switch]$SkipEdit,
    [switch]$SkipBenchmark,
    [switch]$SkipVerify,
    [string]$BenchPort   = "8082",   # 8081 conflicts with the local-vision proxy
    [string]$BenchModel  = "E:/AI/LLM/unsloth/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-UD-Q4_K_XL.gguf",
    [string]$CudaVersion = "12.4"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
function Get-Json { param([string]$u) (curl.exe -s -H "Accept: application/vnd.github+json" -H "User-Agent: Stet-backend-update" $u) -join "`n" | ConvertFrom-Json }

$ph = @($Phase -split ",") | ForEach-Object { $_.Trim() }
$want = { param($p) ($ph -contains "all" -or $ph -contains $p) }

# current backend version from constants.py
$constantsPath = Join-Path $ProjectRoot "stet/constants.py"
$cm = Select-String -Path $constantsPath -Pattern "b([0-9]+)"
if (-not $cm) { throw "Could not read LLAMA_BACKEND_VERSION from $constantsPath" }
$currentVer = "b{0}" -f $cm.Matches[0].Groups[1].Value
$oldDir = "llama-$currentVer-bin-win-cuda-$CudaVersion-x64"
Write-Host "== Current backend: $currentVer ($oldDir) ==" -ForegroundColor Cyan

# ---------------- discover ----------------
if (& $want "discover") {
    if (-not $NewVersion) {
        Write-Host "== Discovering newest bXXXX with win-cuda-$CudaVersion assets... ==" -ForegroundColor Cyan
        $rels = Get-Json ($API + "?per_page=30")
        foreach ($rel in $rels) {
            if ($rel.tag_name -notmatch "^b\d+$") { continue }
            $probe = "llama-$($rel.tag_name)-bin-win-cuda-$CudaVersion-x64.zip"
            if (@($rel.assets | Where-Object { $_.name -eq $probe }).Count -gt 0) {
                $NewVersion = $rel.tag_name; $newRel = $rel
                Write-Host "  -> $NewVersion (published $($rel.published_at))" -ForegroundColor Green
                break
            }
        }
        if (-not $NewVersion) { throw "No bXXXX release with win-cuda-$CudaVersion assets found" }
    } else {
        try { $newRel = Get-Json "$API/tags/$NewVersion" } catch { throw "Release $NewVersion not found" }
        Write-Host "  -> explicit $NewVersion" -ForegroundColor Green
    }
    "NEW_VERSION=$NewVersion" | Set-Content (Join-Path $ProjectRoot "backend_update_version.txt")
    if (@($ph | Where-Object { $_ -ne "discover" }).Count -eq 0) { return }
}

$newDir = "llama-$NewVersion-bin-win-cuda-$CudaVersion-x64"
$newDirPath = Join-Path $ProjectRoot $newDir
$llamaAsset = $newRel.assets | Where-Object { $_.name -eq "llama-$NewVersion-bin-win-cuda-$CudaVersion-x64.zip" } | Select-Object -First 1
$cudaAsset  = $newRel.assets | Where-Object { $_.name -eq "cudart-llama-bin-win-cuda-$CudaVersion-x64.zip" } | Select-Object -First 1
if (-not $llamaAsset -or -not $cudaAsset) { throw "Could not resolve both cuda-$CudaVersion assets for $NewVersion" }

# ---------------- download ----------------
if ((& $want "download") -and -not $SkipDownload) {
    if (-not (Test-Path $newDirPath)) { New-Item -ItemType Directory -Path $newDirPath | Out-Null }
    $staging = Join-Path $ProjectRoot ".backend_staging"
    if (-not (Test-Path $staging)) { New-Item -ItemType Directory -Path $staging | Out-Null }
    $pairs = @(
        @{ Z=$llamaAsset.name; U=$llamaAsset.browser_download_url; E=(($llamaAsset.digest -replace "^sha256:","").ToLower()) },
        @{ Z=$cudaAsset.name;  U=$cudaAsset.browser_download_url;  E=(($cudaAsset.digest  -replace "^sha256:","").ToLower()) }
    )
    foreach ($it in $pairs) {
        $dst = Join-Path $staging $it.Z
        Write-Host "== Download $($it.Z) ==" -ForegroundColor Yellow
        if (-not (Test-Path $dst)) { curl.exe -sL -o $dst $it.U }
        $actual = (Get-FileHash $dst -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $it.E) { throw "SHA256 mismatch for $($it.Z): expected $($it.E) got $actual" }
        Write-Host "  sha256 OK: $actual" -ForegroundColor Green
        Expand-Archive -Path $dst -DestinationPath $newDirPath -Force
    }
    foreach ($dll in @("cublas64_12.dll","cublasLt64_12.dll","cudart64_12.dll","ggml-cuda.dll")) {
        if (-not (Test-Path (Join-Path $newDirPath $dll))) { Write-Host "  WARN: $dll missing => silent CPU fallback!" -ForegroundColor Red }
    }
    Write-Host "== Extracted to $newDirPath ==" -ForegroundColor Green
}

# ---------------- benchmark ----------------
if ((& $want "benchmark") -and -not $SkipBenchmark) {
    if (-not (Test-Path $newDirPath)) { throw "New backend dir missing - run download first" }
    $bench = Join-Path $PSScriptRoot "benchmark_backend_version.ps1"
    if (-not (Test-Path $bench)) { throw "$bench not found" }
    $tmpBench = Join-Path $ProjectRoot "benchmark_run.ps1"
    Copy-Item $bench $tmpBench -Force
    $py = Join-Path $ProjectRoot "venv/Scripts/python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    $helper = Join-Path $PSScriptRoot "apply_backend_edit.py"
    $lsh = ($llamaAsset.digest -replace "^sha256:","")
    $csh = ($cudaAsset.digest -replace "^sha256:","")
    & $py $helper $constantsPath (Join-Path $ProjectRoot "build.py") (Join-Path $ProjectRoot "config.json") $tmpBench $NewVersion $lsh $csh $newDir
    if ($LASTEXITCODE -ne 0) { throw "apply_backend_edit.py failed (exit $LASTEXITCODE)" }
    Write-Host "== Benchmark old($currentVer) vs new($NewVersion) on port $BenchPort ==" -ForegroundColor Cyan
    & pwsh $tmpBench -ModelPath $BenchModel -BenchPort $BenchPort -Repeats 5 -BenchTokens 200 -OldDir $oldDir -NewDir $newDir
}

# ---------------- edit ----------------
if ((& $want "edit") -and -not $SkipEdit) {
    $py = Join-Path $ProjectRoot "venv/Scripts/python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    $helper = Join-Path $PSScriptRoot "apply_backend_edit.py"
    if (-not (Test-Path $helper)) { throw "Missing $helper" }
    $benchReal = Join-Path $PSScriptRoot "benchmark_backend_version.ps1"
    $lsh = ($llamaAsset.digest -replace "^sha256:","")
    $csh = ($cudaAsset.digest -replace "^sha256:","")
    Write-Host "== Applying edits (constants.py, build.py, config.json, benchmark) ==" -ForegroundColor Cyan
    & $py $helper $constantsPath (Join-Path $ProjectRoot "build.py") (Join-Path $ProjectRoot "config.json") $benchReal $NewVersion $lsh $csh $newDir
    if ($LASTEXITCODE -ne 0) { throw "apply_backend_edit.py failed (exit $LASTEXITCODE)" }
}

# ---------------- verify ----------------
if ((& $want "verify") -and -not $SkipVerify) {
    $py = Join-Path $ProjectRoot "venv/Scripts/python.exe"
    if (Test-Path $py) {
        & $py -m pytest (Join-Path $ProjectRoot "tests/test_backend_manager.py") (Join-Path $ProjectRoot "tests/test_backend_edge_cases.py") -q
    } else { Write-Host "  venv python not found - run pytest manually" -ForegroundColor Red }
}

# ---------------- summary ----------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " BACKEND UPDATE SUMMARY: $currentVer -> $NewVersion" -ForegroundColor Cyan
Write-Host "  new dir     : $newDirPath" -ForegroundColor Cyan
Write-Host "  NEXT: verify benchmark_results.json within +-5%, then" -ForegroundColor Green
Write-Host "        python build.py (optional installer), then archive old:" -ForegroundColor Green
Write-Host "        Move-Item "$oldDir" "archive/backups/" (never delete)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
