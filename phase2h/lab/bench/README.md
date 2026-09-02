# Model Config Benchmark — llama-server × parallel × n_gl × ctx

Benchmarks the model server launch options on THIS machine (RTX 3060 Laptop, 6GB VRAM)
for the Stet model gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf.

## Command
    powershell -NoProfile -ExecutionPolicy Bypass -File run_bench.ps1

It launches llama-server detached on a test port (8089), waits for /health, reads
/props n_ctx + total_slots, times the load, records free VRAM before/after, then kills
it. Each config runs serially (no VRAM contention). Results -> results.csv.

## Result (the definitive finding)
| parallel | n_gl | ctx | actual n_ctx | load_s | vram_after MiB |
|----------|------|-----|--------------|--------|----------------|
| 1        | 99   | 4096| 4096         | 4.7    | 229   |
| 1        | 0    | 8192| 8192         | 3.6    | 1487  |
| 1        | 0    | 4096| 4096         | 3.6    | 1495  |
| 2        | 0    | 4096| 2048         | 3.6    | 1505  |
| 2        | 99   | 4096| 2048         | 4.1    | 306   |
| 4        | 0    | 4096| 1024         | 3.6    | 1590  |

**n_ctx = requested_ctx / parallel.** Confirmed in the server's own load line:
    parallel=4 -> "n_slots = 4, n_ctx_slot = 1024"
    parallel=1 -> "n_slots = 1, n_ctx_slot = 4096"

NOT VRAM-gated: even pure CPU (n_gl=0, ~1490 MiB free) gives full n_ctx at parallel=1.
The app's default --parallel 4 is why corrections truncated to 256 tokens/slot.
FIX: config.json "parallel": 1 (or 2). Verified: app relaunched with parallel=1 shows
n_ctx=4096 in /props.

## Notes
- The app server on port 8080 (Id 29224/32364) is excluded from kill; only test-server
  PIDs are cleaned up. Safe to re-run.
- Transient per-config out_*/err_* text files are the raw launcher stdout/stderr;
  results.csv is the authoritative table.
