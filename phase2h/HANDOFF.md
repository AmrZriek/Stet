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

## 2. What was resolved

### (a) Model "broken output / truncated" — ROOT CAUSE FOUND & resolved
- **Cause:** `parallel=4` collapsed per-slot context (`n_ctx_slot = context_size / parallel = 1024`).
- **Fix:** Updated defaults to `parallel=1` across `stet/constants.py` and `stet/llm/model_manager.py` with UI controls (`parallel_spin` 1..16) in Model / Hardware settings.

### (b) Shift+F9 capture reliability in Chromium / WinUI apps — IMPLEMENTED & VERIFIED
- **Cause:** `_send_ctrl_chord` injected synthetic Shift-up and Ctrl+C in one batch while physical Shift was still held, causing deselection/modifier conflict in Chromium/WinUI.
- **Fix:** Implemented `_wait_for_modifiers_released(timeout_sec=0.20)` before injecting Ctrl+C chord in `stet/core/clipboard.py`.

### (c) Clipboard restore race gate — IMPLEMENTED & VERIFIED
- **Cause:** Sequence number mismatch caused `_restore_if_unchanged` to skip restoring `_old_clip` when sequence jumped by >1.
- **Fix:** Updated gate to verify `current == text` (or `current == result`) before restoring `_old_clip` in `stet/core/app.py`.

### (d) Native Win32 FFI workspace integration (Phase 2h) — WIRED & VERIFIED
- Wired raw Win32 FFI skeleton (`ffi-skel`) as `crates/stet-win32` in the `crates` workspace.
- All 189 Rust unit/integration tests and 8/8 interactive Win32 lab probes compile, link against MSVC import libs, and pass cleanly.

### (e) Phase 1 Unified Contracts & IPC Wire Protocol — IMPLEMENTED & VERIFIED
- **1a Contracts (`stet/core/input.py` & `crates/stet-core/src/index_types.rs`):** Implemented `TargetToken`, `UndoToken`, `SelectionCapture`, `SelectionResult`, `PasteResult`, and full Phase 1a `InputCode` error taxonomy.
- **1b IPC Protocol (`stet/core/ipc_client.py` & `crates/stet-core/src/frame.rs` / `handshake.rs`):** Implemented 8-byte length-prefixed JSON-RPC 2.0 framing with 4 MiB boundary, constant-time HMAC-SHA-256 handshake proof-of-possession, and state machine (`CONNECTING -> AUTHENTICATED -> READY -> CLOSING`).
- **1c Engine Protocol (`stet/core/engine_types.py`):** Defined `TaskSpec`, `BudgetPolicy`, `GuardSet`, `CorrectionRequest`, `CorrectionResult`, and `CorrectionEngine` entry point.
- **Verification (`tests/test_phase1_contracts.py`):** 12/12 new contract and wire protocol unit tests passing cleanly in both checkouts.

### (f) Phase 3 Correction Engine Re-Architecture — IMPLEMENTED & VERIFIED
- **3a ContextPlanner (`stet/core/context_planner.py`):** Closed budget arithmetic ($I(x) + G(I(x)) + S \le n_{\text{ctx,slot}}$), monolithic pass vs sequential windowed pass with 2-sentence context overlap and disjoint character-offset ownership intervals.
- **3b PromptCompiler (`stet/core/prompt_compiler.py`):** Unified prompt envelope with fresh 128-bit cryptographic nonces (`CONTENT_BEGIN_<nonce>` / `CONTENT_END_<nonce>`), structural rule wrapping, and unambiguous boundary extraction.
- **3c DocumentProtector (`stet/core/document_protector.py`):** Immutable atom table protecting URLs, paths, emails, code blocks, and user terms via `[REF1]`, `[REF2]`, ... with lossless restoration.
- **3d Validators & Reassembler (`stet/core/validators.py`, `stet/core/reassembler.py`):** Separated `UnitValidator` (hallucination, refusal, divergence ratio, placeholder loss, unrelated output guard) and `DocumentValidator`, with `OffsetReassembler` splicing owning intervals.
- **3e Unified Engine (`stet/core/engine.py`):** Production `CorrectionEngineImpl` orchestrating the full end-to-end pipeline.
- **Verification (`tests/test_phase3_engine.py`):** 15/15 unit and integration tests passing cleanly in both checkouts.

## 3. What is GENUINELY NOT DONE (the honest remaining work)

### Phase 2h native cutover verification against interactive target matrix
- `crates/stet-win32` is link-proven and unit-tested (189/189 tests + 8/8 lab probes pass).
- Cutover to full native core replacement requires running through every target class in `phase2h/lab/matrix.md` on a real Windows desktop session: Win32 Edit, RichEdit/Word, Chromium/Electron, terminals, UIPI, IME, RDP, NVDA/JAWS, Sticky/Filter keys.

### Phase 4 (macOS), Phase 5 (full Qt UI), Phase 6 (CI/OS matrix)
- Phase 4 blocked on Mac hardware (none available in this environment).
- Phase 5/6 need full UI + OS matrix + production wiring into `model_manager.py`.

## 4. Verification state / how to re-verify

- Rust workspace: `cd crates && $env:CARGO_INCREMENTAL='0'; cargo test -j 1` → 189/189 passed.
- Rust lab probes: `cd crates/stet-win32 && cargo test --features lab -j 1 -- --ignored --test-threads=1 lab_` → 8/8 passed.
- Python main test suite: `uv run --with-requirements requirements.txt --with-requirements requirements-dev.txt pytest` (in Stet) → 1293/1293 passed (0 failures, 3 skipped).
- Python worktree test suite: `uv run --with-requirements requirements.txt --with-requirements requirements-dev.txt pytest` (in Stet-wt-phase0) → 1403/1403 passed (0 failures, 9 skipped).
- Model benchmark: `cd phase2h/lab/bench && powershell -NoProfile -ExecutionPolicy Bypass -File run_bench.ps1` (port 8089).
## 5. Authoritative reference files

- `docs/REWRITE_PROGRESS.md` — the live phase-by-phase implementation ledger and test matrix.
- `docs/REWRITE_PLAN.md` — the master specification (Phases 0–6).
- `phase2h/README.md` — the Phase 2h contract (FFI + lab), updated with bench finding.
- `phase2h/lab/bench/README.md` — the benchmark rationale + results table.
- `phase2h/lab/matrix.md` — the interactive Windows acceptance matrix.
## 8. Note for a new harness

- Do NOT run the app from the worktree (`Stet-wt-phase0`) — it has no backend. Run from main.
- Do NOT merge `crates/` into `main` until the interactive matrix passes (it is not wired in).
- The benchmark/per-config artifacts that are transient are the `out_*`/`err_*` logs in
  `phase2h/lab/bench/`; `results.csv` and `run_bench.ps1` are the durable ones.
