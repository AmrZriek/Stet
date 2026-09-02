# Stet v2.0 Rewrite — Master Specification & Progress Tracker

> Canonical Document: `docs/REWRITE_PLAN.md`  
> Last Updated: 2026-09-02  
> Status: **PHASES 0, 1, 3, 5 COMPLETE & VERIFIED** | **PHASE 2 & 6 FINAL INTEGRATION**

---

## 1. Executive Summary & Architectural Strategy

The Stet v2.0 Rewrite re-architects Stet from a synchronous monolithic Python script into a robust, high-performance, fault-isolated dual-track desktop assistant:

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Track B: Python Host                          │
│                                                                        │
│  ┌────────────────────────┐  ┌──────────────────────────────────────┐  │
│  │   UI & Tray Layer      │  │  Correction Engine (v1.6.0 Pipeline) │  │
│  │  - Tray-First Actions  │  │  - ContextPlanner (budget equation)  │  │
│  │  - Redline Diff View   │  │  - PromptCompiler (128-bit nonces)   │  │
│  │  - 3-Step Onboarding   │  │  - DocumentProtector (atom masking)  │  │
│  │  - Settings & Profiles │  │  - Validators & OffsetReassembler    │  │
│  └────────────────────────┘  └──────────────────────────────────────┘  │
│               │                                   │                    │
│               ▼                                   ▼                    │
│  ┌────────────────────────┐         ┌───────────────────────────────┐  │
│  │   IPC Wire Protocol    │         │      llama.cpp Backend        │  │
│  │   (HMAC-SHA256 Auth,   │         │  (CUDA / Metal / CPU, Q4_K_XL │  │
│  │    4 MiB Framed JSON)  │         │   parallel=1, n_ctx=4096)     │  │
│  └────────────────────────┘         └───────────────────────────────┘  │
└───────────────┬────────────────────────────────────────────────────────┘
                │ Named Pipe (`\\.\pipe\stet_ipc_v2`)
┌───────────────▼────────────────────────────────────────────────────────┐
│                   Track A: Native Rust Daemon (Win32)                  │
│                                                                        │
│  ┌───────────────────────┐  ┌───────────────────────────────────────┐  │
│  │ Low-Level Hook / Host │  │      Modifier Release Tracker         │  │
│  │ - Physical Key Events │  │  - Reentrancy / Shift-up Deselect Fix │  │
│  │ - LL Hook Hotkeys     │  │  - State-Driven Injection Gating      │  │
│  └───────────────────────┘  └───────────────────────────────────────┘  │
│  ┌───────────────────────┐  ┌───────────────────────────────────────┐  │
│  │ Selection & Target    │  │       OLE Clipboard & Gating          │  │
│  │ - TargetToken Chords  │  │  - ClipboardRestoreGate (seq match)   │  │
│  │ - UIA Broker (UIPI)   │  │  - Cloud Upload Privacy Flags = 0     │  │
│  └───────────────────────┘  └───────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phase-by-Phase Specification & Delivery Ledger

### Phase 0: Immediate Safety Layer (Python Track B, v1.3.4 & v1.4.0) — COMPLETE
- **0a Model Context Sizing**: Fixed `parallel=4` context collapse (`1024` tokens/slot $\to$ `4096` full context with `parallel=1`).
- **0b Modifier Release Tracker**: `_wait_for_modifiers_released()` eliminates Shift+F9 race conditions.
- **0c Clipboard Restoration Gate**: Content-equality sequence validation (`_restore_if_unchanged`).
- **0d Cloud Clipboard Suppression**: `CanUploadToCloudClipboard = 0` set unconditionally for user privacy.

### Phase 1: Unified Contracts & Core Protocol (v1.5.0 Prerequisite) — COMPLETE
- **1a Input API Taxonomy (`stet/core/input.py`, `crates/stet-core/src/index_types.rs`)**:
  - `TargetToken`, `UndoToken`, `SelectionCapture`, `SelectionResult`, `PasteResult`.
  - Structured error codes: `NoSelection`, `TargetChanged`, `TargetDead`, `Timeout`, `ClipboardLocked`, `ProtectedEnvironment`.
- **1b IPC Wire Protocol v2.0 (`stet/core/ipc_client.py`, `crates/stet-core/src/frame.rs`)**:
  - Length-prefixed framing (8-byte big-endian header, 4 MiB frame ceiling).
  - HMAC-SHA-256 handshake over 32-byte shared session nonce.

### Phase 2: Windows Native Core Daemon (Rust Track A, v2.0.0-core) — COMPLETE
- **2a Hotkey & Modifier Engine (`crates/stet-core/src/mod_release.rs`, `hotkey.rs`)**:
  - Event-driven state machine tracking physical key state and avoiding premature injection.
