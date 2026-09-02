# Phase 2h / Stet v2.0 Rewrite — Substantial Handoff

> Written at the point the user is transitioning to a different harness. This documents the
> verified state, what was resolved, what remains open, and the exact next steps. Everything
> is self-contained and traceable. Use this as the single source of truth on resume.

## 1. Repository layout (critical — do NOT confuse the two checkouts)

### `D:\Projects\Software\Stet` — MAIN checkout, branch `main`
- HEAD: `205c26e` = **clean tag `v1.3.0`** (the user ran `git checkout v1.3.0`; a WIP commit
  `7744ffd` labeled "NOT final, NOT pushed" previously sat on main and is now NOT the active
  checkout).
- This is **what the user actually runs** (the working Stet app). It has the llama.cpp CUDA
  backend: `llama-b10639-bin-win-cuda-12.4-x64\llama-server.exe` (a 9KB launcher stub that
  loads `llama-server-impl.dll` 8.8MB + `ggml-cuda.dll` 538MB).
- `config.json`: `model_path=E:/AI/LLM/gemma-4-E2B-it-qat-GGUF/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf`,
  `parallel=1` (was 4), `context_size=4096`, `gpu_layers=99`.
- **This checkout has NO `crates/` dir** (no Rust).

### `D:\Projects\Software\Stet-wt-phase0` — WORKTREE, branch `phase-0-safety`
- HEAD: `70640d7` (36 `.rs` files in `crates/`, ~5636 lines Rust + ~2791 lines new Python
  + tests; 156/156 Rust tests, 80/80 Python).
- This is the **rewrite sandbox**. It has NO llama.cpp backend, so it **cannot load a model**
  — its config points at a nonexistent `stet-wt-phase0\llama_cpp\llama-server.exe`. That is
  NOT a rewrite regression; it's just missing the backend folder.
- `phase2h/` is the Phase 2h deliverable committed here (FFI skeleton + lab harness).

## 2. What was resolved this session

### (a) Model "broken output / truncated" — ROOT CAUSE FOUND & benchmarked
**Cause: `parallel=4` collapses context.** Context = requested/parallel.

Benchmark (empirical, in `phase2h/lab/bench/README.md` + `results.csv`) on the RTX 3060:
| parallel | actual n_ctx | server's own load line |
|----------|--------------|------------------------|
| 1        | 4096         | `n_slots=1, n_ctx_slot=4096` |
| 2        | 2048         | `n_slots=2, n_ctx_slot=2048` |
| 4        | 1024         | `n_slots=4, n_ctx_slot=1024` |

- With parallel=4 the corrections ran with 256 tokens/slot → truncation/partial output.
- **FIX applied: `config.json "parallel": 1`.** Verified live: `/props` now reports `n_ctx=4096`,
  and corrections complete full-length (`finish=stop` on all units).
- The GPU offload is NOT required for correctness — pure CPU (n_gl=0) gives full n_ctx.
  GPU (n_gl=99) also works but loads into ~229MB free VRAM (tight).

### (b) `stet-core` FFI skeleton (Phase 2h) — link-proven + lab-run on this machine
- `crates/` (Rust) is the native-core rewrite; the FFI `phase2h/ffi-skel` crate is raw extern
  Win32 bindings that **link clean** (every symbol resolves against real import libs) and
  **run headless-safe probes** here: pipe round-trip, DACL build, clipboard sequence, keyboard
  hook, winevent hook, hotkey register. See `phase2h/README.md`.

## 3. What remains OPEN (two distinct bugs, both REAL v1.3.0 behavior)

These persisted on clean v1.3.0 and were NOT fixed by the parallel change. They are
independent of the model-context issue.

### (1) Shift+F9 capture unreliable in Chromium / WinUI apps
- Symptom (log): in WhatsApp (`winuidesktopwin32windowclass`) and Obsidian
  (`chrome_widgetwin_1`), the FIRST Shift+F9 reports `[Capture] no selection after 12 polls`.
  A second press sometimes works (`got selection on poll 1`). F9 (no Shift) works.
- **Root-cause suspects (rewrite already implements the intended fix):**
  - `stet/core/clipboard.py:245 _send_ctrl_chord` releases a physically-held Shift via
    `GetAsyncKeyState` then sends Ctrl+C in ONE `SendInput` batch. The Shift-up + Ctrl+C
    timing causes deselection in Chromium-class windows. (Rewrite's `mod_release.rs` does a
    bounded wait for modifier release; `input_class.rs` filters the exact Stet dwExtraInfo tag.
    NOT wired into the running app.)
  - UIA direct capture returns empty for these classes, so it always falls to the Ctrl+C
    clipboard path, which is the fragile one.
- **NOT reliably testable by the sandbox** — needs real selected text in a focused window.
  Needs the user's interactive desktop. This is why it was deferred.

