$ErrorActionPreference = 'Stop'
$model = 'E:/AI/LLM/gemma-4-E2B-it-qat-GGUF/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf'
$server = 'D:\Projects\Software\Stet\llama-b10639-bin-win-cuda-12.4-x64\llama-server.exe'
$outRoot = 'D:\Projects\Software\Stet-wt-phase0\phase2h\lab\bench'
New-Item -ItemType Directory -Force -Path $outRoot | Out-Null

function Get-FreeVRAM {
    $g = nvidia-smi --query-gpu=memory.free --format=csv,noheader 2>$null
    if ($g) { return ([int]($g.Trim().Split(' ')[0])) }
    return -1
}

function Run-Config([int]$parallel, [int]$ngl, [int]$ctx) {
    $port = 8089  # fixed test port to avoid colliding with app's 8080
    # Kill any prior test server on this the launcher's process tree
    Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne 29224 } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 400
    $freeBefore = Get-FreeVRAM
    $out = "$outRoot\out_p${parallel}_g${ngl}_c${ctx}.txt"
    $err = "$outRoot\err_p${parallel}_g${ngl}_c${ctx}.txt"
    $args = @('--model',$model,'--ctx-size',[string]$ctx,'--n-gpu-layers',[string]$ngl,'--port',[string]$port,'--parallel',[string]$parallel,'--no-warmup','--host','127.0.0.1')
    $proc = Start-Process -FilePath $server -ArgumentList $args -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    $t0 = Get-Date
    # Poll health up to 120s
    $up = $false
    $n_ctx = ''
    for ($i=0; $i -lt 240; $i++) {
        Start-Sleep -Milliseconds 500
        if ($proc.HasExited) { break }
        try {
            $h = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 2
            if ($h.StatusCode -eq 200) {
                $up = $true
                # get n_ctx from props
                $c = (Invoke-WebRequest -Uri "http://127.0.0.1:$port/props" -UseBasicParsing -TimeoutSec 4).Content
                $m = [regex]::Match($c, '"n_ctx":([0-9]+)')
                if ($m.Success) { $n_ctx = $m.Groups[1].Value }
                break
            }
        } catch {}
    }
    $elapsed = ((Get-Date) - $t0).TotalSeconds
    $freeAfter = Get-FreeVRAM
    $backend = '?'
    if (Test-Path $err) { $btxt = Get-Content $err -Raw; if ($btxt -match 'CUDA|ggml_cuda') { $backend = 'cuda' } elseif ($btxt -match 'CPU') { $backend = 'cpu' } }
    # Kill the test server
    if (-not $proc.HasExited) { $proc.Kill(); $proc.WaitForExit(5000) | Out-Null }
    Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne 29224 } | Stop-Process -Force -ErrorAction SilentlyContinue
    [pscustomobject]@{
        parallel=$parallel; ngl=$ngl; ctx=$ctx;
        up=$up; n_ctx=$n_ctx; backend=$backend;
        load_sec=[math]::Round($elapsed,1);
        vram_before=$freeBefore; vram_after=$freeAfter
    }
}

$results = @()
$configs = @(
    @{p=1; g=99; c=4096},
    @{p=1; g=0;  c=8192},
    @{p=1; g=0;  c=4096},
    @{p=2; g=0;  c=4096},
    @{p=2; g=99; c=4096},
    @{p=4; g=0;  c=4096}
)
foreach ($cfg in $configs) {
    $r = Run-Config $cfg.p $cfg.g $cfg.c
    $results += $r
    Write-Output ($r | ConvertTo-Json -Compress)
}

$results | Export-Csv "$outRoot\results.csv" -NoTypeInformation
Write-Output '=== DONE ==='
