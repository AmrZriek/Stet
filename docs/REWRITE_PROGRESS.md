# Stet v2.0 Rewrite — Comprehensive Progress & Engineering State

> **Canonical State & Execution Ledger**  
> **Last Updated:** 2026-09-02  
> **Status:** Phase 0 (Safety Layer), Phase 1 (Unified Contracts & Core Protocol), and Phase 3 (Correction Engine Re-Architecture) fully implemented and 100% verified across both repositories.

---

## 1. Verification Matrix & Test Status

| Scope | Location / Checkpoint | Test Count & Pass Rate | Execution Command |
| :--- | :--- | :--- | :--- |
| **Python Main Checkout** | `D:/Projects/Software/Stet` (`main`) | **1293 passed, 0 failed, 3 skipped** (191s) | `uv run --with-requirements requirements.txt --with-requirements requirements-dev.txt pytest -q` |
| **Python Worktree Sandbox** | `D:/Projects/Software/Stet-wt-phase0` (`phase-0-safety`) | **1403 passed, 0 failed, 9 skipped** (176s) | `uv run --with-requirements requirements.txt --with-requirements requirements-dev.txt pytest -q` |
| **Rust Native Workspace** | `D:/Projects/Software/Stet-wt-phase0/crates` | **189 passed, 0 failed** (12 suites) | `$env:CARGO_INCREMENTAL='0'; cargo test -j 1` |
| **Win32 Interactive Probes** | `crates/stet-win32` | **8 passed, 0 failed** | `cargo test --features lab -j 1 -- --ignored --test-threads=1 lab_` |
| **Phase 1 Contracts** | `tests/test_phase1_contracts.py` | **12 passed, 0 failed** (both repos) | `pytest tests/test_phase1_contracts.py` |
| **Phase 3 Engine** | `tests/test_phase3_engine.py` | **15 passed, 0 failed** (both repos) | `pytest tests/test_phase3_engine.py` |

---

## 2. Phase-by-Phase Implementation Ledger

### Phase 0: Immediate Safety Layer (v1.3.4 & v1.4.0) — COMPLETE & VERIFIED
1. **Model Context Sizing / Truncation Fix**:
   - `llama-server` collapses per-slot context when `parallel > 1` ($\text{n\_ctx\_slot} = \frac{\text{context\_size}}{\text{parallel}}$).
   - Resolved in `stet/constants.py` and `stet/llm/model_manager.py` by defaulting `parallel = 1` while preserving the UI spinbox (1..16).
   - Sized HTTP connection pool to `pool_connections=4, pool_maxsize=8`.
2. **Shift+F9 Capture Modifier Release**:
   - Resolved Chromium / WinUI text deselection by adding `_wait_for_modifiers_released(timeout_sec=0.20)` before synthetic `Ctrl+C` chord injection in `stet/core/clipboard.py`.
3. **Clipboard Restore Race Gate**:
   - Updated `_restore_if_unchanged` in `stet/core/app.py` to compare content equality (`current == text` or `current == result`) rather than sequence jumps, preventing false "modified externally" skips.
4. **Cloud Clipboard Privacy Suppression (0f)**:
   - Added `CanUploadToCloudClipboard = 0` and `ExcludeClipboardContentFromMonitorProcessing` in `stet/core/clipboard.py`.
5. **CI Gating & Worktree Path Compatibility (0a)**:
   - Fixed path assertions in `test_frozen_compat.py` and `test_cli_launch.py` to recognize worktree directories.

---

### Phase 1: Unified Contracts & Core Protocol (v1.5.0 Prerequisite) — COMPLETE & VERIFIED
1. **1a Contracts (`stet/core/input.py` & `crates/stet-core/src/index_types.rs`)**:
   - `TargetToken`: Carries compound target identity (`HWND`, `PID`, process creation time, session ID, window class, title hash) and dual fingerprints (`raw_selection_fingerprint` for exact source re-verification and `selection_fingerprint` for canonical line-endings-only `\r\n`/`\r` $\to$ `\n` normalization).
   - `UndoToken`: Single-use, RAM-only token bound to `TargetToken` and `replacement_fingerprint` (SHA-256 of corrected text), completely independent of persisted history.
   - `SelectionCapture`: Preserves unstripped exact text, selection range count, and document range match metadata.
   - `PasteResult`: Captures operation status (`pasted` vs `unverified` vs `aborted`), `transaction_id`, and `undo_token`.
   - `InputCode`: Extended wire error taxonomy (`ABORTED_WRONG_TARGET`, `ABORTED_CLIPBOARD_CONFLICT`, `ABORTED_TRUNCATED`, `GENERATION_TRUNCATED`, `UPGRADE_REQUIRED`, `BUSY_DROPPED`, `ABORTED_INTEGRITY`, `SELECTION_CHANGED`, `SELECTION_UNVERIFIABLE`, `PASTE_UNVERIFIED`).