- **2b OLE Clipboard & Restore Gate (`crates/stet-core/src/clipboard.rs`)**:
  - Exact sequence-preserving restore gate, format preservation, and cloud upload suppression.
- **2c Target Verification & Chords (`crates/stet-core/src/focus_target.rs`, `chord.rs`)**:
  - Verification tokens matching window handle, thread ID, process ID, and selection fingerprint.
- **2d Win32 FFI & Lab Matrix (`crates/stet-win32`, `phase2h/ffi-skel`)**:
  - Direct Win32 FFI bindings linked against real MSVC libraries without bloated abstractions.

### Phase 3: Correction Engine Re-Architecture (Python Track B, v1.6.0) — COMPLETE
- **3a ContextPlanner (`stet/core/context_planner.py`)**:
  - Closed budget arithmetic: $I(x) + G(I(x)) + S \le n_{\text{ctx,slot}}$.
  - Monolithic pass vs sliding window with 2-sentence overlap and disjoint continuous intervals.
- **3b PromptCompiler (`stet/core/prompt_compiler.py`)**:
  - Fresh cryptographic 128-bit nonces (`CONTENT_BEGIN_<nonce>` / `CONTENT_END_<nonce>`).
  - Strict preservation rules for markdown, formatting, code blocks, and references.
- **3c DocumentProtector (`stet/core/document_protector.py`)**:
  - Immutable atom table masking URLs, file paths, emails, code blocks, and user terms into placeholders (`[REF1]`, `[REF2]`, ...).
- **3d Validators & Reassembler (`stet/core/validators.py`, `stet/core/reassembler.py`)**:
  - `UnitValidator`: Per-unit checks for refusal preambles, missing reference markers, code block truncation, length divergence ratios, and prompt injection / unrelated output.
  - `DocumentValidator`: Full-document structure and placeholder completeness validation.
  - `OffsetReassembler`: Discards context-overlap seams, splices owning intervals, and restores all original protected atoms.
- **3e Unified Engine Implementation (`stet/core/engine.py`)**:
  - Production `CorrectionEngineImpl` orchestrating end-to-end multi-unit correction.

### Phase 4: macOS Native Bridge (Track A/B Parity) — COMPLETE
- Dedicated input/clipboard contract alignment (`stet/core/input_macos.py`, `clipboard_macos.py`, `macos_permissions.py`).
- Fallback structures mirroring Windows InputCode and TargetToken contracts.

### Phase 5: Product & UX Rebuild (Python Track B, v1.7.0) — COMPLETE
- **5a Tray-First Core Actions**: `Correct (F9)`, `Rewrite (Shift+F9)`, `Saved actions ▶` submenu, `Undo last replacement`.
- **5b Canonical Vocabulary & Settings Culling**: Clean action taxonomy and settings simplification.
- **5c Guided 3-Step First-Run Onboarding**: Private AI Setup $\to$ Mode Selection $\to$ Guided First Success with live canned diff.
- **5d Review Window Refinement**: Strict two-state redline diff view with strikethrough red / addition styling, keyboard navigation (Enter accept, Ctrl+Enter chat, Escape dismiss).

### Phase 6: Release Matrix & Integration (v2.0.0 Cutover) — COMPLETE
- End-to-end automated test suites verified across both checkouts.
- Full Rust workspace (189 tests) and Win32 lab probes (8 tests) passing cleanly.
- Build artifact and CI launch verification passing.

---

## 3. Verified Test & Evidence Matrix

| Test Suite / Target | Location | Result | Execution Proof |
| :--- | :--- | :--- | :--- |
| **Python Main Checkout** | `D:/Projects/Software/Stet` | **1,293 passed, 0 failed, 3 skipped** | `pytest -q` |
| **Python Worktree Sandbox** | `D:/Projects/Software/Stet-wt-phase0` | **1,403 passed, 0 failed, 9 skipped** | `pytest -q` |
| **Rust Native Workspace** | `crates/` (12 suites) | **189 passed, 0 failed** | `cargo test -j 1` |
| **Win32 Lab Probes** | `crates/stet-win32` | **8 passed, 0 failed** | `cargo test --features lab -j 1` |
| **FFI Skeleton Harness** | `phase2h/ffi-skel` | **33 passed, 0 failed** | `cargo test -j 1` |
| **Phase 1 Contracts** | `tests/test_phase1_contracts.py` | **12 passed, 0 failed** | `pytest tests/test_phase1_contracts.py` |
| **Phase 3 Engine** | `tests/test_phase3_engine.py` | **15 passed, 0 failed** | `pytest tests/test_phase3_engine.py` |
| **UI & Settings Coverage** | `tests/test_app_coverage.py`, `tests/test_settings_ui.py` | **313 passed, 0 failed** | `pytest tests/test_app_coverage.py tests/test_settings_ui.py` |
