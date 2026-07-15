# Benchmark two llama-server versions side-by-side
param(
    [string]$ModelPath = "E:/AI/LLM/unsloth/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-UD-Q4_K_XL.gguf",
    [string]$ProjectRoot = "D:\Projects\Software\Stet",
    [int]$BenchPort = 8081,
    [int]$WarmupTokens = 128,
    [int]$BenchTokens = 256,
    [int]$Repeats = 5
)

Set-Location -LiteralPath $ProjectRoot

$versions = @(
    @{Label="b9940"; Dir="llama-b9940-bin-win-cuda-12.4-x64"},
    @{Label="b10016"; Dir="llama-b10016-bin-win-cuda-12.4-x64"}
)

$results = @{}
$testPrompts = @(
    "Explain the difference between a CPU and a GPU in one paragraph.",
    "Write a short poem about debugging code at midnight.",
    "What are three best practices for writing secure Python code?"
)

# Ensure the executable name is correct on Windows
$exeName = "llama-server.exe"

foreach ($v in $versions) {
    $serverExe = Join-Path $ProjectRoot $v.Dir $exeName
    if (-not (Test-Path -LiteralPath $serverExe)) {
        Write-Host "ERROR: $serverExe not found!" -ForegroundColor Red
        continue
    }

    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host " Benchmarking: $($v.Label)" -ForegroundColor Cyan
    Write-Host " Binary: $serverExe" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan

    # Kill any lingering server on our benchmark port
    $existing = Get-Process -Name "llama-server" -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "Stopping existing llama-server process..." -ForegroundColor Yellow
        $existing | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }

    $serverDir = Join-Path $ProjectRoot $v.Dir
    $env:PATH = "$serverDir;$env:PATH"

    $args = @(
        "--model", $ModelPath,
        "--ctx-size", "4096",
        "--n-gpu-layers", "99",
        "--threads", "-1",
        "--threads-batch", "-1",
        "--batch-size", "1024",
        "--ubatch-size", "512",
        "--flash-attn", "on",
        "--host", "127.0.0.1",
        "--port", $BenchPort.ToString(),
        "--parallel", "1",
        "--reasoning", "off",
        "--no-warmup",
        "--cache-reuse", "64",
        "--temp", "0.0",
        "--top-k", "1",
        "--top-p", "0.95",
        "--min-p", "0.0"
    )

    Write-Host "Starting server..." -ForegroundColor Yellow
    $proc = Start-Process -FilePath $serverExe -ArgumentList $args `
        -NoNewWindow -PassThru -RedirectStandardOutput "$ProjectRoot\bench_server_$($v.Label).log" `
        -RedirectStandardError "$ProjectRoot\bench_server_$($v.Label)_err.log"

    # Wait for server to be ready
    $maxWait = 60
    $ready = $false
    for ($i = 0; $i -lt $maxWait; $i++) {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$BenchPort/health" -TimeoutSec 2
            if ($resp.status -eq "ok" -or $resp.status -eq "no slot available") {
                $ready = $true
                break
            }
        } catch {
            # not ready yet
        }
        Start-Sleep -Seconds 1
        Write-Host "." -NoNewline
    }
    Write-Host ""

    if (-not $ready) {
        Write-Host "ERROR: Server failed to start within $maxWait seconds!" -ForegroundColor Red
        Write-Host "Server log:" -ForegroundColor Red
        Get-Content "$ProjectRoot\bench_server_$($v.Label).log" -ErrorAction SilentlyContinue | Select-Object -Last 20
        $proc | Stop-Process -Force -ErrorAction SilentlyContinue
        continue
    }
    Write-Host "Server ready!" -ForegroundColor Green

    # Check GPU was detected
    $logContent = Get-Content "$ProjectRoot\bench_server_$($v.Label).log" -ErrorAction SilentlyContinue -Raw
    if ($logContent -match "CUDA\d+\s*:\s*(.+?)\n") {
        Write-Host "GPU detected: $($Matches[1].Trim())" -ForegroundColor Green
    } elseif ($logContent -match "device_info") {
        Write-Host "GPU device info found in log" -ForegroundColor Yellow
    } else {
        Write-Host "WARNING: No GPU device detected in server log — may be CPU-only!" -ForegroundColor Red
    }

    # Warmup
    Write-Host "Warming up ($WarmupTokens tokens)..." -ForegroundColor Yellow
    $warmupBody = @{
        prompt = "Hello, how are you?"
        n_predict = $WarmupTokens
        temperature = 0.0
        top_k = 1
        stream = $false
    } | ConvertTo-Json

    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$BenchPort/completion" `
            -Method Post -Body $warmupBody -ContentType "application/json" -TimeoutSec 30
    } catch {
        Write-Host "Warmup failed: $($_.Exception.Message)" -ForegroundColor Red
    }

    # Benchmark runs
    $versionResults = @()
    foreach ($prompt in $testPrompts) {
        for ($r = 0; $r -lt $Repeats; $r++) {
            $body = @{
                prompt = $prompt
                n_predict = $BenchTokens
                temperature = 0.0
                top_k = 1
                top_p = 0.95
                stream = $false
            } | ConvertTo-Json

            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            try {
                $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$BenchPort/completion" `
                    -Method Post -Body $body -ContentType "application/json" -TimeoutSec 120
                $sw.Stop()
                $elapsed = $sw.Elapsed.TotalSeconds
                $tokCount = $resp.tokens_predicted
                $tps = if ($elapsed -gt 0) { [math]::Round($tokCount / $elapsed, 2) } else { 0 }
                Write-Host "  Run $($r+1): $tokCount tok in $($elapsed.ToString('F2'))s = $tps tok/s" -ForegroundColor White
                $versionResults += @{ Prompt=$prompt; Run=$r; Tokens=$tokCount; Time=$elapsed; TPS=$tps }
            } catch {
                $sw.Stop()
                Write-Host "  Run $($r+1): FAILED - $($_.Exception.Message)" -ForegroundColor Red
                $versionResults += @{ Prompt=$prompt; Run=$r; Tokens=0; Time=0; TPS=0; Error=$_.Exception.Message }
            }
        }
    }

    # Calculate aggregate stats
    $validResults = $versionResults | Where-Object { $_.TPS -gt 0 }
    if ($validResults) {
        $avgTPS = [math]::Round(($validResults | Measure-Object -Property TPS -Average).Average, 2)
        $minTPS = [math]::Round(($validResults | Measure-Object -Property TPS -Minimum).Minimum, 2)
        $maxTPS = [math]::Round(($validResults | Measure-Object -Property TPS -Maximum).Maximum, 2)
        Write-Host "`n  AVERAGE: $avgTPS tok/s (min=$minTPS, max=$maxTPS, runs=$($validResults.Count))" -ForegroundColor Green
        $results[$v.Label] = @{
            AvgTPS = $avgTPS
            MinTPS = $minTPS
            MaxTPS = $maxTPS
            Runs = $validResults.Count
            Details = $versionResults
        }
    } else {
        Write-Host "ERROR: No valid benchmark runs for $($v.Label)" -ForegroundColor Red
        $results[$v.Label] = @{ Error = "No valid results" }
    }

    # Stop server
    Write-Host "Stopping server..." -ForegroundColor Yellow
    $proc | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