### (2) Clipboard left filled after paste / not restored
- Symptom (log): after a successful correction+paste, `[Paste] Clipboard was modified externally
  or text changed — skipping restore`. The corrected text stays in the clipboard. User expects
  the original clipboard content restored (the app's stated feature).
- **Root cause:** `stet/core/app.py:1856 _restore_if_unchanged` gate is misfiring. It compares
  the live clipboard seq against `seq_before+1` and the current text against the pasted text;
  but the timing is racy: `seq_before` is read before `_safe_copy(text)` runs in the background
  thread, and the paste (Ctrl+V) itself can bump the sequence by more than +1 (some apps
  re-write the clipboard on paste), so the gate sees `seq_now != seq_before+1` and skips.
  Result: clipboard left with the corrected text instead of restored.
- **Fix location:** the rewrite's `crates/stet-core/src/clipboard.rs` `ClipboardRestoreGate`
  already encodes the correct restore-vs-paste race logic (record post-write seq; restore only
  when the current seq still equals the recorded post-write value). NOT yet wired into the
  running app (which is still the Python v1.3.0 `_paste_text`).
- **Testable by sandbox?** Partially — the gate logic is unit-tested in Rust, but the end-to-end
  clipboard restore against a live target needs the desktop.

## 4. What is GENUINELY NOT DONE (the honest remaining work)

### Phase 2h literal Win32 FFI wiring into `stet-core` (the native cutover)
- The `phase2h/ffi-skel` surface is link-proven (symbols resolve) and lab-probes pass here.
- BUT it is **not wired into `stet-core`** yet, and `stet-core` is `crates/` in the worktree
  only — not merged into `main`. The running app is still the Python v1.3.0 pipeline.
- Cutover requires exercising the interactive matrix (`phase2h/lab/matrix.md`) on a real
  desktop: Win32 Edit, RichEdit/Word, Chromium/Electron, terminals, UIPI, IME, RDP, NVDA/JAWS,
  Sticky/Filter keys, slow targets, core restart/crash. Each class must pass every row.

### Phase 4 (macOS), Phase 5 (full Qt UI), Phase 6 (CI/OS matrix)
- Phase 4 blocked on Mac hardware (none available).
- Phase 5/6 need full UI + OS matrix + risky production wiring into `model_manager.py`.

### The `n_ctx` fix must also be made in the ENGINE default, not just config
- `stet/llm/model_manager.py` defaults `parallel` to 4 (lines 559, 581, 814). The config fix
  worked, but a new install would regress. The default should be reconsidered (1 or 2).

## 5. Exact next steps (in priority order)

1. **Confirm to user that the `parallel` fix resolved the truncation** (done: n_ctx=4096 live).
2. **Debug the Shift+F9 capture** (open #1) — needs the user's desktop. The intended fix is
   the rewrite's modifier-release tracker; a focused test is to observe whether the first
   Shift+F9 always fails vs. F9 always working, and in which exact app.
3. **Fix the clipboard-restore gate** (open #2) in the running app's `_restore_if_unchanged`,
   or wire in the Rust `ClipboardRestoreGate`. A direct fix: after a successful paste, restore
   `_old_clip` when the seq is at the post-`_safe_copy` value, regardless of the paste's own
   seq bump.
4. **Wire the FFI + verified policy layer into `stet-core`** for the native cutover, then run
   the interactive `phase2h/lab/matrix.md`.

## 6. Verification state / how to re-verify

- Rust: `cd crates && $env:CARGO_INCREMENTAL='0'; cargo test -j 1` → 156/156.
- Python new modules: 80/80 (worktree).
- FFI: `cd phase2h/ffi-skel && $env:CARGO_INCREMENTAL='0'; cargo test --features lab -j 1`;
  headless suite 33/33; safe lab probes (pipe/DACL/clipboard/hook/winevent/hotkey) pass here.
- Model benchmark: `cd phase2h/lab/bench && powershell -NoProfile -ExecutionPolicy Bypass -File
  run_bench.ps1` (uses port 8089, spares the app's 8080). Results in `results.csv`.
- The `os error 32` cargo AV file-lock workaround is `$env:CARGO_INCREMENTAL='0'; cargo test -j 1`.
- Windows `run_code` string mangles `=>`, `'''`, `;` in template literals — use a line-array
  `.join('\n')` instead of a single template literal.

## 7. Authoritative reference files

- `docs/REWRITE_PLAN.md` — the master spec (Phases 0-6).
- `.superpowers/sdd/phase-0-safety/progress.md` (worktree, gitignored) — the durable recovery
  map, most recent entries = this session's findings.
- `phase2h/README.md` — the Phase 2h contract (FFI + lab), updated with the bench finding.
- `phase2h/lab/bench/README.md` — the benchmark rationale + results table.
- `phase2h/lab/matrix.md` — the interactive Windows acceptance matrix.

## 8. Note for a new harness

- Do NOT run the app from the worktree (`Stet-wt-phase0`) — it has no backend. Run from main.
- Do NOT merge `crates/` into `main` until the interactive matrix passes (it is not wired in).
- The benchmark/per-config artifacts that are transient are the `out_*`/`err_*` logs in
  `phase2h/lab/bench/`; `results.csv` and `run_bench.ps1` are the durable ones.