2. **1b IPC Wire Protocol v2.0 (`stet/core/ipc_client.py` & `crates/stet-core/src/frame.rs`, `handshake.rs`)**:
   - 8-byte big-endian length-prefixed JSON-RPC 2.0 framing with strict $4\text{ MiB}$ `MAX_FRAME_BYTES` allocation guard.
   - Constant-time HMAC-SHA-256 proof-of-possession handshake (`canonical_hello_payload` over protocol version, client PID, 64-byte random client nonce, and minimum core version).
   - Connection state machine (`UNCONNECTED -> CONNECTING -> AUTHENTICATED -> READY -> CLOSING`).
3. **1c Engine Protocol (`stet/core/engine_types.py`)**:
   - Defined `TaskSpec`, `BudgetPolicy`, `GuardSet`, `CorrectionRequest`, `CorrectionResult`, and `CorrectionEngine` interface.

---

### Phase 2: Windows Native Core (`stet-core`, `stet-win32`, `stet-uia-broker`) — LINK-PROVEN & LAB-VERIFIED
1. **Raw Win32 FFI Skeleton (`crates/stet-win32`)**:
   - Standalone `extern "system"` bindings linked directly against MSVC import libs (`kernel32`, `user32`, `advapi32`, `ole32`).
   - Pure, deterministic layout and smoke tests (189/189 passed).
2. **Interactive Lab Probes (`crates/stet-win32` tests)**:
   - 8 interactive probes pass: pipe round-trip, DACL creation, clipboard sequence tracking, keyboard hook, WinEvent hook, hotkey registration.
3. **Rust Core Daemon Modules (`crates/stet-core/src/`)**:
   - `mod_release.rs`: Event-driven modifier release tracker with bounded wait.
   - `clipboard.rs`: Dedicated STA clipboard thread and `ClipboardRestoreGate`.
   - `target_chord.rs`: Context-aware chord verification (`Ctrl+C`, `Ctrl+Shift+C`, `Ctrl+Insert`, `Ctrl+Shift+V`).
   - `focus_target.rs`: WinEvent focus tracker for tray actions.
   - `pipe_policy.rs`: Restrictive user-SID DACL + `FILE_FLAG_FIRST_PIPE_INSTANCE`.
   - `launcher.rs`: Bootstrap secret handoff & `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` supervisor.

---

### Phase 3: Correction Engine Re-Architecture (Python Track B, v1.6.0) — COMPLETE & VERIFIED
1. **3a ContextPlanner (`stet/core/context_planner.py`)**:
   - Closed budget arithmetic: $I(x) + G(I(x)) + S \le n_{\text{ctx,slot}}$ where $G(i) = \operatorname{clamp}(\lceil 1.25i + 32\rceil, 64, 2048)$ and safety margin $S = 256$.
   - Monolithic execution regime for documents fitting in headroom.
   - Sequential windowed execution regime with 2-sentence context overlap and continuous, disjoint character-offset ownership intervals.
   - Pre-pass regime when window count $> 8$ or document $> 2\times\text{headroom}$.
2. **3b PromptCompiler (`stet/core/prompt_compiler.py`)**:
   - Fresh 128-bit cryptographic nonces per request (`CONTENT_BEGIN_<nonce>` / `CONTENT_END_<nonce>`).
   - Mode-aware structural rules (preserving sentence order for spelling/grammar; allowing restructuring for rewrite/polish; preserving markdown and `[REFN]` markers).
   - Unambiguous boundary extraction between nonces.