# Comparison
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host " COMPARISON" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if ($results.Count -eq 2 -and $results['b9940'].AvgTPS -and $results['b10016'].AvgTPS) {
    $old = $results['b9940']
    $new = $results['b10016']
    $ratio = [math]::Round($new.AvgTPS / $old.AvgTPS, 4)
    $pct = [math]::Round(($ratio - 1.0) * 100, 2)

    Write-Host "b9940  (old): $($old.AvgTPS) tok/s (runs=$($old.Runs))" -ForegroundColor White
    Write-Host "b10016 (new): $($new.AvgTPS) tok/s (runs=$($new.Runs))" -ForegroundColor White
    Write-Host "Ratio (new/old): $ratio" -ForegroundColor Cyan

    if ($pct -ge -5 -and $pct -le 10) {
        Write-Host "VERDICT: PASS — performance change within acceptable range ($pct%)" -ForegroundColor Green
    } elseif ($pct -lt -5) {
        Write-Host "VERDICT: REGRESSION — $pct% slower — investigate!" -ForegroundColor Red
    } else {
        Write-Host "VERDICT: IMPROVEMENT — $pct% faster!" -ForegroundColor Green
    }
} else {
    Write-Host "Cannot compare — one or both versions failed to produce results" -ForegroundColor Red
}

# Output results as JSON for easy parsing
$results | ConvertTo-Json -Depth 3 | Out-File "$ProjectRoot\benchmark_results.json" -Encoding utf8
Write-Host "`nFull results saved to: $ProjectRoot\benchmark_results.json" -ForegroundColor Cyan