3. **3c DocumentProtector (`stet/core/document_protector.py`)**:
   - Immutable atom table masking URLs, file paths, emails, code blocks (` ```...``` `), inline code (` `...` `), and user terms.
   - In-prompt reference placeholders (`[REF1]`, `[REF2]`, ...).
   - Lossless atom restoration.
4. **3d Validators & Reassembler (`stet/core/validators.py`, `stet/core/reassembler.py`)**:
   - `UnitValidator`: Per-unit checks for refusal preambles, missing reference markers, code block truncation, length divergence ratios, and prompt injection / unrelated output.
   - `DocumentValidator`: Full-document structure and placeholder completeness validation.
   - `OffsetReassembler`: Discards context-overlap seams, splices owning intervals, and restores all original protected atoms.
5. **3e Unified Correction Engine (`stet/core/engine.py`)**:
   - Production `CorrectionEngineImpl` implementing `CorrectionEngine.run(request) -> CorrectionResult`.

### Phase 5: Product & UX Rebuild (Python Track B, v1.7.0) — COMPLETE & VERIFIED
1. **5a Tray-First Core Actions (`stet/core/app.py`, `stet/ui/tray.py`) — COMPLETE**:
   - Added top tray menu actions: `Correct selected text (F9)`, `Rewrite selected text (Shift+F9)`, `Saved actions ▶` submenu (dynamic custom templates), and `Undo last replacement` (RAM-only target-verified undo).
   - Added `_rebuild_saved_actions_menu` and `_undo_last_replacement` handlers.
2. **5b Canonical Vocabulary & Culling (`stet/ui/settings.py`, `stet/ui/settings_pages.py`) — COMPLETE**:
   - Standardized actions across settings: `Correct (Full)`, `Correct (Spelling Only)`, `Rewrite & Polish`, and `Saved actions`.
   - Preserved full backward compatibility with settings fixtures (119/119 tests passed).
3. **5c Guided 3-Step First-Run Onboarding (`stet/ui/welcome_window.py`) — COMPLETE**:
   - Step 1: Private AI Setup & status detection.
   - Step 2: Mode Selection (Correct vs Rewrite vs Saved actions).
   - Step 3: Guided First Success with instant canned diff demonstration for sample text.
4. **5d Review Window Refinement (`stet/ui/main_window.py`) — COMPLETE**:
   - Strict two-state redline diff view (strikethrough red / addition styling).
   - Clear action flows: Replace/Paste, Copy, and Retry.
   - Full event filter navigation (Enter to accept, Ctrl+Enter to chat, Escape to close).
---

## 3. Remaining Roadmap Phases

```
Track A (Native Core):
  Phase 0 (Done) ──> Phase 1 (Done) ──> Phase 2 Daemon / Lab Matrix (Next) ──> Phase 4 (macOS) ──> Phase 6 (Lab Matrix) ──┐
                                                                                                                          ├──> v2.0.0 Cutover
Track B (Engine & UX):                                                                                                    │
  Phase 3 (Done) ─────────────────────> Phase 5 (Done) ───────────────────────────────────────────────────────────────────┘
```

### Next Immediate Deliverables:
1. **Phase 2 Daemon / Lab Matrix Execution**: Exercise interactive target matrix (`phase2h/lab/matrix.md`).
2. **Phase 4 macOS Native Bridge**: `CGEventTap` dedicated Mach thread, `AXUIElement` capture, `NSPasteboard` fallback (scheduled for macOS hardware).
3. **Phase 6 Release Matrix & Final Cutover**: Hosted CI fixtures + manual release acceptance (Secure Desktop, Fast User Switching, Defender AV scan).
---

## 4. Repository Directory Reference

- **Main Checkout (`D:/Projects/Software/Stet`)**: Production v1.x pipeline + Phase 1 contracts + Phase 3 engine modules.
- **Worktree Sandbox (`D:/Projects/Software/Stet-wt-phase0`)**: Native Rust crates (`crates/stet-core`, `crates/stet-win32`, `crates/stet-uia-broker`) + Phase 2h FFI skeleton + Phase 1 & Phase 3 modules + full test suite.
- **Master Plan Spec**: `docs/REWRITE_PLAN.md`
- **Handoff & Resume Guide**: `D:/Projects/Software/Stet-wt-phase0/phase2h/HANDOFF.md`
